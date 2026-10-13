"""LangGraph orchestration of the modernization pipeline.

    ingest -> analyze -> understand -> baseline -> plan -> approve_plan (HUMAN)
        -> [ migrate_unit -> approve_unit (HUMAN) -> commit_unit ] x N -> finalize

State is deliberately small; heavy artifacts (analysis, plan, unit results,
generated code) are JSON files in the run's artifact directory, so the API
and UI can read them at any time and the checkpoint stays cheap.
"""
from __future__ import annotations

import functools
import json
import shutil
import sqlite3
from pathlib import Path
from typing import TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from ..agents import docs as docs_agent
from ..agents import rules as rules_agent
from ..agents import security as security_agent
from ..agents.planner import build_plan
from ..agents.testgen import build_test_plan
from ..agents.translator import migrate_unit
from ..cobol.codegen import module_name_for
from ..cobol.project import (Project, build_graph, paragraph_calls, program_metrics, scan_project,
                             source_text)
from ..config import settings
from ..llm.provider import get_provider, llm_events
from . import gitops
from .store import store

_projects: dict[str, Project] = {}


class State(TypedDict, total=False):
    run_id: str
    options: dict
    order: list[str]
    cursor: int
    results: dict
    decision: dict
    status: str


# ----------------------------------------------------------------- helpers
def project_for(run_id: str) -> Project:
    if run_id not in _projects:
        _projects[run_id] = scan_project(store.source_dir(run_id))
    return _projects[run_id]


def _emit(run_id: str, stage: str, msg: str, level: str = "info", data: dict | None = None) -> None:
    store.emit(run_id, stage, msg, level, data)


def _stage(run_id: str, stage: str, status: str = "running") -> None:
    store.update(run_id, stage=stage, status=status)


def _model_summary(m) -> dict:
    return {
        "files": [{"name": f.name, "assign": f.assign, "organization": f.organization, "status": f.status,
                   "records": f.records} for f in m.files],
        "data": [{"level": d.level, "name": d.name, "pic": d.pic, "usage": d.usage,
                  "occurs": d.occurs, "redefines": d.redefines, "parent": d.parent,
                  "value": (d.value.get("v") if isinstance(d.value, dict) else None),
                  "section": d.section, "flags": d.flags} for d in m.data],
        "paragraphs": [{"name": p.name, "section": p.section, "line": m.origin(p.line + 1)[1],
                        "end_line": m.origin(p.end_line + 1)[1], "statements": len(p.stmts)}
                       for p in m.paragraphs],
        "calls": m.calls,
        "copybooks": m.copybooks,
        "missing_copybooks": m.missing_copybooks,
        "unsupported": [{"line": m.origin(u.line)[1], "file": m.origin(u.line)[0], "construct": u.construct,
                         "text": u.text[:140], "where": u.where} for u in m.unsupported],
        "paragraph_calls": paragraph_calls(m),
    }


def _llm_node(stage: str):
    """Report Groq rate-limit waits made inside this node to the run log."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(state):
            run_id = state["run_id"]
            with llm_events(lambda msg: _emit(run_id, stage, msg, "warning")):
                return fn(state)
        return wrapper
    return deco


# ------------------------------------------------------------------- nodes
def n_ingest(state: State) -> dict:
    run_id = state["run_id"]
    _stage(run_id, "ingest")
    _emit(run_id, "ingest", "Scanning repository")
    project = scan_project(store.source_dir(run_id))
    _projects[run_id] = project
    if not project.programs:
        raise RuntimeError("no COBOL programs (.cbl/.cob/.cobol) found in the repository")
    out = store.output_dir(run_id)
    gitops.init_repo(out)
    legacy = out / "legacy"
    legacy.mkdir(parents=True, exist_ok=True)
    for p in store.source_dir(run_id).rglob("*"):
        if p.is_file() and ".git" not in p.parts and p.suffix.lower() in (".cbl", ".cob", ".cobol", ".cpy", ".copy"):
            dest = legacy / p.relative_to(store.source_dir(run_id))
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(p, dest)
    commit = gitops.commit_all(out, f"Import legacy COBOL sources ({len(project.programs)} programs)")
    _emit(run_id, "ingest", f"Found {len(project.programs)} program(s), {len(project.copybooks)} copybook(s)"
          + (f"; legacy sources committed ({commit})" if commit else ""),
          data={"programs": list(project.programs), "copybooks": list(project.copybooks)})
    for err in project.errors:
        _emit(run_id, "ingest", err, "warning")
    return {"status": "ingested"}


def n_analyze(state: State) -> dict:
    run_id = state["run_id"]
    _stage(run_id, "analyze")
    project = project_for(run_id)
    llm = get_provider()
    programs: dict[str, dict] = {}
    all_findings: list[dict] = []
    for key, m in project.programs.items():
        _emit(run_id, "analyze", f"Parsing {m.path}")
        findings = security_agent.scan_program(m)
        all_findings += findings
        mt = program_metrics(m)
        programs[key] = {
            "program_id": m.program_id, "path": m.path, "module": module_name_for(m.program_id, key),
            "metrics": mt, "security": findings, "model": _model_summary(m),
            "rules": {"summary": "", "rules": []}, "baseline": {"oracle": "none", "cases": [], "notes": []},
            "doc": "",
        }
        _emit(run_id, "analyze",
              f"{m.program_id}: {mt['loc']} lines, {mt['paragraphs']} paragraphs, complexity {mt['complexity']}, "
              f"{mt['unsupported']} unsupported construct(s), {len(findings)} finding(s)")
    analysis = {
        "project": {"programs": len(project.programs), "copybooks": sorted(project.copybooks),
                    "loc": sum(p["metrics"]["loc"] for p in programs.values()), "errors": project.errors},
        "graph": build_graph(project),
        "programs": programs,
        "security": {"findings": all_findings, "counts": security_agent.severity_counts(all_findings)},
        "llm": {"provider": llm.name, "model": llm.model, "available": llm.available()},
    }
    store.write_artifact(run_id, "analysis.json", analysis)
    return {"status": "analyzed"}


@_llm_node('understand')
def n_understand(state: State) -> dict:
    run_id = state["run_id"]
    _stage(run_id, "understand")
    project = project_for(run_id)
    llm = get_provider()
    analysis = store.read_artifact(run_id, "analysis.json")
    findings = analysis["security"]["findings"]
    for key, m in project.programs.items():
        src = source_text(project, key)
        _emit(run_id, "understand", f"Extracting business rules from {m.program_id}"
              + (" with LLM" if llm.available() else " (static analysis, no LLM configured)"))
        rules = rules_agent.extract_rules(m, src, llm)
        _emit(run_id, "understand", f"{m.program_id}: {len(rules['rules'])} rule(s)"
              + (" interpreted by the LLM" if rules.get("used_llm") else ""))
        analysis["programs"][key]["rules"] = {"summary": rules["summary"], "rules": rules["rules"],
                                              "used_llm": rules.get("used_llm", False)}
        doc = docs_agent.generate_doc(m, src, rules, findings, llm)
        analysis["programs"][key]["doc"] = doc
        out_docs = store.artifact_dir(run_id) / "docs"
        out_docs.mkdir(exist_ok=True)
        (out_docs / f"{m.program_id}.md").write_text(doc, encoding="utf-8")
    store.write_artifact(run_id, "analysis.json", analysis)
    return {"status": "understood"}


@_llm_node('baseline')
def n_baseline(state: State) -> dict:
    run_id = state["run_id"]
    _stage(run_id, "baseline")
    project = project_for(run_id)
    llm = get_provider()
    analysis = store.read_artifact(run_id, "analysis.json")
    for key, m in project.programs.items():
        src = source_text(project, key)
        _emit(run_id, "baseline", f"Establishing behavioural baseline for {m.program_id}")
        plan = build_test_plan(project, key, m, src, llm, store.work_dir(run_id) / key)
        store.write_artifact(run_id, f"baseline/{key}.json", {"oracle": plan.oracle, "cases": plan.cases,
                                                             "notes": plan.notes})
        analysis["programs"][key]["baseline"] = {
            "oracle": plan.oracle, "notes": plan.notes,
            "cases": [{"name": c["name"], "description": c.get("description", ""), "source": c.get("source"),
                       "inputs": list(c["inputs"]), "expected_files": list(c.get("expected", {}).get("files", {}))}
                      for c in plan.cases],
        }
        level = "info" if plan.trusted else "warning"
        _emit(run_id, "baseline", f"{m.program_id}: {len(plan.cases)} case(s), oracle = {plan.oracle}", level)
        for note in plan.notes:
            _emit(run_id, "baseline", note, "warning")
    store.write_artifact(run_id, "analysis.json", analysis)
    return {"status": "baselined"}


@_llm_node('plan')
def n_plan(state: State) -> dict:
    run_id = state["run_id"]
    _stage(run_id, "plan")
    project = project_for(run_id)
    analysis = store.read_artifact(run_id, "analysis.json")
    _emit(run_id, "plan", "Building migration plan")
    plan = build_plan(project, analysis, get_provider())
    store.write_artifact(run_id, "plan.json", plan)
    _emit(run_id, "plan", f"Plan ready: {len(plan['units'])} unit(s)")
    return {"status": "planned"}


def n_approve_plan(state: State) -> dict:
    run_id = state["run_id"]
    plan = store.read_artifact(run_id, "plan.json")
    decision = interrupt({"type": "plan_approval", "message": "Review the migration plan before any code is generated",
                          "units": [u["program"] for u in plan["units"]]})
    approved = bool(decision.get("approved"))
    chosen = decision.get("programs") or [u["program"] for u in plan["units"]]
    order = [u["program"] for u in plan["units"] if u["program"] in chosen]
    store.write_artifact(run_id, "plan_decision.json", decision)
    if not approved or not order:
        _emit(run_id, "plan", "Plan rejected - nothing was migrated", "warning")
        return {"status": "rejected", "order": []}
    _emit(run_id, "plan", f"Plan approved for {len(order)} unit(s): {', '.join(order)}")
    return {"order": order, "cursor": 0, "results": {}, "status": "plan_approved"}


@_llm_node('migrate')
def n_migrate(state: State) -> dict:
    run_id = state["run_id"]
    prog = state["order"][state["cursor"]]
    _stage(run_id, f"migrate:{prog}")
    project = project_for(run_id)
    m = project.programs[prog]
    llm = get_provider()
    base = store.read_artifact(run_id, f"baseline/{prog}.json", {"oracle": "none", "cases": []})
    _emit(run_id, "migrate", f"Migrating {m.program_id}")
    res = migrate_unit(m, source_text(project, prog), base["cases"], base["oracle"],
                       store.work_dir(run_id) / "build" / prog, llm, settings.max_repair_attempts,
                       emit=lambda msg: _emit(run_id, "migrate", msg))
    unit = res.to_dict()
    unit["code"] = res.code
    unit["cobol"] = source_text(project, prog)
    unit["decision"] = None
    store.write_artifact(run_id, f"units/{prog}.json", unit)
    level = "info" if res.status in ("verified", "provisional") else "warning"
    _emit(run_id, "migrate", f"{m.program_id}: {res.status} "
          f"({res.verification.get('passed', 0)}/{res.verification.get('total', 0)} cases, oracle {res.oracle})", level)
    results = dict(state.get("results", {}))
    results[prog] = {"status": res.status, "oracle": res.oracle, "module": res.module_name,
                     "passed": res.verification.get("passed", 0), "total": res.verification.get("total", 0)}
    return {"results": results}


def n_approve_unit(state: State) -> dict:
    run_id = state["run_id"]
    prog = state["order"][state["cursor"]]
    info = state["results"][prog]
    opts = state.get("options", {})
    if opts.get("auto_approve_verified") and info["status"] == "verified":
        _emit(run_id, "approve", f"{prog}: auto-approved (verified against {info['oracle']})")
        return {"decision": {"decision": "approve", "auto": True}}
    decision = interrupt({"type": "unit_approval", "program": prog, "status": info["status"],
                          "message": f"Review the migrated {prog} before it is committed"})
    return {"decision": decision}


def _unit_tests_file(module: str, oracle: str) -> str:
    return f'''"""Regression tests for {module}, generated from the legacy behavioural baseline.

Baseline oracle: {oracle}. Each case feeds the original input files to the migrated
program and compares console output and output files byte for byte.
"""
import io
import json
from pathlib import Path

import pytest

import cobol_runtime
import {module}

CASES = json.loads((Path(__file__).parent / "cases" / "{module}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_behaviour_matches_legacy(case, tmp_path):
    for name, content in case["inputs"].items():
        (tmp_path / name).write_bytes(content.encode("latin-1"))
    out = io.StringIO()
    {module}.run(cobol_runtime.CobolIO(root=tmp_path, stdout=out, stdin_text=case.get("stdin", "")))
    assert out.getvalue() == case["expected"]["stdout"]
    for name, want in case["expected"].get("files", {{}}).items():
        assert (tmp_path / name).read_bytes().decode("latin-1") == want
'''


def n_commit(state: State) -> dict:
    run_id = state["run_id"]
    prog = state["order"][state["cursor"]]
    decision = state.get("decision") or {}
    unit = store.read_artifact(run_id, f"units/{prog}.json")
    results = dict(state["results"])
    blocked = unit["status"] in ("blocked", "failed")
    if decision.get("decision") == "approve" and blocked and not decision.get("force"):
        decision = {**decision, "decision": "reject",
                    "note": (decision.get("note", "") + " [not committed: unit is "
                             f"{unit['status']}; approve with force to commit known issues]").strip()}
    if decision.get("decision") != "approve":
        unit["decision"] = {"decision": decision.get("decision", "reject"), "note": decision.get("note", "")}
        store.write_artifact(run_id, f"units/{prog}.json", unit)
        results[prog] = {**results[prog], "decision": "rejected"}
        _emit(run_id, "approve", f"{prog}: rejected - not committed", "warning")
        return {"results": results}
    out = store.output_dir(run_id)
    module = unit["module"]
    shutil.copy(Path(__file__).resolve().parent.parent / "runtime" / "cobol_runtime.py", out / "cobol_runtime.py")
    (out / f"{module}.py").write_text(unit["code"], encoding="utf-8")
    cases = store.read_artifact(run_id, f"baseline/{prog}.json", {"cases": []})["cases"]
    if cases:
        (out / "tests" / "cases").mkdir(parents=True, exist_ok=True)
        (out / "tests" / "cases" / f"{module}.json").write_text(json.dumps(cases, indent=2), encoding="utf-8")
        (out / "tests" / f"test_{module}.py").write_text(_unit_tests_file(module, unit["oracle"]), encoding="utf-8")
        (out / "tests" / "conftest.py").write_text(
            "import sys\nfrom pathlib import Path\n\nsys.path.insert(0, str(Path(__file__).resolve().parent.parent))\n",
            encoding="utf-8")
    analysis = store.read_artifact(run_id, "analysis.json")
    doc = analysis["programs"][prog].get("doc")
    if doc:
        (out / "docs").mkdir(exist_ok=True)
        (out / "docs" / f"{analysis['programs'][prog]['program_id']}.md").write_text(doc, encoding="utf-8")
    ver = unit["verification"]
    msg = (f"Migrate {analysis['programs'][prog]['program_id']} to Python\n\n"
           f"Status: {unit['status']}\nBaseline oracle: {unit['oracle']}\n"
           f"Cases: {ver.get('passed', 0)}/{ver.get('total', 0)} passed\n"
           f"Translation attempts: {len(unit['attempts'])} "
           f"({', '.join(a['source'] for a in unit['attempts'])})")
    commit = gitops.commit_all(out, msg)
    unit["decision"] = {"decision": "approve", "note": decision.get("note", ""), "auto": bool(decision.get("auto")),
                        "commit": commit}
    store.write_artifact(run_id, f"units/{prog}.json", unit)
    results[prog] = {**results[prog], "decision": "approved", "commit": commit}
    _emit(run_id, "commit", f"{prog}: committed" + (f" ({commit})" if commit else ""))
    return {"results": results}


def n_advance(state: State) -> dict:
    return {"cursor": state["cursor"] + 1}


def n_finalize(state: State) -> dict:
    run_id = state["run_id"]
    _stage(run_id, "finalize")
    analysis = store.read_artifact(run_id, "analysis.json")
    plan = store.read_artifact(run_id, "plan.json")
    results = state.get("results", {})
    out = store.output_dir(run_id)
    lines = ["# Migration report", "", f"Run `{run_id}`", "",
             "| Program | Result | Oracle | Cases | Decision |", "|---|---|---|---|---|"]
    for u in plan["units"]:
        r = results.get(u["program"])
        if r:
            lines.append(f"| {u['program']} | {r['status']} | {r['oracle']} | {r['passed']}/{r['total']} | "
                         f"{r.get('decision', '-')} |")
        else:
            lines.append(f"| {u['program']} | not migrated | - | - | - |")
    lines += ["", "## Security findings carried from analysis", ""]
    for f in analysis["security"]["findings"]:
        lines.append(f"- **{f['severity']}** {f['program']} {f['rule']}: {f['title']} ({f['file']}:{f['line']})")
    lines += ["", "## How to verify", "", "```", "pip install pytest", "pytest tests", "```", ""]
    (out / "MIGRATION_REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    (out / "README.md").write_text(
        "# Migrated application\n\nGenerated by Legacy Code Automation.\n\n"
        "- `legacy/` original COBOL sources\n- `*.py` migrated programs (`run()` entry points)\n"
        "- `cobol_runtime.py` COBOL data/I-O semantics (standard library only)\n"
        "- `tests/` regression tests derived from the legacy baseline\n- `docs/` generated documentation\n",
        encoding="utf-8")
    commit = gitops.commit_all(out, "Add migration report")
    approved = [p for p, r in results.items() if r.get("decision") == "approved"]
    report = {"approved": approved, "results": results, "commit": commit,
              "output_dir": str(out), "security": analysis["security"]["counts"]}
    store.write_artifact(run_id, "report.json", report)
    _emit(run_id, "finalize", f"Migration finished: {len(approved)}/{len(plan['units'])} unit(s) approved",
          data={"approved": approved})
    return {"status": "completed"}


# ------------------------------------------------------------------ routing
def route_after_plan(state: State) -> str:
    return "migrate" if state.get("order") else "end"


def route_after_approval(state: State) -> str:
    return "migrate" if (state.get("decision") or {}).get("decision") == "retry" else "commit"


def route_after_advance(state: State) -> str:
    return "migrate" if state["cursor"] < len(state["order"]) else "finalize"


def build_graph_app(checkpointer):
    g = StateGraph(State)
    g.add_node("ingest", n_ingest)
    g.add_node("analyze", n_analyze)
    g.add_node("understand", n_understand)
    g.add_node("baseline", n_baseline)
    g.add_node("plan", n_plan)
    g.add_node("approve_plan", n_approve_plan)
    g.add_node("migrate", n_migrate)
    g.add_node("approve_unit", n_approve_unit)
    g.add_node("commit", n_commit)
    g.add_node("advance", n_advance)
    g.add_node("finalize", n_finalize)
    g.add_edge(START, "ingest")
    g.add_edge("ingest", "analyze")
    g.add_edge("analyze", "understand")
    g.add_edge("understand", "baseline")
    g.add_edge("baseline", "plan")
    g.add_edge("plan", "approve_plan")
    g.add_conditional_edges("approve_plan", route_after_plan, {"migrate": "migrate", "end": END})
    g.add_edge("migrate", "approve_unit")
    g.add_conditional_edges("approve_unit", route_after_approval, {"migrate": "migrate", "commit": "commit"})
    g.add_edge("commit", "advance")
    g.add_conditional_edges("advance", route_after_advance, {"migrate": "migrate", "finalize": "finalize"})
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer)


_app = None


def get_app():
    global _app
    if _app is None:
        conn = sqlite3.connect(store.workspace / "checkpoints.db", check_same_thread=False)
        _app = build_graph_app(SqliteSaver(conn))
    return _app

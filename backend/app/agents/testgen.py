"""Behavioural baseline: pick the strongest available oracle for each program.

Trust order (the UI shows which one was used):
  cobc            original COBOL compiled with GnuCOBOL and executed
  fixture         golden cases shipped in the repo (hand-computed or recorded)
  llm-unverified  cases proposed by the LLM - NOT trustworthy, needs human review
  none            no baseline: the migration cannot be verified
"""
from __future__ import annotations

import logging
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from ..cobol.parser import ProgramModel
from ..cobol.project import Project
from ..llm.provider import LLMError, LLMProvider, complete_json
from ..verification import harness

log = logging.getLogger(__name__)

TRUST = {"cobc": 3, "fixture": 2, "llm-unverified": 1, "none": 0}


@dataclass
class TestPlan:
    cases: list[dict] = field(default_factory=list)
    oracle: str = "none"
    notes: list[str] = field(default_factory=list)

    @property
    def trusted(self) -> bool:
        return TRUST[self.oracle] >= 2


def record_layouts(model: ProgramModel) -> str:
    """Fixed-width layout of every FD record, for prompts that must build input lines."""
    by_parent: dict[str | None, list] = {}
    for d in model.data:
        if d.level != 88:
            by_parent.setdefault(d.parent, []).append(d)

    def size_of(d) -> int:
        if d.info:
            return d.info["size"] * (d.occurs or 1)
        total = sum(size_of(c) for c in by_parent.get(d.name, []) if not c.redefines)
        return total * (d.occurs or 1)

    lines: list[str] = []
    for f in model.files:
        for rec in f.records:
            item = model.item(rec)
            if not item:
                continue
            lines.append(f"File {f.assign} ({f.organization}), record {rec}, {size_of(item)} bytes:")
            offset = 0

            def walk(d, depth=0) -> None:
                nonlocal offset
                kids = by_parent.get(d.name, [])
                if d.info:
                    desc = (f"PIC {d.pic} {d.usage if d.usage != 'DISPLAY' else ''}".strip())
                    lines.append(f"  {'  ' * depth}{d.name}: offset {offset}, {size_of(d)} bytes, {desc}")
                    offset += size_of(d)
                else:
                    lines.append(f"  {'  ' * depth}{d.name}: group")
                    for k in kids:
                        if not k.redefines:
                            walk(k, depth + 1)
            walk(item)
    return "\n".join(lines)


def _llm_cases(model: ProgramModel, source: str, llm: LLMProvider, with_expected: bool) -> list[dict]:
    system = ("You design regression tests for a COBOL batch program. Inputs must be exact fixed-width "
              "records matching the layouts. Cover normal flow, boundaries of every threshold and error paths.")
    shape = ('{"cases": [{"name": str, "description": str, "inputs": {"<assigned file name>": "<file text, '
             'lines separated by \\n>"}' + (', "expected_stdout": str, "expected_files": {"<name>": str}' if with_expected else "")
             + "}]}")
    user = (f"PROGRAM {model.program_id}\n\n=== SOURCE ===\n{source[:max(2000, min(12000, llm.prompt_char_budget(2500) - len(record_layouts(model)) - 1500))]}\n\n=== RECORD LAYOUTS ===\n"
            f"{record_layouts(model)}\n\nReturn JSON {shape}. Provide 3-5 cases. Numeric DISPLAY fields are plain "
            "digits (implied decimal point, no '.'), zero padded to full width; alphanumeric fields are space padded.")
    data = complete_json(llm, system, user, max_tokens=2500)
    cases = []
    for c in data.get("cases", []):
        if not isinstance(c, dict) or not isinstance(c.get("inputs"), dict):
            continue
        case = {"name": str(c.get("name", f"case_{len(cases) + 1}")), "description": str(c.get("description", "")),
                "inputs": {str(k): str(v) for k, v in c["inputs"].items()}}
        if with_expected:
            case["expected"] = {"stdout": str(c.get("expected_stdout", "")),
                                "files": {str(k): str(v) for k, v in (c.get("expected_files") or {}).items()}}
        cases.append(case)
    return cases


def build_test_plan(project: Project, key: str, model: ProgramModel, source: str,
                    llm: LLMProvider, work_dir: Path) -> TestPlan:
    plan = TestPlan()
    fixtures = harness.load_fixture_cases(project.root, model.program_id)
    cobc = harness.find_cobc()
    if cobc:
        exe, err = None, ""
        build_dir = work_dir / "cobc"
        build_dir.mkdir(parents=True, exist_ok=True)
        src = project.root / model.path
        copy_dirs = [p.parent for p in project.root.rglob("*") if p.suffix.lower() in (".cpy", ".copy")]
        exe, err = harness.compile_cobol(cobc, src, sorted(set(copy_dirs)), build_dir)
        if exe is None:
            plan.notes.append(f"cobc failed to compile {model.path}: {err[:300]}")
        else:
            inputs = [c for c in fixtures]
            if not inputs and llm.available():
                try:
                    inputs = _llm_cases(model, source, llm, with_expected=False)
                except LLMError as exc:
                    plan.notes.append(f"LLM could not propose inputs: {exc}")
            for c in inputs:
                res = harness.run_cobol_case(exe, c)
                if res["status"] != "ok":
                    plan.notes.append(f"cobc run failed for case {c['name']}: {res.get('error')}")
                    continue
                case = {"name": c["name"], "description": c.get("description", ""), "inputs": c["inputs"],
                        "expected": {"stdout": res["stdout"], "files": res["files"]}, "source": "cobc"}
                if c.get("expected") and c["expected"] != case["expected"]:
                    plan.notes.append(f"fixture {c['name']} disagrees with the original program run under GnuCOBOL; "
                                      "GnuCOBOL output was used")
                plan.cases.append(case)
            if plan.cases:
                plan.oracle = "cobc"
                return plan
    if fixtures:
        plan.cases, plan.oracle = fixtures, "fixture"
        plan.notes.append("expected outputs come from fixtures shipped with the repo; "
                          "no COBOL compiler (cobc) was available to cross-check them")
        return plan
    if llm.available():
        try:
            cases = _llm_cases(model, source, llm, with_expected=True)
            for c in cases:
                c["source"] = "llm-unverified"
            if cases:
                plan.cases, plan.oracle = cases, "llm-unverified"
                plan.notes.append("no fixtures and no COBOL compiler: expected outputs were predicted by the LLM "
                                  "and are NOT independently verified - review them before trusting the result")
                return plan
        except LLMError as exc:
            plan.notes.append(f"LLM test generation failed: {exc}")
    plan.notes.append("no behavioural baseline available (no fixtures, no cobc, no LLM)")
    return plan

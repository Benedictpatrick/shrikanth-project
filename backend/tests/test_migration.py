"""Parser, translator, verification loop and pipeline tests against the sample repo."""
import re
import time
from pathlib import Path

import pytest

from app.agents.security import scan_program
from app.agents.translator import migrate_unit
from app.cobol.codegen import translate
from app.cobol.parser import parse_program
from app.cobol.project import build_graph, migration_order, program_metrics, scan_project
from app.verification import harness

from conftest import SAMPLE


def model(name):
    return parse_program(SAMPLE / "src" / f"{name}.cbl")


# ----------------------------------------------------------------- parsing
def test_parser_reads_structure_and_copybook():
    m = model("INTCALC")
    assert m.program_id == "INTCALC"
    assert m.copybooks == ["ACCTREC"]
    assert [f.assign for f in m.files] == ["ACCOUNTS.DAT", "POSTED.DAT"]
    assert m.files[0].status == "WS-IN-STATUS" and m.files[0].records == ["ACCT-REC"]
    assert [p.name for p in m.paragraphs][:3] == ["0000-MAIN", "1000-INITIALIZE", "2000-PROCESS-ACCOUNT"]
    assert not m.unsupported
    conds = [d for d in m.data if d.level == 88]
    assert {d.name for d in conds} >= {"ACCT-SAVINGS", "ACCT-CHECKING", "ACCT-PREMIUM", "END-OF-ACCOUNTS"}


def test_parser_flags_unsupported_constructs():
    m = model("NAMEFMT")
    assert {u.construct for u in m.unsupported} == {"STRING statement", "INSPECT statement"}
    t = translate(m)
    assert not t.complete and len(t.issues) == 2
    assert "raise NotImplementedError" in t.code


def test_line_numbers_point_at_original_source():
    m = model("PAYROLL")
    findings = scan_program(m)
    cred = next(f for f in findings if f["rule"] == "SEC-001")
    src = (SAMPLE / "src" / "PAYROLL.cbl").read_text().splitlines()
    assert "WS-ADMIN-PASSWORD" in src[cred["line"] - 1]


def test_continuation_lines_and_free_format(tmp_path):
    p = tmp_path / "T.cbl"
    p.write_text(
        "       IDENTIFICATION DIVISION.\n       PROGRAM-ID. T.\n       DATA DIVISION.\n"
        "       WORKING-STORAGE SECTION.\n       01  A PIC X(40) VALUE 'A LONG LITERAL THAT NEEDS TO CONTI\n"
        "      -    'NUE ON THE NEXT LINE'.\n       PROCEDURE DIVISION.\n       MAIN.\n           DISPLAY A.\n"
        "           STOP RUN.\n")
    m = parse_program(p)
    assert m.program_id == "T"
    assert m.item("A").value == {"t": "str", "v": "A LONG LITERAL THAT NEEDS TO CONTINUE ON THE NEXT LINE"}


def test_security_findings_on_payroll():
    rules = {f["rule"] for f in scan_program(model("PAYROLL"))}
    assert {"SEC-001", "SEC-002", "SEC-008"} <= rules


def test_graph_and_order():
    project = scan_project(SAMPLE)
    assert set(project.programs) == {"INTCALC", "PAYROLL", "NAMEFMT"}
    g = build_graph(project)
    assert any(e["type"] == "copy" and e["target"] == "copybook:ACCTREC" for e in g["edges"])
    assert any(n["type"] == "file" and n["label"] == "ACCOUNTS.DAT" for n in g["nodes"])
    assert sorted(migration_order(project)) == ["INTCALC", "NAMEFMT", "PAYROLL"]
    assert program_metrics(project.programs["PAYROLL"])["complexity"] > 5


# ------------------------------------------------------------- verification
@pytest.mark.parametrize("prog,cases", [("INTCALC", 4), ("PAYROLL", 2)])
def test_rule_based_translation_matches_golden_cases(tmp_path, prog, cases):
    from app.llm.provider import NullProvider
    project = scan_project(SAMPLE)
    m = project.programs[prog]
    golden = harness.load_fixture_cases(SAMPLE, prog)
    assert len(golden) == cases
    res = migrate_unit(m, (SAMPLE / m.path).read_text(), golden, "fixture", tmp_path, NullProvider(), 0)
    assert res.status == "verified", [r for r in res.verification["results"] if not r["passed"]]
    assert res.verification["passed"] == cases


def test_unsupported_unit_is_blocked_without_llm(tmp_path):
    from app.llm.provider import NullProvider
    m = model("NAMEFMT")
    res = migrate_unit(m, (SAMPLE / m.path).read_text(), harness.load_fixture_cases(SAMPLE, "NAMEFMT"),
                       "fixture", tmp_path, NullProvider(), 3)
    assert res.status == "blocked"
    assert len(res.attempts) == 1


def test_verifier_detects_a_wrong_translation(tmp_path):
    from app.llm.provider import NullProvider
    m = model("INTCALC")
    t = translate(m)
    broken = t.code.replace("rounded=True", "rounded=False")  # classic COBOL ROUNDED regression
    path = tmp_path / "intcalc.py"
    import shutil
    from app.agents.translator import RUNTIME_SRC
    shutil.copy(RUNTIME_SRC, tmp_path / "cobol_runtime.py")
    path.write_text(broken)
    ver = harness.verify(path, harness.load_fixture_cases(SAMPLE, "INTCALC"))
    assert ver["failed"] > 0
    assert any("3.12" in line or "3.13" in line for r in ver["results"] for line in r["diff"])


class ScriptedLLM:
    """Stands in for Groq: implements the two STRING/INSPECT stubs the way a good model would."""
    name, model = "scripted", "test"

    def __init__(self):
        self.calls = 0

    def available(self):
        return True

    def prompt_char_budget(self, max_tokens):
        return 30000

    def complete(self, system, user, **_):
        self.calls += 1
        code = re.search(r"=== CURRENT PYTHON MODULE ===\n```python\n(.*?)\n```", user, re.S).group(1)
        out = []
        for line in code.splitlines():
            if "raise NotImplementedError" in line and "STRING statement" in line:
                pad = line[: len(line) - len(line.lstrip())]
                out.append(pad + "f.ws_formatted.move(f.in_last.text.split(' ')[0] + ', ' + f.in_first.text.split(' ')[0])")
            elif "raise NotImplementedError" in line and "INSPECT statement" in line:
                pad = line[: len(line) - len(line.lstrip())]
                out.append(pad + "f.ws_formatted.move(f.ws_formatted.text.upper())")
            else:
                out.append(line)
        return "```python\n" + "\n".join(out) + "\n```"


def test_llm_repair_loop_completes_unsupported_unit(tmp_path):
    llm = ScriptedLLM()
    m = model("NAMEFMT")
    res = migrate_unit(m, (SAMPLE / m.path).read_text(), harness.load_fixture_cases(SAMPLE, "NAMEFMT"),
                       "fixture", tmp_path, llm, 3)
    assert res.status == "verified"
    assert [a.source for a in res.attempts] == ["rules", "llm-repair"]
    assert llm.calls == 1


# ------------------------------------------------------------------ pipeline
def test_full_pipeline_through_api():
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)

    def wait(run_id, states, timeout=120):
        end = time.time() + timeout
        while time.time() < end:
            run = client.get(f"/api/runs/{run_id}").json()
            if run["status"] in states or run["status"] == "failed":
                return run
            time.sleep(0.4)
        raise TimeoutError

    run = client.post("/api/runs", json={"sample": "legacy_bank"}).json()
    rid = run["id"]
    run = wait(rid, ("awaiting_approval",))
    assert run["status"] == "awaiting_approval", run.get("error")
    assert run["pending"]["type"] == "plan_approval"
    plan = client.get(f"/api/runs/{rid}/plan").json()
    assert [u["program"] for u in plan["units"]]
    # a unit decision is refused while the plan is pending
    assert client.post(f"/api/runs/{rid}/units/PAYROLL/decision", json={"decision": "approve"}).status_code == 409
    client.post(f"/api/runs/{rid}/plan/decision", json={"approved": True, "programs": ["INTCALC", "NAMEFMT"]})
    seen = []
    for _ in range(2):
        run = wait(rid, ("awaiting_approval", "completed"))
        if run["status"] == "completed":
            break
        prog = run["pending"]["program"]
        seen.append(prog)
        client.post(f"/api/runs/{rid}/units/{prog}/decision", json={"decision": "approve"})
    run = wait(rid, ("completed",))
    assert run["status"] == "completed", run.get("error")
    assert sorted(seen) == ["INTCALC", "NAMEFMT"]
    # the verified unit is committed; the blocked one is refused without force
    assert run["units"]["INTCALC"]["decision"]["decision"] == "approve"
    assert run["units"]["INTCALC"]["decision"]["commit"]
    assert run["units"]["NAMEFMT"]["status"] == "blocked"
    assert run["units"]["NAMEFMT"]["decision"]["decision"] == "reject"
    tree = client.get(f"/api/runs/{rid}/output/tree").json()
    paths = {f["path"] for f in tree["files"]}
    assert {"intcalc.py", "cobol_runtime.py", "tests/test_intcalc.py", "MIGRATION_REPORT.md"} <= paths
    assert "namefmt.py" not in paths
    assert any("INTCALC" in c["subject"] for c in tree["log"])

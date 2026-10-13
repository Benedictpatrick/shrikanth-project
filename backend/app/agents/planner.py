"""Migration planning: wave order, per-unit strategy, risks and decisions."""
from __future__ import annotations

import logging

from ..cobol.project import Project, migration_order, program_metrics
from ..llm.provider import LLMError, LLMProvider, complete_json

log = logging.getLogger(__name__)


def build_plan(project: Project, analysis: dict, llm: LLMProvider) -> dict:
    order = migration_order(project)
    units = []
    for i, key in enumerate(order, 1):
        m = project.programs[key]
        mt = program_metrics(m)
        info = analysis["programs"][key]
        issues = [u for u in m.unsupported]
        path = "rules" if not issues else "rules+llm"
        risks: list[str] = []
        if issues:
            kinds = sorted({u.construct.split(" (")[0] for u in issues})
            risks.append("needs LLM translation for: " + ", ".join(kinds))
        if any(f["severity"] == "high" for f in info["security"]):
            risks.append("contains a hard-coded credential that must not be carried into the port")
        if not info["baseline"]["cases"]:
            risks.append("no behavioural baseline - output cannot be verified automatically")
        elif info["baseline"]["oracle"] == "llm-unverified":
            risks.append("baseline is LLM-predicted and unverified")
        units.append({
            "order": i, "program": key, "path": m.path, "module": info["module"],
            "depends_on": [c for c in m.calls if c in project.programs],
            "complexity": mt["complexity"], "loc": mt["loc"], "translation": path,
            "oracle": info["baseline"]["oracle"], "test_cases": len(info["baseline"]["cases"]),
            "risks": risks, "rationale": "",
        })
    plan = {
        "strategy": "language_migration",
        "summary": "",
        "target": {"language": "Python 3", "runtime": "cobol_runtime.py (bundled, standard library only)",
                   "layout": "one module per COBOL program, one method per paragraph"},
        "approach": ("Incremental, program by program: translate with the rule-based generator, verify against the "
                     "behavioural baseline, repair failures with the LLM, and commit each approved unit to its own "
                     "git commit in the output repository."),
        "units": units,
        "decisions": [
            {"id": "numeric-model", "question": "Numeric representation",
             "choice": "decimal.Decimal with PIC-aware truncation (COBOL semantics preserved)",
             "alternatives": ["float (not exact)", "int cents (loses generality)"]},
            {"id": "structure", "question": "Control flow",
             "choice": "paragraphs stay as methods; PERFORM becomes calls (traceable to the source)",
             "alternatives": ["restructure into functions (riskier, do after verification)"]},
        ],
        "global_risks": sorted({r for u in units for r in u["risks"]}),
    }
    if llm.available() and units:
        facts = "\n".join(
            f"- {u['program']}: {u['loc']} lines, complexity {u['complexity']}, translation {u['translation']}, "
            f"baseline {u['oracle']} ({u['test_cases']} cases), risks: {'; '.join(u['risks']) or 'none'}"
            for u in units)
        try:
            data = complete_json(
                llm, "You are a modernization architect planning a COBOL to Python migration. Be specific and brief.",
                f"Units in migration order:\n{facts}\n\nReturn JSON {{\"summary\": \"3 sentences on the "
                f"strategy and what to watch\", \"rationale\": {{\"<program>\": \"one sentence why it is in this "
                f"position / what to check\"}}}}", max_tokens=800)
            plan["summary"] = str(data.get("summary", ""))
            for u in units:
                u["rationale"] = str(data.get("rationale", {}).get(u["program"], ""))
        except LLMError as exc:
            log.warning("planner LLM step skipped: %s", exc)
    if not plan["summary"]:
        n_llm = sum(1 for u in units if u["translation"] != "rules")
        plan["summary"] = (f"Migrate {len(units)} program(s) to Python in dependency order. "
                           f"{len(units) - n_llm} translate fully with the rule-based generator; "
                           f"{n_llm} contain constructs that need the LLM path.")
    return plan

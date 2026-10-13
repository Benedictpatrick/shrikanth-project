"""Business-rule extraction: deterministic candidates + LLM interpretation."""
from __future__ import annotations

import logging

from ..cobol.parser import ProgramModel
from ..cobol.project import _walk
from ..llm.provider import LLMError, LLMProvider, complete_json

log = logging.getLogger(__name__)


# ------------------------------------------------------------ rendering helpers
def render_expr(e: dict) -> str:
    t = e.get("t")
    if t == "num":
        return e["v"]
    if t == "str":
        return repr(e["v"])
    if t == "fig":
        return e["v"].replace("_", "-")
    if t == "ref":
        subs = "(" + ", ".join(render_expr(s) for s in e["subs"]) + ")" if e.get("subs") else ""
        return e["name"] + subs
    if t == "neg":
        return "-" + render_expr(e["e"])
    if t == "bin":
        return f"({render_expr(e['l'])} {e['op']} {render_expr(e['r'])})"
    return t or "?"


def render_cond(c: dict) -> str:
    t = c.get("t")
    if t == "cmp":
        return f"{render_expr(c['l'])} {c['op']} {render_expr(c['r'])}"
    if t in ("and", "or"):
        return f"({render_cond(c['l'])} {t.upper()} {render_cond(c['r'])})"
    if t == "not":
        return f"NOT {render_cond(c['e'])}"
    if t == "cond88":
        return render_expr(c["ref"])
    if t == "class":
        return f"{render_expr(c['ref'])} IS {'NOT ' if c['neg'] else ''}{c['cls']}"
    if t == "sign":
        return f"{render_expr(c['ref'])} IS {c['sign']}"
    return t or "?"


def _strip(e: str) -> str:
    return e[1:-1] if e.startswith("(") and e.endswith(")") else e


def _summarize(stmts: list[dict], limit: int = 3) -> str:
    parts: list[str] = []

    def visit(s: dict) -> None:
        k = s.get("kind")
        if len(parts) >= limit + 1:
            return
        if k == "MOVE":
            parts.append(f"set {', '.join(render_expr(d) for d in s['dst'])} to {render_expr(s['src'])}")
        elif k == "ARITH":
            for a in s["assigns"]:
                parts.append(f"{render_expr(a['target'])} = {_strip(render_expr(a['expr']))}")
        elif k == "PERFORM" and s.get("target"):
            parts.append(f"perform {s['target']}")
        elif k == "DISPLAY":
            parts.append("report: " + " ".join(render_expr(i) for i in s["items"])[:60])
        elif k == "WRITE":
            parts.append(f"write {s['record']}")
        elif k == "SET_COND":
            parts.append("set " + ", ".join(render_expr(t) for t in s["targets"]) + " to TRUE")
    for s in stmts or []:
        visit(s)
    if len(parts) > limit:
        parts = parts[:limit] + ["..."]
    return "; ".join(parts) if parts else "no further action"


def heuristic_rules(model: ProgramModel) -> list[dict]:
    """Rules visible directly in the AST: calculations and decisions."""
    rules: list[dict] = []

    def add(kind: str, title: str, desc: str, para: str, line: int) -> None:
        f, n = model.origin(line) if line else (model.path, 0)
        rules.append({"category": kind, "title": title, "description": desc, "paragraph": para,
                      "file": f, "line": n, "origin": "static"})

    for p in model.paragraphs:
        def visit(s: dict, para=p.name) -> None:
            k = s.get("kind")
            line = s.get("line", 0)
            if k == "ARITH":
                for a in s["assigns"]:
                    expr = _strip(render_expr(a["expr"]))
                    t = render_expr(a["target"])
                    if expr == f"{t} + 1" or expr.startswith(f"{t} +") and len(expr) < len(t) + 12:
                        continue  # counters: not interesting as rules
                    note = " (rounded to the nearest unit of the target's precision)" if a["rounded"] else ""
                    add("calculation", f"Computes {t}", f"{t} = {expr}{note}", para, line)
            elif k == "IF":
                then, els = _summarize(s["then"]), _summarize(s["else"])
                desc = f"If {render_cond(s['cond'])}: {then}."
                if s["else"]:
                    desc += f" Otherwise: {els}."
                add("decision", f"Condition on {render_cond(s['cond'])[:50]}", desc, para, line)
            elif k == "EVALUATE":
                for w in s["whens"]:
                    conds = " or ".join(render_cond(c) if c.get("t") != "range"
                                        else f"{render_expr(c['lo'])} THRU {render_expr(c['hi'])}"
                                        if c.get("t") != "ref" else render_expr(c) for c in w["conds"])
                    if s["subject"].get("t") not in ("true", "false"):
                        conds = f"{render_expr(s['subject'])} is {conds}"
                    add("decision", f"Case: {conds[:50]}", f"When {conds}: {_summarize(w['body'])}.", para, line)
                if s.get("other") is not None:
                    add("decision", "Default case", f"Otherwise: {_summarize(s['other'])}.", para, line)
        _walk(p.stmts, visit)
    for i, r in enumerate(rules, 1):
        r["id"] = f"{model.program_id}-R{i:02d}"
    return rules


# ------------------------------------------------------------------ LLM layer
RULES_SYSTEM = (
    "You are a senior COBOL analyst documenting undocumented business rules for a modernization team. "
    "You are given a COBOL program and a list of mechanically extracted facts. Explain the business "
    "behaviour in plain language a product owner can verify. Never invent behaviour that is not in the code. "
    "Keep every number and threshold exactly as in the code."
)


def extract_rules(model: ProgramModel, source: str, llm: LLMProvider) -> dict:
    """Returns {summary, rules[], used_llm}."""
    static = heuristic_rules(model)
    result = {"summary": "", "rules": static, "used_llm": False}
    if not llm.available():
        result["summary"] = (f"{model.program_id}: {len(model.paragraphs)} paragraphs, "
                             f"{len(model.files)} files, {len(static)} rules detected statically.")
        return result
    facts = "\n".join(f"- [{r['paragraph']}] {r['description']}" for r in static[:60])
    user = (
        f"PROGRAM {model.program_id}\n\n=== SOURCE ===\n{source[:max(2000, min(14000, llm.prompt_char_budget(1800) - len(facts) - 1200))]}\n\n=== EXTRACTED FACTS ===\n{facts}\n\n"
        'Return JSON: {"summary": "2-3 sentence purpose of the program", "rules": [{"title": str, '
        '"description": str (one or two sentences, include exact thresholds/rates), '
        '"category": "calculation"|"validation"|"decision"|"reporting"|"data-flow", '
        '"paragraphs": [paragraph names]}]}. Merge related facts into one rule, 4-12 rules in total.'
    )
    try:
        data = complete_json(llm, RULES_SYSTEM, user, max_tokens=1800)
        rules = []
        for i, r in enumerate(data.get("rules", []), 1):
            if not isinstance(r, dict) or not r.get("description"):
                continue
            paras = r.get("paragraphs") or []
            first = model.paragraph(paras[0]) if paras and isinstance(paras[0], str) else None
            f, n = model.origin(first.line + 1) if first else (model.path, 0)
            rules.append({"id": f"{model.program_id}-R{i:02d}", "category": r.get("category", "decision"),
                          "title": str(r.get("title", "Rule"))[:90], "description": str(r["description"]),
                          "paragraph": ", ".join(map(str, paras)), "file": f, "line": n, "origin": "llm"})
        if rules:
            result.update(summary=str(data.get("summary", "")), rules=rules, used_llm=True,
                          static_rules=static)
    except LLMError as exc:
        log.warning("rule extraction fell back to static analysis: %s", exc)
        result["summary"] = f"(LLM unavailable: {exc}) {len(static)} rules detected statically."
    return result

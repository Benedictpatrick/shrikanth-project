"""Technical documentation generation (deterministic structure, LLM prose)."""
from __future__ import annotations

import re

from ..cobol.parser import ProgramModel
from ..cobol.project import paragraph_calls, program_metrics, file_modes
from ..llm.provider import LLMError, LLMProvider


def leading_comment(source: str) -> str:
    lines = []
    for ln in source.splitlines()[:40]:
        if len(ln) > 6 and ln[6] == "*":
            text = ln[7:].strip(" *-")
            if text:
                lines.append(text)
    return " ".join(lines)


def _call_tree(model: ProgramModel) -> list[str]:
    calls = paragraph_calls(model)
    if not model.paragraphs:
        return []
    out: list[str] = []
    seen: set[str] = set()

    def walk(name: str, depth: int) -> None:
        out.append("  " * depth + f"- `{name}`" + (" *(recursive)*" if name in seen else ""))
        if name in seen:
            return
        seen.add(name)
        for c in calls.get(name, []):
            walk(c, depth + 1)
        seen.discard(name)

    walk(model.paragraphs[0].name, 0)
    return out


def generate_doc(model: ProgramModel, source: str, rules: dict, findings: list[dict],
                 llm: LLMProvider) -> str:
    mt = program_metrics(model)
    header = leading_comment(source)
    summary = (rules.get("summary") if rules.get("used_llm") else header) or rules.get("summary") or header
    if llm.available():
        try:
            facts = "\n".join(f"- {r['description']}" for r in rules.get("rules", [])[:25])
            summary = llm.complete(
                "You write concise technical documentation for software engineers inheriting a COBOL system.",
                f"Program {model.program_id}. Header comment: {leading_comment(source)}\nRules:\n{facts}\n\n"
                "Write a 3-5 sentence overview: purpose, inputs, outputs, and the most important processing "
                "rules. No headings, no bullet lists.", max_tokens=400).strip() or summary
        except LLMError:
            pass
    md: list[str] = [f"# {model.program_id}", "", f"> Source: `{model.path}`  ", f"> {mt['loc']} lines, "
                     f"{mt['paragraphs']} paragraphs, complexity {mt['complexity']}", "", "## Overview", "",
                     summary or "_No description available._", ""]
    modes = file_modes(model)
    if model.files:
        md += ["## Files", "", "| Logical name | Assigned to | Organization | Access | Status field | Layout |",
               "|---|---|---|---|---|---|"]
        for f in model.files:
            direction = "/".join(sorted(modes.get(f.name, []))) or "-"
            md.append(f"| `{f.name}` | `{f.assign}` | {f.organization} | {direction} | "
                      f"{('`' + f.status + '`') if f.status else '**none**'} | {', '.join(f.records) or '-'} |")
        md.append("")
    top = [d for d in model.data if d.level in (1, 77)]
    if top:
        md += ["## Data division (top level)", "", "| Level | Name | Picture | Usage | Initial value |", "|---|---|---|---|---|"]
        for d in top:
            v = d.value
            val = (v.get("v") if isinstance(v, dict) else "") or ""
            md.append(f"| {d.level:02d} | `{d.name}` | {('`' + d.pic + '`') if d.pic else 'group'} | "
                      f"{d.usage if d.pic else ''} | {('`' + str(val) + '`') if val != '' else ''} |")
        md.append("")
    tree = _call_tree(model)
    if tree:
        md += ["## Processing flow", ""] + tree + [""]
    if rules.get("rules"):
        md += ["## Business rules", ""]
        for r in rules["rules"]:
            where = f" _(paragraph {r['paragraph']}, {r['file']}:{r['line']})_" if r.get("paragraph") else ""
            md.append(f"- **{r['title']}** - {r['description']}{where}")
        md.append("")
    mine = [f for f in findings if f["program"] == model.program_id]
    if mine:
        md += ["## Security & risk findings", "", "| Severity | Rule | Finding | Where |", "|---|---|---|---|"]
        for f in mine:
            md.append(f"| {f['severity']} | {f['rule']} | {f['title']} - {f['detail']} | {f['file']}:{f['line']} |")
        md.append("")
    if model.unsupported:
        md += ["## Migration notes", "", "Constructs outside the rule-based translation subset:", ""]
        for u in model.unsupported:
            f, n = model.origin(u.line)
            md.append(f"- {u.construct} ({f}:{n}) `{re.sub(chr(10), ' ', u.text)[:80]}`")
        md.append("")
    return "\n".join(md)

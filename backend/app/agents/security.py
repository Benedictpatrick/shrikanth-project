"""Deterministic security and risk scan over the parsed COBOL model."""
from __future__ import annotations

import re

from ..cobol.parser import ProgramModel
from ..cobol.project import _walk

SECRET_NAME = re.compile(r"(PASSWORD|PASSWD|PWD|SECRET|API-?KEY|TOKEN|CREDENTIAL|PRIVATE-?KEY)", re.I)
SENSITIVE_NAME = re.compile(r"(SSN|SOCIAL|PASSWORD|PASSWD|PIN\b|CARD-?NUM|CVV|SECRET)", re.I)


def scan_program(model: ProgramModel) -> list[dict]:
    pid = model.program_id
    findings: list[dict] = []

    def add(sev: str, rule: str, title: str, detail: str, line: int, fix: str) -> None:
        f, n = model.origin(line) if line else (model.path, 0)
        findings.append({"program": pid, "severity": sev, "rule": rule, "title": title,
                         "detail": detail, "file": f, "line": n, "recommendation": fix})

    for d in model.data:
        v = d.value
        if SECRET_NAME.search(d.name) and isinstance(v, dict) and v.get("t") == "str" and v["v"].strip():
            add("high", "SEC-001", "Hard-coded credential",
                f"Data item {d.name} is initialised with a literal secret in source code.", d.line,
                "Load secrets from an environment variable or a secret manager; rotate the exposed value.")
    for f in model.files:
        if not f.status:
            add("medium", "SEC-002", "File without FILE STATUS",
                f"File {f.name} has no FILE STATUS clause, so I/O failures cannot be detected "
                "or handled by the program.", f.line,
                "Declare FILE STATUS and check it after OPEN/READ/WRITE, or handle errors explicitly in the port.")

    size_error_lines: list[int] = []
    flagged_display: set[str] = set()

    def visit(s: dict) -> None:
        k = s.get("kind")
        line = s.get("line", 0)
        if k == "READ":
            fd = next((f for f in model.files if f.name == s.get("file")), None)
            if fd and not fd.status and s.get("at_end") is None:
                add("medium", "SEC-003", "READ without AT END or FILE STATUS",
                    f"READ of {fd.name} has neither an AT END branch nor FILE STATUS; end of file aborts the run.",
                    line, "Add an AT END branch or check FILE STATUS.")
        elif k == "ACCEPT" and not s.get("source"):
            add("medium", "SEC-004", "Unvalidated console input",
                "ACCEPT reads operator input into a data item without validation.", line,
                "Validate class and range of the value before use (NUMERIC test, bounds).")
        elif k == "ARITH" and s.get("on_size_error") is None and s.get("not_on_size_error") is None:
            size_error_lines.append(line)
        elif k == "DISPLAY":
            for it in s.get("items", []):
                if it.get("t") == "ref" and SENSITIVE_NAME.search(it["name"]) and it["name"] not in flagged_display:
                    flagged_display.add(it["name"])
                    add("medium", "SEC-005", "Sensitive data written to console",
                        f"DISPLAY outputs {it['name']}, which looks like sensitive data.", line,
                        "Mask or drop the value from console and log output.")
        elif k == "UNSUPPORTED":
            verb = s.get("verb")
            if verb in ("GO", "ALTER"):
                add("low", "SEC-006", f"Unstructured control flow ({verb})",
                    "GO TO / ALTER makes control flow hard to reason about and to verify.", line,
                    "Restructure into PERFORMed paragraphs before or during the port.")
            if verb == "CALL":
                add("low", "SEC-007", "Inter-program CALL",
                    f"{s.get('text', '')[:80]} - the callee's behaviour must be migrated and verified with it.",
                    line, "Migrate the called program in the same wave and test the interface.")

    for p in model.paragraphs:
        _walk(p.stmts, visit)
    if size_error_lines:
        add("low", "SEC-008", "Arithmetic without ON SIZE ERROR",
            f"{len(size_error_lines)} arithmetic statement(s) silently truncate on overflow "
            f"(first at logical line {size_error_lines[0]}).", size_error_lines[0],
            "Keep the truncation semantics in the port (the runtime does) but add range checks where overflow "
            "would be a business error.")
    for u in model.unsupported:
        if u.construct.startswith("SIGN") or "REDEFINES" in u.construct:
            continue
    return findings


def severity_counts(findings: list[dict]) -> dict:
    out = {"high": 0, "medium": 0, "low": 0}
    for f in findings:
        out[f["severity"]] = out.get(f["severity"], 0) + 1
    return out

"""Translate one COBOL program, verify it, and repair it with the LLM when it fails.

Flow per unit:
  1. deterministic draft (rule-based, uses cobol_runtime)        -> source "rules"
  2. verify against the behavioural baseline
  3. while failing and an LLM is available (max N attempts):
        LLM repairs the module given the failing diffs           -> source "llm-repair"
        re-verify
  The best attempt (most passing cases) is kept. Nothing is accepted without
  a passing run, and the loop never edits tests to make them pass.
"""
from __future__ import annotations

import logging
import py_compile
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..cobol.codegen import Translation, translate
from ..cobol.parser import ProgramModel
from ..llm.provider import LLMError, LLMProvider, extract_python
from ..verification import harness

log = logging.getLogger(__name__)
RUNTIME_SRC = Path(__file__).resolve().parent.parent / "runtime" / "cobol_runtime.py"

RUNTIME_API = """\
Generated programs subclass CobolProgram (from cobol_runtime import *). Storage is byte-accurate COBOL storage.
  self.f.<py_name>            Field for a data item; tables: self.f.<py_name>[i] (1-based). py_name is given in DATA.
  field.value                 Decimal for numeric items, str for alphanumeric/group/edited items
  field.num / field.text      numeric value (Decimal) / raw text of the item (fixed width)
  field.move(src)             COBOL MOVE; src = Field | str | Decimal | int | ZERO | SPACE | HIGH_VALUE | LOW_VALUE | QUOTE
  field.compute(lambda: <Decimal expression>, rounded=False, on_size_error=False) -> bool   (COMPUTE/ADD/...)
  field.initialize(), field.is_numeric(), field.is_alphabetic(), field.size (bytes), field.display_text()
  self.f.<cond_88_name>.test() / .set()      level-88 condition names
  cmp(a, '=', b)              COBOL comparison of .value results; ops: = != < > <= >=
  self.display(*parts)        DISPLAY (parts: Field, str, Decimal, ZERO, SPACE)
  self.accept(field[, 'DATE'])  ACCEPT;  self.stop_run()  STOP RUN
  self.files.<file_name>.open('INPUT'|'OUTPUT'|'EXTEND') / .read(at_end_handled=bool) -> False at end / .write(record_field) / .close()
Record areas are ordinary fields (self.f.<record>); READ fills them, WRITE emits their bytes.
String statements on a Field: use .text to read the bytes as str and .move(str) to store (truncates/pads to the PIC size)."""

REPAIR_SYSTEM = (
    "You are a COBOL-to-Python migration engineer. You repair a Python module that was generated from a COBOL "
    "program so that it behaves EXACTLY like the original. The module uses the cobol_runtime library described "
    "below. Rules: keep the class name, DATA and FILES tables, paragraph method names and the run() function; "
    "implement any `raise NotImplementedError(...)` stub from the COBOL source; never change expected outputs, "
    "never special-case test data; preserve COBOL semantics (truncation, rounding only with ROUNDED, "
    "space padding, fixed-width records). Reply with the COMPLETE corrected module in one ```python block."
)


@dataclass
class Attempt:
    number: int
    source: str            # rules | llm-repair
    passed: int
    failed: int
    total: int
    note: str = ""
    syntax_error: str | None = None


@dataclass
class UnitResult:
    program: str
    module_name: str
    code: str
    issues: list[dict]
    attempts: list[Attempt] = field(default_factory=list)
    verification: dict = field(default_factory=dict)
    status: str = "unverified"     # verified | failed | unverified | blocked
    oracle: str = "none"
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "program": self.program, "module": self.module_name, "status": self.status,
            "oracle": self.oracle, "issues": self.issues, "notes": self.notes,
            "attempts": [a.__dict__ for a in self.attempts],
            "verification": self.verification,
        }


def _write_module(out_dir: Path, module: str, code: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(RUNTIME_SRC, out_dir / "cobol_runtime.py")
    path = out_dir / f"{module}.py"
    path.write_text(code, encoding="utf-8")
    return path


def _syntax_error(path: Path) -> str | None:
    try:
        py_compile.compile(str(path), doraise=True, cfile=str(path.with_suffix(".pyc.tmp")))
    except py_compile.PyCompileError as exc:
        return str(exc)
    finally:
        path.with_suffix(".pyc.tmp").unlink(missing_ok=True)
    return None


def _failure_digest(verification: dict, limit: int = 3) -> str:
    out = []
    for r in verification.get("results", []):
        if r["passed"]:
            continue
        out.append(f"--- case '{r['name']}': {r.get('description', '')}")
        if r.get("error"):
            out.append(f"runtime error: {r['error']}")
        if r.get("traceback"):
            out.append(r["traceback"][-900:])
        out.extend(r.get("diff", [])[:25])
        if len(out) > 120:
            break
        limit -= 1
        if limit <= 0:
            break
    return "\n".join(out)


def migrate_unit(model: ProgramModel, source: str, cases: list[dict], oracle: str, work_dir: Path,
                 llm: LLMProvider, max_attempts: int,
                 emit: Callable[[str], None] = lambda m: None) -> UnitResult:
    draft: Translation = translate(model)
    result = UnitResult(program=model.program_id, module_name=draft.module_name, code=draft.code,
                        issues=draft.issues, oracle=oracle)
    best_code, best_score, best_ver = draft.code, -1, {}
    code, src_kind = draft.code, "rules"
    total_attempts = 1 + (max_attempts if llm.available() else 0)

    for n in range(1, total_attempts + 1):
        path = _write_module(work_dir, draft.module_name, code)
        syntax = _syntax_error(path)
        if syntax:
            ver = {"total": len(cases), "passed": 0, "failed": len(cases), "results": [
                {"name": "<module>", "passed": False, "error": f"SyntaxError: {syntax}", "diff": [],
                 "traceback": None, "description": "module does not compile"}]}
        elif cases:
            ver = harness.verify(path, cases)
        else:
            ver = {"total": 0, "passed": 0, "failed": 0, "results": []}
        attempt = Attempt(n, src_kind, ver["passed"], ver["failed"], ver["total"],
                          syntax_error=syntax,
                          note=("module did not compile" if syntax else
                                f"{ver['passed']}/{ver['total']} cases passed" if cases else "no test cases"))
        result.attempts.append(attempt)
        emit(f"{model.program_id}: attempt {n} ({src_kind}) - {attempt.note}")
        score = ver["passed"] - (1000 if syntax else 0)
        if score > best_score:
            best_code, best_score, best_ver = code, score, ver
        if syntax is None and cases and ver["failed"] == 0:
            break
        if not cases and "NotImplementedError" not in code:
            break  # nothing to verify against, nothing to repair
        if not llm.available() or n >= total_attempts:
            break
        # ---- LLM repair ---------------------------------------------------------
        emit(f"{model.program_id}: asking {llm.name}/{llm.model} to repair the module")
        failure = _failure_digest(ver) if cases else "The module contains NotImplementedError stubs."
        if draft.issues and not syntax:
            failure = ("Unimplemented constructs:\n" + "\n".join(f"- line {i['line']}: {i['message']}"
                                                                 for i in draft.issues) + "\n\n" + failure)
        if syntax:
            failure = f"The module does not compile:\n{syntax}"
        src_budget = max(1500, min(16000, llm.prompt_char_budget(4000) - len(code) - len(RUNTIME_API)
                                   - len(failure[:6000]) - 600))
        user = (f"=== COBOL SOURCE ({model.path}) ===\n{source[:src_budget]}\n\n=== RUNTIME API ===\n{RUNTIME_API}\n\n"
                f"=== CURRENT PYTHON MODULE ===\n```python\n{code}\n```\n\n=== PROBLEMS TO FIX ===\n{failure[:6000]}\n")
        try:
            try:
                reply = llm.complete(REPAIR_SYSTEM, user, temperature=0.1, max_tokens=4000)
            except LLMError as exc:
                if "413" not in str(exc) and "needs ~" not in str(exc):
                    raise
                # request over the token limit: retry with only the module and the problems
                emit(f"{model.program_id}: prompt too large, retrying with a compact prompt")
                compact = (f"=== RUNTIME API ===\n{RUNTIME_API}\n\n=== CURRENT PYTHON MODULE ===\n```python\n{code}\n```"
                           f"\n\n=== PROBLEMS TO FIX ===\n{failure[:2500]}\n")
                reply = llm.complete(REPAIR_SYSTEM, compact, temperature=0.1, max_tokens=3000)
        except LLMError as exc:
            result.notes.append(f"LLM repair failed: {exc}")
            emit(f"{model.program_id}: LLM repair failed: {exc}")
            break
        code, src_kind = extract_python(reply), "llm-repair"

    result.code = best_code
    result.verification = best_ver
    path = _write_module(work_dir, draft.module_name, best_code)
    stubs_left = "NotImplementedError" in best_code
    if stubs_left:
        result.notes.append("module still contains NotImplementedError stubs")
    if stubs_left:
        result.status = "blocked"
    elif cases and best_ver.get("total") and best_ver.get("failed") == 0:
        result.status = "provisional" if oracle == "llm-unverified" else "verified"
    elif cases and best_ver.get("total"):
        result.status = "failed"
    else:
        result.status = "unverified"
    return result

"""Behavioural verification: run migrated programs against golden test cases."""
from __future__ import annotations

import difflib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RUNNER = Path(__file__).with_name("case_runner.py")


def load_fixture_cases(root: Path, program_id: str) -> list[dict]:
    """Golden cases shipped with the legacy repo: tests/<PROGRAM>.json."""
    names = {program_id.upper(), program_id.lower()}
    candidates = [root / "tests", *root.glob("*/tests"), *root.glob("**/tests")]
    seen: set[Path] = set()
    for tests_dir in candidates:
        if tests_dir in seen:
            continue
        seen.add(tests_dir)
        for n in names:
            p = tests_dir / f"{n}.json"
            if p.is_file():
                cases = json.loads(p.read_text(encoding="utf-8"))
                for c in cases:
                    c.setdefault("source", "fixture")
                return cases
    return []


def run_python_case(module_path: Path, case: dict, timeout: float = 30.0) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        case_file = Path(tmp) / "case.json"
        case_file.write_text(json.dumps(case), encoding="utf-8")
        try:
            proc = subprocess.run([sys.executable, "-I", str(RUNNER), str(module_path), str(case_file)],
                                  capture_output=True, text=True, timeout=timeout, cwd=tmp)
        except subprocess.TimeoutExpired:
            return {"status": "error", "error": f"timed out after {timeout:.0f}s", "stdout": "", "files": {}}
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if not lines:
        return {"status": "error", "error": (proc.stderr or "no output")[-800:], "stdout": "", "files": {}}
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError:
        return {"status": "error", "error": (proc.stderr or proc.stdout)[-800:], "stdout": "", "files": {}}


def _clip(text: str, limit: int = 20000) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n... ({len(text) - limit} more characters)"


def _norm(s: str) -> str:
    return s.replace("\r\n", "\n")


def compare(expected: dict, actual: dict) -> dict:
    """Exact comparison of console output and every expected output file."""
    diffs: list[str] = []
    if actual.get("status") != "ok":
        return {"passed": False, "diff": [actual.get("error") or "program failed"]}

    def check(label: str, want: str, got: str) -> None:
        want, got = _norm(want), _norm(got)
        if want != got:
            d = difflib.unified_diff(want.splitlines(), got.splitlines(),
                                     f"expected {label}", f"actual {label}", lineterm="", n=1)
            diffs.extend(list(d)[:40])

    if "stdout" in expected:
        check("stdout", expected["stdout"], actual.get("stdout", ""))
    for name, want in expected.get("files", {}).items():
        if name not in actual.get("files", {}):
            diffs.append(f"expected output file {name} was not produced")
        else:
            check(name, want, actual["files"][name])
    return {"passed": not diffs, "diff": diffs}


def verify(module_path: Path, cases: list[dict]) -> dict:
    results = []
    for case in cases:
        actual = run_python_case(module_path, case)
        cmp_ = compare(case.get("expected", {}), actual)
        results.append({
            "name": case["name"], "description": case.get("description", ""),
            "passed": cmp_["passed"], "diff": cmp_["diff"],
            "inputs": {k: _clip(v) for k, v in case.get("inputs", {}).items()},
            "expected": {"stdout": _clip(case.get("expected", {}).get("stdout", "")),
                         "files": {k: _clip(v) for k, v in case.get("expected", {}).get("files", {}).items()}},
            "actual": {"stdout": _clip(actual.get("stdout", "")),
                       "files": {k: _clip(v) for k, v in actual.get("files", {}).items()}},
            "error": actual.get("error") if actual.get("status") != "ok" else None,
            "traceback": actual.get("traceback"),
            "source": case.get("source", "fixture"),
        })
    passed = sum(1 for r in results if r["passed"])
    return {"total": len(results), "passed": passed, "failed": len(results) - passed, "results": results}


# ------------------------------------------------------------------ cobc oracle
def find_cobc() -> str | None:
    return shutil.which("cobc")


def compile_cobol(cobc: str, source: Path, copy_dirs: list[Path], out_dir: Path) -> tuple[Path | None, str]:
    exe = out_dir / (source.stem + (".exe" if sys.platform == "win32" else ""))
    cmd = [cobc, "-x", "-o", str(exe), str(source)]
    for d in copy_dirs:
        cmd += ["-I", str(d)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0 or not exe.exists():
        return None, (proc.stderr or proc.stdout)[-1500:]
    return exe, ""


def run_cobol_case(exe: Path, case: dict, timeout: float = 30.0) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name, content in case.get("inputs", {}).items():
            (root / name).write_bytes(content.encode("latin-1"))
        try:
            proc = subprocess.run([str(exe)], capture_output=True, timeout=timeout, cwd=tmp,
                                  input=case.get("stdin", "").encode("latin-1"))
        except subprocess.TimeoutExpired:
            return {"status": "error", "error": "timed out", "stdout": "", "files": {}}
        files = {p.name: p.read_bytes().decode("latin-1") for p in sorted(root.iterdir())
                 if p.is_file() and p.name not in case.get("inputs", {})}
        return {"status": "ok" if proc.returncode == 0 else "error",
                "error": None if proc.returncode == 0 else f"exit code {proc.returncode}",
                "stdout": proc.stdout.decode("latin-1"), "files": files}

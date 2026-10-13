"""Subprocess entry point: run ONE migrated program against ONE test case.

Usage: python -I case_runner.py <module.py> <case.json>
Prints a single JSON object on the last line of stdout.
"""
import importlib.util
import io
import json
import sys
import tempfile
import traceback
from pathlib import Path


def main() -> None:
    module_path = Path(sys.argv[1]).resolve()
    case = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    sys.path.insert(0, str(module_path.parent))
    result = {"status": "ok", "stdout": "", "files": {}, "error": None}
    try:
        from cobol_runtime import CobolIO
        spec = importlib.util.spec_from_file_location(module_path.stem, module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, content in case.get("inputs", {}).items():
                (root / name).write_bytes(content.encode("latin-1"))
            buf = io.StringIO()
            cio = CobolIO(root=root, stdout=buf, stdin_text=case.get("stdin", ""))
            module.run(cio)
            result["stdout"] = buf.getvalue()
            inputs = set(case.get("inputs", {}))
            for p in sorted(root.iterdir()):
                if p.is_file() and p.name not in inputs:
                    result["files"][p.name] = p.read_bytes().decode("latin-1")
    except BaseException as exc:  # noqa: BLE001 - report everything to the caller
        result["status"] = "error"
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["traceback"] = traceback.format_exc(limit=6)
    sys.stdout.write("\n" + json.dumps(result) + "\n")


if __name__ == "__main__":
    main()

"""Project-level analysis: scan a repo, build the dependency graph and metrics."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .parser import ProgramModel, parse_program

PROGRAM_EXTS = {".cbl", ".cob", ".cobol"}
COPYBOOK_EXTS = {".cpy", ".copy"}


@dataclass
class Project:
    root: Path
    programs: dict[str, ProgramModel] = field(default_factory=dict)  # keyed by PROGRAM-ID
    copybooks: dict[str, str] = field(default_factory=dict)          # name -> relative path
    errors: list[str] = field(default_factory=list)


def scan_project(root: str | Path) -> Project:
    root = Path(root)
    project = Project(root=root)
    skip = {".git", "node_modules", "__pycache__", ".venv", "output"}
    files = [p for p in sorted(root.rglob("*")) if p.is_file()
             and not any(part in skip for part in p.relative_to(root).parts)]
    copy_dirs = sorted({p.parent for p in files if p.suffix.lower() in COPYBOOK_EXTS})
    for p in files:
        if p.suffix.lower() in COPYBOOK_EXTS:
            project.copybooks[p.stem.upper()] = str(p.relative_to(root)).replace("\\", "/")
    for p in files:
        if p.suffix.lower() not in PROGRAM_EXTS:
            continue
        try:
            model = parse_program(p, copy_dirs)
        except Exception as exc:  # parser bug or unreadable file: report, keep going
            project.errors.append(f"{p.name}: {type(exc).__name__}: {exc}")
            continue
        if not model.program_id:
            model.program_id = p.stem.upper()
        key = model.program_id
        if key in project.programs:
            key = f"{key}@{p.stem.upper()}"
        model.path = str(p.relative_to(root)).replace("\\", "/")
        project.programs[key] = model
    return project


# ------------------------------------------------------------------ metrics
def _walk(stmts, visit) -> None:
    for s in stmts or []:
        visit(s)
        for key in ("then", "else", "body", "on_size_error", "not_on_size_error", "at_end", "not_at_end", "other"):
            v = s.get(key)
            if isinstance(v, list):
                _walk(v, visit)
        for w in s.get("whens", []) or []:
            _walk(w.get("body"), visit)


def _cond_ops(c) -> int:
    if not isinstance(c, dict):
        return 0
    n = 1 if c.get("t") in ("and", "or") else 0
    for k in ("l", "r", "e"):
        n += _cond_ops(c.get(k))
    return n


def program_metrics(model: ProgramModel) -> dict:
    counts = {"statements": 0, "decisions": 0}

    def visit(s: dict) -> None:
        counts["statements"] += 1
        k = s.get("kind")
        if k == "IF":
            counts["decisions"] += 1 + _cond_ops(s.get("cond"))
        elif k == "EVALUATE":
            counts["decisions"] += max(len(s.get("whens", [])), 1)
        elif k == "PERFORM" and (s.get("until") is not None or s.get("times") is not None):
            counts["decisions"] += 1

    for p in model.paragraphs:
        _walk(p.stmts, visit)
    unsupported = len(model.unsupported)
    stmts = max(counts["statements"], 1)
    return {
        "loc": model.loc,
        "paragraphs": len(model.paragraphs),
        "statements": counts["statements"],
        "decisions": counts["decisions"],
        "complexity": counts["decisions"] + 1,
        "data_items": len([d for d in model.data if d.level != 88]),
        "files": len(model.files),
        "copybooks": len(model.copybooks),
        "unsupported": unsupported,
        "translatable": round(max(0.0, 1 - unsupported / stmts), 2),
    }


def paragraph_calls(model: ProgramModel) -> dict[str, list[str]]:
    names = {p.name for p in model.paragraphs} | set(model.sections)
    out: dict[str, list[str]] = {}
    for p in model.paragraphs:
        targets: list[str] = []

        def visit(s: dict, targets=targets) -> None:
            if s.get("kind") == "PERFORM" and s.get("target") in names:
                if s["target"] not in targets:
                    targets.append(s["target"])
                if s.get("thru") and s["thru"] not in targets:
                    targets.append(s["thru"])
        _walk(p.stmts, visit)
        out[p.name] = targets
    return out


def file_modes(model: ProgramModel) -> dict[str, set[str]]:
    modes: dict[str, set[str]] = {f.name: set() for f in model.files}

    def visit(s: dict) -> None:
        if s.get("kind") == "OPEN":
            for e in s.get("entries", []):
                modes.setdefault(e["file"], set()).add(e["mode"])
    for p in model.paragraphs:
        _walk(p.stmts, visit)
    return modes


def build_graph(project: Project) -> dict:
    nodes: list[dict] = []
    edges: list[dict] = []
    seen: set[str] = set()

    def node(nid: str, **kw) -> None:
        if nid not in seen:
            seen.add(nid)
            nodes.append({"id": nid, **kw})

    program_ids = {k: k for k in project.programs}
    for key, m in project.programs.items():
        mt = program_metrics(m)
        node(f"program:{key}", type="program", label=m.program_id or key, path=m.path,
             complexity=mt["complexity"], loc=mt["loc"], unsupported=mt["unsupported"])
        for book in m.copybooks:
            node(f"copybook:{book}", type="copybook", label=book, path=project.copybooks.get(book, ""))
            edges.append({"source": f"program:{key}", "target": f"copybook:{book}", "type": "copy"})
        modes = file_modes(m)
        for f in m.files:
            nid = f"file:{f.assign}"
            node(nid, type="file", label=f.assign)
            md = modes.get(f.name, set())
            direction = "read" if md == {"INPUT"} else "write" if md and "INPUT" not in md else "readwrite"
            edges.append({"source": f"program:{key}", "target": nid, "type": "file", "mode": direction})
        for call in m.calls:
            target = program_ids.get(call)
            if target:
                edges.append({"source": f"program:{key}", "target": f"program:{target}", "type": "call"})
            else:
                node(f"external:{call}", type="external", label=call)
                edges.append({"source": f"program:{key}", "target": f"external:{call}", "type": "call"})
    return {"nodes": nodes, "edges": edges}


def migration_order(project: Project) -> list[str]:
    """Callees before callers; simpler programs first among independents."""
    deps = {k: {c for c in m.calls if c in project.programs} for k, m in project.programs.items()}
    metrics = {k: program_metrics(m)["complexity"] for k, m in project.programs.items()}
    order: list[str] = []
    remaining = dict(deps)
    while remaining:
        ready = sorted([k for k, d in remaining.items() if d <= set(order)],
                       key=lambda k: (metrics[k], k))
        if not ready:  # call cycle: fall back to complexity order
            ready = sorted(remaining, key=lambda k: (metrics[k], k))[:1]
        for k in ready:
            order.append(k)
            remaining.pop(k)
    return order


def source_text(project: Project, key: str) -> str:
    return (project.root / project.programs[key].path).read_text(encoding="latin-1")


def short(text: str, limit: int = 160) -> str:
    return re.sub(r"\s+", " ", text).strip()[:limit]

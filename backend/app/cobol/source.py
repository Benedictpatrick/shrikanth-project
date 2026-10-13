"""Reading COBOL source: reference format, continuation lines, COPY expansion."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

COPYBOOK_EXTS = (".cpy", ".copy", ".cbl", ".cob", ".cobol", "")
_COPY_RE = re.compile(
    r"^\s*COPY\s+([A-Za-z0-9_-]+)(?:\s+(?:IN|OF)\s+[A-Za-z0-9_-]+)?"
    r"(?:\s+REPLACING\s+(.*?))?\s*\.\s*$", re.IGNORECASE | re.DOTALL)
_PSEUDO = re.compile(r"==(.*?)==\s+BY\s+==(.*?)==", re.IGNORECASE | re.DOTALL)


@dataclass
class SrcLine:
    text: str
    file: str
    no: int  # 1-based line number in `file`


def detect_free_format(raw: list[str]) -> bool:
    hits = 0
    for ln in raw:
        if len(ln) > 6 and ln[6] not in " *-/dD" and ln[:6].strip() and not ln[:6].strip().isdigit():
            hits += 1
    return hits > 0 and hits >= max(2, len(raw) // 20)


def read_lines(path: Path, display_name: str | None = None) -> list[SrcLine]:
    """Logical source lines (comments removed, continuations joined)."""
    raw = path.read_text(encoding="latin-1").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    name = display_name or path.name
    free = detect_free_format(raw)
    out: list[SrcLine] = []
    for no, line in enumerate(raw, 1):
        if not line.strip():
            continue
        if free:
            s = line.rstrip()
            if s.lstrip().startswith("*>") or s.startswith("*"):
                continue
            if "*>" in s and not re.search(r"['\"].*\*>.*['\"]", s):
                s = s[:s.index("*>")].rstrip()
            out.append(SrcLine(s, name, no))
            continue
        body = line.ljust(7)
        indicator = body[6]
        if indicator in "*/":
            continue
        if indicator in "dD":
            continue
        content = body[7:72]
        if indicator == "-" and out:
            prev = out[-1]
            cont = content.lstrip()
            quote = next((c for c in prev.text if c in "'\""), "")
            open_literal = bool(quote) and prev.text.count(quote) % 2 == 1
            if open_literal and cont[:1] == quote:
                out[-1] = SrcLine(prev.text + cont[1:], prev.file, prev.no)
            else:
                out[-1] = SrcLine(prev.text.rstrip() + " " + cont.rstrip(), prev.file, prev.no)
            continue
        text = content.rstrip()
        if text.strip():
            out.append(SrcLine(text, name, no))
    return out


def find_copybook(name: str, search_dirs: list[Path]) -> Path | None:
    for d in search_dirs:
        for ext in COPYBOOK_EXTS:
            for cand in (name, name.lower(), name.upper()):
                p = d / f"{cand}{ext}"
                if p.is_file():
                    return p
    return None


def expand_copy(lines: list[SrcLine], search_dirs: list[Path],
                used: list[str], missing: list[str], depth: int = 0) -> list[SrcLine]:
    out: list[SrcLine] = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        text = ln.text
        # a COPY statement may wrap over several lines until the closing period
        if re.match(r"^\s*COPY\s", text, re.IGNORECASE):
            stmt, j = text, i
            while not re.search(r"\.\s*$", stmt) and j + 1 < len(lines):
                j += 1
                stmt += " " + lines[j].text.strip()
            m = _COPY_RE.match(stmt)
            if m and depth < 8:
                book = m.group(1).upper()
                path = find_copybook(book, search_dirs)
                if path is None:
                    missing.append(book)
                    out.append(ln)
                else:
                    if book not in used:
                        used.append(book)
                    inner = read_lines(path, path.name)
                    if m.group(2):
                        pairs = _PSEUDO.findall(m.group(2))
                        inner = [SrcLine(_apply_replacing(x.text, pairs), x.file, x.no) for x in inner]
                    out.extend(expand_copy(inner, search_dirs, used, missing, depth + 1))
                i = j + 1
                continue
        out.append(ln)
        i += 1
    return out


def _apply_replacing(text: str, pairs: list[tuple[str, str]]) -> str:
    for old, new in pairs:
        text = text.replace(old.strip(), new.strip())
    return text


def split_sentences(lines: list[SrcLine]) -> list[tuple[str, int, int]]:
    """Split on periods that are followed by white space, outside literals.

    Returns ``(text, first_line_index, last_line_index)`` triples where the
    indexes point into ``lines``.
    """
    sentences: list[tuple[str, int, int]] = []
    buf: list[str] = []
    start: int | None = None
    quote: str | None = None
    for idx, ln in enumerate(lines):
        text = ln.text
        n = len(text)
        for pos, ch in enumerate(text):
            if start is None and not ch.isspace():
                start = idx
            if quote:
                if ch == quote:
                    quote = None
                buf.append(ch)
                continue
            if ch in "'\"":
                quote = ch
                buf.append(ch)
                continue
            if ch == "." and (pos + 1 >= n or text[pos + 1].isspace()):
                sent = "".join(buf).strip()
                if sent:
                    sentences.append((sent, start if start is not None else idx, idx))
                buf, start = [], None
                continue
            buf.append(ch)
        buf.append(" ")
        if quote:  # unterminated literal across lines: treat the line break as the end
            quote = None
    tail = "".join(buf).strip()
    if tail:
        sentences.append((tail, start if start is not None else len(lines) - 1, len(lines) - 1))
    return sentences

"""Structural COBOL parser.

Turns a COBOL program (fixed or free format, with COPY books) into a
``ProgramModel``: files, data items, and a statement AST for the Procedure
Division. The AST uses plain dicts so it can be shipped to the UI and to
prompts unchanged.

The parser covers an explicit, documented subset (see ``SUPPORTED_VERBS``).
Anything outside the subset is parsed into an ``UNSUPPORTED`` node and
recorded in ``ProgramModel.unsupported`` - it is never silently dropped.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field, asdict
from decimal import Decimal
from pathlib import Path

from .source import SrcLine, expand_copy, read_lines, split_sentences

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "runtime"))
from cobol_runtime import PicInfo, parse_pic, pyname  # noqa: E402

SUPPORTED_VERBS = {
    "MOVE", "COMPUTE", "ADD", "SUBTRACT", "MULTIPLY", "DIVIDE", "IF", "EVALUATE",
    "PERFORM", "DISPLAY", "ACCEPT", "OPEN", "CLOSE", "READ", "WRITE", "STOP",
    "GOBACK", "EXIT", "CONTINUE", "INITIALIZE", "SET",
}
KNOWN_UNSUPPORTED_VERBS = {
    "CALL", "GO", "STRING", "UNSTRING", "INSPECT", "SEARCH", "SORT", "MERGE", "ALTER",
    "RETURN", "REWRITE", "DELETE", "START", "CANCEL", "RELEASE", "USE", "EXEC", "ENTRY",
    "RESET", "UNLOCK", "TRANSFORM", "JSON", "XML",
}
ALL_VERBS = SUPPORTED_VERBS | KNOWN_UNSUPPORTED_VERBS
FIGURATIVES = {
    "ZERO": "ZERO", "ZEROS": "ZERO", "ZEROES": "ZERO", "SPACE": "SPACE", "SPACES": "SPACE",
    "HIGH-VALUE": "HIGH_VALUE", "HIGH-VALUES": "HIGH_VALUE", "LOW-VALUE": "LOW_VALUE",
    "LOW-VALUES": "LOW_VALUE", "QUOTE": "QUOTE", "QUOTES": "QUOTE",
}
RELATIONAL_WORDS = {"=", "<", ">", "<=", ">=", "<>", "EQUAL", "GREATER", "LESS", "NOT"}
SCOPE_STOPS = {"ELSE", "WHEN", "AT", "NOT", "ON", "INVALID", "AFTER", "UNTIL"}

# ------------------------------------------------------------------- tokens
_TOKEN_RE = re.compile(
    r"""\s*(?:
      (?P<str>'(?:[^']|'')*'|"(?:[^"]|"")*")
     |(?P<num>\d+\.\d+|\.\d+)(?![A-Za-z0-9_-])
     |(?P<word>[A-Za-z0-9][A-Za-z0-9_]*(?:-[A-Za-z0-9_]+)*)
     |(?P<op>\*\*|<=|>=|<>|[-+*/=<>():,])
    )""", re.VERBOSE)


@dataclass
class Tok:
    kind: str   # str | num | word | op
    text: str
    line: int
    space_before: bool = True

    @property
    def up(self) -> str:
        return self.text.upper() if self.kind == "word" else self.text


def tokenize(text: str, line: int) -> list[Tok]:
    toks: list[Tok] = []
    pos = 0
    while pos < len(text):
        m = _TOKEN_RE.match(text, pos)
        if not m:
            if text[pos:].strip() == "":
                break
            pos += 1  # skip stray char
            continue
        space = m.group(0)[:1].isspace()
        kind = m.lastgroup
        raw = m.group(kind)
        pos = m.end()
        if kind == "word" and re.fullmatch(r"\d+", raw):
            kind = "num"
        if kind == "str":
            q = raw[0]
            raw = raw[1:-1].replace(q * 2, q)
        toks.append(Tok(kind, raw, line, space))
    # signed numeric literals: "-5", "+1.5" when a sign directly precedes a number
    merged: list[Tok] = []
    i = 0
    while i < len(toks):
        t = toks[i]
        if (t.kind == "op" and t.text in "+-" and i + 1 < len(toks)
                and toks[i + 1].kind == "num" and not toks[i + 1].space_before):
            prev = merged[-1] if merged else None
            operand_before = prev is not None and (
                prev.kind in ("num", "str") or (prev.kind == "op" and prev.text == ")")
                or (prev.kind == "word" and prev.up not in _SIGN_CONTEXT))
            if not operand_before:
                merged.append(Tok("num", t.text + toks[i + 1].text, t.line, t.space_before))
                i += 2
                continue
        merged.append(t)
        i += 1
    return merged


_SIGN_CONTEXT = {
    "TO", "FROM", "BY", "INTO", "GIVING", "IS", "VALUE", "THAN", "EQUAL", "IF", "WHEN",
    "UNTIL", "AND", "OR", "NOT", "MOVE", "COMPUTE", "ADD", "SUBTRACT", "MULTIPLY",
    "DIVIDE", "DISPLAY", "VARYING", "ALSO", "THRU", "THROUGH",
}


# ------------------------------------------------------------------- model
@dataclass
class DataItem:
    level: int
    name: str
    py: str
    section: str                 # file | working | linkage | local
    line: int
    pic: str | None = None
    usage: str = "DISPLAY"
    value: object = None         # str | Decimal-string dict | figurative
    occurs: int | None = None
    redefines: str | None = None
    fd: str | None = None
    parent: str | None = None
    values88: list = field(default_factory=list)
    info: dict | None = None
    flags: list[str] = field(default_factory=list)

    @property
    def is_group(self) -> bool:
        return self.pic is None and self.level != 88


@dataclass
class FileDef:
    name: str
    assign: str
    organization: str
    status: str | None
    line: int
    records: list[str] = field(default_factory=list)
    access: str = "SEQUENTIAL"


@dataclass
class Paragraph:
    name: str
    section: str | None
    line: int
    end_line: int
    stmts: list[dict] = field(default_factory=list)


@dataclass
class Unsupported:
    line: int
    construct: str
    text: str
    where: str = ""


@dataclass
class ProgramModel:
    path: str
    program_id: str = ""
    files: list[FileDef] = field(default_factory=list)
    data: list[DataItem] = field(default_factory=list)
    paragraphs: list[Paragraph] = field(default_factory=list)
    sections: dict[str, list[str]] = field(default_factory=dict)
    copybooks: list[str] = field(default_factory=list)
    missing_copybooks: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    unsupported: list[Unsupported] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    source_lines: list[SrcLine] = field(default_factory=list)
    using: list[str] = field(default_factory=list)
    loc: int = 0

    def item(self, name: str) -> DataItem | None:
        return next((d for d in self.data if d.name == name), None)

    def paragraph(self, name: str) -> Paragraph | None:
        return next((p for p in self.paragraphs if p.name == name), None)

    def paragraph_source(self, p: Paragraph) -> str:
        return "\n".join(l.text for l in self.source_lines[p.line:p.end_line + 1])

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("source_lines", None)
        d["line_map"] = [[l.file, l.no] for l in self.source_lines]
        return d

    def origin(self, line: int) -> tuple[str, int]:
        """(file, line number) for a 1-based logical line index."""
        if 1 <= line <= len(self.source_lines):
            l = self.source_lines[line - 1]
            return l.file, l.no
        return Path(self.path).name, line


# --------------------------------------------------------------- top level
def parse_program(path: str | Path, copy_dirs: list[Path] | None = None) -> ProgramModel:
    path = Path(path)
    model = ProgramModel(path=str(path))
    dirs = [path.parent, *(copy_dirs or [])]
    dirs += [path.parent.parent / "copybooks", path.parent.parent / "copy", path.parent / "copybooks"]
    lines = read_lines(path)
    lines = expand_copy(lines, dirs, model.copybooks, model.missing_copybooks)
    model.source_lines = lines
    model.loc = len(lines)
    sentences = split_sentences(lines)
    _Parser(model, lines, sentences).run()
    return model


class _Parser:
    def __init__(self, model: ProgramModel, lines: list[SrcLine], sentences) -> None:
        self.m = model
        self.lines = lines
        self.sentences = sentences
        self.py_names: dict[str, int] = {}

    def run(self) -> None:
        division = ""
        section = ""
        data_section = ""
        cur_fd: str | None = None
        paragraph: Paragraph | None = None
        proc_section: str | None = None
        want_program_id = False
        for text, first, last in self.sentences:
            up = text.upper()
            words = up.split()
            line_no = first
            m_div = re.fullmatch(r"(IDENTIFICATION|ID|ENVIRONMENT|DATA|PROCEDURE) DIVISION(.*)", up)
            if m_div:
                division = m_div.group(1)
                if division == "ID":
                    division = "IDENTIFICATION"
                if division == "PROCEDURE":
                    self._parse_using(m_div.group(2))
                continue
            if division == "IDENTIFICATION":
                m = re.match(r"PROGRAM-ID\.?\s*([A-Z0-9_-]+)?", up)
                if m:
                    if m.group(1):
                        self.m.program_id = m.group(1)
                    else:
                        want_program_id = True
                elif want_program_id and re.fullmatch(r"[A-Z0-9_-]+", up):
                    self.m.program_id = up
                    want_program_id = False
                continue
            if division == "ENVIRONMENT":
                if up.startswith("SELECT"):
                    self._parse_select(text, line_no)
                continue
            if division == "DATA":
                m = re.fullmatch(r"(FILE|WORKING-STORAGE|LINKAGE|LOCAL-STORAGE) SECTION", up)
                if m:
                    data_section = {"FILE": "file", "WORKING-STORAGE": "working",
                                    "LINKAGE": "linkage", "LOCAL-STORAGE": "local"}[m.group(1)]
                    continue
                if words[0] in ("FD", "SD"):
                    cur_fd = words[1]
                    if words[0] == "SD":
                        self.m.unsupported.append(Unsupported(line_no + 1, "SD (sort file)", text))
                    continue
                if re.match(r"\d+$", words[0]):
                    self._parse_data_item(text, line_no, data_section, cur_fd)
                continue
            if division == "PROCEDURE":
                msec = re.fullmatch(r"([A-Z0-9][A-Z0-9_-]*) SECTION(?: \d+)?", up)
                if msec:
                    proc_section = msec.group(1)
                    self.m.sections.setdefault(proc_section, [])
                    continue
                toks = tokenize(text, first)
                if (len(toks) == 1 and toks[0].kind in ("word", "num")
                        and toks[0].up not in ALL_VERBS and toks[0].up not in ("EXIT", "CONTINUE")):
                    paragraph = Paragraph(toks[0].up, proc_section, first, last)
                    self.m.paragraphs.append(paragraph)
                    if proc_section:
                        self.m.sections[proc_section].append(paragraph.name)
                    continue
                if paragraph is None:
                    paragraph = Paragraph("MAIN-LOGIC", proc_section, first, last)
                    self.m.paragraphs.append(paragraph)
                    if proc_section:
                        self.m.sections[proc_section].append(paragraph.name)
                paragraph.end_line = last
                stmts = _StmtParser(self.m, toks, paragraph.name).parse_sentence()
                paragraph.stmts.extend(stmts)
        self._finalize_data()

    # -- environment -----------------------------------------------------------
    def _parse_using(self, rest: str) -> None:
        m = re.search(r"USING\s+(.*)", rest)
        if m:
            self.m.using = [w for w in m.group(1).split() if w not in ("BY", "REFERENCE", "VALUE")]

    def _parse_select(self, text: str, line: int) -> None:
        m = re.match(r"SELECT\s+(?:OPTIONAL\s+)?([A-Za-z0-9_-]+)\s+ASSIGN\s+(?:TO\s+)?(?:\w+\s+)?('[^']*'|\"[^\"]*\"|[A-Za-z0-9_-]+)",
                     text, re.IGNORECASE)
        if not m:
            self.m.unsupported.append(Unsupported(line + 1, "SELECT clause", text))
            return
        assign = m.group(2).strip("'\"")
        org = re.search(r"ORGANIZATION\s+(?:IS\s+)?((?:LINE\s+)?SEQUENTIAL|INDEXED|RELATIVE)", text, re.IGNORECASE)
        status = re.search(r"FILE\s+STATUS\s+(?:IS\s+)?([A-Za-z0-9_-]+)", text, re.IGNORECASE)
        access = re.search(r"ACCESS\s+(?:MODE\s+)?(?:IS\s+)?(SEQUENTIAL|RANDOM|DYNAMIC)", text, re.IGNORECASE)
        organization = re.sub(r"\s+", " ", org.group(1).upper()) if org else "SEQUENTIAL"
        fd = FileDef(m.group(1).upper(), assign, organization,
                     status.group(1).upper() if status else None, line + 1,
                     access=access.group(1).upper() if access else "SEQUENTIAL")
        if organization in ("INDEXED", "RELATIVE") or fd.access in ("RANDOM", "DYNAMIC"):
            self.m.unsupported.append(Unsupported(line + 1, f"{organization} / {fd.access} file access",
                                                  text, fd.name))
        self.m.files.append(fd)

    # -- data division -----------------------------------------------------------
    def _parse_data_item(self, text: str, line: int, section: str, fd: str | None) -> None:
        m = re.match(r"(\d+)\s*(.*)", text, re.DOTALL)
        level = int(m.group(1))
        rest = m.group(2)
        name = "FILLER"
        mname = re.match(r"([A-Za-z0-9][A-Za-z0-9_-]*)(?:\s+|$)(.*)", rest, re.DOTALL)
        keyword_starts = ("PIC", "PICTURE", "USAGE", "VALUE", "VALUES", "OCCURS", "REDEFINES",
                          "COMP", "BINARY", "SYNC", "SIGN", "JUST", "BLANK")
        if mname and mname.group(1).upper() not in keyword_starts and not re.match(
                r"COMP|PACKED|DISPLAY", mname.group(1).upper()):
            name, rest = mname.group(1).upper(), mname.group(2)
        clean = re.sub(r"'[^']*'|\"[^\"]*\"", "''", rest)
        up = clean.upper()
        item = DataItem(level=level, name=name, py="", section=section or "working",
                        line=line + 1, fd=fd if section == "file" else None)
        if level in (66,):
            self.m.unsupported.append(Unsupported(line + 1, "RENAMES (level 66)", text))
            return
        pic = re.search(r"\bPIC(?:TURE)?\s+(?:IS\s+)?(\S+)", rest, re.IGNORECASE)
        if pic:
            item.pic = pic.group(1).rstrip(".")
        usage = re.search(
            r"\b(?:USAGE\s+(?:IS\s+)?)?(COMP-3|COMPUTATIONAL-3|COMP-5|COMPUTATIONAL-5|COMP-4|COMPUTATIONAL-4|"
            r"COMPUTATIONAL|COMP|BINARY|PACKED-DECIMAL|DISPLAY|INDEX|COMP-1|COMP-2)\b", up)
        if usage:
            item.usage = usage.group(1)
            if item.usage in ("COMP-1", "COMP-2", "INDEX"):
                item.flags.append(f"unsupported usage {item.usage}")
                self.m.unsupported.append(Unsupported(line + 1, f"USAGE {item.usage}", text, name))
        occ = re.search(r"\bOCCURS\s+(\d+)(?:\s+TO\s+(\d+))?", up)
        if occ:
            item.occurs = int(occ.group(2) or occ.group(1))
            if "DEPENDING" in up or occ.group(2):
                item.flags.append("variable-length table (DEPENDING ON)")
                self.m.unsupported.append(Unsupported(line + 1, "OCCURS DEPENDING ON", text, name))
            if re.search(r"\bINDEXED\s+BY\b", up):
                item.flags.append("INDEXED BY")
                self.m.unsupported.append(Unsupported(line + 1, "INDEXED BY (index names)", text, name))
        red = re.search(r"\bREDEFINES\s+([A-Z0-9_-]+)", up)
        if red:
            item.redefines = red.group(1)
        if re.search(r"\bSIGN\s+(IS\s+)?(LEADING|TRAILING)?\s*SEPARATE", up) or re.search(r"\bSIGN\s+(IS\s+)?LEADING", up):
            item.flags.append("SIGN clause")
            self.m.unsupported.append(Unsupported(line + 1, "SIGN LEADING/SEPARATE", text, name))
        for kw in ("SYNC", "SYNCHRONIZED", "JUSTIFIED", "JUST ", "BLANK WHEN ZERO"):
            if re.search(r"\b" + kw.strip() + r"\b", up):
                item.flags.append(kw.strip())
                self.m.unsupported.append(Unsupported(line + 1, kw.strip() + " clause", text, name))
        # VALUE
        if level == 88:
            mv = re.search(r"\bVALUES?\s+(?:ARE\s+|IS\s+)?(.*)", rest, re.IGNORECASE | re.DOTALL)
            item.values88 = _parse_value_list(mv.group(1)) if mv else []
        else:
            mv = re.search(r"\bVALUE\s+(?:IS\s+)?(.*)", rest, re.IGNORECASE | re.DOTALL)
            if mv:
                vals = _parse_value_list(mv.group(1))
                item.value = vals[0] if vals else None
        if item.pic is not None:
            try:
                info = parse_pic(item.pic, item.usage)
                item.info = {"category": info.category, "size": info.size, "digits": info.digits,
                             "scale": info.scale, "signed": info.signed, "usage": info.usage}
            except Exception:
                item.flags.append(f"unsupported PICTURE {item.pic}")
                self.m.unsupported.append(Unsupported(line + 1, f"PICTURE {item.pic}", text, name))
        self.m.data.append(item)

    def _finalize_data(self) -> None:
        stack: list[DataItem] = []
        last: DataItem | None = None
        counts: dict[str, int] = {}
        for it in self.m.data:
            if it.level == 88:
                it.parent = last.name if last else None
            else:
                while stack and stack[-1].level >= it.level:
                    stack.pop()
                if it.level in (1, 77):
                    stack = []
                it.parent = stack[-1].name if stack else None
                stack.append(it)
                last = it
            base = pyname(it.name)
            n = counts.get(base, 0)
            counts[base] = n + 1
            it.py = base if n == 0 else f"{base}_{n + 1}"
        # file -> record names
        for f in self.m.files:
            f.records = [d.name for d in self.m.data
                         if d.section == "file" and d.fd == f.name and d.level == 1]


def _parse_value_list(text: str) -> list:
    """Parse the literals of a VALUE clause (strings, numbers, figuratives, THRU)."""
    toks = tokenize(text.rstrip(". "), 0)
    out: list = []
    i = 0
    stop = {"PIC", "PICTURE", "USAGE", "OCCURS", "REDEFINES", "SYNC", "SIGN", "VALUE", "COMP", "COMP-3"}
    while i < len(toks):
        t = toks[i]
        if t.kind == "word" and t.up in stop:
            break
        if t.kind == "op" and t.text == ",":
            i += 1
            continue
        if t.kind == "word" and t.up in ("THRU", "THROUGH") and out:
            if i + 1 < len(toks):
                out[-1] = [out[-1], _literal_of(toks[i + 1])]
                i += 2
                continue
        lit = _literal_of(t)
        if lit is not None:
            out.append(lit)
        i += 1
    return out


def _literal_of(t: Tok):
    if t.kind == "str":
        return {"t": "str", "v": t.text}
    if t.kind == "num":
        return {"t": "num", "v": t.text}
    if t.kind == "word" and t.up in FIGURATIVES:
        return {"t": "fig", "v": FIGURATIVES[t.up]}
    return None


# -------------------------------------------------------------- statements
class _StmtParser:
    def __init__(self, model: ProgramModel, toks: list[Tok], paragraph: str) -> None:
        self.m = model
        self.toks = toks
        self.i = 0
        self.paragraph = paragraph

    # -- cursor ------------------------------------------------------------------
    def peek(self, k: int = 0) -> Tok | None:
        j = self.i + k
        return self.toks[j] if j < len(self.toks) else None

    def next(self) -> Tok:
        t = self.toks[self.i]
        self.i += 1
        return t

    def at_end(self) -> bool:
        return self.i >= len(self.toks)

    def peek_up(self, k: int = 0) -> str:
        t = self.peek(k)
        return t.up if t is not None else ""

    def accept(self, *words: str) -> bool:
        if self.peek_up() in words:
            self.i += 1
            return True
        return False

    def accept_seq(self, *words: str) -> bool:
        for k, w in enumerate(words):
            if self.peek_up(k) != w:
                return False
        self.i += len(words)
        return True

    def flag(self, tok: Tok | None, construct: str, text: str = "") -> None:
        self.m.unsupported.append(Unsupported((tok.line + 1) if tok else 0, construct,
                                              text, self.paragraph))

    # -- blocks --------------------------------------------------------------------
    def parse_sentence(self) -> list[dict]:
        out: list[dict] = []
        while not self.at_end():
            before = self.i
            out.extend(self.parse_block())
            if self.i == before and not self.at_end():
                tok = self.next()
                self.flag(tok, f"unexpected token '{tok.text}'", tok.text)
        return out

    def parse_block(self) -> list[dict]:
        stmts: list[dict] = []
        while not self.at_end():
            up = self.peek_up()
            if up in ALL_VERBS:
                s = self.parse_statement()
                if s is not None:
                    stmts.append(s)
            elif up.startswith("END-") or up in SCOPE_STOPS:
                break
            else:
                break
        return stmts

    def parse_statement(self) -> dict | None:
        tok = self.next()
        verb = tok.up
        handler = getattr(self, "st_" + verb.replace("-", "_").lower(), None)
        if handler is not None:
            node = handler(tok)
            if node is not None:
                node.setdefault("line", tok.line + 1)
            return node
        return self.unsupported_stmt(tok)

    def unsupported_stmt(self, verb_tok: Tok) -> dict:
        parts = [verb_tok.text]
        end_marker = "END-" + verb_tok.up
        while not self.at_end():
            u = self.peek_up()
            if u == end_marker:
                self.i += 1
                break
            if u in ALL_VERBS or u.startswith("END-") or u in ("ELSE", "WHEN"):
                break
            parts.append(self.next().text)
        text = " ".join(parts)
        verb = verb_tok.up
        if verb == "CALL" and len(parts) > 1:
            name = parts[1].strip("'\"").upper()
            if name not in self.m.calls:
                self.m.calls.append(name)
        self.flag(verb_tok, f"{verb} statement", text)
        return {"kind": "UNSUPPORTED", "verb": verb, "text": text, "line": verb_tok.line + 1}

    # -- operands ------------------------------------------------------------------
    STOP_OPERAND = {"TO", "FROM", "BY", "INTO", "GIVING", "ROUNDED", "ON", "SIZE", "UNTIL", "VARYING",
                    "TIMES", "THRU", "THROUGH", "AT", "NOT", "ELSE", "WHEN", "USING", "REMAINDER",
                    "AFTER", "INVALID", "END", "THEN", "AND", "OR", "ALSO", "TEST", "WITH", "OTHER"}

    def parse_ref(self) -> dict:
        t = self.next()
        name = t.up
        ref: dict = {"t": "ref", "name": name, "subs": [], "qual": []}
        while self.peek_up() in ("OF", "IN") and self.peek(1) is not None and self.peek(1).kind == "word":
            self.i += 1
            ref["qual"].append(self.next().up)
        if self.peek() is not None and self.peek().kind == "op" and self.peek().text == "(":
            self.i += 1
            subs: list[dict] = []
            depth_start = self.i
            while not self.at_end() and not (self.peek().kind == "op" and self.peek().text == ")"):
                if self.peek().kind == "op" and self.peek().text == ":":
                    self.flag(t, "reference modification", name)
                    ref["refmod"] = True
                    self.i += 1
                    continue
                if self.peek().kind == "op" and self.peek().text == ",":
                    self.i += 1
                    continue
                subs.append(self.parse_arith())
                if self.i == depth_start:  # no progress
                    self.i += 1
            self.accept_op(")")
            ref["subs"] = subs
        return ref

    def accept_op(self, text: str) -> bool:
        t = self.peek()
        if t is not None and t.kind == "op" and t.text == text:
            self.i += 1
            return True
        return False

    def parse_primary(self) -> dict:
        t = self.peek()
        if t is None:
            return {"t": "num", "v": "0"}
        if t.kind == "num":
            self.i += 1
            return {"t": "num", "v": t.text}
        if t.kind == "str":
            self.i += 1
            return {"t": "str", "v": t.text}
        if t.kind == "op" and t.text == "(":
            self.i += 1
            e = self.parse_arith()
            self.accept_op(")")
            return e
        if t.kind == "op" and t.text in "+-":
            self.i += 1
            e = self.parse_power()
            return {"t": "neg", "e": e} if t.text == "-" else e
        if t.kind == "word":
            if t.up in FIGURATIVES:
                self.i += 1
                return {"t": "fig", "v": FIGURATIVES[t.up]}
            if t.up == "FUNCTION":
                self.i += 1
                fn = self.next().text if not self.at_end() else ""
                if self.accept_op("("):
                    depth = 1
                    while not self.at_end() and depth:
                        x = self.next()
                        if x.kind == "op" and x.text == "(":
                            depth += 1
                        elif x.kind == "op" and x.text == ")":
                            depth -= 1
                self.flag(t, f"intrinsic FUNCTION {fn.upper()}", fn)
                return {"t": "unsupported", "v": f"FUNCTION {fn}"}
            return self.parse_ref()
        self.i += 1
        return {"t": "num", "v": "0"}

    def parse_power(self) -> dict:
        base = self.parse_primary()
        if self.peek() is not None and self.peek().kind == "op" and self.peek().text == "**":
            self.i += 1
            exp = self.parse_power()
            return {"t": "bin", "op": "**", "l": base, "r": exp}
        return base

    def parse_term(self) -> dict:
        left = self.parse_power()
        while self.peek() is not None and self.peek().kind == "op" and self.peek().text in ("*", "/"):
            op = self.next().text
            left = {"t": "bin", "op": op, "l": left, "r": self.parse_power()}
        return left

    def parse_arith(self) -> dict:
        left = self.parse_term()
        while self.peek() is not None and self.peek().kind == "op" and self.peek().text in ("+", "-"):
            op = self.next().text
            left = {"t": "bin", "op": op, "l": left, "r": self.parse_term()}
        return left

    # -- conditions ------------------------------------------------------------------
    def parse_cond(self) -> dict:
        left = self.parse_and()
        while self.peek_up() == "OR":
            self.i += 1
            left = {"t": "or", "l": left, "r": self.parse_and()}
        return left

    def parse_and(self) -> dict:
        left = self.parse_not()
        while self.peek_up() == "AND":
            self.i += 1
            left = {"t": "and", "l": left, "r": self.parse_not()}
        return left

    def parse_not(self) -> dict:
        if self.peek_up() == "NOT":
            self.i += 1
            return {"t": "not", "e": self.parse_not()}
        return self.parse_relation()

    def parse_relation(self) -> dict:
        t = self.peek()
        if t is not None and t.kind == "op" and t.text == "(":
            save = self.i
            self.i += 1
            try:
                inner = self.parse_cond()
                if self.accept_op(")"):
                    nxt = self.peek()
                    if nxt is None or nxt.up in ("AND", "OR", "THEN") or nxt.up in ALL_VERBS \
                            or nxt.up in SCOPE_STOPS or nxt.up.startswith("END-"):
                        return inner
            except IndexError:
                pass
            self.i = save
        left = self.parse_arith()
        neg = False
        had_is = False
        if self.peek_up() == "IS":
            self.i += 1
            had_is = True
        if self.peek_up() == "NOT":
            self.i += 1
            neg = True
        up = self.peek_up()
        if up in ("NUMERIC", "ALPHABETIC", "ALPHABETIC-LOWER", "ALPHABETIC-UPPER"):
            self.i += 1
            return {"t": "class", "ref": left, "cls": up, "neg": neg}
        if up in ("POSITIVE", "NEGATIVE"):
            self.i += 1
            return {"t": "sign", "ref": left, "sign": up, "neg": neg}
        op = None
        t = self.peek()
        if t is not None and t.kind == "op" and t.text in ("=", "<", ">", "<=", ">=", "<>"):
            op = {"<>": "!="}.get(t.text, t.text)
            self.i += 1
        elif up in ("EQUAL", "GREATER", "LESS"):
            self.i += 1
            self.accept("TO", "THAN")
            op = {"EQUAL": "=", "GREATER": ">", "LESS": "<"}[up]
            if op in (">", "<") and self.peek_up() == "OR":
                self.i += 1
                self.accept("EQUAL")
                self.accept("TO")
                op += "="
        elif (had_is or neg) and up in FIGURATIVES and FIGURATIVES[up] == "ZERO":
            op = "="  # X IS [NOT] ZERO
        if op is None:
            if left.get("t") == "ref":
                node = {"t": "cond88", "ref": left}
                return {"t": "not", "e": node} if neg else node
            self.flag(self.peek(), "abbreviated or unsupported condition")
            return {"t": "truthy", "e": left}
        right = self.parse_arith()
        node = {"t": "cmp", "op": op, "l": left, "r": right}
        return {"t": "not", "e": node} if neg else node

    # -- statements ---------------------------------------------------------------------
    def st_move(self, tok: Tok) -> dict:
        if self.accept("CORRESPONDING", "CORR"):
            self.flag(tok, "MOVE CORRESPONDING")
        src = self.parse_arith()
        dst: list[dict] = []
        if self.accept("TO"):
            while not self.at_end() and self.peek().kind == "word" and self.peek_up() not in ALL_VERBS \
                    and not self.peek_up().startswith("END-") and self.peek_up() not in SCOPE_STOPS:
                dst.append(self.parse_ref())
        return {"kind": "MOVE", "src": src, "dst": dst}

    def _targets(self) -> list[dict]:
        out = []
        while not self.at_end() and self.peek().kind == "word" and self.peek_up() not in ALL_VERBS \
                and self.peek_up() not in self.STOP_OPERAND and not self.peek_up().startswith("END-"):
            ref = self.parse_ref()
            rounded = self.accept("ROUNDED")
            out.append({"ref": ref, "rounded": rounded})
        return out

    def _size_error(self) -> tuple[list | None, list | None]:
        on = not_on = None
        while True:
            if self.accept_seq("ON", "SIZE", "ERROR") or self.accept_seq("SIZE", "ERROR"):
                on = self.parse_block()
            elif self.accept_seq("NOT", "ON", "SIZE", "ERROR") or self.accept_seq("NOT", "SIZE", "ERROR"):
                not_on = self.parse_block()
            else:
                break
        return on, not_on

    def _arith_end(self, verb: str) -> None:
        self.accept("END-" + verb)

    def st_compute(self, tok: Tok) -> dict:
        targets = self._targets()
        self.accept("=")
        expr = self.parse_arith()
        on, not_on = self._size_error()
        self._arith_end("COMPUTE")
        return {"kind": "ARITH", "assigns": [{"target": t["ref"], "rounded": t["rounded"], "expr": expr}
                                             for t in targets],
                "on_size_error": on, "not_on_size_error": not_on}

    def _operands_until(self, *stops: str) -> list[dict]:
        ops = []
        while not self.at_end() and self.peek_up() not in stops and self.peek_up() not in ALL_VERBS \
                and not self.peek_up().startswith("END-") and self.peek_up() not in SCOPE_STOPS:
            before = self.i
            ops.append(self.parse_arith())
            if self.i == before:
                self.i += 1
        return ops

    @staticmethod
    def _sum(ops: list[dict]) -> dict:
        e = ops[0]
        for o in ops[1:]:
            e = {"t": "bin", "op": "+", "l": e, "r": o}
        return e

    def st_add(self, tok: Tok) -> dict:
        ops = self._operands_until("TO", "GIVING")
        assigns: list[dict] = []
        if self.accept("CORRESPONDING", "CORR"):
            self.flag(tok, "ADD CORRESPONDING")
        if self.accept("TO"):
            tos = self._operand_receivers()
            if self.accept("GIVING"):
                giving = self._targets()
                total = self._sum(ops + [t["ref"] for t in tos])
                assigns = [{"target": g["ref"], "rounded": g["rounded"], "expr": total} for g in giving]
            else:
                for t in tos:
                    assigns.append({"target": t["ref"], "rounded": t["rounded"],
                                    "expr": {"t": "bin", "op": "+", "l": t["ref"], "r": self._sum(ops)}})
        elif self.accept("GIVING"):
            giving = self._targets()
            assigns = [{"target": g["ref"], "rounded": g["rounded"], "expr": self._sum(ops)} for g in giving]
        on, not_on = self._size_error()
        self._arith_end("ADD")
        return {"kind": "ARITH", "assigns": assigns, "on_size_error": on, "not_on_size_error": not_on}

    def _operand_receivers(self) -> list[dict]:
        """Receivers after TO/FROM/BY/INTO: identifiers (or a literal when GIVING follows)."""
        out = []
        while not self.at_end() and self.peek_up() not in ("GIVING", "ON", "NOT", "SIZE", "REMAINDER") \
                and self.peek_up() not in ALL_VERBS and not self.peek_up().startswith("END-") \
                and self.peek_up() not in SCOPE_STOPS:
            before = self.i
            node = self.parse_arith()
            rounded = self.accept("ROUNDED")
            out.append({"ref": node, "rounded": rounded})
            if self.i == before:
                self.i += 1
        return out

    def st_subtract(self, tok: Tok) -> dict:
        ops = self._operands_until("FROM", "GIVING")
        assigns: list[dict] = []
        if self.accept("FROM"):
            froms = self._operand_receivers()
            if self.accept("GIVING"):
                giving = self._targets()
                base = froms[0]["ref"]
                assigns = [{"target": g["ref"], "rounded": g["rounded"],
                            "expr": {"t": "bin", "op": "-", "l": base, "r": self._sum(ops)}} for g in giving]
            else:
                for f in froms:
                    assigns.append({"target": f["ref"], "rounded": f["rounded"],
                                    "expr": {"t": "bin", "op": "-", "l": f["ref"], "r": self._sum(ops)}})
        on, not_on = self._size_error()
        self._arith_end("SUBTRACT")
        return {"kind": "ARITH", "assigns": assigns, "on_size_error": on, "not_on_size_error": not_on}

    def st_multiply(self, tok: Tok) -> dict:
        left = self.parse_arith()
        assigns: list[dict] = []
        if self.accept("BY"):
            by = self._operand_receivers()
            if self.accept("GIVING"):
                giving = self._targets()
                assigns = [{"target": g["ref"], "rounded": g["rounded"],
                            "expr": {"t": "bin", "op": "*", "l": left, "r": by[0]["ref"]}} for g in giving]
            else:
                for b in by:
                    assigns.append({"target": b["ref"], "rounded": b["rounded"],
                                    "expr": {"t": "bin", "op": "*", "l": b["ref"], "r": left}})
        on, not_on = self._size_error()
        self._arith_end("MULTIPLY")
        return {"kind": "ARITH", "assigns": assigns, "on_size_error": on, "not_on_size_error": not_on}

    def st_divide(self, tok: Tok) -> dict:
        first = self.parse_arith()
        assigns: list[dict] = []
        mode = "INTO" if self.accept("INTO") else "BY" if self.accept("BY") else None
        if mode == "INTO":
            tos = self._operand_receivers()
            if self.accept("GIVING"):
                giving = self._targets()
                assigns = [{"target": g["ref"], "rounded": g["rounded"],
                            "expr": {"t": "bin", "op": "/", "l": tos[0]["ref"], "r": first}} for g in giving]
            else:
                for t in tos:
                    assigns.append({"target": t["ref"], "rounded": t["rounded"],
                                    "expr": {"t": "bin", "op": "/", "l": t["ref"], "r": first}})
        elif mode == "BY":
            by = self.parse_arith()
            if self.accept("GIVING"):
                giving = self._targets()
                assigns = [{"target": g["ref"], "rounded": g["rounded"],
                            "expr": {"t": "bin", "op": "/", "l": first, "r": by}} for g in giving]
        if self.peek_up() == "REMAINDER":
            self.flag(tok, "DIVIDE ... REMAINDER")
            self.i += 1
            self._targets()
        on, not_on = self._size_error()
        self._arith_end("DIVIDE")
        return {"kind": "ARITH", "assigns": assigns, "on_size_error": on, "not_on_size_error": not_on}

    def st_if(self, tok: Tok) -> dict:
        cond = self.parse_cond()
        self.accept("THEN")
        if self.peek_up() == "NEXT":
            self.flag(tok, "NEXT SENTENCE")
            self.i += 2
        then = self.parse_block()
        els: list[dict] = []
        if self.accept("ELSE"):
            if self.peek_up() == "NEXT":
                self.flag(tok, "NEXT SENTENCE")
                self.i += 2
            els = self.parse_block()
        self.accept("END-IF")
        return {"kind": "IF", "cond": cond, "then": then, "else": els}

    def st_evaluate(self, tok: Tok) -> dict:
        subject: dict
        if self.peek_up() == "TRUE":
            self.i += 1
            subject = {"t": "true"}
        elif self.peek_up() == "FALSE":
            self.i += 1
            subject = {"t": "false"}
        else:
            subject = self.parse_arith()
        if self.peek_up() == "ALSO":
            self.flag(tok, "EVALUATE ... ALSO")
            self.i = len(self.toks)
            return {"kind": "UNSUPPORTED", "verb": "EVALUATE", "text": "EVALUATE ... ALSO"}
        whens: list[dict] = []
        other: list[dict] | None = None
        while self.peek_up() == "WHEN":
            conds: list = []
            is_other = False
            while self.accept("WHEN"):
                if self.accept("OTHER"):
                    is_other = True
                else:
                    if subject.get("t") in ("true", "false"):
                        conds.append(self.parse_cond())
                    else:
                        lo = self.parse_arith() if self.peek_up() != "ANY" else None
                        if lo is None:
                            self.i += 1
                            conds.append({"t": "any"})
                        elif self.accept("THRU", "THROUGH"):
                            conds.append({"t": "range", "lo": lo, "hi": self.parse_arith()})
                        else:
                            conds.append(lo)
                if self.peek_up() == "ALSO":
                    self.flag(tok, "EVALUATE ... ALSO")
                    self.i = len(self.toks)
                    break
            body = self.parse_block()
            if is_other:
                other = body
            else:
                whens.append({"conds": conds, "body": body})
        self.accept("END-EVALUATE")
        return {"kind": "EVALUATE", "subject": subject, "whens": whens, "other": other}

    def st_perform(self, tok: Tok) -> dict:
        node: dict = {"kind": "PERFORM", "target": None, "thru": None, "times": None,
                      "until": None, "test_after": False, "varying": None, "body": None}
        if self.peek() is not None and self.peek().kind in ("word", "num") and self.peek_up() not in (
                "UNTIL", "VARYING", "WITH", "TEST", "FOREVER") and self.peek_up() not in ALL_VERBS \
                and not self._looks_like_times():
            node["target"] = self.next().up
            if self.accept("THRU", "THROUGH"):
                node["thru"] = self.next().up
        if self._looks_like_times() and self.peek_up() != "UNTIL":
            node["times"] = self.parse_arith()
            self.accept("TIMES")
        if self.accept("WITH"):
            pass
        if self.accept("TEST"):
            node["test_after"] = self.accept("AFTER")
            self.accept("BEFORE")
        if self.accept("VARYING"):
            var = self.parse_ref()
            self.accept("FROM")
            start = self.parse_arith()
            self.accept("BY")
            step = self.parse_arith()
            self.accept("UNTIL")
            node["varying"] = {"var": var, "from": start, "by": step}
            node["until"] = self.parse_cond()
            if self.peek_up() == "AFTER":
                self.flag(tok, "PERFORM VARYING ... AFTER (nested)")
                while not self.at_end() and self.peek_up() not in ALL_VERBS and not self.peek_up().startswith("END-"):
                    self.i += 1
        elif self.accept("UNTIL"):
            node["until"] = self.parse_cond()
        elif self.accept("FOREVER"):
            node["forever"] = True
        if node["target"] is None:
            node["body"] = self.parse_block()
            if not self.accept("END-PERFORM"):
                pass
        return node

    def _looks_like_times(self) -> bool:
        t = self.peek()
        if t is None:
            return False
        nxt = self.peek(1)
        if t.kind == "num" and nxt is not None and nxt.up == "TIMES":
            return True
        if t.kind == "word" and nxt is not None and nxt.up == "TIMES":
            return True
        return False

    def st_display(self, tok: Tok) -> dict:
        items = self._operands_until("UPON", "WITH")
        advancing = True
        if self.accept("UPON"):
            self.i += 1
        if self.accept("WITH"):
            if self.accept_seq("NO", "ADVANCING"):
                advancing = False
                self.flag(tok, "DISPLAY WITH NO ADVANCING")
        self.accept("END-DISPLAY")
        return {"kind": "DISPLAY", "items": items, "advancing": advancing}

    def st_accept(self, tok: Tok) -> dict:
        target = self.parse_ref()
        source = None
        if self.accept("FROM"):
            words = []
            while not self.at_end() and self.peek_up() not in ALL_VERBS and not self.peek_up().startswith("END-") \
                    and self.peek_up() not in SCOPE_STOPS:
                words.append(self.next().up)
            source = " ".join(words)
        return {"kind": "ACCEPT", "target": target, "source": source}

    def st_open(self, tok: Tok) -> dict:
        entries = []
        mode = "INPUT"
        while not self.at_end() and self.peek_up() not in ALL_VERBS and not self.peek_up().startswith("END-") \
                and self.peek_up() not in SCOPE_STOPS:
            u = self.peek_up()
            if u in ("INPUT", "OUTPUT", "I-O", "EXTEND"):
                mode = u
                self.i += 1
                continue
            entries.append({"mode": mode, "file": self.next().up})
        return {"kind": "OPEN", "entries": entries}

    def st_close(self, tok: Tok) -> dict:
        files = []
        while not self.at_end() and self.peek_up() not in ALL_VERBS and not self.peek_up().startswith("END-") \
                and self.peek_up() not in SCOPE_STOPS:
            if self.peek_up() in ("WITH", "LOCK", "NO", "REWIND"):
                self.i += 1
                continue
            files.append(self.next().up)
        return {"kind": "CLOSE", "files": files}

    def st_read(self, tok: Tok) -> dict:
        file = self.next().up
        node = {"kind": "READ", "file": file, "into": None, "at_end": None, "not_at_end": None}
        self.accept("NEXT")
        self.accept("RECORD")
        if self.accept("INTO"):
            node["into"] = self.parse_ref()
        if self.peek_up() == "KEY" or self.peek_up() == "INVALID":
            self.flag(tok, "READ with KEY / INVALID KEY (indexed access)")
        while True:
            if self.accept_seq("AT", "END"):
                node["at_end"] = self.parse_block()
            elif self.accept_seq("NOT", "AT", "END"):
                node["not_at_end"] = self.parse_block()
            else:
                break
        self.accept("END-READ")
        return node

    def st_write(self, tok: Tok) -> dict:
        record = self.next().up
        node = {"kind": "WRITE", "record": record, "from": None}
        if self.accept("FROM"):
            node["from"] = self.parse_ref()
        if self.peek_up() in ("BEFORE", "AFTER"):
            self.flag(tok, "WRITE ... ADVANCING")
            while not self.at_end() and self.peek_up() not in ALL_VERBS and not self.peek_up().startswith("END-"):
                self.i += 1
        self.accept("END-WRITE")
        return node

    def st_stop(self, tok: Tok) -> dict:
        self.accept("RUN")
        return {"kind": "STOP_RUN"}

    def st_goback(self, tok: Tok) -> dict:
        return {"kind": "STOP_RUN"}

    def st_exit(self, tok: Tok) -> dict | None:
        if self.accept("PROGRAM"):
            return {"kind": "STOP_RUN"}
        if self.peek_up() in ("PERFORM", "PARAGRAPH", "SECTION"):
            self.flag(tok, f"EXIT {self.peek_up()}")
            self.i += 1
            return {"kind": "UNSUPPORTED", "verb": "EXIT", "text": "EXIT " + self.toks[self.i - 1].text}
        return {"kind": "NOP"}

    def st_continue(self, tok: Tok) -> dict:
        return {"kind": "NOP"}

    def st_initialize(self, tok: Tok) -> dict:
        targets = []
        while not self.at_end() and self.peek().kind == "word" and self.peek_up() not in ALL_VERBS \
                and self.peek_up() not in ("REPLACING", "WITH", "TO", "DEFAULT") and not self.peek_up().startswith("END-"):
            targets.append(self.parse_ref())
        if self.peek_up() in ("REPLACING", "WITH", "TO", "DEFAULT"):
            self.flag(tok, "INITIALIZE ... REPLACING")
            while not self.at_end() and self.peek_up() not in ALL_VERBS and not self.peek_up().startswith("END-"):
                self.i += 1
        return {"kind": "INITIALIZE", "targets": targets}

    def st_set(self, tok: Tok) -> dict:
        targets = []
        while not self.at_end() and self.peek_up() not in ("TO", "UP", "DOWN"):
            targets.append(self.parse_ref())
        if self.accept("TO"):
            if self.peek_up() in ("TRUE", "FALSE"):
                val = self.next().up == "TRUE"
                return {"kind": "SET_COND", "targets": targets, "value": val}
            return {"kind": "MOVE", "src": self.parse_arith(), "dst": targets}
        if self.accept("UP"):
            self.accept("BY")
            by = self.parse_arith()
            return {"kind": "ARITH", "assigns": [{"target": t, "rounded": False,
                                                   "expr": {"t": "bin", "op": "+", "l": t, "r": by}}
                                                  for t in targets], "on_size_error": None, "not_on_size_error": None}
        if self.accept("DOWN"):
            self.accept("BY")
            by = self.parse_arith()
            return {"kind": "ARITH", "assigns": [{"target": t, "rounded": False,
                                                   "expr": {"t": "bin", "op": "-", "l": t, "r": by}}
                                                  for t in targets], "on_size_error": None, "not_on_size_error": None}
        self.flag(tok, "SET statement form")
        return {"kind": "UNSUPPORTED", "verb": "SET", "text": "SET"}

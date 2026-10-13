"""cobol_runtime - COBOL data and I/O semantics for migrated Python programs.

Migrated programs are thin Python: control flow is plain Python, while every
rule that makes COBOL behave like COBOL lives here, so it is written, tested
and reviewed exactly once:

* byte-accurate storage, so group moves, REDEFINES and record I/O behave
  like the original (DISPLAY, COMP and COMP-3 numerics, overpunched signs)
* PIC-aware stores: truncation by default, rounding only for ROUNDED,
  high-order truncation, size-error detection
* numeric-edited pictures (Z * 9 . , $ + - CR DB B 0 /, floating insertion)
* COBOL comparison rules, level-88 condition names
* LINE SEQUENTIAL / SEQUENTIAL file handling with FILE STATUS codes

This module is self-contained (standard library only) and is copied next to
every migrated program.
"""
from __future__ import annotations

import keyword
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from decimal import (ROUND_DOWN, ROUND_HALF_UP, Decimal, DivisionByZero,
                     InvalidOperation, getcontext)
from pathlib import Path

getcontext().prec = 64

__all__ = [
    "CobolProgram", "CobolIO", "CobolRuntimeError", "StopRun", "Decimal",
    "ZERO", "SPACE", "HIGH_VALUE", "LOW_VALUE", "QUOTE", "cmp", "pyname",
    "parse_pic", "PicInfo",
]


class CobolRuntimeError(Exception):
    """A condition that would abort a COBOL run unit."""


class StopRun(Exception):
    """Raised by STOP RUN / GOBACK to unwind to the program entry point."""


# ---------------------------------------------------------------- figuratives
class _Figurative:
    def __init__(self, name: str, char: str) -> None:
        self.name, self.char = name, char

    def __repr__(self) -> str:
        return self.name


ZERO = _Figurative("ZERO", "0")
SPACE = _Figurative("SPACE", " ")
HIGH_VALUE = _Figurative("HIGH-VALUE", "\xff")
LOW_VALUE = _Figurative("LOW-VALUE", "\x00")
QUOTE = _Figurative("QUOTE", '"')

_PY_RESERVED = {"self", "files", "none", "true", "false"}


def pyname(name: str) -> str:
    """Python attribute name for a COBOL data name (shared with the translator)."""
    n = re.sub(r"[^0-9A-Za-z]", "_", name).lower()
    if not n or n[0].isdigit():
        n = "n" + n
    if keyword.iskeyword(n) or n in _PY_RESERVED:
        n += "_"
    return n


# ----------------------------------------------------------------- pictures
@dataclass(frozen=True)
class PicInfo:
    category: str          # 'numeric' | 'edited' | 'alnum'
    size: int              # bytes of storage
    digits: int = 0
    scale: int = 0
    signed: bool = False
    usage: str = "DISPLAY"  # DISPLAY | COMP | COMP-3
    pattern: str = ""      # expanded picture (edited items)


_REPEAT = re.compile(r"(.)\((\d+)\)")
_USAGE_ALIASES = {
    "DISPLAY": "DISPLAY", "COMP": "COMP", "COMPUTATIONAL": "COMP", "BINARY": "COMP",
    "COMP-4": "COMP", "COMPUTATIONAL-4": "COMP", "COMP-5": "COMP", "COMPUTATIONAL-5": "COMP",
    "COMP-3": "COMP-3", "COMPUTATIONAL-3": "COMP-3", "PACKED-DECIMAL": "COMP-3",
}


def expand_pic(pic: str) -> str:
    pic = pic.upper()
    while True:
        new = _REPEAT.sub(lambda m: m.group(1) * int(m.group(2)), pic)
        if new == pic:
            return pic
        pic = new


def parse_pic(pic: str, usage: str = "DISPLAY") -> PicInfo:
    p = expand_pic(pic)
    usage = _USAGE_ALIASES.get(usage.upper(), "DISPLAY")
    m = re.fullmatch(r"(S?)(9*)(?:V(9*))?", p)
    if m and (m.group(2) or m.group(3)):
        ints, frac = m.group(2), m.group(3) or ""
        digits = len(ints) + len(frac)
        if usage == "COMP-3":
            size = digits // 2 + 1
        elif usage == "COMP":
            size = 2 if digits <= 4 else 4 if digits <= 9 else 8
        else:
            size = digits
        return PicInfo("numeric", size, digits, len(frac), bool(m.group(1)), usage)
    if re.fullmatch(r"[XA9]+", p) and re.search(r"[XA]", p):
        return PicInfo("alnum", len(p))
    if re.fullmatch(r"(?:[Z9*.,B0/$+\-VS]|CR|DB)+", p):
        body = p.replace("V", "").replace("S", "")
        toks = _tokenize_pic(body)
        floats = _floating_chars(toks)
        return PicInfo("edited", len(body), _digit_slots(toks, floats),
                       _scale_of(p, floats), False, "DISPLAY", body)
    raise CobolRuntimeError(f"unsupported PICTURE clause: {pic!r}")


def _tokenize_pic(p: str) -> list[str]:
    toks, i = [], 0
    while i < len(p):
        if p[i:i + 2] in ("CR", "DB"):
            toks.append(p[i:i + 2])
            i += 2
        else:
            toks.append(p[i])
            i += 1
    return toks


def _floating_chars(toks: list[str]) -> set[str]:
    return {c for c in "$+-" if toks.count(c) >= 2}


def _digit_slots(toks: list[str], floats: set[str]) -> int:
    n = sum(1 for t in toks if t in ("9", "Z", "*"))
    for c in floats:
        n += toks.count(c) - 1
    return n


def _scale_of(p: str, floats: set[str]) -> int:
    p = p.replace("S", "")
    cut = p.find("V") if "V" in p else p.find(".")
    if cut < 0:
        return 0
    return sum(1 for c in p[cut + 1:] if c in "9Z*")


def _edit(info: PicInfo, n: int, neg: bool) -> str:
    """Format the unsigned scaled integer `n` through a numeric-edited picture."""
    toks = _tokenize_pic(info.pattern)
    floats = _floating_chars(toks)
    total = info.digits
    digits = f"{n:0{total}d}"[-total:] if total else ""
    if n == 0 and "9" not in toks and not any(c in floats for c in toks):
        return " " * len(info.pattern)  # BLANK WHEN ZERO behaviour of all-Z pictures
    fill = "*" if "*" in toks else " "
    out: list[str] = []
    di = 0
    suppress = True
    float_char: str | None = None
    first_seen: set[str] = set()
    placed = False

    def symbol(c: str) -> str:
        if c == "$":
            return "$"
        if c == "+":
            return "-" if neg else "+"
        return "-" if neg else " "

    def end_suppression() -> None:
        nonlocal suppress, placed
        if suppress and float_char and not placed and out:
            out[-1] = symbol(float_char)
            placed = True
        suppress = False

    for t in toks:
        if t in floats:
            float_char = t
            if t not in first_seen:
                first_seen.add(t)
                out.append(" ")
                continue
            d = digits[di]
            di += 1
            if suppress and d == "0":
                out.append(" ")
            else:
                end_suppression()
                out.append(d)
        elif t == "9":
            d = digits[di]
            di += 1
            end_suppression()
            out.append(d)
        elif t in ("Z", "*"):
            d = digits[di]
            di += 1
            if suppress and d == "0":
                out.append(fill)
            else:
                end_suppression()
                out.append(d)
        elif t == ",":
            out.append(fill if suppress else ",")
        elif t == ".":
            end_suppression()
            out.append(".")
        elif t == "B":
            out.append(" ")
        elif t in ("0", "/"):
            out.append(t)
        elif t == "$":
            out.append("$")
        elif t == "+":
            out.append("-" if neg else "+")
        elif t == "-":
            out.append("-" if neg else " ")
        elif t in ("CR", "DB"):
            out.append(t if neg else "  ")
        # 'V' and 'S' occupy no print position
    return "".join(out)


# ----------------------------------------------------------------- comparison
def _num_from_text(s: str) -> Decimal | None:
    t = s.strip()
    if re.fullmatch(r"[+-]?\d+(\.\d*)?|[+-]?\.\d+", t):
        return Decimal(t)
    return None


def cmp(a, op: str, b) -> bool:
    """COBOL relational test between values (Decimal, str or figurative)."""
    if isinstance(a, _Figurative) and not isinstance(b, _Figurative):
        a = _fig_for(a, b)
    elif isinstance(b, _Figurative) and not isinstance(a, _Figurative):
        b = _fig_for(b, a)
    elif isinstance(a, _Figurative):
        a, b = a.char, b.char
    if isinstance(a, Decimal) and isinstance(b, str):
        n = _num_from_text(b)
        if n is not None:
            b = n
        else:
            a = format(abs(int(a)), "d")
    elif isinstance(a, str) and isinstance(b, Decimal):
        n = _num_from_text(a)
        if n is not None:
            a = n
        else:
            b = format(abs(int(b)), "d")
    if isinstance(a, str) and isinstance(b, str):
        width = max(len(a), len(b))
        a, b = a.ljust(width), b.ljust(width)
    if op == "=":
        return a == b
    if op == "!=":
        return a != b
    if op == "<":
        return a < b
    if op == ">":
        return a > b
    if op == "<=":
        return a <= b
    if op == ">=":
        return a >= b
    raise CobolRuntimeError(f"unknown relational operator {op!r}")


def _fig_for(fig: _Figurative, other):
    if isinstance(other, Decimal):
        return Decimal(0) if fig is ZERO else fig.char * 1
    return fig.char * max(len(other), 1)


# ------------------------------------------------------------------ storage
class Memory:
    __slots__ = ("buf",)

    def __init__(self, size: int = 0) -> None:
        self.buf = bytearray(b" " * size)

    def ensure(self, size: int) -> None:
        if len(self.buf) < size:
            self.buf.extend(b" " * (size - len(self.buf)))


class Item:
    """Static description of one data item (group, elementary or level-88)."""

    def __init__(self, d: dict) -> None:
        self.name: str = d["name"]
        self.py: str = d.get("py") or pyname(d["name"])
        self.level: int = d["level"]
        self.pic: str | None = d.get("pic")
        self.usage: str = d.get("usage", "DISPLAY")
        self.value = d.get("value")
        self.occurs: int = d.get("occurs") or 1
        self.is_table = bool(d.get("occurs"))
        self.redefines: str | None = d.get("redefines")
        self.fd: str | None = d.get("fd")
        self.values88: list = d.get("values", [])
        self.parent: Item | None = None
        self.children: list[Item] = []
        self.conditions: list[Item] = []
        self.info: PicInfo | None = None
        self.rel = 0              # offset inside parent (element 1)
        self.offset = 0           # absolute offset in memory (element 1)
        self.size = 0             # bytes of ONE element
        self.mem: Memory | None = None
        self.redef_target: Item | None = None
        if self.pic is not None:
            self.info = parse_pic(self.pic, self.usage)

    @property
    def is_group(self) -> bool:
        return self.pic is None and self.level != 88

    @property
    def total(self) -> int:
        return self.size * self.occurs

    def ancestors_with_occurs(self) -> list["Item"]:
        chain, it = [], self
        while it is not None:
            if it.is_table:
                chain.append(it)
            it = it.parent
        return list(reversed(chain))


class Layout:
    """Builds storage and the attribute namespace from a data-description list."""

    def __init__(self, defs: list[dict]) -> None:
        self.items: list[Item] = []
        self.roots: list[Item] = []
        self.fd_memories: dict[str, Memory] = {}
        self.by_name: dict[str, Item] = {}
        stack: list[Item] = []
        last: Item | None = None
        seen_py: dict[str, int] = {}
        for d in defs:
            it = Item(d)
            n = seen_py.get(it.py, 0)
            seen_py[it.py] = n + 1
            if n:
                it.py = f"{it.py}_{n + 1}"
            if it.level == 88:
                if last is None:
                    raise CobolRuntimeError("level 88 without a preceding item")
                it.parent = last
                last.conditions.append(it)
            else:
                while stack and stack[-1].level >= it.level:
                    stack.pop()
                if it.level in (1, 77) or not stack:
                    stack = []
                    self.roots.append(it)
                else:
                    it.parent = stack[-1]
                    stack[-1].children.append(it)
                stack.append(it)
                last = it
            self.items.append(it)
            self.by_name.setdefault(it.name, it)
        for root in self.roots:
            self._measure(root)
        self._assign_memory()
        self.fields = Fields()
        for it in self.items:
            if it.level == 88:
                setattr(self.fields, it.py, _CondRef(it, self))
            else:
                setattr(self.fields, it.py, Field(it, self))
        self._initialize()

    # -- sizes -------------------------------------------------------------
    def _measure(self, it: Item) -> None:
        if not it.children:
            it.size = it.info.size if it.info else 0
            return
        running, end = 0, 0
        siblings: dict[str, Item] = {}
        for ch in it.children:
            self._measure(ch)
            if ch.redefines:
                target = siblings.get(ch.redefines)
                if target is None:
                    raise CobolRuntimeError(f"REDEFINES target {ch.redefines} not found")
                ch.redef_target = target
                ch.rel = target.rel
            else:
                ch.rel = running
                running += ch.total
            siblings[ch.name] = ch
            end = max(end, ch.rel + ch.total)
        it.size = end

    def _assign_memory(self) -> None:
        prev: Item | None = None
        for root in self.roots:
            if root.redefines and prev is not None:
                target = next((r for r in self.roots if r.name == root.redefines), prev)
                root.redef_target = target
                mem = target.mem
            elif root.fd:
                mem = self.fd_memories.setdefault(root.fd, Memory())
            else:
                mem = Memory()
            mem.ensure(root.total)
            root.mem = mem
            root.rel = 0
            self._place(root, 0, mem)
            prev = root

    def _place(self, it: Item, base: int, mem: Memory) -> None:
        it.mem = mem
        it.offset = base + it.rel
        for ch in it.children:
            self._place(ch, it.offset, mem)

    # -- initial values ------------------------------------------------------
    def _initialize(self) -> None:
        def walk(it: Item, skip: bool) -> None:
            skip = skip or bool(it.redefines)
            if it.children:
                for ch in it.children:
                    walk(ch, skip)
                if it.value is not None:
                    for f in self._occurrences(it):
                        f.move(it.value)
                return
            if it.value is not None:
                for f in self._occurrences(it):
                    f.move(it.value)
            elif not skip:
                for f in self._occurrences(it):
                    f.initialize()
        for root in self.roots:
            walk(root, False)

    def _occurrences(self, it: Item):
        chain = it.ancestors_with_occurs()
        base = getattr(self.fields, it.py)
        if not chain:
            yield base
            return

        def rec(ref: "Field", depth: int):
            if depth == len(chain):
                yield ref
                return
            for i in range(1, chain[depth].occurs + 1):
                yield from rec(ref[i], depth + 1)
        yield from rec(base, 0)


class Fields:
    """Attribute namespace: ``f.ws_total`` is the field for WS-TOTAL."""


class Field:
    """A (possibly subscripted) view onto one data item's bytes."""

    __slots__ = ("item", "layout", "adj", "pending")

    def __init__(self, item: Item, layout: Layout, adj: int = 0,
                 pending: tuple[Item, ...] | None = None) -> None:
        self.item = item
        self.layout = layout
        self.adj = adj
        self.pending = tuple(item.ancestors_with_occurs()) if pending is None else pending

    # -- addressing ----------------------------------------------------------
    def __getitem__(self, index) -> "Field":
        if not self.pending:
            raise CobolRuntimeError(f"{self.item.name} is not a table")
        owner = self.pending[0]
        i = int(index)
        if i < 1 or i > owner.occurs:
            raise CobolRuntimeError(
                f"subscript {i} out of range 1..{owner.occurs} for {owner.name}")
        return Field(self.item, self.layout, self.adj + (i - 1) * owner.size, self.pending[1:])

    @property
    def _off(self) -> int:
        if self.pending:
            raise CobolRuntimeError(f"{self.item.name} needs a subscript")
        return self.item.offset + self.adj

    def _bytes(self) -> bytes:
        o = self._off
        return bytes(self.item.mem.buf[o:o + self.item.size])

    def _put(self, data: bytes) -> None:
        o = self._off
        self.item.mem.buf[o:o + self.item.size] = data

    # -- classification --------------------------------------------------------
    @property
    def category(self) -> str:
        return "group" if self.item.is_group else self.item.info.category

    @property
    def size(self) -> int:
        return self.item.size

    # -- reading ---------------------------------------------------------------
    @property
    def text(self) -> str:
        return self._bytes().decode("latin-1")

    @property
    def num(self) -> Decimal:
        cat = self.category
        if cat == "numeric":
            return self._get_numeric()
        s = self.text
        if cat == "edited":
            digits = re.sub(r"\D", "", s)
            n = Decimal(int(digits or "0")).scaleb(-self.item.info.scale)
            neg = "-" in s or "CR" in s or "DB" in s
            return -n if neg else n
        n = _num_from_text(s)
        if n is not None:
            return n
        digits = re.sub(r"\D", "", s)
        return Decimal(int(digits or "0"))

    @property
    def value(self):
        return self._get_numeric() if self.category == "numeric" else self.text

    def _get_numeric(self) -> Decimal:
        info = self.item.info
        raw = self._bytes()
        if info.usage == "COMP-3":
            nibbles = []
            for b in raw:
                nibbles += [b >> 4, b & 0x0F]
            sign = nibbles.pop()
            n = 0
            for d in nibbles:
                n = n * 10 + (d if d <= 9 else 0)
            neg = sign in (0x0B, 0x0D)
        elif info.usage == "COMP":
            n = int.from_bytes(raw, "big", signed=info.signed)
            neg = n < 0
            n = abs(n)
        else:
            s = raw.decode("latin-1")
            neg = False
            if info.signed and s and s[-1] in "pqrstuvwxy":
                neg = True
                s = s[:-1] + chr(ord(s[-1]) - 0x40)
            n = int("".join(c if c.isdigit() else "0" for c in s) or "0")
        v = Decimal(n).scaleb(-info.scale)
        return -v if neg else v

    def display_text(self) -> str:
        """Text DISPLAY would print (GnuCOBOL 'pretty display' for numerics)."""
        if self.category != "numeric":
            return self.text
        info = self.item.info
        v = self._get_numeric()
        s = f"{abs(int(v.scaleb(info.scale))):0{info.digits}d}"
        if info.scale:
            s = s[:-info.scale] + "." + s[-info.scale:]
        if info.signed:
            s = ("-" if v < 0 else "+") + s
        return s

    def digit_string(self) -> str:
        """Unsigned digits of a numeric item without decimal point (MOVE to alphanumeric)."""
        info = self.item.info
        v = self._get_numeric()
        return f"{abs(int(v.scaleb(info.scale))):0{info.digits}d}"

    # -- writing ---------------------------------------------------------------
    def initialize(self) -> None:
        cat = self.category
        if cat == "numeric":
            self._store(Decimal(0), False, False)
        elif cat == "edited" or cat == "alnum":
            self._put(b" " * self.item.size)
        else:
            for f in self._leaves():
                f.initialize()

    def _leaves(self):
        def rec(it: Item, adj: int):
            for ch in it.children:
                if ch.redefines:
                    continue
                if ch.children:
                    for i in range(ch.occurs):
                        yield from rec(ch, adj + i * ch.size)
                else:
                    for i in range(ch.occurs):
                        yield Field(ch, self.layout, adj + i * ch.size, ())
        yield from rec(self.item, self.adj)

    def _put_text(self, s: str) -> None:
        size = self.item.size
        self._put(s.encode("latin-1", "replace")[:size].ljust(size, b" "))

    def _store(self, v: Decimal, rounded: bool, check: bool) -> bool:
        """Store a number; returns False on size error (when `check`)."""
        info = self.item.info
        v = v.quantize(Decimal(1).scaleb(-info.scale),
                       rounding=ROUND_HALF_UP if rounded else ROUND_DOWN)
        scaled = int(v.scaleb(info.scale))
        neg, n = scaled < 0, abs(scaled)
        limit = 10 ** info.digits
        if n >= limit:
            if check:
                return False
            n %= limit
        if neg and (n == 0 or (not info.signed and info.category == "numeric")):
            neg = False
        if info.category == "edited":
            self._put_text(_edit(info, n, neg))
        else:
            self._write_numeric(n, neg)
        return True

    def _write_numeric(self, n: int, neg: bool) -> None:
        info = self.item.info
        if info.usage == "COMP-3":
            digits = f"{n:0{info.size * 2 - 1}d}"
            sign = 0x0D if neg else (0x0C if info.signed else 0x0F)
            nib = [int(c) for c in digits] + [sign]
            self._put(bytes((nib[i] << 4) | nib[i + 1] for i in range(0, len(nib), 2)))
        elif info.usage == "COMP":
            value = -n if neg else n
            self._put(value.to_bytes(info.size, "big", signed=info.signed))
        else:
            s = f"{n:0{info.digits}d}"
            if neg and info.signed:
                s = s[:-1] + chr(ord(s[-1]) + 0x40)
            self._put(s.encode("latin-1"))

    def compute(self, fn, rounded: bool = False, on_size_error: bool = False) -> bool:
        """COMPUTE/ADD/...: evaluate `fn()`, store it; False means size error."""
        if self.category not in ("numeric", "edited"):
            raise CobolRuntimeError(f"{self.item.name} is not numeric")
        try:
            v = Decimal(fn())
        except (ZeroDivisionError, DivisionByZero, InvalidOperation):
            return False
        return self._store(v, rounded, on_size_error)

    def move(self, src) -> None:
        """MOVE src TO self (src: Field, str literal, number or figurative)."""
        cat = self.category
        if isinstance(src, _Figurative):
            if cat == "numeric":
                self._store(Decimal(0), False, False)
            elif cat == "edited" and src is ZERO:
                self._store(Decimal(0), False, False)
            else:
                self._put(src.char.encode("latin-1") * self.item.size)
            return
        if isinstance(src, Field):
            scat = src.category
            if cat in ("numeric", "edited"):
                self._store(src.num if scat != "group" else _digits_of(src.text), False, False)
            elif scat == "numeric":
                self._put_text(src.digit_string())
            else:
                self._put_text(src.text)
            return
        if isinstance(src, (int, Decimal)):
            d = Decimal(src)
            if cat in ("numeric", "edited"):
                self._store(d, False, False)
            else:
                self._put_text(format(abs(d.to_integral_value(ROUND_DOWN)), "d"))
            return
        s = str(src)
        if cat in ("numeric", "edited"):
            self._store(_digits_of(s), False, False)
        else:
            self._put_text(s)

    def is_numeric(self) -> bool:
        s = self.text
        if self.category == "numeric":
            return True
        return bool(s) and all(c.isdigit() for c in s)

    def is_alphabetic(self) -> bool:
        s = self.text
        return bool(s) and all(c.isalpha() or c == " " for c in s)


def _digits_of(s: str) -> Decimal:
    n = _num_from_text(s)
    if n is not None:
        return n
    digits = re.sub(r"\D", "", s)
    return Decimal(int(digits or "0"))


class _CondRef:
    """Level-88 condition name: ``f.end_of_file.test()`` / ``.set()``."""

    def __init__(self, item: Item, layout: Layout, adj: Field | None = None) -> None:
        self.item, self.layout, self._parent = item, layout, adj

    @property
    def _field(self) -> Field:
        return self._parent or getattr(self.layout.fields, self.item.parent.py)

    def __getitem__(self, index) -> "_CondRef":
        return _CondRef(self.item, self.layout, self._field[index])

    def test(self) -> bool:
        v = self._field.value
        for entry in self.item.values88:
            lo, hi = entry if isinstance(entry, tuple) else (entry, entry)
            if cmp(v, ">=", lo) and cmp(v, "<=", hi):
                return True
        return False

    def set(self) -> None:
        first = self.item.values88[0]
        self._field.move(first[0] if isinstance(first, tuple) else first)


# ------------------------------------------------------------------- file I/O
class CobolIO:
    """Run-time environment: where files live, and the console streams."""

    def __init__(self, root: str | Path = ".", stdout=None, stdin_text: str | None = None) -> None:
        self.root = Path(root)
        self.stdout = stdout if stdout is not None else sys.stdout
        self._stdin = iter((stdin_text or "").splitlines()) if stdin_text is not None else None

    def resolve(self, assign: str) -> Path:
        return self.root / assign

    def read_stdin_line(self) -> str:
        if self._stdin is not None:
            return next(self._stdin, "")
        return sys.stdin.readline().rstrip("\n")


class FileHandler:
    def __init__(self, program: "CobolProgram", spec: dict) -> None:
        self.program = program
        self.name: str = spec["name"]
        self.assign: str = spec["assign"]
        self.organization = spec.get("organization", "LINE SEQUENTIAL")
        self.status_name: str | None = spec.get("status")
        self.record_name: str | None = spec.get("record")
        self._fh = None
        self._mode: str | None = None
        self._lines: list[bytes] | None = None
        self._pos = 0

    # the record area is the shared FD memory
    def _record_item(self) -> Item:
        layout = self.program.layout
        if self.record_name:
            return layout.by_name[self.record_name]
        raise CobolRuntimeError(f"file {self.name} has no record description")

    def _set_status(self, code: str) -> None:
        if self.status_name:
            getattr(self.program.f, pyname(self.status_name)).move(code)

    @property
    def has_status(self) -> bool:
        return self.status_name is not None

    def open(self, mode: str) -> bool:
        path = self.program.io.resolve(self.assign)
        mode = mode.upper()
        if self._fh is not None or self._lines is not None:
            self._set_status("41")
            return False
        if mode == "INPUT":
            if not path.exists():
                self._set_status("35")
                if not self.has_status:
                    raise CobolRuntimeError(f"file not found: {self.assign}")
                return False
            data = path.read_bytes()
            self._lines = self._split(data)
            self._pos = 0
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = open(path, "ab" if mode == "EXTEND" else "wb")
        self._mode = mode
        self._set_status("00")
        return True

    def _split(self, data: bytes) -> list[bytes]:
        if self.organization.upper().startswith("LINE"):
            lines = data.split(b"\n")
            if lines and lines[-1] == b"":
                lines.pop()
            return [ln.rstrip(b"\r") for ln in lines]
        size = self._record_item().size
        return [data[i:i + size] for i in range(0, len(data), size)] if size else []

    def read(self, at_end_handled: bool = True) -> bool:
        if self._lines is None:
            self._set_status("47")
            raise CobolRuntimeError(f"READ on file {self.name} that is not open for input")
        if self._pos >= len(self._lines):
            self._set_status("10")
            if not at_end_handled and not self.has_status:
                raise CobolRuntimeError(f"end of file on {self.name} (status 10) with no AT END")
            return False
        line = self._lines[self._pos]
        self._pos += 1
        mem = self._record_item().mem
        size = len(mem.buf)
        mem.buf[:] = line[:size].ljust(size, b" ")
        self._set_status("00")
        return True

    def write(self, record: Field) -> bool:
        if self._fh is None:
            self._set_status("48")
            raise CobolRuntimeError(f"WRITE on file {self.name} that is not open for output")
        data = record._bytes()
        if self.organization.upper().startswith("LINE"):
            data = data.rstrip(b" ") + b"\n"
        self._fh.write(data)
        self._set_status("00")
        return True

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
        self._fh = None
        self._lines = None
        self._mode = None
        self._set_status("00")

    @property
    def is_open(self) -> bool:
        return self._fh is not None or self._lines is not None


class _FileNamespace:
    pass


# --------------------------------------------------------------------- program
class CobolProgram:
    """Base class of every migrated program."""

    PROGRAM_ID = ""
    DATA: list[dict] = []
    FILES: list[dict] = []

    def __init__(self, io: CobolIO | None = None) -> None:
        self.io = io or CobolIO()
        self.layout = Layout(self.DATA)
        self.f = self.layout.fields
        self.files = _FileNamespace()
        self._handlers: list[FileHandler] = []
        for spec in self.FILES:
            handler = FileHandler(self, spec)
            self._handlers.append(handler)
            setattr(self.files, pyname(spec["name"]), handler)

    # -- statements ---------------------------------------------------------------
    def display(self, *parts) -> None:
        out = []
        for p in parts:
            if isinstance(p, Field):
                out.append(p.display_text())
            elif isinstance(p, _Figurative):
                out.append(p.char)
            elif isinstance(p, (int, Decimal)):
                out.append(format(Decimal(p), "f"))
            else:
                out.append(str(p))
        self.io.stdout.write("".join(out) + "\n")

    def accept(self, target: Field, source: str | None = None) -> None:
        if source is None:
            target.move(self.io.read_stdin_line())
            return
        now = datetime.now()
        text = {
            "DATE": now.strftime("%y%m%d"), "DATE YYYYMMDD": now.strftime("%Y%m%d"),
            "DAY": now.strftime("%y%j"), "DAY YYYYDDD": now.strftime("%Y%j"),
            "TIME": now.strftime("%H%M%S") + f"{now.microsecond // 10000:02d}",
        }.get(source.upper())
        if text is None:
            raise CobolRuntimeError(f"unsupported ACCEPT source {source!r}")
        target.move(text)

    def stop_run(self) -> None:
        raise StopRun()

    # -- run unit ------------------------------------------------------------------
    def main(self) -> None:  # overridden by the generated program
        raise NotImplementedError

    def run(self) -> int:
        try:
            self.main()
        except StopRun:
            pass
        finally:
            for handler in self._handlers:
                if handler.is_open:
                    handler.close()
        return 0

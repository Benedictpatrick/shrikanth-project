"""Deterministic COBOL -> Python translation of the supported subset.

The generated module is a subclass of ``cobol_runtime.CobolProgram``: control
flow becomes plain Python (one method per paragraph) and every data access
goes through the runtime so COBOL numeric/alphanumeric semantics are kept.

Constructs outside the subset are emitted as ``raise NotImplementedError``
stubs and reported in ``Translation.issues`` so the caller can route the unit
to the LLM translation / repair path instead of pretending it was converted.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .parser import DataItem, ProgramModel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "runtime"))
from cobol_runtime import pyname  # noqa: E402


class Untranslatable(Exception):
    pass


@dataclass
class Translation:
    module_name: str
    class_name: str
    code: str
    issues: list[dict] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.issues


def module_name_for(program_id: str, fallback: str = "program") -> str:
    return re.sub(r"[^0-9a-z]+", "_", (program_id or fallback).lower()).strip("_") or fallback


def _class_name(program_id: str) -> str:
    parts = re.split(r"[^0-9A-Za-z]+", program_id or "Program")
    name = "".join(p.capitalize() for p in parts if p) or "Program"
    return name if not name[0].isdigit() else "P" + name


class Generator:
    def __init__(self, model: ProgramModel) -> None:
        self.m = model
        self.issues: list[dict] = []
        self.by_name: dict[str, list[DataItem]] = {}
        for d in model.data:
            self.by_name.setdefault(d.name, []).append(d)
        self.paragraph_names = [p.name for p in model.paragraphs]
        self.record_file: dict[str, str] = {}
        for f in model.files:
            for r in f.records:
                self.record_file[r] = f.name
        self.file_names = {f.name for f in model.files}
        self._tmp = 0

    # -- helpers ------------------------------------------------------------------
    def issue(self, line: int, text: str) -> str:
        self.issues.append({"line": line, "message": text})
        return text

    def resolve(self, ref: dict) -> DataItem:
        cands = self.by_name.get(ref["name"])
        if not cands:
            raise Untranslatable(f"undefined data name {ref['name']}")
        if len(cands) > 1:
            chain = ref.get("qual") or []
            by_parent = [c for c in cands if self._has_ancestor(c, chain)] if chain else []
            if len(by_parent) == 1:
                return by_parent[0]
            raise Untranslatable(f"ambiguous reference to {ref['name']}")
        return cands[0]

    def _has_ancestor(self, item: DataItem, chain: list[str]) -> bool:
        names = []
        it = item
        while it.parent:
            names.append(it.parent)
            it = next((d for d in self.m.data if d.name == it.parent), None)
            if it is None:
                break
        return all(c in names for c in chain)

    def field(self, ref: dict) -> str:
        if ref.get("refmod"):
            raise Untranslatable(f"reference modification on {ref['name']}")
        item = self.resolve(ref)
        out = f"f.{item.py}"
        for s in ref.get("subs", []):
            out += f"[{self.num(s)}]"
        return out

    # -- expressions -----------------------------------------------------------------
    def num(self, e: dict) -> str:
        t = e["t"]
        if t == "num":
            return f"Decimal('{e['v']}')"
        if t == "ref":
            return f"{self.field(e)}.num"
        if t == "fig":
            if e["v"] == "ZERO":
                return "Decimal(0)"
            raise Untranslatable(f"figurative {e['v']} in an arithmetic expression")
        if t == "neg":
            return f"(-{self.num(e['e'])})"
        if t == "bin":
            return f"({self.num(e['l'])} {e['op']} {self.num(e['r'])})"
        if t == "str":
            return f"Decimal({e['v']!r})"
        raise Untranslatable(f"expression {t}")

    def value(self, e: dict) -> str:
        t = e["t"]
        if t == "ref":
            return f"{self.field(e)}.value"
        if t == "str":
            return repr(e["v"])
        if t == "fig":
            return e["v"]
        return self.num(e)

    def source(self, e: dict) -> str:
        """Operand for MOVE / DISPLAY: keeps Field objects so storage semantics apply."""
        t = e["t"]
        if t == "ref":
            return self.field(e)
        if t == "str":
            return repr(e["v"])
        if t == "fig":
            return e["v"]
        if t == "num":
            return f"Decimal('{e['v']}')"
        return self.num(e)

    def cond(self, c: dict) -> str:
        t = c["t"]
        if t == "cmp":
            return f"cmp({self.value(c['l'])}, '{c['op']}', {self.value(c['r'])})"
        if t == "and":
            return f"({self.cond(c['l'])} and {self.cond(c['r'])})"
        if t == "or":
            return f"({self.cond(c['l'])} or {self.cond(c['r'])})"
        if t == "not":
            return f"(not {self.cond(c['e'])})"
        if t == "cond88":
            item = self.resolve(c["ref"])
            if item.level != 88:
                raise Untranslatable(f"{item.name} used as a condition but is not a condition name")
            return f"{self.field(c['ref'])}.test()"
        if t == "class":
            ref = self.field(c["ref"])
            fn = {"NUMERIC": "is_numeric", "ALPHABETIC": "is_alphabetic"}.get(c["cls"])
            if fn is None:
                raise Untranslatable(f"class condition {c['cls']}")
            return f"({'not ' if c['neg'] else ''}{ref}.{fn}())"
        if t == "sign":
            ref = self.field(c["ref"])
            op = ">" if c["sign"] == "POSITIVE" else "<"
            expr = f"{ref}.num {op} 0"
            return f"(not ({expr}))" if c["neg"] else f"({expr})"
        raise Untranslatable("abbreviated or unsupported condition")

    # -- statements -------------------------------------------------------------------------
    def block(self, stmts: list[dict], ind: int) -> list[str]:
        out: list[str] = []
        for s in stmts or []:
            out.extend(self.stmt(s, ind))
        return out or ["    " * ind + "pass"]

    def stmt(self, s: dict, ind: int) -> list[str]:
        pad = "    " * ind
        try:
            fn = getattr(self, "s_" + s["kind"].lower())
            return fn(s, ind)
        except Untranslatable as exc:
            line = s.get("line", 0)
            self.issue(line, str(exc))
            return [f"{pad}raise NotImplementedError({('line %d: %s' % (line, exc))!r})"]

    def s_nop(self, s, ind):
        return ["    " * ind + "pass"]

    def s_unsupported(self, s, ind):
        pad = "    " * ind
        line = s.get("line", 0)
        msg = f"{s.get('verb')} statement is outside the rule-based subset: {s.get('text', '')[:120]}"
        self.issue(line, msg)
        return [f"{pad}# COBOL: {s.get('text', '')[:100]}",
                f"{pad}raise NotImplementedError({('line %d: %s' % (line, msg))!r})"]

    def s_move(self, s, ind):
        pad = "    " * ind
        if not s["dst"]:
            raise Untranslatable("MOVE without receiving item")
        src = self.source(s["src"])
        return [f"{pad}{self.field(d)}.move({src})" for d in s["dst"]]

    def s_arith(self, s, ind):
        pad = "    " * ind
        if not s["assigns"]:
            raise Untranslatable("arithmetic statement form not understood")
        handled = s["on_size_error"] is not None or s["not_on_size_error"] is not None
        out: list[str] = []
        if handled:
            out.append(f"{pad}_size_error = False")
        for a in s["assigns"]:
            target = self.field(a["target"])
            call = f"{target}.compute(lambda: {self.num(a['expr'])}, rounded={a['rounded']}"
            if handled:
                out.append(f"{pad}if not {call}, on_size_error=True):")
                out.append(f"{pad}    _size_error = True")
            else:
                out.append(f"{pad}{call})")
        if handled:
            out.append(f"{pad}if _size_error:")
            out.extend(self.block(s["on_size_error"], ind + 1))
            if s["not_on_size_error"]:
                out.append(f"{pad}else:")
                out.extend(self.block(s["not_on_size_error"], ind + 1))
        return out

    def s_if(self, s, ind):
        pad = "    " * ind
        out = [f"{pad}if {self.cond(s['cond'])}:"]
        out += self.block(s["then"], ind + 1)
        if s["else"]:
            out.append(f"{pad}else:")
            out += self.block(s["else"], ind + 1)
        return out

    def s_evaluate(self, s, ind):
        pad = "    " * ind
        subject = s["subject"]
        out: list[str] = []
        ev = None
        if subject["t"] not in ("true", "false"):
            self._tmp += 1
            ev = f"_ev{self._tmp}"
            out.append(f"{pad}{ev} = {self.value(subject)}")
        first = True
        for w in s["whens"]:
            parts = []
            for c in w["conds"]:
                if subject["t"] == "true":
                    parts.append(self.cond(c))
                elif subject["t"] == "false":
                    parts.append(f"(not {self.cond(c)})")
                elif c["t"] == "range":
                    parts.append(f"(cmp({ev}, '>=', {self.value(c['lo'])}) and cmp({ev}, '<=', {self.value(c['hi'])}))")
                elif c["t"] == "any":
                    parts.append("True")
                else:
                    parts.append(f"cmp({ev}, '=', {self.value(c)})")
            test = " or ".join(parts) or "False"
            out.append(f"{pad}{'if' if first else 'elif'} {test}:")
            out += self.block(w["body"], ind + 1)
            first = False
        if s["other"] is not None:
            if first:
                out += self.block(s["other"], ind)
            else:
                out.append(f"{pad}else:")
                out += self.block(s["other"], ind + 1)
        elif first:
            out.append(f"{pad}pass")
        return out

    def _call(self, target: str, thru: str | None, pad: str) -> list[str]:
        names = self.paragraph_names
        if thru:
            if target not in names or thru not in names:
                raise Untranslatable(f"PERFORM {target} THRU {thru}: paragraph not found")
            a, b = names.index(target), names.index(thru)
            if a > b:
                raise Untranslatable(f"PERFORM {target} THRU {thru}: range is reversed")
            return [f"{pad}self.p_{pyname(n)}()" for n in names[a:b + 1]]
        if target in names:
            return [f"{pad}self.p_{pyname(target)}()"]
        if target in self.m.sections:
            return [f"{pad}self.p_{pyname(n)}()" for n in self.m.sections[target]] or [f"{pad}pass"]
        raise Untranslatable(f"PERFORM of undefined paragraph {target}")

    def s_perform(self, s, ind):
        pad = "    " * ind
        out: list[str] = []
        inner = ind
        loop_header: str | None = None
        footer: list[str] = []
        pre: list[str] = []
        if s.get("varying"):
            v = s["varying"]
            var = self.field(v["var"])
            pre.append(f"{pad}{var}.move({self.source(v['from'])})")
            step = f"{pad}    {var}.compute(lambda: {var}.num + {self.num(v['by'])})"
            if s["test_after"]:
                loop_header = "while True:"
                footer = [f"{pad}    if {self.cond(s['until'])}:", f"{pad}        break", step]
            else:
                loop_header = f"while not {self.cond(s['until'])}:"
                footer = [step]
        elif s.get("until") is not None:
            if s["test_after"]:
                loop_header = "while True:"
                footer = [f"{pad}    if {self.cond(s['until'])}:", f"{pad}        break"]
            else:
                loop_header = f"while not {self.cond(s['until'])}:"
        elif s.get("times") is not None:
            loop_header = f"for _ in range(int({self.num(s['times'])})):"
        elif s.get("forever"):
            loop_header = "while True:"
        out.extend(pre)
        if loop_header:
            out.append(f"{pad}{loop_header}")
            inner = ind + 1
        ipad = "    " * inner
        if s["target"]:
            body = self._call(s["target"], s.get("thru"), ipad)
        else:
            body = self.block(s["body"], inner)
        out.extend(body)
        out.extend(footer)
        return out

    def s_display(self, s, ind):
        pad = "    " * ind
        if not s["items"]:
            return [f"{pad}self.display()"]
        return [f"{pad}self.display({', '.join(self.source(i) for i in s['items'])})"]

    def s_accept(self, s, ind):
        pad = "    " * ind
        target = self.field(s["target"])
        if s.get("source"):
            return [f"{pad}self.accept({target}, {s['source']!r})"]
        return [f"{pad}self.accept({target})"]

    def _file(self, name: str) -> str:
        if name not in self.file_names:
            raise Untranslatable(f"undefined file {name}")
        return f"self.files.{pyname(name)}"

    def s_open(self, s, ind):
        pad = "    " * ind
        return [f"{pad}{self._file(e['file'])}.open({e['mode']!r})" for e in s["entries"]]

    def s_close(self, s, ind):
        pad = "    " * ind
        return [f"{pad}{self._file(n)}.close()" for n in s["files"]]

    def s_read(self, s, ind):
        pad = "    " * ind
        fh = self._file(s["file"])
        fdef = next(f for f in self.m.files if f.name == s["file"])
        handled = s["at_end"] is not None
        out: list[str] = []
        into = []
        if s["into"] is not None:
            if not fdef.records:
                raise Untranslatable(f"READ INTO but {s['file']} has no record")
            rec = self.by_name[fdef.records[0]][0]
            into = [f"{self.field(s['into'])}.move(f.{rec.py})"]
        if s["at_end"] is None and s["not_at_end"] is None:
            if not into:
                return [f"{pad}{fh}.read(at_end_handled=False)"]
            out.append(f"{pad}if {fh}.read(at_end_handled=False):")
            out += [f"{pad}    {l}" for l in into]
            return out
        out.append(f"{pad}if {fh}.read(at_end_handled={handled}):")
        body = [f"{'    ' * (ind + 1)}{l}" for l in into]
        body += self.block(s["not_at_end"], ind + 1) if s["not_at_end"] else []
        out += body or [f"{pad}    pass"]
        if s["at_end"]:
            out.append(f"{pad}else:")
            out += self.block(s["at_end"], ind + 1)
        return out

    def s_write(self, s, ind):
        pad = "    " * ind
        rec = s["record"]
        if rec not in self.record_file:
            raise Untranslatable(f"WRITE of {rec}: record not found in any FD")
        item = self.by_name[rec][0]
        out = []
        if s["from"] is not None:
            out.append(f"{pad}f.{item.py}.move({self.field(s['from'])})")
        out.append(f"{pad}{self._file(self.record_file[rec])}.write(f.{item.py})")
        return out

    def s_stop_run(self, s, ind):
        return ["    " * ind + "self.stop_run()"]

    def s_initialize(self, s, ind):
        pad = "    " * ind
        return [f"{pad}{self.field(t)}.initialize()" for t in s["targets"]]

    def s_set_cond(self, s, ind):
        pad = "    " * ind
        if not s["value"]:
            raise Untranslatable("SET condition TO FALSE")
        out = []
        for t in s["targets"]:
            item = self.resolve(t)
            if item.level != 88:
                raise Untranslatable(f"SET {item.name} TO TRUE: not a condition name")
            out.append(f"{pad}{self.field(t)}.set()")
        return out

    # -- module --------------------------------------------------------------------------------
    def data_literal(self, v) -> str:
        if v is None:
            return "None"
        if isinstance(v, list):  # THRU range
            return f"({self.data_literal(v[0])}, {self.data_literal(v[1])})"
        if v["t"] == "str":
            return repr(v["v"])
        if v["t"] == "num":
            return f"Decimal('{v['v']}')"
        return v["v"]

    def generate(self) -> Translation:
        m = self.m
        src_name = Path(m.path).name
        cls = _class_name(m.program_id or Path(m.path).stem)
        lines: list[str] = []
        a = lines.append
        a(f'"""{m.program_id or src_name} - migrated from {src_name} by Legacy Code Automation.')
        a("")
        a("Rule-based translation: control flow is plain Python, all COBOL data semantics")
        a('come from ``cobol_runtime``. Paragraph methods keep their original names."""')
        a("from cobol_runtime import *")
        a("")
        a("")
        a(f"class {cls}(CobolProgram):")
        a(f"    PROGRAM_ID = {m.program_id!r}")
        a("")
        a("    DATA = [")
        for d in m.data:
            parts = [f"level={d.level}", f"name={d.name!r}", f"py={d.py!r}"]
            if d.pic is not None:
                parts.append(f"pic={d.pic!r}")
                if d.usage and d.usage != "DISPLAY":
                    parts.append(f"usage={d.usage!r}")
            if d.occurs:
                parts.append(f"occurs={d.occurs}")
            if d.redefines:
                parts.append(f"redefines={d.redefines!r}")
            if d.fd:
                parts.append(f"fd={d.fd!r}")
            if d.level == 88:
                parts.append("values=[" + ", ".join(self.data_literal(v) for v in d.values88) + "]")
            elif d.value is not None:
                parts.append(f"value={self.data_literal(d.value)}")
            a(f"        dict({', '.join(parts)}),")
        a("    ]")
        a("")
        a("    FILES = [")
        for fd in m.files:
            parts = [f"name={fd.name!r}", f"assign={fd.assign!r}", f"organization={fd.organization!r}"]
            if fd.status:
                parts.append(f"status={fd.status!r}")
            if fd.records:
                parts.append(f"record={fd.records[0]!r}")
            a(f"        dict({', '.join(parts)}),")
        a("    ]")
        a("")
        a("    def main(self):")
        if m.paragraphs:
            for p in m.paragraphs:
                a(f"        self.p_{pyname(p.name)}()")
        else:
            a("        pass")
        for p in m.paragraphs:
            a("")
            first = m.origin(p.line + 1)[1]
            last = m.origin(p.end_line + 1)[1]
            a(f"    def p_{pyname(p.name)}(self):")
            a(f"        # COBOL paragraph {p.name} ({src_name} lines {first}-{last})")
            a("        f = self.f")
            body = self.block(p.stmts, 2)
            lines.extend(body)
        a("")
        a("")
        a("def run(io=None):")
        a(f"    return {cls}(io).run()")
        a("")
        a("")
        a('if __name__ == "__main__":')
        a("    raise SystemExit(run())")
        a("")
        for u in m.unsupported:
            if not any(i["message"].startswith(u.construct) or u.construct in i["message"] for i in self.issues):
                self.issues.append({"line": u.line, "message": f"{u.construct}: {u.text[:100]}"})
        return Translation(module_name_for(m.program_id, Path(m.path).stem), cls, "\n".join(lines),
                           self.issues)


def translate(model: ProgramModel) -> Translation:
    return Generator(model).generate()

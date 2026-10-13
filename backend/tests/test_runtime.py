"""Unit tests for the COBOL data/I-O semantics in cobol_runtime."""
from decimal import Decimal as D

import pytest

from cobol_runtime import (CobolIO, CobolProgram, CobolRuntimeError, Layout, ZERO, SPACE, cmp, parse_pic)
from cobol_runtime import _edit


def field(pic, usage="DISPLAY", value=None, **extra):
    layout = Layout([dict(level=1, name="X", pic=pic, usage=usage, value=value, **extra)])
    return layout.fields.x


# ------------------------------------------------------------------ pictures
@pytest.mark.parametrize("pic,usage,size,digits,scale,signed", [
    ("9(5)", "DISPLAY", 5, 5, 0, False),
    ("S9(7)V99", "DISPLAY", 9, 9, 2, True),
    ("S9(9)V99", "COMP-3", 6, 11, 2, True),
    ("S9(4)", "COMP", 2, 4, 0, True),
    ("9(9)", "COMP", 4, 9, 0, False),
    ("S9(18)", "COMP", 8, 18, 0, True),
])
def test_numeric_pictures(pic, usage, size, digits, scale, signed):
    info = parse_pic(pic, usage)
    assert (info.category, info.size, info.digits, info.scale, info.signed) == ("numeric", size, digits, scale, signed)


@pytest.mark.parametrize("pic,value,expected", [
    ("ZZZ,ZZ9.99-", D("3.13"), "      3.13 "),
    ("ZZZ,ZZ9.99-", D("-5"), "      5.00-"),
    ("Z,ZZZ,ZZ9.99-", D("1503.13"), "    1,503.13 "),
    ("Z,ZZZ,ZZ9.99", D("0"), "        0.00"),
    ("ZZ,ZZ9.99", D("1234.5"), " 1,234.50"),
    ("$$,$$9.99", D("5"), "    $5.00"),
    ("$$,$$9.99", D("1234.56"), "$1,234.56"),
    ("---9.99", D("-2.5"), "  -2.50"),
    ("ZZ9.99CR", D("-12.5"), " 12.50CR"),
    ("ZZ9.99CR", D("12.5"), " 12.50  "),
    ("**9.99", D("1.5"), "**1.50"),
    ("99/99/99", D("010124"), "01/01/24"),
    ("ZZZ9", D("0"), "   0"),
])
def test_edited_pictures(pic, value, expected):
    f = field(pic)
    f.move(value)
    assert f.text == expected


def test_all_zero_suppress_picture_is_blank_for_zero():
    f = field("ZZZ.ZZ")
    f.move(D("0"))
    assert f.text == " " * 6


# ------------------------------------------------------------------- numerics
def test_move_truncates_high_and_low_order_digits():
    f = field("9(3)V9")
    f.move(D("12345.678"))
    assert f.value == D("345.6")


def test_unsigned_receives_absolute_value():
    f = field("9(3)")
    f.move(D("-42"))
    assert f.value == D("42")


def test_signed_display_overpunch_roundtrip():
    f = field("S9(5)V99")
    f.move(D("-250.00"))
    assert f.value == D("-250.00")
    assert f.text.endswith("p")  # GnuCOBOL ASCII overpunch for a trailing negative zero digit


def test_comp3_packed_bytes_and_value():
    f = field("S9(5)V99", "COMP-3")
    f.move(D("-123.45"))
    assert f._bytes() == bytes([0x00, 0x12, 0x34, 0x5D])
    assert f.value == D("-123.45")
    f.move(D("99999.99"))
    assert f._bytes()[-1] & 0x0F == 0x0C


def test_comp_binary_is_big_endian_signed():
    f = field("S9(4)", "COMP")
    f.move(D("-2"))
    assert f._bytes() == b"\xff\xfe"
    assert f.value == D("-2")


def test_compute_truncates_unless_rounded():
    f = field("9V99")
    assert f.compute(lambda: D("2.678"))
    assert f.value == D("2.67")
    assert f.compute(lambda: D("2.675"), rounded=True)
    assert f.value == D("2.68")
    assert f.compute(lambda: D("0.005"), rounded=True)
    assert f.value == D("0.01")


def test_size_error_leaves_field_unchanged_when_handled():
    f = field("9(2)", value=D("7"))
    assert f.compute(lambda: D("150"), on_size_error=True) is False
    assert f.value == D("7")
    # without ON SIZE ERROR the high-order digits are truncated
    assert f.compute(lambda: D("150")) is True
    assert f.value == D("50")


def test_division_by_zero_is_a_size_error():
    f = field("9(3)", value=D("5"))
    assert f.compute(lambda: D("1") / D("0"), on_size_error=True) is False
    assert f.value == D("5")


# ---------------------------------------------------------------- alphanumeric
def test_alnum_move_pads_and_truncates():
    f = field("X(5)")
    f.move("AB")
    assert f.text == "AB   "
    f.move("ABCDEFG")
    assert f.text == "ABCDE"
    f.move(SPACE)
    assert f.text == "     "
    f.move(ZERO)
    assert f.text == "00000"


def test_numeric_to_alnum_moves_digits():
    n = field("9(3)V9", value=D("12.5"))
    layout = Layout([dict(level=1, name="A", pic="X(6)")])
    a = layout.fields.a
    a.move(n)
    assert a.text == "0125  "


# ----------------------------------------------------------------- comparisons
@pytest.mark.parametrize("a,op,b,expected", [
    (D("1"), "<", D("2"), True),
    ("ABC", "=", "ABC  ", True),
    ("A", "<", "B", True),
    (D("5"), "=", ZERO, False),
    (D("0"), "=", ZERO, True),
    ("   ", "=", SPACE, True),
    ("12", "=", D("12"), True),
    (D("2.50"), ">=", D("2.5"), True),
])
def test_cmp(a, op, b, expected):
    assert cmp(a, op, b) is expected


# ------------------------------------------------------- groups, tables, 88
def test_group_move_and_redefines_share_storage():
    layout = Layout([
        dict(level=1, name="REC"),
        dict(level=5, name="A", pic="X(3)", value="ABC"),
        dict(level=5, name="N", pic="9(2)", value=D("42")),
        dict(level=1, name="ALT", redefines="REC"),
        dict(level=5, name="ALL-TEXT", pic="X(5)"),
    ])
    f = layout.fields
    assert f.all_text.text == "ABC42"
    f.all_text.move("XYZ07")
    assert f.a.text == "XYZ" and f.n.value == D("7")


def test_occurs_table_subscripts_and_bounds():
    layout = Layout([
        dict(level=1, name="T"),
        dict(level=5, name="ROW", occurs=3),
        dict(level=10, name="CODE", pic="9"),
        dict(level=10, name="NAME", pic="X(2)"),
    ])
    f = layout.fields
    for i in (1, 2, 3):
        f.code[i].move(D(i))
        f.name[i].move("N" + str(i))
    assert f.t.text == "1N12N23N3"
    assert f.name[2].text == "N2"
    with pytest.raises(CobolRuntimeError):
        f.code[4]


def test_level_88_conditions():
    layout = Layout([
        dict(level=1, name="FLAG", pic="X", value="N"),
        dict(level=88, name="IS-YES", values=["Y", "y"]),
        dict(level=88, name="IS-NO", values=["N"]),
    ])
    f = layout.fields
    assert f.is_no.test() and not f.is_yes.test()
    f.is_yes.set()
    assert f.flag.text == "Y" and f.is_yes.test()


# ----------------------------------------------------------------------- files
def test_line_sequential_read_write_and_status(tmp_path):
    (tmp_path / "IN.DAT").write_text("AB12\nCD34\n")

    class Prog(CobolProgram):
        DATA = [
            dict(level=1, name="IN-REC", fd="IN"), dict(level=5, name="K", pic="X(2)", fd="IN"),
            dict(level=5, name="N", pic="9(2)", fd="IN"),
            dict(level=1, name="OUT-REC", fd="OUT", pic="X(6)"),
            dict(level=1, name="ST", pic="XX"),
        ]
        FILES = [dict(name="IN", assign="IN.DAT", status="ST", record="IN-REC"),
                 dict(name="OUT", assign="OUT.DAT", record="OUT-REC")]

        def main(self):
            f = self.f
            self.files.in_.open("INPUT")
            self.files.out.open("OUTPUT")
            while self.files.in_.read(at_end_handled=True):
                f.out_rec.move(f.k.text + "-" + f.n.text)
                self.files.out.write(f.out_rec)
            assert f.st.text == "10"
            self.files.in_.close()
            self.files.out.close()

    Prog(CobolIO(root=tmp_path)).run()
    assert (tmp_path / "OUT.DAT").read_text() == "AB-12\nCD-34\n"


def test_missing_input_file_sets_status_35(tmp_path):
    class Prog(CobolProgram):
        DATA = [dict(level=1, name="R", fd="F", pic="X(2)"), dict(level=1, name="ST", pic="XX")]
        FILES = [dict(name="F", assign="NOPE.DAT", status="ST", record="R")]

        def main(self):
            assert self.files.f.open("INPUT") is False
            assert self.f.st.text == "35"

    Prog(CobolIO(root=tmp_path)).run()

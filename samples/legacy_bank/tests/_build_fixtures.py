"""Builds the golden test fixtures for the sample legacy repo.

The expected outputs are computed here with plain Decimal arithmetic and
f-string formatting, independently of the COBOL runtime that the migrated
programs use. They stand in for a real COBOL compiler as the behavioural
oracle (``cobc`` is used instead when it is installed).
"""
import json
from decimal import Decimal as D, ROUND_HALF_UP
from pathlib import Path

HERE = Path(__file__).parent


def q2(x):
    return x.quantize(D("0.01"), rounding=ROUND_HALF_UP)


def edited(value, int_slots, trailing_sign=False):
    """Zero-suppressed, comma-grouped number in a field of `int_slots` digits."""
    neg = value < 0
    body = f"{abs(value):,.2f}"
    width = int_slots + (int_slots - 1) // 3 + 3  # digits, commas, '.dd'
    out = body.rjust(width)
    if trailing_sign:
        out += "-" if neg else " "
    return out


def overpunch(value_cents):
    """9 digits, SIGN TRAILING, GnuCOBOL ASCII overpunch (p-y for negative)."""
    neg = value_cents < 0
    digits = f"{abs(value_cents):09d}"
    if neg:
        digits = digits[:-1] + chr(0x70 + int(digits[-1]))
    return digits


# ---------------------------------------------------------------- INTCALC
accounts = [
    (100001, "ALICE JONES", "S", D("1500.00")),
    (100002, "BOB SMITH", "C", D("800.00")),
    (100003, "CAROL WHITE", "C", D("2500.50")),
    (100004, "DAVE BROWN", "P", D("15000.00")),
    (100005, "EVE BLACK", "P", D("5000.00")),
    (100006, "FRANK GREEN", "S", D("-250.00")),
    (100007, "GRACE HALL", "X", D("100.00")),
    (100008, "HENRY KING", "S", D("0.01")),
]


def intcalc_case(rows):
    lines, posted, out, skipped, total = [], 0, [], 0, D("0")
    for acct_id, name, kind, bal in rows:
        lines.append(f"{acct_id:06d}{name:<12}{kind}{overpunch(int(bal * 100))}")
        if bal < 0:
            interest = D("-5.00")
        elif kind == "S":
            interest = q2(bal * D("0.0250") / 12)
        elif kind == "C":
            interest = q2(bal * (D("0.0050") if bal >= 1000 else D("0")) / 12)
        elif kind == "P":
            interest = q2(bal * (D("0.0400") if bal > 10000 else D("0.0300")) / 12)
        else:
            out.append(f"UNKNOWN ACCOUNT TYPE FOR {acct_id:06d}")
            skipped += 1
            continue
        new_bal = bal + interest
        total += interest
        posted += 1
        rec = (f"{acct_id:06d} {name:<12} {edited(interest, 6, True)} "
               f"{edited(new_bal, 7, True)}")
        out.append(("FILE", rec.rstrip()))
    files = "\n".join(r[1] for r in out if isinstance(r, tuple)) + "\n"
    stdout = [r for r in out if not isinstance(r, tuple)]
    stdout += [
        f"ACCOUNTS READ     : {len(rows):05d}",
        f"INTEREST POSTED   : {posted:05d}",
        f"ACCOUNTS SKIPPED  : {skipped:05d}",
        f"TOTAL INTEREST    : {edited(total, 7, True)}",
    ]
    return {
        "inputs": {"ACCOUNTS.DAT": "\n".join(lines) + ("\n" if lines else "")},
        "expected": {"stdout": "\n".join(stdout) + "\n", "files": {"POSTED.DAT": files}},
    }


intcalc = [
    {"name": "mixed_accounts", "description": "Savings, checking, premium, overdrawn and unknown account types",
     **intcalc_case(accounts)},
    {"name": "empty_file", "description": "No accounts: only the summary is printed",
     **intcalc_case([])},
    {"name": "checking_threshold", "description": "Checking earns interest only from a balance of 1000.00",
     **intcalc_case([(200001, "AT LIMIT", "C", D("1000.00")), (200002, "JUST UNDER", "C", D("999.99"))])},
    {"name": "premium_tiers", "description": "Premium rate steps up above 10000.00",
     **intcalc_case([(300001, "AT TIER", "P", D("10000.00")), (300002, "OVER TIER", "P", D("10000.01"))])},
]
intcalc[1]["expected"]["files"]["POSTED.DAT"] = ""

# ---------------------------------------------------------------- PAYROLL
DEPTS = {10: ("ENG", D("5.00")), 20: ("OPS", D("2.50")), 30: ("SAL", D("7.50"))}
employees = [
    (101, "ADA LOVE", 10, D("40.0"), D("50.00")),
    (102, "BEN OTT", 20, D("45.5"), D("20.00")),
    (103, "CY DREW", 30, D("20.0"), D("15.50")),
    (104, "DI EVANS", 99, D("38.5"), D("31.25")),
    (105, "ED FOX", 10, D("0.0"), D("25.00")),
    (106, "FAY GALE", 10, D("60.0"), D("100.00")),
]


def payroll_case(rows):
    lines, slips, out = [], [], []
    paid = rejected = ot_count = 0
    total = D("0")
    for emp_id, name, dept, hours, rate in rows:
        lines.append(f"{emp_id:05d}{name:<10}{dept:02d}{int(hours * 10):04d}{int(rate * 100):05d}")
        if hours == 0 or rate == 0:
            out.append(f"REJECTED EMPLOYEE {emp_id:05d}")
            rejected += 1
            continue
        reg = min(hours, D("40"))
        ot = max(hours - 40, D("0"))
        gross = q2(reg * rate + ot * rate * D("1.5"))
        label, pct = DEPTS.get(dept, ("???", D("0")))
        bonus = q2(gross * pct / 100) if dept in DEPTS else D("0.00")
        taxable = gross + bonus
        tax_rate = D("0.10") if taxable <= 500 else D("0.20") if taxable <= 1500 else D("0.30")
        tax = q2(taxable * tax_rate)
        net = gross + bonus - tax
        paid += 1
        ot_count += 1 if ot > 0 else 0
        total += net
        slips.append(f"{emp_id:05d} {name:<10} {label} {edited(gross, 5)} "
                     f"{edited(bonus, 5)} {edited(tax, 5)} {edited(net, 5)}".rstrip())
    out += [
        f"EMPLOYEES PAID    : {paid:04d}",
        f"EMPLOYEES REJECTED: {rejected:04d}",
        f"WITH OVERTIME     : {ot_count:04d}",
        f"TOTAL NET PAY     : {edited(total, 7)}",
    ]
    return {
        "inputs": {"EMPLOYEES.DAT": "\n".join(lines) + ("\n" if lines else "")},
        "expected": {"stdout": "\n".join(out) + "\n",
                     "files": {"PAYSLIPS.OUT": "\n".join(slips) + ("\n" if slips else "")}},
    }


payroll = [
    {"name": "weekly_run", "description": "Regular, overtime, bonus tiers, unknown department and a rejected record",
     **payroll_case(employees)},
    {"name": "tax_boundaries", "description": "Taxable pay exactly on and just over the 500 and 1500 tax bands",
     **payroll_case([(201, "BAND ONE", 99, D("10.0"), D("50.00")),
                     (202, "BAND TWO", 99, D("30.0"), D("50.00")),
                     (203, "OVER TWO", 99, D("30.0"), D("50.01"))])},
]

# ---------------------------------------------------------------- NAMEFMT
names = [("Ada", "Lovelace"), ("grace", "hopper"), ("Alan", "Turing")]
nameline = lambda f, l: f"{f:<10}{l:<12}"
namefmt = [{
    "name": "three_names", "description": "Mixed case first/last names become 'LAST, FIRST' in upper case",
    "inputs": {"NAMES.DAT": "\n".join(nameline(f, l) for f, l in names) + "\n"},
    "expected": {
        "stdout": "NAMES FORMATTED: 0003\n",
        "files": {"FORMATTED.DAT": "\n".join(f"{l}, {f}".upper() for f, l in names) + "\n"},
    },
}]

for prog, cases in (("INTCALC", intcalc), ("PAYROLL", payroll), ("NAMEFMT", namefmt)):
    (HERE / f"{prog}.json").write_text(json.dumps(cases, indent=2) + "\n", encoding="utf-8")
    print(prog, len(cases), "cases")

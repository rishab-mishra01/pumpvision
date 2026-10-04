"""Parse the pump accounting software's "Party Statement" and bill exports.

Input is the text of the PDF (one string per file). Text extraction is left to
the caller because pypdf is not a dependency of this app.

A statement line looks like
    03/09/2026 SV 2942 1,011.60 56,821.67 CM. No. 1688 HSD 1 0 L
i.e. date, voucher type, voucher no, signed amount, running balance, remarks.
Voucher types: SV credit sale (+), CP cash paid on the party's behalf (+),
BD bank receipt (-), CR cash receipt (-), FC paid by fleet card (-).

parse_statement() checks that the running balance chains from the opening to the
printed closing and raises if it does not -- a statement that does not add up
must never be loaded.
"""
import re
from datetime import date

_NUM = r"-?[\d,]+\.\d{2}"
_LINE = re.compile(rf"^(\d{{2}}/\d{{2}}/\d{{4}}) ([A-Z]{{2}}) (\d+) ({_NUM}) ({_NUM})\s*(.*)$")
_PERIOD = re.compile(r"Party Statement (\d{2}/\d{2}/\d{4}) - (\d{2}/\d{2}/\d{4})")
_CLOSING = re.compile(r"Closing Rs\. (-?[\d.]+) ([DC]r)")
# Report furniture that repeats on every page; never part of a line's remarks.
_HEADER_BITS = ("Shree Petroleum", "N.H. 7, Padra", "Party Statement", "Party Sr. No",
                "SUNDRY DEBTORS", "Page ", "Closing Rs.")


def _num(s):
    return float(s.replace(",", ""))


def _dmy(s):
    d, m, y = s.split("/")
    return date(int(y), int(m), int(d))


def parse_statement(text):
    lines = [l.strip() for l in text.splitlines()]
    party = opening = closing = period = None
    rows = []
    for i, l in enumerate(lines):
        if period is None and (m := _PERIOD.search(l)):
            period = (_dmy(m.group(1)), _dmy(m.group(2)))
        if l == "SUNDRY DEBTORS" and party is None and i > 0:
            party = lines[i - 1].strip()
        if l.startswith("Opening") and opening is None:
            opening = _num(l.split()[1])
        if m := _CLOSING.search(l):
            closing = float(m.group(1))
        if m := _LINE.match(l):
            d, typ, vno, amt, bal, rem = m.groups()
            rows.append({"date": _dmy(d), "type": typ, "voucher": int(vno),
                         "amount": _num(amt), "balance": _num(bal), "remarks": rem.strip(),
                         "line": i + 1})
        elif rows and l and not re.match(r"^\d{2}/\d{2}/\d{4}", l) \
                and not any(b in l for b in _HEADER_BITS) and not re.match(rf"^{_NUM}", l):
            rows[-1]["remarks"] = (rows[-1]["remarks"] + " " + l).strip()

    if None in (party, opening, closing, period):
        raise ValueError(f"statement header incomplete: party={party} opening={opening} "
                         f"closing={closing} period={period}")
    bal = opening
    for r in rows:
        bal = round(bal + r["amount"], 2)
        if abs(bal - r["balance"]) > 0.02:
            raise ValueError(f"{party}: running balance breaks at line {r['line']} "
                             f"(computed {bal:.2f}, printed {r['balance']:.2f})")
    if abs(bal - closing) > 0.02:
        raise ValueError(f"{party}: lines sum to {bal:.2f} but closing is {closing:.2f}")
    return {"party": party, "period": period, "opening": opening,
            "closing": closing, "rows": rows}


_ITEM = re.compile(rf"^(\d+) (\d{{2}}/\d{{2}}/\d{{4}}) (\S+) (.+?) (-?[\d,]+\.\d{{2,3}}) ({_NUM}) ({_NUM})\s*$")


def parse_bills(text):
    """Bills keyed by bill number; a bill continued over pages is merged."""
    bills, cur = {}, None
    for l in (x.strip() for x in text.splitlines()):
        if m := re.match(r"Bill No (\S+)\s+Date (\S+)", l):
            cur = bills.setdefault(m.group(1), {"no": m.group(1), "date": _dmy(m.group(2)),
                                                "name": None, "vehicle": None, "items": {}, "total": None})
            continue
        if cur is None:
            continue
        if l.startswith("Name ") and not cur["name"]:
            cur["name"] = l[5:].split(" Date From")[0].strip()
        elif l.startswith("Vehicle No") and not cur["vehicle"]:
            cur["vehicle"] = l.split(":", 1)[1].strip() or None
        elif m := re.match(r"^Total ([\d.,]+)", l):
            cur["total"] = _num(m.group(1))
        elif m := _ITEM.match(l):
            sl, d, memo, prod, q, r, a = m.groups()
            cur["items"][int(sl)] = {"date": _dmy(d), "memo": memo, "product": prod.strip(),
                                     "qty": _num(q), "rate": _num(r), "amount": _num(a)}
    out = []
    for b in bills.values():
        b["items"] = [b["items"][k] for k in sorted(b["items"])]
        s = round(sum(i["amount"] for i in b["items"]), 2)
        if b["total"] is not None and abs(s - b["total"]) > 0.05:
            raise ValueError(f"bill {b['no']}: items sum to {s:.2f} but total is {b['total']:.2f}")
        out.append(b)
    return out

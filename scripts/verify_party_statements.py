"""Monthly verification pass: the accounting software's statements vs Pumpvision.

    PUMPVISION_SKIP_BOOTSTRAP=1 .venv/bin/python scripts/verify_party_statements.py <folder of PDFs>

Read-only. For every "Party Statement" PDF in the folder it reports:
  1. the statement's closing balance vs the app's balance on that date
     (current balance with every later movement taken back out);
  2. bill items charged at a rate different from IRAS for that day (HSD/Petrol/XP-95);
  3. credit sales on the statement that are on none of the party's bills.
Statement and bill PDFs are matched by file name: XYZ_STATMENT_*.pdf with XYZ_BILL_*.pdf.
Parties are matched to customers by scripts/cutover_credit_20260930.PARTY_TO_CUSTOMER.
"""
import glob
import os
import re
import sys
from datetime import datetime, time, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from party_statements import parse_bills, parse_statement  # noqa: E402
from cutover_credit_20260930 import PARTY_TO_CUSTOMER  # noqa: E402

BILL_PRODUCT = {"HSD": "HS", "Petrol": "MS", "XP-95": "X2"}


def pdf_text(path):
    import pypdf
    return "\n".join((p.extract_text() or "") for p in pypdf.PdfReader(path).pages)


def stem(path):
    """'SIPL_STATMENT_02_COPY.pdf' -> 'SIPL'; 'CMR_AMRUT__BILL_02.pdf' -> 'CMR'."""
    return re.split(r"_(?:STATMENT|BILL)|BILL_", os.path.basename(path))[0].split("_")[0].upper()


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    folder = sys.argv[1]
    from pumpvision import create_app
    from pumpvision.models import (AccountEntry, CreditTransaction, Customer, IrasPrice, LubeTransaction,
                                   PaymentReceived, db)

    app = create_app()
    problems = 0
    with app.app_context():
        bills_by_stem = {}
        for f in glob.glob(os.path.join(folder, "*.pdf")):
            if "BILL" in os.path.basename(f).upper():
                bills_by_stem.setdefault(stem(f), []).extend(parse_bills(pdf_text(f)))

        for f in sorted(glob.glob(os.path.join(folder, "*.pdf"))):
            if "STATMENT" not in os.path.basename(f).upper():
                continue
            st = parse_statement(pdf_text(f))
            end = st["period"][1]
            print(f"\n== {st['party']}  ({st['period'][0]:%d %b} – {end:%d %b %Y})")
            cid = PARTY_TO_CUSTOMER.get(st["party"], (None,))[0]
            if cid is None:
                print("   ! no customer mapping for this party -- add it to PARTY_TO_CUSTOMER")
                problems += 1
                continue
            c = db.session.get(Customer, cid)

            # 1. app balance on the statement's closing date
            after = end + timedelta(days=1)
            later = (
                sum(t.amount for t in CreditTransaction.query.filter(
                    CreditTransaction.customer_id == cid, CreditTransaction.is_legacy_entry.isnot(True),
                    db.or_(CreditTransaction.transaction_date > after,
                           db.and_(CreditTransaction.transaction_date == after,
                                   CreditTransaction.transaction_time >= time(6, 0)))))
                + sum(l.amount for l in LubeTransaction.query.filter(
                    LubeTransaction.customer_id == cid, LubeTransaction.payment_mode == "credit",
                    LubeTransaction.op_date > end))
                + sum(e.amount for e in AccountEntry.query.filter(
                    AccountEntry.customer_id == cid, AccountEntry.entry_date > end))
                - sum(p.amount for p in PaymentReceived.query.filter(
                    PaymentReceived.customer_id == cid, PaymentReceived.status == "confirmed",
                    PaymentReceived.payment_date > end))
            )
            app_bal = round((c.outstanding_balance or 0) - later, 2)
            diff = round(st["closing"] - app_bal, 2)
            flag = "OK" if abs(diff) < 1 else "MISMATCH"
            problems += flag != "OK"
            print(f"   balance  statement ₹{st['closing']:,.2f}   app ₹{app_bal:,.2f}   diff ₹{diff:,.2f}   {flag}")

            # 2. bill rates vs IRAS, 3. statement sales missing from bills
            bills = bills_by_stem.get(stem(f), [])
            if not bills:
                print("   (no bill file for this party)")
                continue
            for b in bills:
                for it in b["items"]:
                    code = BILL_PRODUCT.get(it["product"])
                    if not code:
                        continue
                    at = datetime.combine(it["date"], time(12, 0))
                    p = IrasPrice.query.filter(IrasPrice.product == code, IrasPrice.effective_from <= at,
                                               IrasPrice.effective_to >= at).first()
                    if p and abs(p.rate_per_litre - it["rate"]) > 0.005:
                        problems += 1
                        print(f"   ! rate   {it['date']:%d %b} memo {it['memo']} {it['product']} {it['qty']:g} @ "
                              f"₹{it['rate']:.2f}, IRAS ₹{p.rate_per_litre:.2f} -> "
                              f"₹{(it['rate'] - p.rate_per_litre) * it['qty']:,.2f}")
            items = [it for b in bills for it in b["items"]]
            memos = {(it["date"], it["memo"]) for it in items}
            amounts = {(it["date"], round(it["amount"], 2)) for it in items}
            # Bills cover whole months; only check the statement lines inside those months.
            first = min(it["date"] for it in items).replace(day=1)
            missing = []
            for r in st["rows"]:
                if r["type"] != "SV" or r["date"] < first:
                    continue
                m = re.search(r"CM\. No\. (\d+)", r["remarks"])
                if m and (r["date"], m.group(1)) in memos:
                    continue
                if not m and (r["date"], round(r["amount"], 2)) in amounts:
                    continue  # line printed without a memo number; matched on date + amount
                missing.append(r)
            if missing:
                problems += 1
                print(f"   ! {len(missing)} credit sales (₹{sum(r['amount'] for r in missing):,.2f}) on no bill supplied")
    print(f"\n{problems} problem(s) found." if problems else "\nAll statements agree with the app.")


if __name__ == "__main__":
    main()

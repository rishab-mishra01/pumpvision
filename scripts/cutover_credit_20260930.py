"""One-off credit + lube cutover from the pump's accounting software, as of 30 Sep 2026.

    PUMPVISION_SKIP_BOOTSTRAP=1 .venv/bin/python scripts/cutover_credit_20260930.py \
        --statements <dir of *STATMENT*.txt> --lube <LUBE_PROFARMA.xlsx> [--apply]

Dry run unless --apply. Re-running is safe: opening entries and alerts are keyed
and skipped if present, and the lube step runs only once (it is skipped once any
product carries a purchase rate). The run aborts, writing nothing, if any mapped
customer already has a sale, payment, lube sale or account entry dated after 30 Sep,
because overwriting the balance would erase it. Customer rows are locked for the
whole transaction so a concurrent sale or payment cannot interleave.

1. The 13 customers with a September statement get an OPENING entry at their
   30 Sep closing; their stored balance is set to it (signed -- TANKER is in credit).
   September's individual lines are NOT loaded: they are already billed in the
   accounting software, and loading them would put them into cash reconciliation
   and invoicing.
2. The other customers are labelled "not verified" (their balance is still the
   April 2026 import).
3. Lube: the current sheet's purchase/sale rates are mapped onto the seeded SKUs.
   SKUs with no sale rate are made inactive.
4. Alerts for the owner (Credit home): sales on no supplied bill, billing at a
   rate other than IRAS, and lube priced below cost / without a rate.
"""
import argparse
import glob
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from party_statements import parse_statement  # noqa: E402

AS_OF = date(2026, 9, 30)
SOURCE = "Party statement, closing 30/09/2026"

# Statement party name -> (customers.customer_id, expected company_name in the DB).
PARTY_TO_CUSTOMER = {
    "B P MISHRA": (25, "BP MISHRA"), "CITY CAR": (3, "CITY CAR"),
    "CMR INFRASTRUCTURE PVT LTD": (5, "CMR"), "H.D.F.C. BANK  PADRA REWA": (21, "HDFC BANK PADRA REWA"),
    "KHANIJ RH DGM": (8, "KHANIJ RH DGM"), "N K HONDA": (4, "NK HONDA"),
    "PRATEEK SINGH": (12, "PRATEEK SINGH"), "RAKESH TRIPATHI": (11, "RAKESH TRIPATHI"),
    "ADEN (N) RAILWAY": (1, "ADEN (N) RAILWAY"), "ALTIUS (SIPL)": (14, "SIPL"),
    "SONU TIWARI": (15, "SONU TIWARI"), "STAR AUTOMOBLIE": (16, "STAR AUTOMOBILES"),
    "TANKER": (26, "TANKER"),
}

# Sheet "02.10.2026" item code (column A) -> (lube_products.id, expected name, pack_size)
LUBE_MAP = {
    6: (1, "2T Supreme", "1L"),
    10: (39, "Brake Oil", "250ml"),
    11: (29, "Gear HP 90", "1L"),
    42: (30, "Gear HP 90", "5L"),
    7: (7, "Kool Plus", "1L"),
    31: (14, "Pride XL Plus 15W40", "10L"),
    44: (15, "Pride XL Plus 15W40", "15L"),
    43: (16, "Pride XL Plus 15W40", "20L"),
    18: (22, "Super 20W40 MG", "500ml"),
    17: (23, "Super 20W40 MG", "1L"),
    48: (20, "Servo SMG 20W40", "5L"),     # sheet: SERVO SUPER 20W40 MG 05 LTR
    47: (21, "Servo SMG 20W40", "7.5L"),   # sheet: SERVO SUPER 20W40 MG 7.5 LTR
    15: (25, "Super 20W40 MG", "20L"),
    19: (38, "Transfluid A", "1L"),
    35: (26, "Fleet CF4 15W40", "1L"),
    36: (27, "Fleet CF4 15W40", "5L"),
    49: (18, "Servo FLT CF4 15W40", "7.5L"),
    41: (33, "System 46", "26L"),
    40: (32, "Hydra Shakti 68", "26L"),
    12: (41, "Grease MP3", "1kg"),
    13: (42, "Grease MP3", "2kg"),
    50: (44, "Servo Clear Blue", "20L"),
}

ALERTS = [
    ("credit_alert", "CMR: 41 September credit sales (₹2,05,560.60) are on the statement but not on the "
                     "only bill supplied (SP/2026/176, AMRUT 2.0). Check for a separate CMR bill (e.g. O&M)."),
    ("credit_alert", "19 Sep 2026: 6 diesel sales billed at ₹101.60/L instead of IRAS ₹101.16 "
                     "(SIPL ×5, Rakesh Tripathi ×1, 646 L). Overcharged ₹284.24."),
]


def read_lube_sheet(path):
    import openpyxl
    ws = openpyxl.load_workbook(path, data_only=True)["02.10.2026"]
    rows = {}
    for r in ws.iter_rows(min_row=5, values_only=True):
        code, name, purchase, sale = r[0], r[1], r[2], r[3]
        if isinstance(code, int) and name:
            rows[code] = {"name": " ".join(str(name).split()), "purchase": purchase, "sale": sale}
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--statements", required=True)
    ap.add_argument("--lube", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if os.environ.get("PUMPVISION_SKIP_BOOTSTRAP", "").lower() not in ("1", "true", "yes"):
        sys.exit("Set PUMPVISION_SKIP_BOOTSTRAP=1 so loading the app does not migrate or seed.")

    from pumpvision import create_app
    from pumpvision.models import (AccountEntry, AppNotification, CreditTransaction, Customer, LubeProduct,
                                   LubeTransaction, PaymentReceived, db)

    app = create_app()
    with app.app_context():
        ids = [cid for cid, _ in PARTY_TO_CUSTOMER.values()]
        # Lock the customers first: attendant/manager routes read-modify-write the balance.
        locked = {c.customer_id: c for c in
                  Customer.query.filter(Customer.customer_id.in_(ids)).with_for_update().all()}
        for party, (cid, name) in PARTY_TO_CUSTOMER.items():
            if cid not in locked or locked[cid].company_name != name:
                sys.exit(f"customer {cid} is {locked.get(cid) and locked[cid].company_name!r}, expected {name!r}")
        later = (
            [f"sale {t.transaction_date} cust {t.customer_id}" for t in CreditTransaction.query.filter(
                CreditTransaction.customer_id.in_(ids), CreditTransaction.transaction_date > AS_OF)]
            + [f"payment {p.payment_date} cust {p.customer_id}" for p in PaymentReceived.query.filter(
                PaymentReceived.customer_id.in_(ids), PaymentReceived.payment_date > AS_OF)]
            + [f"lube {l.op_date} cust {l.customer_id}" for l in LubeTransaction.query.filter(
                LubeTransaction.customer_id.in_(ids), LubeTransaction.op_date > AS_OF)]
            + [f"entry {e.entry_date} cust {e.customer_id}" for e in AccountEntry.query.filter(
                AccountEntry.customer_id.in_(ids),
                db.or_(AccountEntry.source.is_(None), AccountEntry.source != SOURCE))]
        )
        if later:
            sys.exit("activity already recorded after the cutoff -- overwriting the balance would erase it:\n  "
                     + "\n  ".join(later))

        # 1. statements -> opening balances
        seen = set()
        for f in sorted(glob.glob(os.path.join(args.statements, "*STATMENT*.txt"))):
            st = parse_statement(open(f, encoding="utf-8").read())
            cid = PARTY_TO_CUSTOMER.get(st["party"], (None,))[0]
            if cid is None:
                sys.exit(f"no customer mapping for statement party {st['party']!r} ({f})")
            if st["period"][1] != AS_OF:
                sys.exit(f"{st['party']}: statement ends {st['period'][1]}, expected {AS_OF}")
            if cid in seen:
                sys.exit(f"{st['party']}: two statements for customer {cid}")
            seen.add(cid)
            c = locked[cid]
            done = AccountEntry.query.filter_by(customer_id=cid, entry_type="OPENING", source=SOURCE).first()
            print(f"{c.company_name:28s} {c.outstanding_balance or 0:>14,.2f} -> {st['closing']:>14,.2f}"
                  f"{'   (already loaded)' if done else ''}")
            if done:
                continue
            db.session.add(AccountEntry(
                customer_id=cid, entry_date=AS_OF, entry_type="OPENING", amount=st["closing"],
                notes=f"Balance carried in from the accounting software. "
                      f"Replaces the April 2026 import figure ₹{c.outstanding_balance or 0:,.2f}.",
                source=SOURCE,
            ))
            c.outstanding_balance = st["closing"]
            c.balance_as_of = AS_OF
            c.balance_source = SOURCE[:120]
        if len(seen) != len(PARTY_TO_CUSTOMER):
            sys.exit(f"expected {len(PARTY_TO_CUSTOMER)} statements, found {len(seen)}")

        # 2. the rest: say plainly that the balance is unverified
        others = Customer.query.filter(Customer.customer_id.notin_(seen)).all()
        for c in others:
            if not c.balance_source:
                c.balance_source = "April 2026 import -- not verified since"
        print(f"{len(others)} customers without a statement left at their April balance, marked not verified")

        # 3. lube (one-shot: once purchase rates exist, later price edits are the owner's)
        lube_alerts = []
        if LubeProduct.query.filter(LubeProduct.purchase_rate.isnot(None)).count():
            print("lube rates already applied -- skipping the lube step")
            sheet = {}
        else:
            sheet = read_lube_sheet(args.lube)
        missing = set(sheet) - set(LUBE_MAP)
        if missing:
            sys.exit(f"sheet codes with no SKU mapping: {sorted(missing)}")
        mapped = set()
        for code, row in sorted(sheet.items()):
            pid, name, pack = LUBE_MAP[code]
            p = db.session.get(LubeProduct, pid)
            if p is None or (p.name, p.pack_size) != (name, pack):
                sys.exit(f"lube id {pid} is {p and (p.name, p.pack_size)}, expected {(name, pack)}")
            mapped.add(p.id)
            sale = float(row["sale"]) if row["sale"] else None
            cost = float(row["purchase"]) if row["purchase"] else None
            p.purchase_rate = cost
            if sale:
                p.sale_rate = sale
                p.is_active = True
            else:
                p.is_active = False
                lube_alerts.append(f"Lube {p.name} {p.pack_size}: no sale rate on the 02 Oct sheet -- made inactive.")
            if sale and cost and sale < cost:
                lube_alerts.append(f"Lube {p.name} {p.pack_size}: sale rate ₹{sale:,.0f} is below cost ₹{cost:,.0f}.")
            print(f"lube #{code:>2} {row['name'][:34]:34s} -> {p.id:>2} {p.name} {p.pack_size}: "
                  f"cost {cost} sale {sale}{'' if sale else '  INACTIVE'}")
        if sheet:
            unrated = [p for p in LubeProduct.query.filter(LubeProduct.id.notin_(mapped)).all() if not p.sale_rate]
            for p in unrated:
                p.is_active = False
            print(f"{len(unrated)} seeded SKUs not on the sheet and without a rate made inactive")

        # 4. alerts (keyed on the message text)
        for kind, msg in ALERTS + [("lube_alert", m) for m in lube_alerts]:
            if not AppNotification.query.filter_by(notification_type=kind, message=msg).first():
                db.session.add(AppNotification(notification_type=kind, message=msg, reference_date=AS_OF))
            print(f"ALERT {kind}: {msg}")

        total = sum(c.outstanding_balance or 0 for c in Customer.query.all())
        print(f"total outstanding after cutover: ₹{total:,.2f}")
        if args.apply:
            db.session.commit()
            print("APPLIED")
        else:
            db.session.rollback()
            print("DRY RUN -- nothing written (use --apply)")


if __name__ == "__main__":
    main()

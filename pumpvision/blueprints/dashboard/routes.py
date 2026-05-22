import json
from collections import defaultdict
from datetime import date, time, timedelta
from pathlib import Path
from types import SimpleNamespace

from flask import Blueprint, render_template
from flask_login import current_user, login_required
from sqlalchemy import and_, func, or_

from pumpvision.decorators import owner_required
from pumpvision.models import (
    AppSetting, CreditTransaction, Expense,
    LubeTransaction, NozzleTotalizer, PaytmTransaction, SdmsSummary, TankReading, db,
)
from pumpvision.services.prices import get_rsp

dashboard_bp = Blueprint("dashboard", __name__)

_NOZZLE_PRODUCT = {7: 'HS', 16: 'HS', 18: 'MS', 15: 'MS', 17: 'X2', 11: 'XG'}
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def _product_sales(op_date):
    """Per-product net litres and revenue for the given operational day."""
    next_date = op_date + timedelta(days=1)
    opening = {r.nozzle_no: r.totalizer_end
               for r in NozzleTotalizer.query.filter_by(operational_date=op_date)}
    closing = {r.nozzle_no: r.totalizer_end
               for r in NozzleTotalizer.query.filter_by(operational_date=next_date)}

    litres = defaultdict(float)
    for nozzle_no, product in _NOZZLE_PRODUCT.items():
        t_open = opening.get(nozzle_no)
        t_close = closing.get(nozzle_no)
        if t_open is not None and t_close is not None:
            setting = db.session.get(AppSetting, f"pump_test_nozzle_{nozzle_no}")
            pump_test = float(setting.value) if setting else 0.0
            litres[product] += max(0.0, t_close - t_open - pump_test)

    out = {}
    for p in ('HS', 'MS', 'X2', 'XG'):
        l = round(litres[p], 2)
        rsp = get_rsp(p, op_date) or 0.0
        out[p] = {'litres': l, 'revenue': round(l * rsp, 2), 'rsp': rsp}
    return out


def _fleet_total(op_date):
    """Returns (amount, available). available=False means scraper hasn't run for this date.
    Reads from SdmsSummary DB first; falls back to local JSON for dev/debug compatibility."""
    row = SdmsSummary.query.filter_by(op_date=op_date).first()
    if row is not None:
        return row.fleet_card_total or 0.0, True

    # JSON fallback (local dev or pre-migration data)
    path = _PROJECT_ROOT / 'data' / 'sdms' / f'sdms_pad_{op_date:%Y-%m-%d}_summary.json'
    if not path.exists():
        return 0.0, False
    try:
        val = float(json.loads(path.read_text(encoding='utf-8')).get('fleet_card_total', 0))
        return val, True
    except (json.JSONDecodeError, ValueError, KeyError):
        return 0.0, False


def _cng_sdms(op_date):
    """
    Returns SimpleNamespace(kg_sold, rsp_per_kg, revenue) or None if no SDMS data.
    Reads from SdmsSummary DB first; falls back to local JSON for dev/debug compatibility.
    Attendant CngShiftReading entries are preserved separately and not used for display.
    """
    row = SdmsSummary.query.filter_by(op_date=op_date).first()
    if row is not None:
        if (row.cng_kg_total or 0.0) > 0:
            return SimpleNamespace(
                kg_sold=row.cng_kg_total,
                rsp_per_kg=row.cng_rsp_per_kg or 93.40,
                revenue=row.cng_revenue or 0.0,
            )
        return None

    # JSON fallback (local dev or pre-migration data)
    path = _PROJECT_ROOT / 'data' / 'sdms' / f'sdms_pad_{op_date:%Y-%m-%d}_summary.json'
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        kg  = float(data.get('cng_kg_total', 0))
        rsp = float(data.get('cng_rsp_per_kg', 93.40))
        rev = float(data.get('cng_revenue', 0))
        if kg <= 0:
            return None
        return SimpleNamespace(kg_sold=kg, rsp_per_kg=rsp, revenue=rev)
    except (json.JSONDecodeError, ValueError, KeyError):
        return None


def _credit_total(op_date):
    nd = op_date + timedelta(days=1)
    return db.session.query(func.sum(CreditTransaction.amount)).filter(
        or_(
            and_(CreditTransaction.transaction_date == op_date,
                 CreditTransaction.transaction_time >= time(6, 0)),
            and_(CreditTransaction.transaction_date == nd,
                 CreditTransaction.transaction_time < time(6, 0)),
        )
    ).scalar() or 0.0


def _cash_for_date(op_date):
    """Derived cash for op_date. Returns None if no fuel data at all."""
    products = _product_sales(op_date)
    cng = _cng_sdms(op_date)
    if not any(p['litres'] > 0 for p in products.values()) and not cng:
        return None
    fuel_rev = sum(p['revenue'] for p in products.values()) + (cng.revenue if cng else 0.0)
    lube = db.session.query(func.sum(LubeTransaction.amount)).filter_by(
        op_date=op_date, payment_mode='cash').scalar() or 0.0
    paytm = db.session.query(func.sum(PaytmTransaction.amount)).filter_by(
        operational_date=op_date).scalar() or 0.0
    expenses = db.session.query(func.sum(Expense.amount)).filter_by(
        op_date=op_date).scalar() or 0.0
    fleet, _ = _fleet_total(op_date)
    return round(fuel_rev + lube - paytm - _credit_total(op_date) - fleet - expenses, 2)


def _stock_watch(op_date):
    """Returns list of at-risk products (≤ 7 days remaining). Empty if no ATG data."""
    alerts = []
    for product in ('HS', 'MS', 'X2', 'XG'):
        reading = TankReading.query.filter_by(product=product).order_by(
            TankReading.scraped_at.desc()).first()
        if not reading or not reading.volume_litres:
            continue

        nozzles = [n for n, p in _NOZZLE_PRODUCT.items() if p == product]
        total, days = 0.0, 0
        for i in range(1, 8):
            day = op_date - timedelta(days=i)
            nd = day + timedelta(days=1)
            op = {r.nozzle_no: r.totalizer_end
                  for r in NozzleTotalizer.query.filter_by(operational_date=day)}
            cl = {r.nozzle_no: r.totalizer_end
                  for r in NozzleTotalizer.query.filter_by(operational_date=nd)}
            if not all(n in op and n in cl for n in nozzles):
                continue
            day_l = 0.0
            for n in nozzles:
                s = db.session.get(AppSetting, f"pump_test_nozzle_{n}")
                pt = float(s.value) if s else 0.0
                day_l += max(0.0, cl[n] - op[n] - pt)
            if day_l > 0:
                total += day_l
                days += 1

        if not days:
            continue
        avg = total / days
        if avg <= 0:
            continue
        rem = reading.volume_litres / avg
        if rem <= 7:
            order_d = op_date + timedelta(days=max(0, int(rem) - 2))
            day_str = order_d.strftime('%d %b').lstrip('0').strip()
            alerts.append({
                'product': product,
                'days': rem,
                'days_int': int(rem),
                'order_by': day_str,
                'urgent': rem <= 2,
            })
    return sorted(alerts, key=lambda x: x['days'])


@dashboard_bp.route("/summary")
@dashboard_bp.route("/summary/<date_str>")
@login_required
@owner_required
def summary(date_str=None):
    yesterday = date.today() - timedelta(days=1)
    if date_str:
        try:
            op_date = date.fromisoformat(date_str)
        except ValueError:
            op_date = yesterday
    else:
        op_date = yesterday

    prev_date = (op_date - timedelta(days=1)).isoformat()
    next_date = (op_date + timedelta(days=1)).isoformat() if op_date < yesterday else None

    products = _product_sales(op_date)
    cng = _cng_sdms(op_date)
    has_data = any(p['litres'] > 0 for p in products.values()) or bool(cng)

    lube_cash = db.session.query(func.sum(LubeTransaction.amount)).filter_by(
        op_date=op_date, payment_mode='cash').scalar() or 0.0

    fuel_rev = sum(p['revenue'] for p in products.values()) + (cng.revenue if cng else 0.0)
    total_rev = round(fuel_rev + lube_cash, 2)

    paytm = db.session.query(func.sum(PaytmTransaction.amount)).filter_by(
        operational_date=op_date).scalar() or 0.0
    paytm_available = db.session.query(func.count(PaytmTransaction.id)).filter_by(
        operational_date=op_date).scalar() > 0

    credit = _credit_total(op_date)
    fleet_raw, fleet_available = _fleet_total(op_date)
    fleet = fleet_raw

    expenses = db.session.query(func.sum(Expense.amount)).filter_by(
        op_date=op_date).scalar() or 0.0
    expenses_available = db.session.query(func.count(Expense.id)).filter_by(
        op_date=op_date).scalar() > 0

    derived_cash = round(total_rev - paytm - credit - fleet - expenses, 2)
    cash_paise = f'{round((abs(derived_cash) - int(abs(derived_cash))) * 100):02d}'

    date_display = str(op_date.day) + op_date.strftime(' %b %Y')
    dow_display = op_date.strftime('%A')

    return render_template(
        "owner/summary.html",
        op_date=op_date,
        prev_date=prev_date,
        next_date=next_date,
        has_data=has_data,
        products=products,
        cng=cng,
        lube_cash=round(lube_cash, 2),
        fuel_rev=round(fuel_rev, 2),
        total_rev=total_rev,
        paytm=round(paytm, 2),
        paytm_available=paytm_available,
        credit=round(credit, 2),
        fleet=round(fleet, 2),
        fleet_available=fleet_available,
        expenses=round(expenses, 2),
        expenses_available=expenses_available,
        derived_cash=derived_cash,
        cash_paise=cash_paise,
        date_display=date_display,
        dow_display=dow_display,
    )


@dashboard_bp.route("/dashboard")
@login_required
@owner_required
def index():
    op_date = date.today() - timedelta(days=1)

    products = _product_sales(op_date)
    cng = _cng_sdms(op_date)

    lube_cash = db.session.query(func.sum(LubeTransaction.amount)).filter_by(
        op_date=op_date, payment_mode='cash').scalar() or 0.0
    lube_units = int(db.session.query(func.sum(LubeTransaction.quantity)).filter_by(
        op_date=op_date).scalar() or 0)

    fuel_rev = sum(p['revenue'] for p in products.values()) + (cng.revenue if cng else 0.0)
    total_rev = round(fuel_rev + lube_cash, 2)

    paytm = db.session.query(func.sum(PaytmTransaction.amount)).filter_by(
        operational_date=op_date).scalar() or 0.0
    credit = _credit_total(op_date)
    fleet_raw, fleet_available = _fleet_total(op_date)
    fleet = fleet_raw
    expenses = db.session.query(func.sum(Expense.amount)).filter_by(
        op_date=op_date).scalar() or 0.0
    expenses_available = db.session.query(func.count(Expense.id)).filter_by(
        op_date=op_date).scalar() > 0

    derived_cash = round(total_rev - paytm - credit - fleet - expenses, 2)

    yesterday_cash = _cash_for_date(op_date - timedelta(days=1))
    trend = round(derived_cash - yesterday_cash, 2) if yesterday_cash is not None else None

    prices = {p: get_rsp(p, op_date) or 0.0 for p in ('HS', 'MS', 'X2', 'XG')}
    s = db.session.get(AppSetting, 'cng_rsp_per_kg')
    prices['CNG'] = float(s.value) if s else 93.40

    has_data = any(p['litres'] > 0 for p in products.values()) or bool(cng)

    return render_template(
        "dashboard/index.html",
        op_date=op_date,
        products=products,
        cng=cng,
        lube_cash=round(lube_cash, 2),
        lube_units=lube_units,
        fuel_rev=round(fuel_rev, 2),
        total_rev=total_rev,
        paytm=round(paytm, 2),
        credit=round(credit, 2),
        fleet=round(fleet, 2),
        fleet_available=fleet_available,
        expenses=round(expenses, 2),
        expenses_available=expenses_available,
        derived_cash=derived_cash,
        trend=trend,
        prices=prices,
        stock_alerts=_stock_watch(op_date),
        has_data=has_data,
        first_name=current_user.first_name or 'there',
    )

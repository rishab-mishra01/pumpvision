"""Read-only data contract and transactional events for Live Operations."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_

from pumpvision.extensions import db
from pumpvision.models import (
    CngShiftReading,
    CreditTransaction,
    EntryAudit,
    Expense,
    IrasPrice,
    LubeTransaction,
    ManualTotalizerReading,
    NozzleTotalizer,
    OpsEvent,
    PaymentReceived,
    PaytmTransaction,
    ReviewFinding,
    ReviewStatus,
    SdmsSummary,
    TankReading,
    User,
)
from pumpvision.services.operational import IST, cycle_bounds, get_operational_date, now_ist
from pumpvision.services.prices import get_rsp
from pumpvision.services.totalizers import opening_reading


UTC = timezone.utc
NOZZLES = (
    (7, "HS1", "HS"),
    (16, "HS2", "HS"),
    (18, "MS1", "MS"),
    (15, "MS2", "MS"),
    (17, "X2", "X2"),
    (11, "XG", "XG"),
)
DB_LABELS = {7: "HSD 1", 16: "HSD 2", 18: "MS 1", 15: "MS 2", 17: "XP", 11: "XG"}


def record_event(
    event_type: str,
    title: str,
    *,
    detail: str | None = None,
    source: str = "pumpvision",
    severity: str = "info",
    op_date: date | None = None,
    business_at: datetime | None = None,
    observed_at: datetime | None = None,
    actor=None,
    entity_type: str | None = None,
    entity_id: str | int | None = None,
    idempotency_key: str | None = None,
    payload: dict | None = None,
) -> OpsEvent:
    """Stage an event in the caller's current transaction; never commits itself."""
    if idempotency_key:
        existing = OpsEvent.query.filter_by(idempotency_key=idempotency_key).first()
        if existing is not None:
            return existing
    event = OpsEvent(
        event_type=event_type,
        title=title[:180],
        detail=(detail or "")[:500] or None,
        source=source[:40],
        severity=severity,
        op_date=op_date,
        business_at=business_at,
        observed_at=observed_at,
        actor_id=getattr(actor, "id", None),
        actor_role=getattr(actor, "role", None),
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        idempotency_key=idempotency_key,
        payload_json=json.dumps(payload, sort_keys=True) if payload else None,
    )
    db.session.add(event)
    return event


def _utc_iso(value: datetime | None) -> str | None:
    """Convert a naive UTC model timestamp to an offset-aware IST ISO string."""
    if value is None:
        return None
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value
    return aware.astimezone(IST).isoformat(timespec="seconds")


def _ist_iso(value: datetime | None) -> str | None:
    """Attach/convert IST to timestamps that are documented as IST wall time."""
    if value is None:
        return None
    aware = value.replace(tzinfo=IST) if value.tzinfo is None else value.astimezone(IST)
    return aware.isoformat(timespec="seconds")


def _age_minutes(value: datetime | None, *, value_is_utc: bool = True) -> int | None:
    if value is None:
        return None
    if value_is_utc:
        aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value
    else:
        aware = value.replace(tzinfo=IST) if value.tzinfo is None else value.astimezone(IST)
    return max(0, int((datetime.now(UTC) - aware.astimezone(UTC)).total_seconds() // 60))


def _actor_name(user_id: int | None, fallback: str) -> str:
    if not user_id:
        return fallback
    user = db.session.get(User, user_id)
    return (user.first_name or user.username) if user else fallback


def _price_record(product: str, op_date: date):
    target = datetime.combine(op_date, datetime.min.time()).replace(hour=6)
    exact = IrasPrice.query.filter(
        IrasPrice.product == product,
        IrasPrice.effective_from <= target,
        or_(IrasPrice.effective_to.is_(None), IrasPrice.effective_to >= target),
    ).order_by(IrasPrice.effective_from.desc()).first()
    if exact:
        return exact, "iras_exact"
    earlier = IrasPrice.query.filter(
        IrasPrice.product == product,
        IrasPrice.effective_from <= target,
    ).order_by(IrasPrice.effective_from.desc()).first()
    return (earlier, "iras_fallback") if earlier else (None, "local_fallback")


def _financial_snapshot(op_date: date) -> dict:
    from pumpvision.blueprints.dashboard.routes import (
        _cng_sdms,
        _credit_total,
        _fleet_total,
        _paytm_cycle,
        _product_sales,
    )

    products = _product_sales(op_date)
    cng = _cng_sdms(op_date)
    iras_closing = NozzleTotalizer.query.filter_by(operational_date=op_date + timedelta(days=1)).count()
    manual_closing = ManualTotalizerReading.query.filter_by(operational_date=op_date, is_locked=True).count()
    liquid_available = iras_closing >= 6 or manual_closing >= 6
    cng_available = cng is not None

    liquid_revenue = sum(row["revenue"] for row in products.values()) if liquid_available else None
    gross = round(liquid_revenue + cng.revenue, 2) if liquid_revenue is not None and cng_available else None
    lube_cash = db.session.query(func.sum(LubeTransaction.amount)).filter_by(
        op_date=op_date, payment_mode="cash"
    ).scalar() or 0.0
    expenses = db.session.query(func.sum(Expense.amount)).filter_by(op_date=op_date).scalar() or 0.0
    expense_count = Expense.query.filter_by(op_date=op_date).count()
    paytm, paytm_count = _paytm_cycle(op_date)
    credit = _credit_total(op_date)
    fleet, fleet_available = _fleet_total(op_date)
    collections = db.session.query(func.sum(PaymentReceived.amount)).filter_by(
        payment_date=op_date
    ).scalar() or 0.0
    collections_count = PaymentReceived.query.filter_by(payment_date=op_date).count()

    complete = gross is not None and paytm_count > 0 and fleet_available
    expected_cash = None
    if complete:
        expected_cash = round(gross + lube_cash - paytm - credit - fleet - expenses, 2)

    return {
        "gross_fuel_sales": gross,
        "expected_sales_cash": expected_cash,
        "physical_cash": None,
        "expenses": round(expenses, 2),
        "expense_count": expense_count,
        "credit_sales": round(credit, 2),
        "paytm_pos": round(paytm, 2) if paytm_count else None,
        "fleet_card": round(fleet, 2) if fleet_available else None,
        "account_collections": round(collections, 2),
        "collection_count": collections_count,
        "lube_cash": round(lube_cash, 2),
        "state": "provisional" if not complete else "final",
        "missing": [
            label for label, missing in (
                ("liquid fuel close", not liquid_available),
                ("CNG close/SDMS", not cng_available),
                ("Paytm POS", paytm_count == 0),
                ("fleet card/SDMS", not fleet_available),
            ) if missing
        ],
    }


def _shift_snapshot(op_date: date) -> dict:
    liquid = ManualTotalizerReading.query.filter_by(operational_date=op_date).all()
    cng = CngShiftReading.query.filter_by(op_date=op_date).all()
    locked = sum(bool(row.is_locked) for row in liquid if row.nozzle_no is not None)
    entered = len({row.nozzle_no for row in liquid if row.nozzle_no is not None})
    iras = NozzleTotalizer.query.filter_by(operational_date=op_date + timedelta(days=1)).count()
    if locked >= 6 and iras >= 6:
        state = "final"
    elif locked >= 6:
        state = "submitted"
    elif entered or cng:
        state = "provisional"
    else:
        state = "open"
    latest_manual = max((row.recorded_at for row in liquid), default=None)
    return {
        "state": state,
        "liquid_entered": entered,
        "liquid_locked": locked,
        "liquid_total": 6,
        "cng_entered": len({row.nozzle_no for row in cng}),
        "cng_total": 2,
        "iras_received": iras,
        "submitted_at": _ist_iso(latest_manual) if locked >= 6 else None,
    }


def _nozzle_snapshot(op_date: date) -> list[dict]:
    out = []
    for nozzle_no, label, product in NOZZLES:
        manual = ManualTotalizerReading.query.filter_by(
            operational_date=op_date, nozzle_no=nozzle_no
        ).first()
        iras = NozzleTotalizer.query.filter_by(
            operational_date=op_date + timedelta(days=1), nozzle_no=nozzle_no
        ).first()
        opening = opening_reading(nozzle_no, op_date)
        manual_value = manual.totalizer_value if manual else None
        iras_value = iras.totalizer_end if iras else None
        variance = round(manual_value - iras_value, 2) if manual_value is not None and iras_value is not None else None
        if manual and iras:
            status = "reconciled" if abs(variance or 0) <= 1 else "review"
        elif manual:
            status = "manual_submitted" if manual.is_locked else "manual_draft"
        elif iras:
            status = "iras_only"
        else:
            status = "pending"
        out.append({
            "nozzle_no": nozzle_no,
            "label": label,
            "product": product,
            "opening": opening,
            "manual": manual_value,
            "manual_at": _ist_iso(manual.recorded_at) if manual else None,
            "manual_locked": bool(manual and manual.is_locked),
            "iras": iras_value,
            "iras_at": _utc_iso(iras.scraped_at) if iras else None,
            "variance": variance,
            "status": status,
        })
    return out


def _cng_snapshot(op_date: date) -> dict:
    from pumpvision.blueprints.dashboard.routes import _cng_sdms

    manual = CngShiftReading.query.filter_by(op_date=op_date).order_by(CngShiftReading.nozzle_no).all()
    sdms = SdmsSummary.query.filter_by(op_date=op_date).first()
    priced = _cng_sdms(op_date)
    manual_total = round(sum(row.kg_sold for row in manual), 2) if manual else None
    sdms_total = round(sdms.cng_kg_total, 2) if sdms and (sdms.cng_kg_total or 0) > 0 else None
    variance = round(manual_total - sdms_total, 2) if manual_total is not None and sdms_total is not None else None
    if sdms_total is not None and manual_total is not None:
        state = "reconciled" if abs(variance or 0) <= 1 else "review"
    elif manual_total is not None:
        state = "awaiting_sdms"
    elif sdms_total is not None:
        state = "sdms_only"
    else:
        state = "pending"
    return {
        "state": state,
        "manual_total": manual_total,
        "manual_at": _utc_iso(max((r.created_at for r in manual), default=None)),
        "manual_nozzles": [
            {
                "nozzle_no": row.nozzle_no,
                "opening": row.opening_reading,
                "closing": row.closing_reading,
                "kg_sold": row.kg_sold,
                "submitted_by": _actor_name(row.submitted_by, "Attendant"),
                "submitted_at": _utc_iso(row.created_at),
            }
            for row in manual
        ],
        "sdms_total": sdms_total,
        "sdms_at": _utc_iso((sdms.updated_at or sdms.created_at) if sdms else None),
        "variance": variance,
        "selling_price": priced.rsp_per_kg if priced else None,
        "cost_per_kg": priced.cost_per_kg if priced else None,
        "margin_per_kg": priced.margin_per_kg if priced else None,
        "price_basis": priced.price_basis if priced else None,
        "source": priced.source if priced else None,
    }


def _prices_snapshot(op_date: date) -> list[dict]:
    out = []
    for product in ("HS", "MS", "X2", "XG"):
        row, source = _price_record(product, op_date)
        out.append({
            "product": product,
            "rate": get_rsp(product, op_date),
            "source": source,
            "effective_from": _ist_iso(row.effective_from) if row else None,
            "fetched_at": _utc_iso(row.imported_at) if row else None,
        })
    return out


def _source_snapshot(op_date: date, *, include_technical: bool) -> list[dict]:
    latest_price = db.session.query(func.max(IrasPrice.imported_at)).scalar()
    latest_boundary = db.session.query(func.max(NozzleTotalizer.scraped_at)).filter_by(
        operational_date=op_date + timedelta(days=1)
    ).scalar()
    latest_paytm = db.session.query(func.max(PaytmTransaction.imported_at)).filter_by(
        operational_date=op_date
    ).scalar()
    sdms = SdmsSummary.query.filter_by(op_date=op_date).first()
    latest_atg = db.session.query(func.max(TankReading.scraped_at)).scalar()

    def source(key, label, value, *, expected, state=None, note=None, utc=True):
        age = _age_minutes(value, value_is_utc=utc)
        resolved = state or ("pending" if value is None else "healthy" if age is not None and age <= expected else "stale")
        return {
            "key": key,
            "label": label,
            "state": resolved,
            "observed_at": _utc_iso(value) if utc else _ist_iso(value),
            "age_minutes": age,
            "note": note,
        }

    rows = [
        source("database", "PostgreSQL", None, expected=2, state="healthy", note="Connected through the web application"),
        source("iras_prices", "IRAS prices", latest_price, expected=36 * 60),
        source("iras_totalizers", "IRAS nozzle boundaries", latest_boundary, expected=36 * 60),
        source("paytm", "Paytm POS", latest_paytm, expected=36 * 60),
        source(
            "sdms", "SDMS / eLedger", (sdms.updated_at or sdms.created_at) if sdms else None,
            expected=36 * 60,
            state="healthy" if sdms and (sdms.cng_kg_total or 0) > 0 else "pending",
            note="CNG and fleet-card settlement data",
        ),
        source("atg", "ATG tank stock", latest_atg, expected=90, utc=False),
    ]
    if include_technical:
        for status in ReviewStatus.query.order_by(ReviewStatus.grp, ReviewStatus.component).all():
            rows.append({
                "key": f"review:{status.component}",
                "label": status.component,
                "state": {"ok": "healthy", "warn": "stale", "fail": "failed"}.get(status.status, status.status),
                "observed_at": _utc_iso(status.checked_at),
                "age_minutes": _age_minutes(status.checked_at),
                "note": status.detail,
            })
    return rows


def _alerts(role: str) -> list[dict]:
    query = ReviewFinding.query.filter(ReviewFinding.resolved_at.is_(None))
    if role in ("owner", "manager"):
        query = query.filter(ReviewFinding.audience == "owner")
    return [
        {
            "id": finding.id,
            "severity": finding.severity,
            "title": finding.title,
            "detail": finding.detail,
            "source": finding.source,
            "last_seen": _utc_iso(finding.last_seen),
            "acknowledged": finding.acknowledged_at is not None,
        }
        for finding in query.order_by(ReviewFinding.last_seen.desc()).limit(12)
    ]


def _derived_activity(op_date: date) -> list[dict]:
    """Recent evidence already present in business tables, including pre-deploy rows."""
    items: list[dict] = []

    for row in Expense.query.order_by(Expense.created_at.desc()).limit(8):
        items.append({
            "key": f"expense:{row.id}", "kind": "expense", "severity": "info",
            "title": f"Expense added · ₹{row.amount:,.2f}",
            "detail": " / ".join(x for x in (row.category, row.sub_category, row.description) if x),
            "source": "manual", "actor": _actor_name(row.logged_by, "Manager"),
            "actor_role": "manager", "op_date": row.op_date.isoformat(),
            "at": _utc_iso(row.created_at),
        })

    for row in CreditTransaction.query.order_by(CreditTransaction.created_at.desc()).limit(8):
        items.append({
            "key": f"credit:{row.transaction_id}", "kind": "credit_sale", "severity": "info",
            "title": f"Credit sale recorded · ₹{row.amount:,.2f}",
            "detail": f"{row.product} · {row.litres:,.2f} L · {row.vehicle_number}",
            "source": "manual", "actor": row.attendant_name, "actor_role": "attendant/manager",
            "op_date": row.transaction_date.isoformat(), "at": _utc_iso(row.created_at),
        })

    for row in PaymentReceived.query.order_by(PaymentReceived.created_at.desc()).limit(6):
        items.append({
            "key": f"payment:{row.payment_id}", "kind": "payment", "severity": "info",
            "title": f"Account collection recorded · ₹{row.amount:,.2f}",
            "detail": f"{row.payment_mode or 'Unspecified'} · {row.status}",
            "source": "manual", "actor": "Manager/Owner", "actor_role": "manager/owner",
            "op_date": row.payment_date.isoformat(), "at": _utc_iso(row.created_at),
        })

    manual = ManualTotalizerReading.query.filter_by(operational_date=op_date).order_by(
        ManualTotalizerReading.recorded_at.desc()
    ).all()
    if manual:
        latest = max(row.recorded_at for row in manual)
        locked = sum(bool(row.is_locked) for row in manual if row.nozzle_no is not None)
        items.append({
            "key": f"shift:{op_date.isoformat()}:{locked}:{latest.isoformat()}",
            "kind": "shift_close", "severity": "info",
            "title": "Shift close submitted manually" if locked >= 6 else "Shift-close readings in progress",
            "detail": f"{len(manual)}/6 nozzle readings entered · {locked}/6 locked",
            "source": "manual", "actor": "Attendant", "actor_role": "attendant",
            "op_date": op_date.isoformat(), "at": _ist_iso(latest),
        })

    prices_at = db.session.query(func.max(IrasPrice.imported_at)).scalar()
    if prices_at:
        items.append({
            "key": f"iras-price:{prices_at.isoformat()}", "kind": "price", "severity": "info",
            "title": "Current fuel prices fetched",
            "detail": "IRAS price records imported into PumpVision",
            "source": "iras", "actor": "IRAS", "actor_role": "system",
            "op_date": op_date.isoformat(), "at": _utc_iso(prices_at),
        })

    boundary_at = db.session.query(func.max(NozzleTotalizer.scraped_at)).filter_by(
        operational_date=op_date + timedelta(days=1)
    ).scalar()
    if boundary_at:
        count = NozzleTotalizer.query.filter_by(operational_date=op_date + timedelta(days=1)).count()
        items.append({
            "key": f"iras-boundary:{op_date}:{boundary_at.isoformat()}", "kind": "nozzle", "severity": "info",
            "title": "IRAS nozzle boundary received",
            "detail": f"{count}/6 nozzle readings available for reconciliation",
            "source": "iras", "actor": "IRAS", "actor_role": "system",
            "op_date": op_date.isoformat(), "at": _utc_iso(boundary_at),
        })

    sdms = SdmsSummary.query.filter_by(op_date=op_date).first()
    if sdms:
        at = sdms.updated_at or sdms.created_at
        items.append({
            "key": f"sdms:{row_key(sdms.id, at)}", "kind": "cng", "severity": "info",
            "title": "SDMS / eLedger summary fetched",
            "detail": f"CNG {sdms.cng_kg_total:,.2f} kg · fleet card ₹{sdms.fleet_card_total:,.2f}",
            "source": "sdms", "actor": "SDMS/eLedger", "actor_role": "system",
            "op_date": op_date.isoformat(), "at": _utc_iso(at),
        })

    latest_atg = TankReading.query.order_by(TankReading.scraped_at.desc()).first()
    if latest_atg:
        items.append({
            "key": f"atg:{latest_atg.scraped_at.isoformat()}", "kind": "source", "severity": "info",
            "title": "Tank stock readings fetched",
            "detail": "Latest ATG snapshot received",
            "source": "atg", "actor": "IRAS ATG", "actor_role": "system",
            "op_date": op_date.isoformat(), "at": _ist_iso(latest_atg.scraped_at),
        })

    items.sort(key=lambda item: item.get("at") or "", reverse=True)
    return items[:30]


def row_key(row_id, at):
    return f"{row_id}:{at.isoformat() if at else 'unknown'}"


def snapshot(op_date: date | None, role: str) -> dict:
    selected = op_date or get_operational_date()
    start, end = cycle_bounds(selected)
    include_technical = role in ("developer", "super_admin")
    return {
        "generated_at": _ist_iso(now_ist()),
        "cycle": {
            "op_date": selected.isoformat(),
            "start": _ist_iso(start),
            "end": _ist_iso(end),
            "is_current": selected == get_operational_date(),
        },
        "shift": _shift_snapshot(selected),
        "financial": _financial_snapshot(selected),
        "prices": _prices_snapshot(selected),
        "nozzles": _nozzle_snapshot(selected),
        "cng": _cng_snapshot(selected),
        "sources": _source_snapshot(selected, include_technical=include_technical),
        "alerts": _alerts(role),
        "activity": _derived_activity(selected),
        "role": role,
        "technical_access": include_technical,
    }


def events_after(after_id: int, op_date: date | None, role: str, limit: int = 100) -> dict:
    query = OpsEvent.query.filter(OpsEvent.id > max(0, after_id))
    if op_date:
        query = query.filter(or_(OpsEvent.op_date == op_date, OpsEvent.op_date.is_(None)))
    rows = query.order_by(OpsEvent.id.asc()).limit(min(max(limit, 1), 100)).all()
    events = []
    for row in rows:
        if role in ("owner", "manager") and row.severity == "technical":
            continue
        actor = row.actor
        events.append({
            "id": row.id,
            "key": f"ops:{row.id}",
            "kind": row.event_type,
            "severity": row.severity,
            "title": row.title,
            "detail": row.detail,
            "source": row.source,
            "actor": (actor.first_name or actor.username) if actor else row.source.upper(),
            "actor_role": row.actor_role or "system",
            "op_date": row.op_date.isoformat() if row.op_date else None,
            "at": _ist_iso(row.business_at) or _utc_iso(row.recorded_at),
            "observed_at": _ist_iso(row.observed_at),
        })
    return {"events": events, "last_id": rows[-1].id if rows else after_id}

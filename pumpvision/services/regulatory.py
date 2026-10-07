"""Regulatory items the owner watches: the quarterly mock drill and fuel testing."""
from datetime import date, timedelta

MOCK_DRILL_INTERVAL_DAYS = 91   # "every three months"
MOCK_DRILL_WARN_DAYS = 14       # amber this long before it falls due


def mock_drill_status(today: date | None = None) -> dict:
    """state: never | ok | soon | overdue. days_left is negative once overdue."""
    from pumpvision.models import MockDrill

    today = today or date.today()
    last = MockDrill.query.order_by(MockDrill.drill_date.desc(), MockDrill.id.desc()).first()
    if last is None:
        return {"state": "never", "last": None, "due": None, "days_left": None}
    due = last.drill_date + timedelta(days=MOCK_DRILL_INTERVAL_DAYS)
    days_left = (due - today).days
    state = "overdue" if days_left < 0 else "soon" if days_left <= MOCK_DRILL_WARN_DAYS else "ok"
    return {"state": state, "last": last.drill_date, "due": due, "days_left": days_left}


def drill_needs_attention(status: dict) -> bool:
    return status["state"] in ("never", "soon", "overdue")


def tested_litres(op_date: date) -> dict:
    """{product: litres drawn off for testing on op_date}."""
    from sqlalchemy import func
    from pumpvision.models import FuelTest, db

    rows = db.session.query(FuelTest.product, func.sum(FuelTest.litres)) \
        .filter(FuelTest.op_date == op_date).group_by(FuelTest.product).all()
    return {p: float(l or 0.0) for p, l in rows}

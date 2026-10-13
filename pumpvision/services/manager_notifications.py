"""Manager notification creation inside an existing business transaction."""
from __future__ import annotations

from datetime import date

from pumpvision.extensions import db
from pumpvision.models import ManagerNotification


def notify_manager(
    event_key: str,
    message: str,
    *,
    notification_type: str,
    reference_date: date | None = None,
) -> ManagerNotification:
    """Stage one idempotent manager notification; the caller owns the commit."""
    existing = ManagerNotification.query.filter_by(event_key=event_key).first()
    if existing is not None:
        return existing
    row = ManagerNotification(
        event_key=event_key[:180],
        message=message[:500],
        notification_type=notification_type[:30],
        reference_date=reference_date,
    )
    db.session.add(row)
    return row

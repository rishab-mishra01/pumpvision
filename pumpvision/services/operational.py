from datetime import date, datetime, timedelta


def get_operational_date(now: datetime | None = None) -> date:
    """
    The operational day runs 06:00 → 05:59 the next morning.
    Before 06:00 → the shift still belongs to yesterday.
    At or after 06:00 → the shift belongs to today.
    """
    if now is None:
        now = datetime.now()
    if now.hour < 6:
        return now.date() - timedelta(days=1)
    return now.date()

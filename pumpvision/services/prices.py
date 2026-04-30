from datetime import date, datetime, time

from sqlalchemy import or_

from pumpvision.models import IrasPrice, LocalPrice


def get_rsp(product: str, op_date: date) -> float | None:
    """
    RSP for a product on a given operational date.
    Tries IrasPrice first (accurate historical), falls back to LocalPrice.
    The op_date 06:00 boundary is used as the lookup timestamp.
    """
    target_dt = datetime.combine(op_date, time(6, 0))

    price = IrasPrice.query.filter(
        IrasPrice.product == product,
        IrasPrice.effective_from <= target_dt,
        or_(IrasPrice.effective_to == None, IrasPrice.effective_to >= target_dt),
    ).order_by(IrasPrice.effective_from.desc()).first()

    if price:
        return price.rate_per_litre

    local = LocalPrice.query.filter_by(product=product).order_by(
        LocalPrice.effective_from.desc()
    ).first()
    return local.rate_per_litre if local else None

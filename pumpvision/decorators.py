from functools import wraps
from flask import abort
from flask_login import current_user

from pumpvision.authorization import has_capability


def role_required(role):
    """Require one exact role.

    Exact-role checks protect all existing write-capable dashboards.  They must
    not be replaced with dashboard view capabilities, because super_admin is a
    read-only preview role.
    """
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not current_user.is_authenticated or current_user.role != role:
                abort(403)
            return f(*args, **kwargs)
        return decorated
    return decorator


def capability_required(capability):
    """Require an additive capability without implying any write permission."""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not has_capability(current_user, capability):
                abort(403)
            return f(*args, **kwargs)
        return decorated
    return decorator


owner_required = role_required("owner")
attendant_required = role_required("attendant")
manager_required = role_required("manager")
developer_required = role_required("developer")
super_admin_required = role_required("super_admin")

from functools import wraps
from flask import abort
from flask_login import current_user


def owner_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != "owner":
            abort(403)
        return f(*args, **kwargs)
    return decorated


def attendant_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != "attendant":
            abort(403)
        return f(*args, **kwargs)
    return decorated

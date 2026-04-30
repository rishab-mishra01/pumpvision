from flask import Blueprint, redirect, url_for
from flask_login import login_required

from pumpvision.decorators import owner_required

owner_bp = Blueprint("owner", __name__)


@owner_bp.route("/")
@login_required
@owner_required
def index():
    return redirect(url_for("dashboard.index"))

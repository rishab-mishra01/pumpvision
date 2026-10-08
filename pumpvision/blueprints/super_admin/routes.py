"""GET-only, read-only previews for all Pumpvision dashboard roles."""
from flask import Blueprint, make_response, render_template
from flask_login import login_required

from pumpvision.authorization import (
    VIEW_ADMIN_DASHBOARD,
    VIEW_ATTENDANT_DASHBOARD,
    VIEW_DEVELOPER_DASHBOARD,
    VIEW_MANAGER_DASHBOARD,
)
from pumpvision.decorators import capability_required, super_admin_required

super_admin_bp = Blueprint("super_admin", __name__)


def _render(selected, **context):
    response = make_response(render_template(
        "super_admin/console.html", selected=selected, **context
    ))
    # Dashboard previews contain current operational data and should not be kept
    # in a shared or browser cache after logout.
    response.headers["Cache-Control"] = "no-store"
    return response


@super_admin_bp.route("/")
@login_required
@super_admin_required
def overview():
    from pumpvision.models import ReviewFinding, ReviewStatus

    statuses = ReviewStatus.query.order_by(ReviewStatus.grp, ReviewStatus.component).all()
    status_counts = {
        state: sum(row.status == state for row in statuses)
        for state in ("ok", "warn", "fail")
    }
    latest_check = max((row.checked_at for row in statuses), default=None)
    warnings = (
        ReviewFinding.query
        .filter(
            ReviewFinding.resolved_at.is_(None),
            ReviewFinding.category.in_((
                "data", "maths", "reconciliation", "regulatory", "business",
            )),
        )
        .order_by(ReviewFinding.last_seen.desc())
        .limit(8)
        .all()
    )
    return _render(
        "overview",
        statuses=statuses,
        status_counts=status_counts,
        latest_check=latest_check,
        warnings=warnings,
    )


@super_admin_bp.route("/admin")
@login_required
@super_admin_required
@capability_required(VIEW_ADMIN_DASHBOARD)
def admin_preview():
    from pumpvision.blueprints.dashboard.routes import dashboard_view_data

    return _render("admin", admin=dashboard_view_data(first_name="Super Admin"))


@super_admin_bp.route("/manager")
@login_required
@super_admin_required
@capability_required(VIEW_MANAGER_DASHBOARD)
def manager_preview():
    from pumpvision.blueprints.manager.routes import manager_home_data

    return _render("manager", manager=manager_home_data())


@super_admin_bp.route("/attendant")
@login_required
@super_admin_required
@capability_required(VIEW_ATTENDANT_DASHBOARD)
def attendant_preview():
    from pumpvision.blueprints.attendant.routes import attendant_home_data

    return _render("attendant", attendant=attendant_home_data(display_name="Attendant"))


@super_admin_bp.route("/developer")
@login_required
@super_admin_required
@capability_required(VIEW_DEVELOPER_DASHBOARD)
def developer_preview():
    from pumpvision.blueprints.dev.routes import developer_dashboard_data

    return _render("developer", developer=developer_dashboard_data())

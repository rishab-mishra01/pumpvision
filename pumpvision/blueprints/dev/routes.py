import json
from datetime import datetime, timedelta

from flask import Blueprint, flash, redirect, render_template, url_for
from flask_login import login_required

from pumpvision.decorators import developer_required

dev_bp = Blueprint("dev", __name__)

_SEV_ORDER = {"critical": 0, "warn": 1, "info": 2}


@dev_bp.route("/")
@login_required
@developer_required
def index():
    from pumpvision.models import AppSetting, ReviewFinding, ReviewStatus, db

    rows = ReviewFinding.query.filter(ReviewFinding.resolved_at.is_(None)).all()
    rows.sort(key=lambda r: (r.acknowledged_at is not None, _SEV_ORDER.get(r.severity, 9), r.first_seen))
    resolved = ReviewFinding.query.filter(
        ReviewFinding.resolved_at >= datetime.utcnow() - timedelta(days=3)
    ).order_by(ReviewFinding.resolved_at.desc()).limit(30).all()

    s = db.session.get(AppSetting, "review_last_run")
    try:
        last = json.loads(s.value) if s else None
    except ValueError:
        last = None
    counts = {k: sum(1 for r in rows if r.severity == k and r.acknowledged_at is None) for k in _SEV_ORDER}
    board = {}
    for st in ReviewStatus.query.order_by(ReviewStatus.component).all():
        board.setdefault(st.grp, []).append(st)
    return render_template("dev/dashboard.html", board=board, findings=rows, resolved=resolved, last=last,
                           counts=counts, now=datetime.utcnow())


@dev_bp.route("/ack/<int:finding_id>", methods=["POST"])
@login_required
@developer_required
def ack(finding_id):
    from pumpvision.models import ReviewFinding, db

    row = db.session.get(ReviewFinding, finding_id)
    if row and row.resolved_at is None:
        row.acknowledged_at = None if row.acknowledged_at else datetime.utcnow()
        db.session.commit()
    return redirect(url_for("dev.index"))


@dev_bp.route("/run", methods=["POST"])
@login_required
@developer_required
def run_now():
    from pumpvision.services.review import run_review

    s = run_review()
    flash(f"Review done: {s['open']} open, {s['new']} new, {s['resolved']} resolved"
          + (f", checks crashed: {', '.join(s['failed_checks'])}" if s["failed_checks"] else "") + ".",
          "error" if s["failed_checks"] else "ok")
    return redirect(url_for("dev.index"))

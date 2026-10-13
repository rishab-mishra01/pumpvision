"""Authenticated, read-only Live Operations UI and JSON API."""
from __future__ import annotations

import hashlib
import json
from datetime import date

from flask import Blueprint, abort, make_response, render_template, request, url_for
from flask_login import current_user, login_required

from pumpvision.authorization import VIEW_OPS_DASHBOARD
from pumpvision.decorators import capability_required
from pumpvision.models import EntryAudit, User
from pumpvision.services import ops as ops_service


ops_bp = Blueprint("ops", __name__)


def _selected_date(raw: str | None = None) -> date | None:
    value = (raw if raw is not None else request.args.get("cycle_date", "")).strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        abort(400, description="cycle_date must be YYYY-MM-DD")


def _json_response(payload, *, conditional: bool = True, ignore_etag_keys=()):
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True, ensure_ascii=False)
    etag_payload = payload
    if ignore_etag_keys and isinstance(payload, dict):
        etag_payload = {key: value for key, value in payload.items() if key not in ignore_etag_keys}
    etag_body = json.dumps(etag_payload, separators=(",", ":"), sort_keys=True, ensure_ascii=False)
    tag = hashlib.sha256(etag_body.encode("utf-8")).hexdigest()
    if conditional and request.if_none_match.contains(tag):
        response = make_response("", 304)
    else:
        response = make_response(body)
        response.mimetype = "application/json"
        response.set_etag(tag)
    response.headers["Cache-Control"] = "private, no-cache, max-age=0, must-revalidate"
    response.headers["Vary"] = "Cookie"
    return response


def _protected(view):
    return login_required(capability_required(VIEW_OPS_DASHBOARD)(view))


@ops_bp.route("/")
@_protected
def index():
    response = make_response(render_template(
        "ops/dashboard.html",
        cycle_date=_selected_date(),
        ops_config={
            "snapshotUrl": url_for("ops.api_snapshot"),
            "eventsUrl": url_for("ops.api_events"),
            "auditUrl": url_for("ops.api_audit"),
            "role": current_user.role,
        },
    ))
    response.headers["Cache-Control"] = "no-store"
    return response


@ops_bp.route("/api/v1/snapshot")
@_protected
def api_snapshot():
    return _json_response(
        ops_service.snapshot(_selected_date(), current_user.role),
        ignore_etag_keys=("generated_at",),
    )


@ops_bp.route("/api/v1/events")
@_protected
def api_events():
    try:
        after_id = int(request.args.get("after_id", "0"))
        limit = int(request.args.get("limit", "100"))
    except ValueError:
        abort(400, description="after_id and limit must be integers")
    return _json_response(
        ops_service.events_after(after_id, _selected_date(), current_user.role, limit),
        conditional=False,
    )


@ops_bp.route("/api/v1/reconciliation/<date_str>")
@_protected
def api_reconciliation(date_str):
    selected = _selected_date(date_str)
    data = ops_service.snapshot(selected, current_user.role)
    return _json_response({
        "generated_at": data["generated_at"],
        "cycle": data["cycle"],
        "nozzles": data["nozzles"],
        "cng": data["cng"],
    })


@ops_bp.route("/api/v1/alerts")
@_protected
def api_alerts():
    data = ops_service.snapshot(_selected_date(), current_user.role)
    return _json_response({"generated_at": data["generated_at"], "alerts": data["alerts"]})


@ops_bp.route("/api/v1/jobs")
@_protected
def api_jobs():
    data = ops_service.snapshot(_selected_date(), current_user.role)
    return _json_response({"generated_at": data["generated_at"], "sources": data["sources"]})


@ops_bp.route("/api/v1/audit")
@_protected
def api_audit():
    if current_user.role == "manager":
        abort(403)
    try:
        after_id = int(request.args.get("after_id", "0"))
    except ValueError:
        abort(400, description="after_id must be an integer")
    rows = EntryAudit.query.filter(EntryAudit.id > max(0, after_id)).order_by(
        EntryAudit.id.asc()
    ).limit(100).all()
    user_ids = {row.actor_id for row in rows if row.actor_id}
    users = {user.id: (user.first_name or user.username) for user in User.query.filter(User.id.in_(user_ids))} if user_ids else {}
    payload = [{
        "id": row.id,
        "kind": row.kind,
        "entry_id": row.entry_id,
        "action": row.action,
        "op_date": row.op_date.isoformat() if row.op_date else None,
        "reason": row.reason,
        "actor": users.get(row.actor_id, row.actor_role or "System"),
        "actor_role": row.actor_role,
        "created_at": ops_service._utc_iso(row.created_at),
    } for row in rows]
    return _json_response({"audit": payload, "last_id": rows[-1].id if rows else after_id}, conditional=False)

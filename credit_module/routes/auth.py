import os

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required, login_user, logout_user

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/", methods=["GET"])
def index():
    from flask_login import current_user
    if current_user.is_authenticated:
        if current_user.role == "owner":
            return redirect(url_for("owner.dashboard"))
        return redirect(url_for("attendant.log_transaction"))
    return redirect(url_for("auth.login"))


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    from flask_login import current_user
    if current_user.is_authenticated:
        if current_user.role == "owner":
            return redirect(url_for("owner.dashboard"))
        return redirect(url_for("attendant.log_transaction"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        owner_username = os.environ.get("OWNER_USERNAME", "owner")
        owner_password = os.environ.get("OWNER_PASSWORD", "owner123")
        attendant_username = os.environ.get("ATTENDANT_USERNAME", "attendant")
        attendant_password = os.environ.get("ATTENDANT_PASSWORD", "attendant123")

        from app import User

        if username == owner_username and password == owner_password:
            user = User(owner_username, "owner")
            login_user(user)
            return redirect(url_for("owner.dashboard"))
        elif username == attendant_username and password == attendant_password:
            user = User(attendant_username, "attendant")
            login_user(user)
            return redirect(url_for("attendant.log_transaction"))
        else:
            flash("Invalid username or password.", "error")

    return render_template("login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))

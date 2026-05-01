import os

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

auth_bp = Blueprint("auth", __name__)


def _role_home():
    if current_user.role == "attendant":
        return redirect(url_for("attendant.home"))
    return redirect(url_for("dashboard.index"))


@auth_bp.route("/", methods=["GET"])
def index():
    if current_user.is_authenticated:
        return _role_home()
    return redirect(url_for("auth.login"))


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return _role_home()

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        owner_username     = os.environ.get("OWNER_USERNAME",     "owner")
        owner_password     = os.environ.get("OWNER_PASSWORD",     "owner123")
        attendant_username = os.environ.get("ATTENDANT_USERNAME", "attendant")
        attendant_password = os.environ.get("ATTENDANT_PASSWORD", "attendant123")

        from pumpvision.user import User

        if username == owner_username and password == owner_password:
            login_user(User(owner_username, "owner"))
            return redirect(url_for("dashboard.index"))
        elif username == attendant_username and password == attendant_password:
            login_user(User(attendant_username, "attendant"))
            return redirect(url_for("attendant.home"))
        else:
            flash("Invalid username or password.", "error")

    return render_template("auth/login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))

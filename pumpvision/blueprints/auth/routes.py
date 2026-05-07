from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

auth_bp = Blueprint("auth", __name__)


def _role_home():
    if current_user.role == "attendant":
        return redirect(url_for("attendant.home"))
    if current_user.role == "manager":
        return redirect(url_for("manager.home"))
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

        from pumpvision.models import User
        user = User.query.filter_by(username=username).first()

        if user and user.is_active and user.check_password(password):
            login_user(user)
            return _role_home()
        else:
            flash("Invalid username or password.", "error")

    return render_template("auth/login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))

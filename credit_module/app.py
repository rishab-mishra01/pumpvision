import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask
from flask_login import LoginManager

load_dotenv(Path(__file__).parent / ".env")

login_manager = LoginManager()


def create_app():
    app = Flask(__name__)

    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-me-in-production")
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
        "DATABASE_URL", "sqlite:///pumpvision_credit.db"
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # Render/Railway provide DATABASE_URL as postgres:// but SQLAlchemy needs postgresql://
    db_url = app.config["SQLALCHEMY_DATABASE_URI"]
    if db_url.startswith("postgres://"):
        app.config["SQLALCHEMY_DATABASE_URI"] = db_url.replace("postgres://", "postgresql://", 1)

    from models import db
    db.init_app(app)

    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Please log in to access this page."

    from routes.auth import auth_bp
    from routes.owner import owner_bp
    from routes.attendant import attendant_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(owner_bp, url_prefix="/owner")
    app.register_blueprint(attendant_bp, url_prefix="/attendant")

    with app.app_context():
        db.create_all()
        _seed_data()

    return app


def _seed_data():
    from models import db, LocalPrice, AppSetting

    # Seed local_prices if empty
    if LocalPrice.query.count() == 0:
        seed_prices = [
            LocalPrice(product="HS", rate_per_litre=93.40, effective_from=datetime(2024, 1, 1)),
            LocalPrice(product="MS", rate_per_litre=107.99, effective_from=datetime(2024, 1, 1)),
            LocalPrice(product="X2", rate_per_litre=117.33, effective_from=datetime(2024, 1, 1)),
            LocalPrice(product="XG", rate_per_litre=97.10, effective_from=datetime(2024, 1, 1)),
        ]
        db.session.add_all(seed_prices)

    # Seed alert threshold if not set
    if AppSetting.query.get("alert_threshold") is None:
        db.session.add(AppSetting(key="alert_threshold", value="80"))

    db.session.commit()


# User class for Flask-Login (not a DB model — credentials from .env)
from flask_login import UserMixin


class User(UserMixin):
    def __init__(self, user_id, role):
        self.id = user_id
        self.role = role


@login_manager.user_loader
def load_user(user_id):
    owner_username = os.environ.get("OWNER_USERNAME", "owner")
    attendant_username = os.environ.get("ATTENDANT_USERNAME", "attendant")

    if user_id == owner_username:
        return User(user_id, "owner")
    if user_id == attendant_username:
        return User(user_id, "attendant")
    return None


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)

import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask

load_dotenv(Path(__file__).parent.parent / ".env")


def create_app():
    app = Flask(
        __name__,
        template_folder="templates",
        instance_path=str(Path(__file__).parent.parent / "instance"),
    )

    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-me-in-production")
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
        "DATABASE_URL", "sqlite:///pumpvision.db"
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db_url = app.config["SQLALCHEMY_DATABASE_URI"]
    if db_url.startswith("postgres://"):
        app.config["SQLALCHEMY_DATABASE_URI"] = db_url.replace("postgres://", "postgresql://", 1)

    from .extensions import db, login_manager, migrate

    db.init_app(app)
    login_manager.init_app(app)
    migrate.init_app(app, db)

    from .user import load_user_by_id

    @login_manager.user_loader
    def load_user(user_id):
        return load_user_by_id(user_id)

    from .blueprints.auth.routes import auth_bp
    from .blueprints.dashboard.routes import dashboard_bp
    from .blueprints.credit.owner import credit_bp
    from .blueprints.attendant.routes import attendant_bp
    from .blueprints.paytm.routes import paytm_bp
    from .blueprints.recon.routes import recon_bp
    from .blueprints.meters.routes import meters_bp
    from .blueprints.owner.routes import owner_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(credit_bp, url_prefix="/credit")
    app.register_blueprint(attendant_bp, url_prefix="/attendant")
    app.register_blueprint(paytm_bp, url_prefix="/paytm")
    app.register_blueprint(recon_bp, url_prefix="/recon")
    app.register_blueprint(meters_bp, url_prefix="/meters")
    app.register_blueprint(owner_bp, url_prefix="/owner")

    @app.context_processor
    def inject_notification_count():
        from flask_login import current_user
        if current_user.is_authenticated and current_user.role == "owner":
            from .models import AppNotification
            count = AppNotification.query.filter_by(is_read=False).count()
            return {"unread_notification_count": count}
        return {"unread_notification_count": 0}

    with app.app_context():
        db.create_all()
        _seed_data()

    return app


def _seed_data():
    from .models import db, LocalPrice, AppSetting

    if LocalPrice.query.count() == 0:
        db.session.add_all([
            LocalPrice(product="HS", rate_per_litre=93.40,  effective_from=datetime(2024, 1, 1)),
            LocalPrice(product="MS", rate_per_litre=107.99, effective_from=datetime(2024, 1, 1)),
            LocalPrice(product="X2", rate_per_litre=117.33, effective_from=datetime(2024, 1, 1)),
            LocalPrice(product="XG", rate_per_litre=98.65,  effective_from=datetime(2024, 1, 1)),
        ])

    if db.session.get(AppSetting, "alert_threshold") is None:
        db.session.add(AppSetting(key="alert_threshold", value="80"))

    for nozzle_no in [7, 11, 15, 16, 17, 18]:
        key = f"pump_test_nozzle_{nozzle_no}"
        if db.session.get(AppSetting, key) is None:
            db.session.add(AppSetting(key=key, value="5.0"))

    db.session.commit()

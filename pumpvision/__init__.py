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

    @login_manager.user_loader
    def load_user(user_id):
        from .models import User
        try:
            return db.session.get(User, int(user_id))
        except (ValueError, TypeError):
            return None

    from .blueprints.auth.routes import auth_bp
    from .blueprints.dashboard.routes import dashboard_bp
    from .blueprints.credit.owner import credit_bp
    from .blueprints.attendant.routes import attendant_bp
    from .blueprints.paytm.routes import paytm_bp
    from .blueprints.recon.routes import recon_bp
    from .blueprints.meters.routes import meters_bp
    from .blueprints.owner.routes import owner_bp
    from .blueprints.manager.routes import manager_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(credit_bp, url_prefix="/credit")
    app.register_blueprint(attendant_bp, url_prefix="/attendant")
    app.register_blueprint(paytm_bp, url_prefix="/paytm")
    app.register_blueprint(recon_bp, url_prefix="/recon")
    app.register_blueprint(meters_bp, url_prefix="/meters")
    app.register_blueprint(owner_bp, url_prefix="/owner")
    app.register_blueprint(manager_bp, url_prefix="/manager")

    @app.context_processor
    def inject_notification_count():
        from flask_login import current_user
        if current_user.is_authenticated and current_user.role == "owner":
            from .models import AppNotification
            count = AppNotification.query.filter_by(is_read=False).count()
            return {"unread_notification_count": count}
        return {"unread_notification_count": 0}

    with app.app_context():
        from flask_migrate import upgrade
        upgrade()
        db.create_all()
        _seed_data()

    return app


def _seed_data():
    from werkzeug.security import generate_password_hash
    from .models import db, User, LocalPrice, AppSetting, LubeProduct

    # ── Users ──────────────────────────────────────────────────────────────
    if User.query.count() == 0:
        users_to_seed = [
            User(
                username=os.getenv("OWNER_USERNAME", "admin"),
                password_hash=generate_password_hash(os.getenv("OWNER_PASSWORD", "")),
                role="owner",
                first_name="Rishab",
            ),
            User(
                username=os.getenv("ATTENDANT_USERNAME", "operations"),
                password_hash=generate_password_hash(os.getenv("ATTENDANT_PASSWORD", "")),
                role="attendant",
                first_name="Attendant",
            ),
            User(
                username=os.getenv("MANAGER_USERNAME", "manager"),
                password_hash=generate_password_hash(os.getenv("MANAGER_PASSWORD", "")),
                role="manager",
                first_name="Manager",
            ),
        ]
        for u in users_to_seed:
            db.session.add(u)
        db.session.commit()

    # ── Existing price + settings seeds ────────────────────────────────────
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

    # ── Lube catalogue (44 SKUs from pump_stock + godam_stock Apr 2026) ────
    if LubeProduct.query.count() == 0:
        lube_catalogue = [
            # Engine Oils
            LubeProduct(name="2T Supreme",                      pack_size="1L",    sale_rate=0.0),
            LubeProduct(name="2T Tractor Oil MG 20W40",         pack_size="7.5L",  sale_rate=0.0),
            LubeProduct(name="4T Green Oil",                    pack_size="1L",    sale_rate=0.0),
            LubeProduct(name="4T Green Oil",                    pack_size="900ml", sale_rate=0.0),
            LubeProduct(name="4T Oil",                          pack_size="1L",    sale_rate=0.0),
            LubeProduct(name="Honda Josh",                      pack_size="900ml", sale_rate=0.0),
            LubeProduct(name="Kool Plus",                       pack_size="1L",    sale_rate=0.0),
            LubeProduct(name="Premium 15W40 CF4",               pack_size="1L",    sale_rate=0.0),
            LubeProduct(name="Premium 15W40",                   pack_size="10L",   sale_rate=0.0),
            LubeProduct(name="Premium 15W40",                   pack_size="15L",   sale_rate=0.0),
            LubeProduct(name="Premium 15W40",                   pack_size="20L",   sale_rate=0.0),
            LubeProduct(name="Pride TC 15W40",                  pack_size="7.5L",  sale_rate=0.0),
            LubeProduct(name="Pride TC 15W40",                  pack_size="10L",   sale_rate=0.0),
            LubeProduct(name="Pride XL Plus 15W40",             pack_size="10L",   sale_rate=0.0),
            LubeProduct(name="Pride XL Plus 15W40",             pack_size="15L",   sale_rate=0.0),
            LubeProduct(name="Pride XL Plus 15W40",             pack_size="20L",   sale_rate=0.0),
            LubeProduct(name="Servo FLT CF4 15W40",             pack_size="1L",    sale_rate=0.0),
            LubeProduct(name="Servo FLT CF4 15W40",             pack_size="7.5L",  sale_rate=0.0),
            LubeProduct(name="Servo FLT CF4 15W40",             pack_size="15L",   sale_rate=0.0),
            LubeProduct(name="Servo SMG 20W40",                 pack_size="5L",    sale_rate=0.0),
            LubeProduct(name="Servo SMG 20W40",                 pack_size="7.5L",  sale_rate=0.0),
            LubeProduct(name="Super 20W40 MG",                  pack_size="500ml", sale_rate=0.0),
            LubeProduct(name="Super 20W40 MG",                  pack_size="1L",    sale_rate=0.0),
            LubeProduct(name="Super 20W40 MG",                  pack_size="10L",   sale_rate=0.0),
            LubeProduct(name="Super 20W40 MG",                  pack_size="20L",   sale_rate=0.0),
            LubeProduct(name="Fleet CF4 15W40",                 pack_size="1L",    sale_rate=0.0),
            LubeProduct(name="Fleet CF4 15W40",                 pack_size="5L",    sale_rate=0.0),
            LubeProduct(name="Fleet Supreme CF4 Plus 15W40",    pack_size="15L",   sale_rate=0.0),
            # Gear Oils & Hydraulic
            LubeProduct(name="Gear HP 90",                      pack_size="1L",    sale_rate=0.0),
            LubeProduct(name="Gear HP 90",                      pack_size="5L",    sale_rate=0.0),
            LubeProduct(name="Gear HP 90",                      pack_size="20L",   sale_rate=0.0),
            LubeProduct(name="Hydra Shakti 68",                 pack_size="26L",   sale_rate=0.0),
            LubeProduct(name="System 46",                       pack_size="26L",   sale_rate=0.0),
            LubeProduct(name="System 46",                       pack_size="20L",   sale_rate=0.0),
            LubeProduct(name="System 68 (bucket)",              pack_size="20L",   sale_rate=0.0),
            LubeProduct(name="System 68 (hydraulic)",           pack_size="20L",   sale_rate=0.0),
            LubeProduct(name="System 68",                       pack_size="26L",   sale_rate=0.0),
            # Transmission
            LubeProduct(name="Transfluid A",                    pack_size="1L",    sale_rate=0.0),
            # Brake Fluid
            LubeProduct(name="Brake Oil",                       pack_size="250ml", sale_rate=0.0),
            LubeProduct(name="Brake Oil",                       pack_size="500ml", sale_rate=0.0),
            # Grease
            LubeProduct(name="Grease MP3",                      pack_size="1kg",   sale_rate=0.0),
            LubeProduct(name="Grease MP3",                      pack_size="2kg",   sale_rate=0.0),
            # Urea / AdBlue
            LubeProduct(name="IOC ClearBlue",                   pack_size="20L",   sale_rate=0.0),
            LubeProduct(name="Servo Clear Blue",                pack_size="20L",   sale_rate=0.0),
        ]
        for p in lube_catalogue:
            db.session.add(p)

    db.session.commit()

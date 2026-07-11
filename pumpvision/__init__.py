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
        db_url = app.config["SQLALCHEMY_DATABASE_URI"]

    if db_url.startswith("postgresql"):
        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
            "pool_pre_ping": True,   # discard stale connections before use
            "pool_recycle": 300,     # retire connections after 5 min (< Railway idle timeout)
        }

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

    @app.template_filter('inr')
    def inr_filter(v):
        """Indian rupee format — whole number only. E.g. 467404.41 → '4,67,404'"""
        if v is None:
            return '—'
        neg = v < 0
        s = str(int(abs(v)))
        if len(s) <= 3:
            whole = s
        else:
            last3 = s[-3:]
            rest = s[:-3]
            groups = []
            while rest:
                groups.append(rest[-2:])
                rest = rest[:-2]
            groups.reverse()
            whole = ','.join(groups) + ',' + last3
        return ('-' if neg else '') + whole

    @app.template_filter('thousands')
    def thousands_filter(v):
        """Western comma format, integer. E.g. 4872 → '4,872'"""
        if v is None:
            return '—'
        return f'{int(v):,}'

    @app.template_filter('inr_full')
    def inr_full_filter(v):
        """Indian rupee format with paise. E.g. 467404.41 → '4,67,404.41'"""
        if v is None:
            return '—'
        paise = f'{round((abs(v) - int(abs(v))) * 100):02d}'
        return inr_filter(v) + '.' + paise

    @app.template_filter('dshort')
    def dshort_filter(d):
        """Cross-platform '21 May' — no leading zero, works on Windows + Linux."""
        if d is None:
            return '—'
        return f"{d.day} {d.strftime('%b')}"

    @app.template_filter('dlong')
    def dlong_filter(d):
        """Cross-platform '21 May 2026' — no leading zero, works on Windows + Linux."""
        if d is None:
            return '—'
        return f"{d.day} {d.strftime('%b %Y')}"

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
        from flask_migrate import upgrade
        upgrade()
        _seed_data()

    return app


def _seed_data():
    from werkzeug.security import generate_password_hash
    from .models import db, User, LocalPrice, AppSetting, LubeProduct

    # ── Users — insert if missing; fix password if it was seeded from empty env var ──
    from werkzeug.security import check_password_hash
    _ensure_user = [
        (os.getenv("OWNER_USERNAME",     "admin"),      os.getenv("OWNER_PASSWORD",     ""), "owner",     "Rishab"),
        (os.getenv("ATTENDANT_USERNAME", "operations"), os.getenv("ATTENDANT_PASSWORD", ""), "attendant", "Attendant"),
        (os.getenv("MANAGER_USERNAME",   "manager"),    os.getenv("MANAGER_PASSWORD",   ""), "manager",   "Manager"),
    ]
    changed = False
    for username, password, role, first_name in _ensure_user:
        existing = User.query.filter_by(username=username).first()
        if not existing:
            db.session.add(User(
                username=username,
                password_hash=generate_password_hash(password),
                role=role,
                first_name=first_name,
            ))
            changed = True
        elif password and check_password_hash(existing.password_hash, ""):
            # Was seeded before env var was set — update to the real password now
            existing.password_hash = generate_password_hash(password)
            changed = True
    if changed:
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

    _cng_setting = db.session.get(AppSetting, "cng_rsp_per_kg")
    if _cng_setting is None:
        db.session.add(AppSetting(key="cng_rsp_per_kg", value="93.40"))
    elif _cng_setting.value == "87.00":
        # Correct the old wrong default — safe to overwrite because 87.00 was never
        # a valid RSP and was only seeded by a prior coding error.
        _cng_setting.value = "93.40"

    if db.session.get(AppSetting, "expense_categories") is None:
        db.session.add(AppSetting(key="expense_categories", value="Staff,Maintenance,Utilities,Supplies,Misc"))

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

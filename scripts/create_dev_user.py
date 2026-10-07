"""Create (or reset) the developer login and print a fresh random password once.
    DATABASE_URL=... python scripts/create_dev_user.py [username]"""
import os
import secrets
import sys
from pathlib import Path

os.environ.setdefault("PUMPVISION_SKIP_BOOTSTRAP", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from werkzeug.security import generate_password_hash  # noqa: E402

from pumpvision import create_app  # noqa: E402
from pumpvision.models import User, db  # noqa: E402

name = sys.argv[1] if len(sys.argv) > 1 else "developer"
pw = secrets.token_urlsafe(12)
app = create_app()
with app.app_context():
    u = User.query.filter_by(username=name).first()
    if u and u.role != "developer":
        sys.exit(f"'{name}' exists with role {u.role}; pick another username")
    if u is None:
        u = User(username=name, role="developer", first_name="Developer")
        db.session.add(u)
    u.password_hash = generate_password_hash(pw)
    u.is_active = True
    db.session.commit()
print(f"developer login: {name} / {pw}")

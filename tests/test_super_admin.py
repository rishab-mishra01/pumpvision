import os
import tempfile
import unittest
from html.parser import HTMLParser

from werkzeug.security import generate_password_hash

# Prevent app creation from migrating or seeding any configured database.  The
# suite creates and owns an isolated SQLite database below.
_DB_FILE = tempfile.NamedTemporaryFile(prefix="pumpvision-test-", suffix=".db", delete=False)
_DB_FILE.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_FILE.name}"
os.environ["PUMPVISION_SKIP_BOOTSTRAP"] = "1"

from pumpvision import create_app  # noqa: E402
from pumpvision.authorization import (  # noqa: E402
    DASHBOARD_VIEW_CAPABILITIES,
    ROLE_CAPABILITIES,
    VIEW_ADMIN_DASHBOARD,
    VIEW_ATTENDANT_DASHBOARD,
    VIEW_DEVELOPER_DASHBOARD,
    VIEW_MANAGER_DASHBOARD,
    VIEW_OPS_DASHBOARD,
    capabilities_for_role,
    has_capability,
)
from pumpvision.extensions import db  # noqa: E402
from pumpvision.models import User  # noqa: E402


class _IdAndFormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.forms = 0

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if "id" in values:
            self.ids.append(values["id"])
        if tag == "form":
            self.forms += 1


class SuperAdminTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        with cls.app.app_context():
            db.session.remove()
            db.drop_all()
        os.unlink(_DB_FILE.name)

    def setUp(self):
        with self.app.app_context():
            for role in ("owner", "manager", "attendant", "developer", "super_admin"):
                db.session.add(User(
                    username=role,
                    password_hash=generate_password_hash("testing-password"),
                    role=role,
                    first_name=role.replace("_", " ").title(),
                    is_active=True,
                ))
            db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            for table in reversed(db.metadata.sorted_tables):
                db.session.execute(table.delete())
            db.session.commit()

    def _login_session(self, role):
        with self.app.app_context():
            user_id = User.query.filter_by(username=role).one().id
        with self.client.session_transaction() as session:
            session["_user_id"] = str(user_id)
            session["_fresh"] = True

    def test_capability_matrix_is_explicit_and_super_admin_is_view_only(self):
        self.assertEqual(ROLE_CAPABILITIES["owner"], {VIEW_ADMIN_DASHBOARD, VIEW_OPS_DASHBOARD})
        self.assertEqual(ROLE_CAPABILITIES["manager"], {VIEW_MANAGER_DASHBOARD, VIEW_OPS_DASHBOARD})
        self.assertEqual(ROLE_CAPABILITIES["attendant"], {VIEW_ATTENDANT_DASHBOARD})
        self.assertEqual(ROLE_CAPABILITIES["developer"], {VIEW_DEVELOPER_DASHBOARD, VIEW_OPS_DASHBOARD})
        self.assertEqual(ROLE_CAPABILITIES["super_admin"], DASHBOARD_VIEW_CAPABILITIES | {VIEW_OPS_DASHBOARD})
        self.assertEqual(capabilities_for_role("unknown"), frozenset())
        with self.app.app_context():
            user = User.query.filter_by(role="super_admin").one()
            self.assertTrue(has_capability(user, VIEW_ADMIN_DASHBOARD))
            self.assertFalse(has_capability(user, "dashboard.admin.write"))

    def test_ordinary_roles_cannot_access_super_admin_console(self):
        for role in ("owner", "manager", "attendant", "developer"):
            with self.subTest(role=role):
                self.client = self.app.test_client()
                self._login_session(role)
                self.assertEqual(self.client.get("/super-admin/").status_code, 403)

    def test_super_admin_can_view_every_lazy_tab(self):
        self._login_session("super_admin")
        pages = {
            "/super-admin/": "Operational overview",
            "/super-admin/admin": "Owner dashboard preview",
            "/super-admin/manager": "Manager dashboard preview",
            "/super-admin/attendant": "Attendant dashboard preview",
            "/super-admin/developer": "Developer dashboard preview",
        }
        for path, marker in pages.items():
            with self.subTest(path=path):
                response = self.client.get(path)
                body = response.get_data(as_text=True)
                self.assertEqual(response.status_code, 200)
                self.assertIn("Super Admin", body)
                self.assertIn("Read-only preview", body)
                self.assertIn(marker, body)
                self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_write_endpoints_remain_denied_to_super_admin(self):
        self._login_session("super_admin")
        write_endpoints = (
            "/owner/scan",
            "/manager/expense/",
            "/attendant/shift/submit",
            "/dev/run",
        )
        for path in write_endpoints:
            with self.subTest(path=path):
                self.assertEqual(self.client.post(path, data={}).status_code, 403)

    def test_login_redirects_super_admin_and_preserves_other_roles(self):
        expected = {
            "super_admin": "/super-admin/",
            "owner": "/dashboard",
            "manager": "/manager/",
            "attendant": "/attendant/",
            "developer": "/dev/",
        }
        for role, location in expected.items():
            with self.subTest(role=role):
                client = self.app.test_client()
                response = client.post("/login", data={
                    "username": role,
                    "password": "testing-password",
                })
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.headers["Location"], location)

    def test_same_account_can_stay_logged_in_on_multiple_devices(self):
        """A login/logout in one browser must not replace another browser's session."""
        first_device = self.app.test_client()
        second_device = self.app.test_client()
        credentials = {"username": "owner", "password": "testing-password"}

        self.assertEqual(first_device.post("/login", data=credentials).status_code, 302)
        self.assertEqual(second_device.post("/login", data=credentials).status_code, 302)
        self.assertEqual(first_device.get("/dashboard").status_code, 200)
        self.assertEqual(second_device.get("/dashboard").status_code, 200)

        # Signing out on one machine only clears that machine's cookie.
        self.assertEqual(first_device.get("/logout").status_code, 302)
        self.assertEqual(first_device.get("/dashboard").status_code, 302)
        self.assertEqual(second_device.get("/dashboard").status_code, 200)

    def test_existing_dashboard_routes_still_render_for_their_roles(self):
        routes = {
            "owner": "/dashboard",
            "manager": "/manager/",
            "attendant": "/attendant/",
            "developer": "/dev/",
        }
        for role, path in routes.items():
            with self.subTest(role=role):
                self.client = self.app.test_client()
                self._login_session(role)
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_preview_templates_have_unique_ids_and_no_forms(self):
        self._login_session("super_admin")
        for path in (
            "/super-admin/", "/super-admin/admin", "/super-admin/manager",
            "/super-admin/attendant", "/super-admin/developer",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                parser = _IdAndFormParser()
                parser.feed(response.get_data(as_text=True))
                self.assertEqual(len(parser.ids), len(set(parser.ids)))
                self.assertEqual(parser.forms, 0)


if __name__ == "__main__":
    unittest.main()

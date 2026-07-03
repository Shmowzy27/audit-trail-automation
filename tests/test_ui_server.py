"""Safety + behavior tests for the review-UI server endpoints.

These lock in the guarantees that matter most for the in-UI environment switch and
the audit viewer:
  - entering PRODUCTION requires an explicit typed confirmation (sandbox-before-production),
  - guard paths never rewrite the active .env,
  - the audit viewer refuses path traversal and only serves its own backups.

The tests exercise only read-only and early-return guard paths, so they never modify
the real .env on the machine running them.
"""

import os
import tempfile
from pathlib import Path
from unittest import TestCase

# Use the tracked example config so importing the server doesn't depend on the
# local (gitignored) config.json being present.
os.environ.setdefault("QBO_CONFIG_FILE", "config.example.json")

from ui import server  # noqa: E402  (import after setting QBO_CONFIG_FILE)


class UiServerEnvSwitchTests(TestCase):
    def setUp(self):
        self.client = server.app.test_client()
        self._saved_env = os.environ.get("QBO_ENVIRONMENT")
        os.environ["QBO_ENVIRONMENT"] = "sandbox"
        # Hermetic .env.* in a temp dir so the switch guards don't depend on the
        # machine's real env files (which never ship to a public checkout).
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        (root / ".env").write_text("QBO_ENVIRONMENT=sandbox\n", encoding="utf-8")
        (root / ".env.sandbox").write_text("QBO_ENVIRONMENT=sandbox\n", encoding="utf-8")
        (root / ".env.production").write_text("QBO_ENVIRONMENT=production\n", encoding="utf-8")
        self._saved_root, self._saved_envfile = server.PROJECT_ROOT, server.ENV_FILE
        server.PROJECT_ROOT, server.ENV_FILE = root, root / ".env"
        self._env_mtime = server.ENV_FILE.stat().st_mtime_ns

    def tearDown(self):
        server.PROJECT_ROOT, server.ENV_FILE = self._saved_root, self._saved_envfile
        self._tmp.cleanup()
        if self._saved_env is None:
            os.environ.pop("QBO_ENVIRONMENT", None)
        else:
            os.environ["QBO_ENVIRONMENT"] = self._saved_env

    def _assert_env_untouched(self):
        self.assertEqual(
            server.ENV_FILE.stat().st_mtime_ns, self._env_mtime,
            "guard path must not rewrite the active .env",
        )

    def test_env_endpoint_reports_environment_and_variants(self):
        body = self.client.get("/api/env").get_json()
        self.assertEqual(body["environment"], "sandbox")
        self.assertIn("sandbox", body["variants"])
        self.assertIn("production", body["variants"])

    def test_switch_to_production_requires_typed_confirmation(self):
        r = self.client.post("/api/env/switch", json={"target": "production"})
        body = r.get_json()
        self.assertFalse(body["ok"])
        self.assertTrue(body["needs_confirm"])
        self._assert_env_untouched()

    def test_switch_to_production_rejects_wrong_confirmation(self):
        r = self.client.post("/api/env/switch", json={"target": "production", "confirm": "yes"})
        body = r.get_json()
        self.assertFalse(body["ok"])
        self.assertTrue(body.get("needs_confirm"))
        self._assert_env_untouched()

    def test_switch_rejects_unknown_target(self):
        body = self.client.post("/api/env/switch", json={"target": "staging"}).get_json()
        self.assertFalse(body["ok"])
        self._assert_env_untouched()

    def test_switch_to_same_environment_is_a_noop(self):
        body = self.client.post("/api/env/switch", json={"target": "sandbox"}).get_json()
        self.assertTrue(body["ok"])
        self.assertFalse(body["switched"])
        self._assert_env_untouched()


class UiServerAuditViewerTests(TestCase):
    def setUp(self):
        self.client = server.app.test_client()

    def test_audit_list_returns_json_list(self):
        body = self.client.get("/api/audits").get_json()
        self.assertIn("audits", body)
        self.assertIsInstance(body["audits"], list)

    def test_audit_detail_rejects_path_traversal(self):
        r = self.client.get("/api/audit", query_string={"file": "../../.env"})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(r.get_json()["ok"])

    def test_audit_detail_rejects_non_audit_name(self):
        r = self.client.get("/api/audit", query_string={"file": "secrets.json"})
        self.assertEqual(r.status_code, 400)

    def test_audit_detail_missing_file_is_404(self):
        r = self.client.get("/api/audit", query_string={"file": "deposit-999-20200101T000000Z.json"})
        self.assertEqual(r.status_code, 404)

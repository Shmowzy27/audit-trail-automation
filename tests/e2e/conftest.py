"""End-to-end UI tests: launch the real Flask app in a background thread and drive
it with a real browser (Playwright).

Hermetic by construction — no test can touch live books:
  - QuickBooks is stubbed out (QBO_TOKEN_FILE points at a nonexistent file, so any
    QuickBooksClient() call fails fast *before* the network),
  - config comes from the tracked config.example.json (not the gitignored real one),
  - the statement + audit folders point at an empty temp dir,
  - the environment is pinned to sandbox.
"""

import os
import tempfile
import threading
from pathlib import Path

import pytest

# Load the example config (not the gitignored real config.json) before importing server.
os.environ.setdefault("QBO_CONFIG_FILE", "config.example.json")

from werkzeug.serving import make_server  # noqa: E402  (import after env is set)
from ui import server  # noqa: E402


@pytest.fixture(scope="session")
def ui_base_url():
    """Serve the real Flask app on an ephemeral port; yield its base URL."""
    os.environ["QBO_ENVIRONMENT"] = "sandbox"
    os.environ["QBO_TOKEN_FILE"] = str(Path(tempfile.gettempdir()) / "no_such_qbo_token.json")

    tmp = tempfile.TemporaryDirectory()
    empty = Path(tmp.name)
    saved = (server.STATEMENT_FOLDER, server.AUDIT_DIR)
    server.STATEMENT_FOLDER = empty   # /api/pdfs -> []
    server.AUDIT_DIR = empty          # /api/audits -> []

    httpd = make_server("127.0.0.1", 0, server.app)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()
        thread.join(timeout=5)
        server.STATEMENT_FOLDER, server.AUDIT_DIR = saved
        tmp.cleanup()

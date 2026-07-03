"""Fetch Keyrenter owner statements from the AppFolio owner portal.

AppFolio's owner portal has no API, so this drives a real (Chromium) browser via
Playwright. It is deliberately split into small, recoverable steps:

- ``login()``  — opens a VISIBLE browser; the user logs in manually once (email
  codes / 2FA all work naturally). The session cookies are saved to
  ``secrets/appfolio_state.json`` and reused headlessly afterwards.
- ``dump()``   — saves a screenshot + HTML of the statements page under
  ``runtime/appfolio-dump/`` so page-structure changes can be diagnosed and the
  selectors refined without guessing.
- ``fetch()``  — reuses the saved session, opens the statements page, downloads
  the statement PDFs into an inbox folder, then hands them to the SAME
  ``ingest_folder`` staging used for manual downloads (content-based dedupe, so
  re-downloading old statements is harmless).

If the saved session has expired, ``fetch`` reports it plainly and asks for a
fresh ``--login`` instead of failing cryptically. Browser automation is fragile
by nature — that is why it feeds the proven drop-folder path rather than doing
anything clever itself.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from .intake import ingest_folder

PORTAL_STATEMENTS_URL = "https://example-portal.appfolio.com/oportal/statements"
STATE_FILE = Path("secrets/appfolio_state.json")
DEFAULT_INBOX = Path("runtime/appfolio-inbox")
DUMP_FOLDER = Path("runtime/appfolio-dump")


def _playwright():
    """Import Playwright lazily so the rest of the app never depends on it."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - environment-specific
        raise RuntimeError(
            "Playwright is not installed. Run:\n"
            "  pip install playwright\n"
            "  playwright install chromium"
        ) from exc
    return sync_playwright


def login(portal_url: str = PORTAL_STATEMENTS_URL, state_file: Path = STATE_FILE) -> dict:
    """Open a visible browser for a one-time manual portal login; save the session."""
    sync_playwright = _playwright()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(portal_url)
        print("\nA browser window is open.")
        print("1. Log in to the owner portal (email code / password — whatever it asks).")
        print("2. Make sure you can SEE the statements list.")
        input("3. Then come back here and press Enter to save the session... ")
        state_file.parent.mkdir(parents=True, exist_ok=True)
        context.storage_state(path=str(state_file))
        browser.close()
    return {"mode": "appfolio-login", "status": "session_saved", "state_file": str(state_file)}


def login_ui(
    portal_url: str = PORTAL_STATEMENTS_URL,
    state_file: Path = STATE_FILE,
    timeout_seconds: int = 300,
) -> dict:
    """Browser-driven login for the Connectors UI (no terminal interaction).

    Opens a visible browser; tries the .env credential auto-login first; if the
    portal still wants a human (verification code, new password), the window
    stays open and we simply WAIT until the user has finished signing in (or the
    timeout passes), then save the session.
    """
    import os
    import time

    sync_playwright = _playwright()

    # Headless first: with .env credentials the login usually needs no window at
    # all. Only fall back to a visible browser when a human is genuinely needed
    # (no credentials, wrong password, or a device-verification code).
    if os.getenv("APPFOLIO_EMAIL", "").strip() and os.getenv("APPFOLIO_PASSWORD", "").strip():
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()
            page.goto(portal_url, wait_until="networkidle")
            status = "ok" if not _looks_logged_out(page) else _auto_login(page, portal_url)
            if status == "ok":
                state_file.parent.mkdir(parents=True, exist_ok=True)
                context.storage_state(path=str(state_file))
                browser.close()
                return {"status": "connected", "detail": "Portal session saved (credential login, no window needed)."}
            browser.close()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(portal_url, wait_until="networkidle")
        if _looks_logged_out(page):
            _auto_login(page, portal_url)
        deadline = time.time() + timeout_seconds
        while _looks_logged_out(page) and time.time() < deadline:
            page.wait_for_timeout(2000)   # user is signing in inside the window
        logged_in = not _looks_logged_out(page)
        if logged_in:
            state_file.parent.mkdir(parents=True, exist_ok=True)
            context.storage_state(path=str(state_file))
        browser.close()
    if logged_in:
        return {"status": "connected", "detail": "Portal session saved."}
    return {
        "status": "timeout",
        "detail": f"Sign-in was not completed within {timeout_seconds // 60} minutes — try again.",
    }


def _looks_logged_out(page) -> bool:
    url = page.url.lower()
    if any(marker in url for marker in ("sign_in", "login", "users/sign", "log_in")):
        return True
    return page.locator("input[type='password'], input[name*='email' i]").count() > 0


def _verification_wall(page) -> bool:
    """The portal sometimes asks for an emailed verification code on a new device."""
    return "verification" in (page.content() or "").lower() and not _looks_logged_out(page)


def _auto_login(page, portal_url: str) -> str:
    """Try logging in with APPFOLIO_EMAIL / APPFOLIO_PASSWORD from the environment.

    Returns "ok", "no_credentials", "failed", or "verification_required".
    Selectors match the portal's real login form (see runtime/appfolio-dump):
    #user_email / #user_password posting to /oportal/users/log_in.
    """
    import os

    email = os.getenv("APPFOLIO_EMAIL", "").strip()
    password = os.getenv("APPFOLIO_PASSWORD", "").strip()
    if not email or not password:
        return "no_credentials"
    page.fill("#user_email", email)
    page.fill("#user_password", password)
    page.click("input[type='submit'][name='commit']")
    page.wait_for_load_state("networkidle")
    page.goto(portal_url, wait_until="networkidle")
    if _verification_wall(page):
        return "verification_required"
    return "ok" if not _looks_logged_out(page) else "failed"


def _open_statements(p, *, portal_url: str, state_file: Path, headless: bool):
    """Open the statements page, reusing the saved session and self-healing an
    expired one via env-credential login (refreshed session is saved back)."""
    browser = p.chromium.launch(headless=headless)
    context = browser.new_context(
        storage_state=str(state_file) if state_file.exists() else None,
        accept_downloads=True,
    )
    page = context.new_page()
    page.goto(portal_url, wait_until="networkidle")
    login_status = "ok"
    if _looks_logged_out(page):
        login_status = _auto_login(page, portal_url)
        if login_status == "ok":
            state_file.parent.mkdir(parents=True, exist_ok=True)
            context.storage_state(path=str(state_file))
    return browser, context, page, login_status


def dump(
    *,
    portal_url: str = PORTAL_STATEMENTS_URL,
    state_file: Path = STATE_FILE,
    headless: bool = True,
) -> dict:
    """Save a screenshot + HTML of the statements page for selector diagnosis."""
    sync_playwright = _playwright()
    DUMP_FOLDER.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser, _context, page, login_status = _open_statements(
            p, portal_url=portal_url, state_file=state_file, headless=headless
        )
        logged_out = _looks_logged_out(page)
        page.screenshot(path=str(DUMP_FOLDER / "statements.png"), full_page=True)
        (DUMP_FOLDER / "statements.html").write_text(page.content(), encoding="utf-8")
        browser.close()
    return {
        "mode": "appfolio-dump",
        "login_status": login_status,
        "logged_out": logged_out,
        "screenshot": str(DUMP_FOLDER / "statements.png"),
        "html": str(DUMP_FOLDER / "statements.html"),
    }


def extract_zips(inbox: Path) -> list[dict]:
    """Unpack portal ZIPs: each statement period downloads as a .zip with the
    owner-packet PDF inside. Extracted PDFs are prefixed with the zip's name so
    five zips each containing an identically-named PDF cannot overwrite each
    other. Consumed zips are removed; bad zips are reported, not fatal.
    """
    results: list[dict] = []
    for zpath in sorted(inbox.glob("*.zip")):
        try:
            with zipfile.ZipFile(zpath) as archive:
                pdf_members = [
                    m for m in archive.namelist() if m.lower().endswith(".pdf")
                ]
                for member in pdf_members:
                    name = f"{zpath.stem} - {Path(member).name}"
                    (inbox / name).write_bytes(archive.read(member))
                    results.append({"zip": zpath.name, "extracted": name})
                if not pdf_members:
                    results.append({"zip": zpath.name, "extracted": None, "detail": "no PDF inside"})
            zpath.unlink()
        except zipfile.BadZipFile:
            results.append({"zip": zpath.name, "extracted": None, "detail": "not a valid zip"})
    return results


# Candidate download targets, broad on purpose for the first iteration; --dump
# output is used to tighten these to the portal's actual structure.
DOWNLOAD_SELECTORS = [
    "a[href$='.pdf']",
    "a[href*='statement'][href*='download']",
    "a:has-text('Download')",
    "button:has-text('Download')",
    "a:has-text('View Statement')",
]


def fetch(
    *,
    statements_folder: str | Path,
    portal_url: str = PORTAL_STATEMENTS_URL,
    state_file: Path = STATE_FILE,
    inbox: str | Path = DEFAULT_INBOX,
    headless: bool = True,
    max_downloads: int = 12,
    apply: bool = False,
) -> dict:
    """Download recent statement PDFs from the portal, then stage them.

    Downloads land in ``inbox`` either way; staging into the statements folder
    honors the usual dry-run-unless-apply rule. Content-based dedupe makes
    re-downloading already-known statements a no-op.
    """
    sync_playwright = _playwright()
    inbox = Path(inbox)
    inbox.mkdir(parents=True, exist_ok=True)
    downloaded: list[str] = []

    with sync_playwright() as p:
        browser, _context, page, login_status = _open_statements(
            p, portal_url=portal_url, state_file=state_file, headless=headless
        )
        if login_status != "ok" and _looks_logged_out(page):
            browser.close()
            detail = {
                "no_credentials": (
                    "Session expired and no APPFOLIO_EMAIL / APPFOLIO_PASSWORD are set "
                    "in .env for auto-login. Add them, or run with --login."
                ),
                "failed": (
                    "Session expired and the credential auto-login failed — check "
                    "APPFOLIO_EMAIL / APPFOLIO_PASSWORD in .env, or run with --login."
                ),
                "verification_required": (
                    "The portal is asking for an emailed verification code (new device). "
                    "Run once with --login to complete it; the session is saved after."
                ),
            }.get(login_status, "The saved portal session no longer works. Run with --login again.")
            return {"mode": "appfolio-fetch", "status": "session_expired", "login_status": login_status, "detail": detail}

        seen: set[str] = set()
        for selector in DOWNLOAD_SELECTORS:
            for element in page.locator(selector).all():
                if len(downloaded) >= max_downloads:
                    break
                marker = element.get_attribute("href") or (element.inner_text() or selector)
                if marker in seen:
                    continue
                seen.add(marker)
                try:
                    with page.expect_download(timeout=15000) as download_info:
                        element.click()
                    download = download_info.value
                    target = inbox / download.suggested_filename
                    download.save_as(str(target))
                    downloaded.append(target.name)
                except Exception:  # noqa: BLE001 - not every candidate link downloads.
                    continue
        no_candidates = not seen
        if no_candidates:
            # Nothing matched the selectors — capture evidence for diagnosis.
            DUMP_FOLDER.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(DUMP_FOLDER / "statements.png"), full_page=True)
            (DUMP_FOLDER / "statements.html").write_text(page.content(), encoding="utf-8")
        browser.close()

    # The portal delivers each statement period as a ZIP with the packet PDF
    # inside — unpack them (uniquely named) so staging sees plain PDFs.
    extracted = extract_zips(inbox)

    result: dict = {
        "mode": "appfolio-fetch",
        "status": "ok" if downloaded else ("no_download_links_found" if no_candidates else "no_new_downloads"),
        "downloaded": downloaded,
        "extracted": extracted,
        "inbox": str(inbox),
    }
    if no_candidates:
        result["detail"] = (
            "No statement links matched the known selectors. A screenshot and the "
            f"page HTML were saved under {DUMP_FOLDER} — share those to refine the selectors."
        )
    if downloaded or extracted:
        result["staging"] = ingest_folder(inbox, statements_folder, apply=apply)
    return result

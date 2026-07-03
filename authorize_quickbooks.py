from __future__ import annotations

import json
import os
import secrets
import time
import webbrowser
from base64 import b64encode
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from dotenv import load_dotenv


def required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing {name} in .env")
    return value


def start_authorization() -> dict:
    """Build the Intuit consent URL and reserve the local callback listener.

    Split from the blocking wait so a UI can open the URL in the SAME browser
    the user is already in, instead of webbrowser.open() picking the OS default.
    """
    load_dotenv()
    client_id = required("QBO_CLIENT_ID")
    client_secret = required("QBO_CLIENT_SECRET")
    redirect_uri = required("QBO_REDIRECT_URI")
    token_file = Path(os.getenv("QBO_TOKEN_FILE", "secrets/qbo_token.json"))
    state = secrets.token_urlsafe(32)

    parsed_redirect = urlparse(redirect_uri)
    callback_host = "localhost"
    callback_port = int(os.getenv("QBO_LOCAL_CALLBACK_PORT", "8000"))
    if parsed_redirect.hostname in {"localhost", "127.0.0.1"}:
        callback_host = parsed_redirect.hostname or callback_host
        callback_port = parsed_redirect.port or callback_port

    authorization_url = "https://appcenter.intuit.com/connect/oauth2?" + urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "scope": "com.intuit.quickbooks.accounting",
            "redirect_uri": redirect_uri,
            "state": state,
        }
    )

    callback: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            values = parse_qs(urlparse(self.path).query)
            callback.update({key: items[0] for key, items in values.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"QuickBooks authorization received. You can close this window."
            )

        def log_message(self, _format, *_args):
            return

    try:
        server = HTTPServer((callback_host, callback_port), Handler)
    except OSError as exc:
        raise RuntimeError(
            f"Callback port {callback_port} is busy — a previous connect may still be "
            "waiting for a sign-in. Restart the UI server (or finish/close that sign-in) "
            "and try again."
        ) from exc
    server.timeout = 300  # don't wait forever if the sign-in is abandoned

    return {
        "authorization_url": authorization_url,
        "server": server,
        "callback": callback,
        "state": state,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "token_file": token_file,
    }


def finish_authorization(ctx: dict) -> dict:
    """Wait for the Intuit redirect, validate the environment, save the token."""
    server: HTTPServer = ctx["server"]
    callback: dict[str, str] = ctx["callback"]
    try:
        server.handle_request()
    finally:
        server.server_close()

    if not callback:
        raise RuntimeError(
            "Timed out (5 minutes) waiting for the Intuit sign-in to complete. "
            "Nothing was saved — try Connect again and finish the consent page."
        )
    if callback.get("state") != ctx["state"]:
        raise RuntimeError("OAuth state mismatch; authorization was rejected.")
    if "error" in callback:
        raise RuntimeError(f"QuickBooks authorization failed: {callback['error']}")

    basic = b64encode(f"{ctx['client_id']}:{ctx['client_secret']}".encode()).decode()
    response = requests.post(
        "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer",
        headers={
            "Authorization": f"Basic {basic}",
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "authorization_code",
            "code": callback["code"],
            "redirect_uri": ctx["redirect_uri"],
        },
        timeout=30,
    )
    response.raise_for_status()
    tokens = response.json()
    tokens["realm_id"] = callback["realmId"]
    tokens["expires_at"] = time.time() + int(tokens["expires_in"])

    # SAFETY GATE: only accept a company that actually answers on the API host
    # for the CURRENT environment. Intuit's consent screen lists every company
    # the signed-in user can access — live ones included — so with
    # QBO_ENVIRONMENT=sandbox, picking the live company must be rejected, not
    # silently saved.
    environment = os.getenv("QBO_ENVIRONMENT", "sandbox").strip().lower()
    base_url = (
        "https://sandbox-quickbooks.api.intuit.com"
        if environment == "sandbox"
        else "https://quickbooks.api.intuit.com"
    )
    check = requests.get(
        f"{base_url}/v3/company/{tokens['realm_id']}/companyinfo/{tokens['realm_id']}",
        headers={
            "Authorization": f"Bearer {tokens['access_token']}",
            "Accept": "application/json",
        },
        timeout=30,
    )
    if check.status_code != 200:
        raise RuntimeError(
            f"The company you connected is NOT a {environment.upper()} company "
            f"(API check returned {check.status_code}). Authorization was DISCARDED "
            f"— nothing was saved. Re-run and pick your {environment} company "
            "(e.g. 'Sandbox Company_US_1') in the Intuit window."
        )
    company_name = check.json().get("CompanyInfo", {}).get("CompanyName", "?")

    token_file: Path = ctx["token_file"]
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(json.dumps(tokens, indent=2), encoding="utf-8")
    return {"company": company_name, "environment": environment, "token_file": str(token_file)}


def main() -> None:
    ctx = start_authorization()
    print("Opening QuickBooks authorization in your browser...")
    webbrowser.open(ctx["authorization_url"])
    result = finish_authorization(ctx)
    print(f"Connected {result['environment'].upper()} company: {result['company']}")
    print(f"Saved QuickBooks authorization to {result['token_file']}")


if __name__ == "__main__":
    main()

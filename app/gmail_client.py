from __future__ import annotations

import base64
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def quiet_profile(token_file: str | Path) -> str | None:
    """The connected email address, WITHOUT ever starting an interactive login.

    For status checks: a missing/expired/revoked token returns None instead of a
    consent browser window popping up mid-status-poll.
    """
    path = Path(token_file)
    if not path.exists():
        return None
    try:
        credentials = Credentials.from_authorized_user_file(str(path), SCOPES)
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        if not credentials.valid:
            return None
        service = build("gmail", "v1", credentials=credentials)
        return service.users().getProfile(userId="me").execute().get("emailAddress")
    except Exception:  # noqa: BLE001 - any auth problem just means "not connected".
        return None


def start_gmail_authorization(credentials_file: str | Path) -> dict:
    """Build the Google consent URL and reserve a local callback listener.

    Split from the blocking wait (mirroring the QuickBooks flow) so a UI can
    open the URL in the SAME browser the user is in, instead of the Google
    library launching the OS-default browser.
    """
    flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), SCOPES)
    callback: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            callback.update(
                {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Gmail authorization received. You can close this window.")

        def log_message(self, _format, *_args):
            return

    server = HTTPServer(("localhost", 0), Handler)   # port 0 = pick a free port
    server.timeout = 300
    flow.redirect_uri = f"http://localhost:{server.server_address[1]}/"
    auth_url, _state = flow.authorization_url(access_type="offline", prompt="consent")
    return {"authorization_url": auth_url, "server": server, "callback": callback, "flow": flow}


def finish_gmail_authorization(ctx: dict, token_file: str | Path) -> str:
    """Wait for the Google redirect, exchange the code, save the token.

    Returns the connected email address.
    """
    server: HTTPServer = ctx["server"]
    try:
        server.handle_request()
    finally:
        server.server_close()
    callback = ctx["callback"]
    if not callback:
        raise RuntimeError(
            "Timed out (5 minutes) waiting for the Google sign-in to complete. "
            "Nothing was saved — try Connect again."
        )
    if "error" in callback:
        raise RuntimeError(f"Google authorization failed: {callback['error']}")
    flow: InstalledAppFlow = ctx["flow"]
    flow.fetch_token(code=callback["code"])
    credentials = flow.credentials
    path = Path(token_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(credentials.to_json(), encoding="utf-8")
    service = build("gmail", "v1", credentials=credentials)
    return service.users().getProfile(userId="me").execute().get("emailAddress", "")


class GmailClient:
    def __init__(self, credentials_file: str, token_file: str):
        self.credentials_file = Path(credentials_file)
        self.token_file = Path(token_file)
        self.service = build("gmail", "v1", credentials=self._authorize())

    def _authorize(self) -> Credentials:
        credentials = None
        if self.token_file.exists():
            credentials = Credentials.from_authorized_user_file(
                str(self.token_file), SCOPES
            )
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        if not credentials or not credentials.valid:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(self.credentials_file), SCOPES
            )
            credentials = flow.run_local_server(port=0)
        self.token_file.parent.mkdir(parents=True, exist_ok=True)
        self.token_file.write_text(credentials.to_json(), encoding="utf-8")
        return credentials

    def profile(self) -> dict:
        """Who we are actually connected as (emailAddress, message counts)."""
        return self.service.users().getProfile(userId="me").execute()

    def search(self, query: str, max_results: int = 25) -> list[dict]:
        response = (
            self.service.users()
            .messages()
            .list(userId="me", q=query, maxResults=max_results)
            .execute()
        )
        return response.get("messages", [])

    def get_message(self, message_id: str) -> dict:
        return (
            self.service.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )

    def pdf_attachments(self, message_id: str) -> list[tuple[str, bytes]]:
        message = self.get_message(message_id)
        attachments: list[tuple[str, bytes]] = []

        def walk(part: dict) -> None:
            filename = part.get("filename", "")
            mime_type = part.get("mimeType", "")
            body = part.get("body", {})
            if filename and (
                mime_type == "application/pdf" or filename.lower().endswith(".pdf")
            ):
                encoded = body.get("data")
                if not encoded and body.get("attachmentId"):
                    attachment = (
                        self.service.users()
                        .messages()
                        .attachments()
                        .get(
                            userId="me",
                            messageId=message_id,
                            id=body["attachmentId"],
                        )
                        .execute()
                    )
                    encoded = attachment["data"]
                if encoded:
                    attachments.append((filename, base64.urlsafe_b64decode(encoded)))
            for child in part.get("parts", []):
                walk(child)

        walk(message.get("payload", {}))
        return attachments

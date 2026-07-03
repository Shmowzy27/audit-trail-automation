"""Local review-and-approve web UI for the owner-statement automation.

This is a THIN layer over the existing engine. It never bypasses a safety check:
- "Preview" runs the normal dry run (no QuickBooks changes).
- "Apply" goes through the exact screening + approval + post-write verification path.
- Defaults to whatever QBO_ENVIRONMENT says (sandbox unless deliberately changed).

Run it:
    python -m ui.server
Then open http://127.0.0.1:5000 in a browser.

Point it at your statement folder with the STATEMENT_FOLDER env var; it defaults to the
sibling "Quickbooks Automation Testing" folder used during development.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_file, send_from_directory

from app.config import load_config, property_config
from app.parser import parse_statement_pdf
from app.pdf_order import pdf_sort_key
from app.service import process_pdf
from app.approvals import create_screening_approval
from app.gmail_client import quiet_profile
from app.qbo_inspect import read_accounts, read_customers, read_deposit, read_deposits
from app.quickbooks import QuickBooksClient, QuickBooksError

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
RUNTIME = PROJECT_ROOT / "runtime"
RUNTIME.mkdir(exist_ok=True)
AUDIT_DIR = RUNTIME / "audit"
ENV_FILE = PROJECT_ROOT / ".env"

DEFAULT_FOLDER = (
    r"D:\Project Automation\Quickbooks Automation Testing\Owner Statements 2021-2026"
)
STATEMENT_FOLDER = Path(os.getenv("STATEMENT_FOLDER", DEFAULT_FOLDER)).resolve()
CONFIG = load_config(os.getenv("QBO_CONFIG_FILE", "config.json"))

app = Flask(__name__)


def environment() -> str:
    return os.getenv("QBO_ENVIRONMENT", "sandbox").strip().lower()


_AUDIT_NAME_RE = re.compile(r"deposit-(?P<id>.+)-(?P<stamp>\d{8}T\d{6}Z)\.json$")


def _parse_audit_name(name: str) -> tuple[str, str]:
    """('deposit-544-20260703T024431Z.json') -> ('544', '2026-07-03 02:44 UTC')."""
    m = _AUDIT_NAME_RE.match(name)
    if not m:
        return ("?", "")
    s = m.group("stamp")   # YYYYMMDDTHHMMSSZ
    pretty = f"{s[0:4]}-{s[4:6]}-{s[6:8]} {s[9:11]}:{s[11:13]} UTC"
    return (m.group("id"), pretty)


def _audit_lines(lines: list[dict]) -> list[dict]:
    """Flatten QBO deposit lines to {amount, account, entity} for display."""
    out = []
    for ln in lines:
        if ln.get("DetailType") != "DepositLineDetail":
            continue
        det = ln.get("DepositLineDetail", {}) or {}
        out.append({
            "amount": ln.get("Amount"),
            "account": (det.get("AccountRef") or {}).get("name", ""),
            "entity": (det.get("Entity") or {}).get("name", ""),
        })
    return out


@app.get("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


def _env_variants() -> dict[str, bool]:
    """Which .env.<name> source files exist to switch to."""
    return {name: (PROJECT_ROOT / f".env.{name}").exists() for name in ("sandbox", "production")}


@app.get("/api/env")
def api_env():
    return jsonify({
        "environment": environment(),
        "statement_folder": str(STATEMENT_FOLDER),
        "variants": _env_variants(),
    })


@app.post("/api/env/switch")
def api_env_switch():
    """Point the running app at a different QuickBooks environment by swapping the
    active .env for .env.<target> and hot-reloading it in-process (no restart).

    Safety: switching TO production requires an explicit typed confirmation, refuses
    to run while any connector sign-in job is in flight, and only ever *reads* the
    committed .env.<target> source files. This changes which company the app talks
    to — it does NOT touch the books; every apply still goes through screening +
    approval + post-write verification.
    """
    data = request.get_json(silent=True) or {}
    target = str(data.get("target", "")).strip().lower()
    if target not in ("sandbox", "production"):
        return jsonify({"ok": False, "error": "Target must be 'sandbox' or 'production'."}), 200
    if target == environment():
        return jsonify({"ok": True, "switched": False, "environment": target,
                        "note": f"Already targeting {target.upper()}."})

    source = PROJECT_ROOT / f".env.{target}"
    if not source.exists():
        return jsonify({"ok": False,
                        "error": f".env.{target} not found in the project root — create it first."}), 200

    # A running OAuth/portal job belongs to the OLD environment; don't pull the rug.
    with _CONNECT_LOCK:
        if any(j.get("running") for j in _CONNECT_JOBS.values()):
            return jsonify({"ok": False,
                            "error": "A connector sign-in is running — wait for it to finish, then switch."}), 200

    # Guard: entering production must be deliberate (sandbox-before-production).
    if target == "production" and str(data.get("confirm", "")).strip() != "PRODUCTION":
        return jsonify({"ok": False, "needs_confirm": True,
                        "error": "Type PRODUCTION to confirm switching to the live company."}), 200

    try:
        if ENV_FILE.exists():
            shutil.copy2(ENV_FILE, PROJECT_ROOT / ".env.bak")   # gitignored (.env.*)
        shutil.copy2(source, ENV_FILE)
        load_dotenv(ENV_FILE, override=True)   # hot-reload into this process
    except Exception as exc:  # noqa: BLE001 - surface the reason, change nothing silently.
        return jsonify({"ok": False, "error": f"Switch failed: {str(exc)[:200]}"}), 200

    _STATUS_CACHE.clear()   # connector status must re-probe against the new environment
    status = _quickbooks_status()
    return jsonify({
        "ok": True,
        "switched": True,
        "environment": environment(),
        "quickbooks": status,
        "note": (f"Now targeting {environment().upper()}. QuickBooks: {status['detail']}"
                 + ("" if status["connected"]
                    else " — reconnect QuickBooks in Connectors to sign into this environment.")),
    })


@app.get("/api/audits")
def api_audits():
    """List the pre-apply audit backups written before every QuickBooks apply.

    Each backup is the exact split plan snapshotted just before the write. A sibling
    '<name>-MISMATCH.json' means post-write verification caught a mismatch (the apply
    was refused as unsafe). Both facts are surfaced per row.
    """
    items = []
    if AUDIT_DIR.exists():
        for path in sorted(AUDIT_DIR.glob("deposit-*.json"),
                           key=lambda p: p.stat().st_mtime, reverse=True):
            if path.name.endswith("-MISMATCH.json"):
                continue   # shown as a flag on the plan it belongs to, not its own row
            try:
                plan = json.loads(path.read_text(encoding="utf-8"))
            except Exception:   # noqa: BLE001 - skip an unreadable/partial backup
                continue
            deposit_id, stamp = _parse_audit_name(path.name)
            mismatch = path.with_name(f"{path.stem}-MISMATCH.json").exists()
            items.append({
                "file": path.name,
                "deposit_id": plan.get("deposit_id", deposit_id),
                "when": stamp,
                "total": plan.get("deposit_total"),
                "line_count": plan.get("split_line_count"),
                "replaced": plan.get("replacing_existing_line_count"),
                "status": "mismatch" if mismatch else "applied",
            })
    return jsonify({"audits": items, "count": len(items)})


@app.get("/api/audit")
def api_audit():
    """Detail of one audit backup: the proposed split lines and the original deposit."""
    name = Path(request.args.get("file", "")).name   # strip any directory component
    if not (name.startswith("deposit-") and name.endswith(".json")) or "MISMATCH" in name:
        return jsonify({"ok": False, "error": "Bad audit file name."}), 400
    path = AUDIT_DIR / name
    if not path.is_file():
        return jsonify({"ok": False, "error": "Audit file not found."}), 404
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": f"Unreadable backup: {str(exc)[:160]}"}), 200

    payload = plan.get("update_payload", {})
    original = plan.get("original_deposit", {})
    deposit_id, stamp = _parse_audit_name(name)
    return jsonify({
        "ok": True,
        "file": name,
        "deposit_id": plan.get("deposit_id", deposit_id),
        "when": stamp,
        "txn_date": payload.get("TxnDate"),
        "deposit_account": (payload.get("DepositToAccountRef") or {}).get("name"),
        "total": plan.get("deposit_total"),
        "line_count": plan.get("split_line_count"),
        "private_note": payload.get("PrivateNote", ""),
        "mismatch": path.with_name(f"{path.stem}-MISMATCH.json").exists(),
        "lines": _audit_lines(payload.get("Line", [])),
        "original_lines": _audit_lines(original.get("Line", [])),
    })


@app.get("/api/pdfs")
def api_pdfs():
    pdfs = []
    if STATEMENT_FOLDER.exists():
        for p in sorted(STATEMENT_FOLDER.rglob("*.pdf"), key=pdf_sort_key):
            pdfs.append({"name": p.name, "path": str(p), "folder": p.parent.name})
    return jsonify({"pdfs": pdfs, "count": len(pdfs)})


def _safe_pdf_path(raw: str) -> Path | None:
    """Only allow serving PDFs that live under STATEMENT_FOLDER."""
    try:
        candidate = Path(raw).resolve()
    except Exception:
        return None
    if candidate.suffix.lower() != ".pdf" or not candidate.is_file():
        return None
    if STATEMENT_FOLDER not in candidate.parents and candidate != STATEMENT_FOLDER:
        return None
    return candidate


@app.get("/vendor/<path:filename>")
def vendor(filename):
    """Serve the locally vendored PDF.js library (works behind firewalls / offline)."""
    resp = send_from_directory(BASE_DIR / "vendor", filename)
    if filename.endswith(".mjs"):
        resp.headers["Content-Type"] = "text/javascript"   # ES modules need a JS mime
    return resp


@app.get("/api/pdf")
def api_pdf():
    path = _safe_pdf_path(request.args.get("path", ""))
    if not path:
        return jsonify({"error": "PDF not found or not allowed."}), 404
    return send_file(str(path), mimetype="application/pdf")


@app.get("/api/pdf-text")
def api_pdf_text():
    """Per-page extracted text of a packet, for the in-app 'search this packet' box."""
    path = _safe_pdf_path(request.args.get("path", ""))
    if not path:
        return jsonify({"ok": False, "error": "PDF not found or not allowed."}), 404
    import pdfplumber

    pages = []
    try:
        with pdfplumber.open(str(path)) as pdf:
            for page in pdf.pages:
                pages.append(page.extract_text(x_tolerance=2, y_tolerance=3) or "")
    except Exception as exc:  # noqa: BLE001 - a bad PDF just yields no searchable text.
        return jsonify({"ok": False, "error": str(exc)[:160]}), 200
    return jsonify({"ok": True, "pages": pages})


def _curate(dry: dict) -> dict:
    """Shape the dry-run result into what the frontend needs."""
    statement = dry.get("statement", {})
    screening = dry.get("screening", {})
    review = dry.get("expert_rule_review", {})
    confidence = review.get("confidence", {})
    conf_by_line = {c["line"]: c for c in confidence.get("lines", [])}

    lines = []
    for i, ln in enumerate(screening.get("planned_split_preview", []), start=1):
        c = conf_by_line.get(i, {})
        lines.append(
            {
                "line": ln.get("line_num", i),
                "amount": ln.get("amount"),
                "account": ln.get("account"),
                "customer": ln.get("customer"),
                "category": ln.get("source_category", ""),
                "confidence": c.get("confidence", ""),
                "driver": c.get("driver", ""),
                "needs_account": not ln.get("account"),
            }
        )
    return {
        "statement": {
            "property": statement.get("property_name"),
            "month": statement.get("statement_month"),
            "net": statement.get("stated_net_income"),
        },
        "screening": {
            "status": screening.get("status"),
            "apply_allowed": screening.get("apply_allowed"),
            "reasons": screening.get("reasons", []),
            "total_matches": screening.get("total_matches"),
            "deposit_id": screening.get("deposit_id"),
            "current_total": screening.get("current_deposit_total"),
            "expected_total": screening.get("expected_pdf_total"),
            "summary": screening.get("summary", ""),
        },
        "confidence": {
            "overall": confidence.get("overall"),
            "summary": confidence.get("summary", {}),
            "review_line_count": confidence.get("review_line_count", 0),
        },
        "warnings": review.get("warnings", []),
        "changes": review.get("changes", []),
        "lines": lines,
    }


_ACCOUNTS: list[str] | None = None
_CUSTOMERS: list[str] | None = None


@app.get("/api/accounts")
def api_accounts():
    """Active QuickBooks accounts for the editable Account dropdown.

    Returns name/type/full path so the frontend can group by account type and
    indent sub-accounts — the same structure QuickBooks' own account picker uses,
    instead of one confusing flat alphabetical list of colon-joined names.
    """
    global _ACCOUNTS
    if _ACCOUNTS is None:
        _ACCOUNTS = sorted(
            (
                {
                    "fqn": a["fully_qualified_name"],
                    "name": a.get("name") or a["fully_qualified_name"].split(":")[-1],
                    "type": a.get("account_type") or "Other",
                }
                for a in read_accounts().get("accounts", [])
                if a.get("active") and a.get("fully_qualified_name")
            ),
            key=lambda a: a["fqn"].lower(),   # parents sort before their sub-accounts
        )
    return jsonify({"accounts": _ACCOUNTS})


@app.get("/api/customers")
def api_customers():
    """Active QuickBooks customer names, for the editable Customer dropdown."""
    global _CUSTOMERS
    if _CUSTOMERS is None:
        d = read_customers()
        _CUSTOMERS = sorted(
            c["display_name"]
            for c in d.get("customers", [])
            if c.get("active") and c.get("display_name")
        )
    return jsonify({"customers": _CUSTOMERS})


@app.get("/api/deposit")
def api_deposit():
    """Read the deposit's ACTUAL current split lines from QuickBooks (read-only)."""
    dep_id = (request.args.get("id") or "").strip()
    if not dep_id:
        return jsonify({"ok": False, "error": "No deposit id."}), 400
    try:
        d = read_deposit(dep_id)
    except (QuickBooksError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 200
    dep = d.get("deposit", {})
    return jsonify(
        {
            "ok": True,
            "deposit_id": dep.get("id"),
            "total": dep.get("total"),
            "line_count": dep.get("line_count"),
            "lines": dep.get("lines", []),
        }
    )


def _known_memo_keywords() -> list[str]:
    """Every property's memo_keywords from config, lowercased — the signal for
    'this deposit came from one of our property managers'."""
    kws: list[str] = []
    for prop in CONFIG.get("properties", {}).values():
        kws.extend(prop.get("memo_keywords", []))
    return [k.lower() for k in kws if k]


@app.get("/api/deposits")
def api_deposits():
    """Browse existing QuickBooks deposits in a date range (read-only).

    Backs the 'look up existing deposit' bar so a reviewer can find a deposit by date
    instead of relying on the amount-search. Each result already carries its current
    split lines, so inspection needs no extra call.
    """
    start = (request.args.get("start") or "").strip()
    end = (request.args.get("end") or "").strip()
    if not start or not end:
        return jsonify({"ok": False, "error": "Pick a From and To date."}), 400
    memo = (request.args.get("memo") or "").strip() or None
    # Default to only the configured property managers (Keyrenter / Sample PM),
    # matched by the same memo_keywords the auto-matcher uses. scope=all shows
    # every deposit (payroll, Amazon, restaurants, etc.).
    scope = (request.args.get("scope") or "known").strip().lower()
    try:
        d = read_deposits(start, end, memo_keyword=memo)
    except (QuickBooksError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 200
    deposits = d.get("deposits", [])
    if scope != "all":
        keywords = _known_memo_keywords()
        if keywords:
            deposits = [
                dep for dep in deposits
                if any(k in json.dumps(dep).lower() for k in keywords)
            ]
    return jsonify({"ok": True, "count": len(deposits), "deposits": deposits, "scope": scope})


# ---------------------------------------------------------------------------
# Connectors: one place to see and (re)connect Gmail, QuickBooks, and the
# Keyrenter AppFolio portal. Connect actions open the provider's own sign-in
# window on this computer; they run as background jobs the UI polls.
# ---------------------------------------------------------------------------

_CONNECT_JOBS: dict[str, dict] = {}
_CONNECT_LOCK = threading.Lock()
_STATUS_CACHE: dict[str, tuple[float, dict]] = {}
_STATUS_TTL = 120  # seconds — status probes hit real APIs; don't spam them.


def _cached(name: str, probe):
    now = time.time()
    hit = _STATUS_CACHE.get(name)
    if hit and now - hit[0] < _STATUS_TTL:
        return hit[1]
    value = probe()
    _STATUS_CACHE[name] = (now, value)
    return value


def _gmail_status() -> dict:
    cred = os.getenv("GOOGLE_CREDENTIALS_FILE", "")
    token = os.getenv("GOOGLE_TOKEN_FILE", "secrets/google_token.json")
    if not cred or not Path(cred).exists():
        return {"connected": False, "detail": "Setup needed: GOOGLE_CREDENTIALS_FILE missing."}
    email = quiet_profile(token)
    if email:
        return {"connected": True, "detail": f"Connected as {email}"}
    return {"connected": False, "detail": "Not connected."}


def _quickbooks_status() -> dict:
    try:
        client = QuickBooksClient()
        # QuickBooksClient.query() already unwraps the QueryResponse envelope.
        info = client.query("select * from CompanyInfo").get("CompanyInfo", [{}])[0]
        return {
            "connected": True,
            "detail": f"Connected: {info.get('CompanyName', '?')} ({environment()})",
        }
    except Exception as exc:  # noqa: BLE001 - any failure = not connected, with the reason.
        return {"connected": False, "detail": str(exc)[:160]}


def _keyrenter_status() -> dict:
    from app.appfolio import STATE_FILE

    creds_set = bool(os.getenv("APPFOLIO_EMAIL", "").strip()) and bool(
        os.getenv("APPFOLIO_PASSWORD", "").strip()
    )
    suffix = " · auto-login ready" if creds_set else " · no auto-login credentials in .env"
    if STATE_FILE.exists():
        saved = datetime.fromtimestamp(STATE_FILE.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        return {"connected": True, "detail": f"Portal session saved {saved}{suffix}"}
    return {"connected": False, "detail": f"No saved portal session{suffix}"}


@app.get("/api/connectors")
def api_connectors():
    connectors = [
        {"id": "gmail", "name": "Gmail — Nashville packets", **_cached("gmail", _gmail_status)},
        {"id": "quickbooks", "name": f"QuickBooks Online — {environment().upper()}", **_cached("quickbooks", _quickbooks_status)},
        {"id": "keyrenter", "name": "Keyrenter portal (AppFolio)", **_cached("keyrenter", _keyrenter_status)},
    ]
    jobs = {k: {"running": v["running"], "result": v.get("result")} for k, v in _CONNECT_JOBS.items()}
    return jsonify({"connectors": connectors, "jobs": jobs})


def _start_job(name: str, target) -> bool:
    with _CONNECT_LOCK:
        if _CONNECT_JOBS.get(name, {}).get("running"):
            return False
        _CONNECT_JOBS[name] = {"running": True, "result": None}

    def wrapper():
        try:
            result = target()
        except Exception as exc:  # noqa: BLE001 - surface the reason to the UI.
            result = {"status": "error", "detail": str(exc)[:300]}
        _CONNECT_JOBS[name] = {"running": False, "result": result}
        _STATUS_CACHE.pop(name, None)   # re-probe on the next status call

    threading.Thread(target=wrapper, daemon=True).start()
    return True


@app.post("/api/connectors/<name>/connect")
def api_connector_connect(name: str):
    if name == "gmail":
        # Same-browser consent, like QuickBooks: build the URL here, return it,
        # and let the PAGE open it; a thread waits for the redirect.
        from app.gmail_client import finish_gmail_authorization, start_gmail_authorization

        cred = os.getenv("GOOGLE_CREDENTIALS_FILE", "")
        if not cred or not Path(cred).exists():
            return jsonify({"ok": False, "error": "GOOGLE_CREDENTIALS_FILE is missing — see the Gmail setup steps."}), 200
        try:
            ctx = start_gmail_authorization(cred)
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(exc)[:300]}), 200

        def go():
            email = finish_gmail_authorization(
                ctx, os.getenv("GOOGLE_TOKEN_FILE", "secrets/google_token.json")
            )
            return {"status": "connected", "detail": f"Connected as {email}"}

        started = _start_job(name, go)
        if not started:
            ctx["server"].server_close()
            return jsonify({"ok": True, "started": False, "note": "A Gmail connect is already in progress."})
        return jsonify({
            "ok": True,
            "started": True,
            "auth_url": ctx["authorization_url"],
            "note": "Complete the Google sign-in in the tab that opened (sign in as the packets mailbox).",
        })
    elif name == "quickbooks":
        # Build the consent URL here and return it, so the PAGE opens it in the
        # SAME browser the user is in (webbrowser.open would pick the OS default).
        import authorize_quickbooks

        try:
            ctx = authorize_quickbooks.start_authorization()
        except RuntimeError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 200

        def go():
            result = authorize_quickbooks.finish_authorization(ctx)
            return {
                "status": "connected",
                "detail": f"Connected {result['environment'].upper()}: {result['company']}",
            }

        started = _start_job(name, go)
        if not started:
            ctx["server"].server_close()
            return jsonify({"ok": True, "started": False,
                            "note": "A QuickBooks connect is already in progress."})
        return jsonify({
            "ok": True,
            "started": True,
            "auth_url": ctx["authorization_url"],
            "note": (
                f"Complete the Intuit consent in the tab that opened — pick your "
                f"{environment().upper()} company. A wrong-environment company is rejected."
            ),
        })
    elif name == "keyrenter":
        def go():
            from app.appfolio import login_ui

            return login_ui()
    else:
        return jsonify({"ok": False, "error": "Unknown connector."}), 404
    started = _start_job(name, go)
    note = "A sign-in window will open on this computer — finish the login there."
    if name == "quickbooks":
        note = (
            f"A sign-in window will open — pick your {environment().upper()} company in the "
            "Intuit window. A company from the wrong environment is rejected and nothing is saved."
        )
    return jsonify({
        "ok": True,
        "started": started,
        "note": note if started else "A connect for this service is already in progress.",
    })


@app.post("/api/fetch-packets")
def api_fetch_packets():
    """One click: pull new owner packets from Gmail AND the Keyrenter portal,
    stage them into the statements folder, and report what arrived. Runs as a
    background job (the portal fetch drives a browser and takes ~30s)."""
    def go():
        from app.intake import fetch_gmail_packets

        out: dict = {"status": "ok"}
        staged: list[str] = []
        duplicates = 0

        try:
            g = fetch_gmail_packets(CONFIG, STATEMENT_FOLDER, apply=True)
            out["gmail"] = {"connected_as": g.get("connected_as", ""), "new_messages": g.get("new_messages", 0)}
            for message in g.get("messages", []):
                for a in message.get("attachments", []):
                    if a["action"] == "staged":
                        staged.append(Path(a["target"]).name)
                    elif a["action"] == "duplicate":
                        duplicates += 1
        except Exception as exc:  # noqa: BLE001 - report per-source, keep going.
            out["gmail"] = {"error": str(exc)[:200]}

        try:
            from app.appfolio import fetch as fetch_keyrenter

            k = fetch_keyrenter(statements_folder=STATEMENT_FOLDER, apply=True)
            out["keyrenter"] = {"status": k.get("status"), "detail": k.get("detail", "")}
            for item in (k.get("staging") or {}).get("items", []):
                if item["action"] == "staged":
                    staged.append(Path(item["target"]).name)
                elif item["action"] == "duplicate":
                    duplicates += 1
        except Exception as exc:  # noqa: BLE001
            out["keyrenter"] = {"error": str(exc)[:200]}

        out["staged"] = staged
        out["duplicates"] = duplicates
        problems = [s for s in ("gmail", "keyrenter") if "error" in out.get(s, {})]
        summary = (
            f"✓ {len(staged)} new packet(s): {', '.join(staged)}"
            if staged else "No new packets — everything already in the folder."
        )
        if duplicates:
            summary += f" ({duplicates} already-known skipped)"
        if problems:
            summary += f" — PROBLEM with {', '.join(problems)}, check Connectors."
        out["detail"] = summary
        return out

    started = _start_job("fetch_packets", go)
    return jsonify({"ok": True, "started": started})


@app.post("/api/connectors/<name>/disconnect")
def api_connector_disconnect(name: str):
    """Remove the LOCALLY saved sign-in for a connector so a different account
    can be connected. Nothing is changed at the provider — this only deletes the
    token/session file on this computer."""
    with _CONNECT_LOCK:
        if _CONNECT_JOBS.get(name, {}).get("running"):
            return jsonify({"ok": False, "error": "A connect is in progress — finish or wait for it first."}), 200

    note = ""
    if name == "gmail":
        target = Path(os.getenv("GOOGLE_TOKEN_FILE", "secrets/google_token.json"))
    elif name == "quickbooks":
        target = Path(os.getenv("QBO_TOKEN_FILE", "secrets/qbo_token.json"))
    elif name == "keyrenter":
        from app.appfolio import STATE_FILE as target  # noqa: N811

        if os.getenv("APPFOLIO_EMAIL", "").strip():
            note = (
                "Portal session removed. Note: auto-login credentials are still in .env — "
                "to connect a DIFFERENT portal account, update APPFOLIO_EMAIL/PASSWORD there too."
            )
    else:
        return jsonify({"ok": False, "error": "Unknown connector."}), 404

    existed = target.exists()
    if existed:
        target.unlink()
    _CONNECT_JOBS.pop(name, None)
    _STATUS_CACHE.pop(name, None)   # status reflects the disconnect immediately
    return jsonify({
        "ok": True,
        "removed": existed,
        "note": note or (
            "Saved sign-in removed from this computer. Click Connect to sign in "
            "with the same or a different account."
        ),
    })


@app.post("/api/find-deposit")
def api_find_deposit():
    """Find the QuickBooks deposit matching a packet (read-only, no split planning).

    Backs the top-bar "Preview deposit" button: parse the packet, run the same
    amount + date-window search the automation uses, and report the match — or a
    plain "no deposit found" message when none exists.
    """
    data = request.get_json(force=True)
    pdf = data.get("pdf", "")
    if not _safe_pdf_path(pdf):
        return jsonify({"ok": False, "error": "Invalid PDF path."}), 400
    try:
        statement = parse_statement_pdf(pdf)
        settings = property_config(CONFIG, statement.property_name)
        deposit = QuickBooksClient().find_matching_deposit(
            statement, settings, CONFIG.get("deposit_search", {})
        )
    except (QuickBooksError, ValueError) as exc:
        return jsonify({"ok": True, "found": False, "message": str(exc)}), 200
    return jsonify(
        {
            "ok": True,
            "found": True,
            "deposit_id": deposit.get("Id"),
            "statement_net": str(statement.stated_net_income),
            "month": statement.statement_month.isoformat(),
        }
    )


@app.post("/api/preview")
def api_preview():
    data = request.get_json(force=True)
    pdf = data.get("pdf", "")
    if not _safe_pdf_path(pdf):
        return jsonify({"ok": False, "error": "Invalid PDF path."}), 400
    deposit_id = (data.get("deposit_id") or "").strip() or None
    overrides = data.get("overrides") or {}
    try:
        dry = process_pdf(pdf, CONFIG, apply=False, deposit_id=deposit_id, overrides=overrides)
    except (QuickBooksError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 200
    return jsonify({"ok": True, "environment": environment(), **_curate(dry)})


@app.post("/api/apply")
def api_apply():
    data = request.get_json(force=True)
    pdf = data.get("pdf", "")
    if not _safe_pdf_path(pdf):
        return jsonify({"ok": False, "error": "Invalid PDF path."}), 400
    deposit_id = (data.get("deposit_id") or "").strip() or None
    approve = bool(data.get("approve"))
    overrides = data.get("overrides") or {}
    notes = data.get("notes", "") or "Approved via review UI."
    approved_by = data.get("approved_by", "") or "UI reviewer"

    try:
        # Always re-run the dry run first — the apply must agree with what was reviewed
        # (same overrides), so the approval fingerprint matches.
        dry = process_pdf(pdf, CONFIG, apply=False, deposit_id=deposit_id, overrides=overrides)
        screening = dry["screening"]
        status = screening.get("status")
        allow_resplit = status in ("correction_preview", "already_split", "already_split_matches")

        if screening.get("apply_allowed"):
            result = process_pdf(
                pdf, CONFIG, apply=True, deposit_id=deposit_id,
                allow_resplit=allow_resplit, overrides=overrides,
            )
            return jsonify({"ok": True, "status": "applied", "result": result.get("result")})

        if status in ("needs_review", "correction_preview"):
            if not approve:
                return jsonify(
                    {"ok": False, "status": "needs_approval", "screening": _curate(dry)["screening"]}
                )
            review_path = RUNTIME / f"ui-review-{deposit_id or 'auto'}.json"
            review_path.write_text(json.dumps(dry, indent=2), encoding="utf-8")
            approval = create_screening_approval(
                str(review_path), approved_by=approved_by, notes=notes
            )
            approval_path = RUNTIME / f"ui-approval-{deposit_id or 'auto'}.json"
            approval_path.write_text(json.dumps(approval, indent=2), encoding="utf-8")
            result = process_pdf(
                pdf,
                CONFIG,
                apply=True,
                deposit_id=deposit_id,
                allow_resplit=allow_resplit,
                approval_file=str(approval_path),
                overrides=overrides,
            )
            return jsonify({"ok": True, "status": "applied", "result": result.get("result")})

        return jsonify(
            {"ok": False, "status": "blocked", "reasons": screening.get("reasons", [])}
        )
    except (QuickBooksError, ValueError) as exc:
        return jsonify({"ok": False, "status": "error", "error": str(exc)}), 200


if __name__ == "__main__":
    print(f"Environment: {environment().upper()}  |  Statements: {STATEMENT_FOLDER}")
    print("Open http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=False)

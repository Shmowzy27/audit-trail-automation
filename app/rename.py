"""Standardize owner-statement PDF filenames.

Target format:  Owner Packet - <Provider> - <MM-YY> - <Net amount>.pdf
e.g.            Owner Packet - Keyrenter - 08-25 - 4689.89.pdf

- Dry run by default; only `apply=True` renames on disk.
- Idempotent: files already in the target format are skipped, so this can be re-run on
  a folder that gains new packets over time (only the new ones get renamed).
- On apply, a reversible mapping (old -> new) is written under runtime/.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from .parser import parse_statement_pdf

TARGET_RE = re.compile(
    r"^Owner Packet - (Keyrenter|Nashville) - \d{2}-\d{2} - .+\.pdf$", re.IGNORECASE
)


def packet_name(provider: str, statement) -> str:
    """The single naming truth for owner packets: Owner Packet - <Provider> - <MM-YY> - <Net>.pdf"""
    stmt_date = statement.statement_month.strftime("%m-%y")
    return f"Owner Packet - {provider} - {stmt_date} - {statement.stated_net_income}.pdf"


def infer_provider(folder_name: str, property_name: str) -> str:
    lowered_folder = folder_name.lower()
    if "keyrenter" in lowered_folder:
        return "Keyrenter"
    if "nashville" in lowered_folder:
        return "Nashville"
    lowered_property = (property_name or "").lower()
    if "500" in lowered_property or "nashville" in lowered_property:
        return "Nashville"
    return "Keyrenter"


def rename_packets(folder: str | Path, *, provider: str | None = None, apply: bool = False) -> dict:
    folder = Path(folder)
    if not folder.is_dir():
        raise ValueError(f"Not a folder: {folder}")

    pdfs = sorted(folder.glob("*.pdf"))
    used = {p.name for p in pdfs}  # avoid colliding with any existing filename
    planned: list[dict] = []
    skipped: list[str] = []
    failed: list[dict] = []

    for pdf in pdfs:
        if TARGET_RE.match(pdf.name):
            skipped.append(pdf.name)
            continue
        try:
            statement = parse_statement_pdf(pdf)
        except Exception as exc:  # noqa: BLE001 - report every unparseable file cleanly.
            failed.append({"file": pdf.name, "error": str(exc)[:140]})
            continue

        prov = provider or infer_provider(folder.name, statement.property_name)
        name = packet_name(prov, statement)
        base = name[: -len(".pdf")]
        counter = 2
        while name in used and (folder / name) != pdf:
            name = f"{base} ({counter}).pdf"
            counter += 1
        used.add(name)
        if name != pdf.name:
            planned.append({"old": pdf.name, "new": name})
        else:
            skipped.append(pdf.name)

    result = {
        "mode": "rename-packets",
        "folder": str(folder),
        "applied": apply,
        "planned_count": len(planned),
        "skipped_count": len(skipped),
        "failed_count": len(failed),
        "planned": planned,
        "skipped": skipped,
        "failed": failed,
    }

    if apply and planned:
        renamed: list[dict] = []
        for item in planned:
            src = folder / item["old"]
            dst = folder / item["new"]
            if dst.exists() and dst != src:
                failed.append({"file": item["old"], "error": "target already exists"})
                continue
            src.rename(dst)
            renamed.append(item)
        result["renamed_count"] = len(renamed)
        result["failed_count"] = len(failed)
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        mapping_path = Path("runtime") / f"rename-map-{folder.name}-{stamp}.json"
        mapping_path.parent.mkdir(parents=True, exist_ok=True)
        mapping_path.write_text(json.dumps({**result, "renamed": renamed}, indent=2), encoding="utf-8")
        result["mapping_file"] = str(mapping_path)

    return result

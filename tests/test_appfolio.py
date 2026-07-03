"""Tests for the AppFolio zip unpacking (the portal delivers statements as ZIPs)."""

import zipfile
from pathlib import Path

from app.appfolio import extract_zips


def _make_zip(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)


def test_identically_named_pdfs_get_unique_prefixes(tmp_path: Path):
    # Every portal zip contains "Owner Packet.pdf" — they must not overwrite each other.
    _make_zip(tmp_path / "May 2026.zip", {"Owner Packet.pdf": b"may"})
    _make_zip(tmp_path / "Jun 2026.zip", {"Owner Packet.pdf": b"jun"})
    results = extract_zips(tmp_path)
    names = sorted(r["extracted"] for r in results)
    assert names == ["Jun 2026 - Owner Packet.pdf", "May 2026 - Owner Packet.pdf"]
    assert (tmp_path / "May 2026 - Owner Packet.pdf").read_bytes() == b"may"
    assert (tmp_path / "Jun 2026 - Owner Packet.pdf").read_bytes() == b"jun"
    assert not list(tmp_path.glob("*.zip"))   # consumed zips are removed


def test_non_pdf_members_are_skipped_and_bad_zip_reported(tmp_path: Path):
    _make_zip(tmp_path / "mixed.zip", {"Owner Packet.pdf": b"x", "notes.txt": b"y"})
    (tmp_path / "broken.zip").write_bytes(b"not a zip at all")
    results = extract_zips(tmp_path)
    extracted = [r["extracted"] for r in results if r["extracted"]]
    assert extracted == ["mixed - Owner Packet.pdf"]
    assert any(r.get("detail") == "not a valid zip" for r in results)
    assert not (tmp_path / "mixed - notes.txt").exists()

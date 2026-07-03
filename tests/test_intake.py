"""Tests for the intake pipeline's pairing and auto-apply policy.

These cover the pure logic: filename indexing, the deposit date window, and the
clean-only auto-apply gate. The Gmail/QBO calls themselves are exercised live.
"""

from decimal import Decimal
from pathlib import Path

from app.intake import (
    PACKET_FILE_RE,
    _stored_targets_missing,
    deposit_in_window,
    packets_by_amount,
    should_auto_apply,
)
from app.state import ProcessingState
from app.rename import packet_name
from app.parser import parse_statement_text
from tests.test_keyrenter_parser import SAMPLE


def test_packet_name_matches_intake_filename_pattern():
    statement = parse_statement_text(SAMPLE)
    name = packet_name("Keyrenter", statement)
    assert PACKET_FILE_RE.match(name), name


def test_packets_by_amount_indexes_standardized_names(tmp_path: Path):
    sub = tmp_path / "keyrenter history"
    sub.mkdir()
    (sub / "Owner Packet - Keyrenter - 09-25 - 3604.60.pdf").write_bytes(b"x")
    (sub / "Owner Packet - Nashville - 04-26 - 1892.33.pdf").write_bytes(b"x")
    (sub / "random scan.pdf").write_bytes(b"x")   # ignored: not standardized
    index = packets_by_amount(tmp_path)
    assert set(index) == {Decimal("3604.60"), Decimal("1892.33")}
    assert index[Decimal("3604.60")][0]["month"] == 9
    assert index[Decimal("3604.60")][0]["year"] == 2025


def test_deposit_window_matches_the_search_window():
    packet = {"month": 9, "year": 2025}
    assert deposit_in_window(packet, "2025-09-30")       # in month
    assert deposit_in_window(packet, "2025-08-27")       # month start - 5 days
    assert deposit_in_window(packet, "2025-12-14")       # month end + 75 days
    assert not deposit_in_window(packet, "2025-08-20")   # too early
    assert not deposit_in_window(packet, "2025-12-15")   # too late
    assert not deposit_in_window(packet, "not-a-date")


def _dry(status="ready_to_split", apply_allowed=True, warnings=(), low=0):
    return {
        "screening": {"status": status, "apply_allowed": apply_allowed},
        "expert_rule_review": {
            "warnings": list(warnings),
            "confidence": {"summary": {"high": 30, "medium": 2, "low": low}},
        },
    }


def test_clean_screening_auto_applies():
    assert should_auto_apply(_dry())


def test_warnings_block_auto_apply():
    assert not should_auto_apply(_dry(apply_allowed=False, warnings=[{"type": "unclassified_line"}]))


def test_non_ready_status_blocks_auto_apply():
    assert not should_auto_apply(_dry(status="needs_review", apply_allowed=False))
    assert not should_auto_apply(_dry(status="correction_preview", apply_allowed=False))
    assert not should_auto_apply(_dry(status="blocked", apply_allowed=False))


def test_low_confidence_blocks_auto_apply():
    assert not should_auto_apply(_dry(low=1))


def test_gmail_dedupe_self_heals_when_staged_packet_deleted(tmp_path: Path):
    # A processed message whose staged packet still exists is skipped; delete
    # the packet and the same message must be re-processed (self-healing).
    packet = tmp_path / "Owner Packet - Nashville - 05-26 - 2485.91.pdf"
    packet.write_bytes(b"x")
    state = ProcessingState(tmp_path / "state.json")
    state.mark_processed(
        "msg1",
        {"attachments": [{"action": "staged", "target": str(packet)}]},
    )
    assert state.is_processed("msg1")
    assert not _stored_targets_missing(state, "msg1")   # packet present -> skip
    packet.unlink()
    assert _stored_targets_missing(state, "msg1")       # packet gone -> re-fetch


def test_missing_confidence_blocks_auto_apply():
    # Defensive default: with no confidence data, do NOT auto-apply.
    dry = _dry()
    dry["expert_rule_review"]["confidence"] = {}
    assert not should_auto_apply(dry)

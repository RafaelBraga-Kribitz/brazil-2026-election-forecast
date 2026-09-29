"""Test 3: the pre-registration is intact (hash recorded at registration); addenda are separate dated files."""

from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_prereg_hash_matches_registration():
    recorded = (ROOT / "PREREG.sha256").read_text(encoding="utf-8").split()[0]
    actual = hashlib.sha256((ROOT / "PREREG.md").read_bytes()).hexdigest()
    assert actual == recorded, "PREREG.md changed after registration; record changes in PREREG_ADDENDUM_*.md"


def test_addenda_state_timing_relative_to_results():
    for p in sorted(ROOT.glob("PREREG_ADDENDUM_*.md")):
        text = p.read_text(encoding="utf-8").lower()
        assert "before any" in text or "after seeing" in text, f"{p.name} must state timing relative to results"

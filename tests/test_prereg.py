"""Test 3: the pre-registration is intact (hash recorded at registration); addenda are separate dated files."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CRLF, LF = b"\r\n", b"\n"


def test_prereg_hash_matches_registration():
    """The registration hash was taken on the author's Windows checkout (CRLF line endings); git stores LF.
    The check is therefore on the text, independent of line-ending conversion."""
    recorded = (ROOT / "PREREG.sha256").read_text(encoding="utf-8").split()[0]
    text_lf = (ROOT / "PREREG.md").read_bytes().replace(CRLF, LF)
    candidates = {hashlib.sha256(text_lf).hexdigest(), hashlib.sha256(text_lf.replace(LF, CRLF)).hexdigest()}
    assert recorded in candidates, "PREREG.md changed after registration; record changes in PREREG_ADDENDUM_*.md"


def test_prereg_equals_registration_commit():
    try:
        blob = subprocess.check_output(["git", "show", "27789a2:PREREG.md"], cwd=ROOT, stderr=subprocess.DEVNULL)
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.skip("git history not available")
    assert (ROOT / "PREREG.md").read_bytes().replace(CRLF, LF) == blob.replace(CRLF, LF)


def test_addenda_state_timing_relative_to_results():
    for p in sorted(ROOT.glob("PREREG_ADDENDUM_*.md")):
        text = p.read_text(encoding="utf-8").lower()
        assert "before any" in text or "after seeing" in text, f"{p.name} must state timing relative to results"

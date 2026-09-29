"""Test 10: every parsed observation carries provenance, and the curated tables pass schema validation."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from brfc.schema import CANONICAL_COLUMNS, validate_polls

ROOT = Path(__file__).resolve().parents[1]
POLL_FILES = sorted((ROOT / "data" / "interim").glob("polls_wiki_*.csv"))
# Documented source-side rounding (the parsed values match the source cells); see data/README.md.
KNOWN_SOURCE_ISSUES = ("total-basis poll-scenarios summing above 101.5%",)


@pytest.mark.parametrize("path", POLL_FILES, ids=lambda p: p.stem)
def test_poll_tables_schema_and_provenance(path):
    d = pd.read_csv(path, dtype={"election": str})
    assert list(d.columns) == CANONICAL_COLUMNS
    problems = [p for p in validate_polls(d) if not p.startswith(KNOWN_SOURCE_ISSUES)]
    assert problems == []
    for col in ("source_url", "source_revision", "retrieval_timestamp", "source_hash"):
        assert d[col].notna().all() and (d[col].astype(str) != "").all(), col


def test_every_poll_source_revision_is_locked():
    lock = json.loads((ROOT / "data" / "SOURCES.lock.json").read_text(encoding="utf-8"))
    locked = {(x["revision"], x["sha256"]) for x in lock}
    for path in POLL_FILES:
        d = pd.read_csv(path, dtype=str)
        assert set(zip(d["source_revision"], d["source_hash"], strict=True)) <= locked, path.name


def test_results_have_provenance_and_consistent_totals():
    r = pd.read_csv(ROOT / "data" / "manual" / "results_secondary.csv", dtype={"election": str})
    assert r["source_url"].notna().all()
    tot = r.groupby(["election", "round"]).agg(v=("votes", "sum"), t=("total_valid_votes", "first"))
    assert (tot["v"] == tot["t"]).all()
    assert set(tot.index) == {(e, k) for e in ("2014", "2018", "2022") for k in (1, 2)}


def test_corrections_apply_only_to_logged_fields():
    from brfc.data import apply_corrections, load_polls

    raw = pd.concat(pd.read_csv(p, dtype={"election": str}) for p in POLL_FILES if "2026" in p.name)
    fixed = load_polls(("2026",))
    c = pd.read_csv(ROOT / "data" / "manual" / "corrections.csv", dtype=str)
    for x in c.itertuples():
        if not (raw["poll_id"] == x.poll_id).any():  # a later revision may already carry the corrected dates
            continue
        assert (raw.loc[raw["poll_id"] == x.poll_id, x.field].astype(str) == x.wikipedia_value).all()
        assert (fixed.loc[fixed["poll_id"] == x.poll_id, x.field].astype(str) == x.corrected_value).all()
        assert (fixed.loc[fixed["poll_id"] == x.poll_id, "verification_status"] == "corrected").all()
    assert apply_corrections(fixed).equals(fixed)

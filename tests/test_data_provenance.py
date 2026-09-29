"""Test 10: every parsed observation carries provenance, and the curated tables pass schema validation.

Wikipedia tables (polls_wiki_*.csv) cite a pinned revision recorded in data/SOURCES.lock.json. The two
non-Wikipedia tables cite no revision; their source_hash must be the SHA-256 of the artefact they were built from
(polls_releases.csv: data/manual/final_poll_verification.csv; polls_2014_supplement.csv: the
data/manual/research_2014/<file>.csv holding the poll)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from brfc.names import canonical_pollster
from brfc.schema import CANONICAL_COLUMNS, validate_polls

ROOT = Path(__file__).resolve().parents[1]
INTERIM = ROOT / "data" / "interim"
MANUAL = ROOT / "data" / "manual"
POLL_FILES = sorted(INTERIM.glob("polls_wiki_*.csv"))
RELEASES = INTERIM / "polls_releases.csv"
SUPPLEMENT = INTERIM / "polls_2014_supplement.csv"
NON_WIKI_FILES = [p for p in (RELEASES, SUPPLEMENT) if p.exists()]
RESEARCH_2014 = MANUAL / "research_2014"
RESEARCH_NOT_INPUTS = {"verification.csv", "supplement_exclusions.csv"}
# Documented source-side rounding (the parsed values match the source cells); see data/README.md.
KNOWN_SOURCE_ISSUES = ("total-basis poll-scenarios summing above 101.5%",)


def _text_sha256(path: Path) -> set[str]:
    """SHA-256 of a text artefact with LF and with CRLF line endings: git stores LF (.gitattributes eol=lf) and a
    working copy may use either, so a hash recorded on one checkout is checkable on every other."""
    lf = path.read_bytes().replace(b"\r\n", b"\n")
    return {hashlib.sha256(lf).hexdigest(), hashlib.sha256(lf.replace(b"\n", b"\r\n")).hexdigest()}


def _lock_triples() -> set[tuple[str, str, str]]:
    lock = json.loads((ROOT / "data" / "SOURCES.lock.json").read_text(encoding="utf-8"))
    return {(x["source_url"], x["revision"], x["sha256"]) for x in lock}


@pytest.mark.parametrize("path", POLL_FILES, ids=lambda p: p.stem)
def test_poll_tables_schema_and_provenance(path):
    d = pd.read_csv(path, dtype={"election": str})
    assert list(d.columns) == CANONICAL_COLUMNS
    problems = [p for p in validate_polls(d) if not p.startswith(KNOWN_SOURCE_ISSUES)]
    assert problems == []
    for col in ("source_url", "source_revision", "retrieval_timestamp", "source_hash"):
        assert d[col].notna().all() and (d[col].astype(str) != "").all(), col
    assert (d["source_type"] == "wikipedia_revision").all()


def test_every_poll_source_revision_is_locked():
    lock = json.loads((ROOT / "data" / "SOURCES.lock.json").read_text(encoding="utf-8"))
    locked = {(x["revision"], x["sha256"]) for x in lock}
    for path in POLL_FILES:
        d = pd.read_csv(path, dtype=str)
        assert set(zip(d["source_revision"], d["source_hash"], strict=True)) <= locked, path.name


def test_lock_lists_exactly_the_wikipedia_revisions_in_use():
    """The lock is regenerated from the tables (scripts/refresh_2026_polls.update_lock): no stale entry, no gap,
    and nothing from the non-Wikipedia tables."""
    used = set()
    for path in POLL_FILES:
        d = pd.read_csv(path, dtype=str)
        used |= set(zip(d["source_url"], d["source_revision"], d["source_hash"], strict=True))
    assert _lock_triples() == used
    locked_hashes = {h for _, _, h in _lock_triples()}
    for path in NON_WIKI_FILES:
        assert not set(pd.read_csv(path, dtype=str)["source_hash"]) & locked_hashes, path.name


@pytest.mark.parametrize("path", NON_WIKI_FILES, ids=lambda p: p.stem)
def test_non_wikipedia_tables_schema_and_provenance(path):
    d = pd.read_csv(path, dtype={"election": str})
    assert list(d.columns) == CANONICAL_COLUMNS
    problems = [p for p in validate_polls(d) if not p.startswith(KNOWN_SOURCE_ISSUES)]
    assert problems == []
    for col in ("source_url", "retrieval_timestamp", "source_hash", "verification_source"):
        assert d[col].notna().all() and (d[col].astype(str) != "").all(), col
    assert d["source_revision"].isna().all()  # not a Wikipedia revision: checked by artefact hash instead
    assert (d["source_type"] != "wikipedia_revision").all()


@pytest.mark.skipif(not RELEASES.exists(), reason="release rows not built")
def test_release_rows_hash_is_the_verification_file():
    d = pd.read_csv(RELEASES, dtype=str)
    assert set(d["source_hash"]) <= _text_sha256(MANUAL / "final_poll_verification.csv")


@pytest.mark.skipif(not SUPPLEMENT.exists(), reason="2014 supplement not built")
def test_supplement_rows_hash_is_the_research_file_they_were_built_from():
    files = {}  # (round, pollster, field_start, field_end, n) -> hashes of the research files that hold the poll
    inputs = sorted(p for p in RESEARCH_2014.glob("*.csv") if p.name not in RESEARCH_NOT_INPUTS)
    assert inputs
    for f in inputs:
        r = pd.read_csv(f, dtype=str).dropna(subset=["sample_size"])
        h = _text_sha256(f)
        for x in r.itertuples(index=False):
            k = (int(x.round), canonical_pollster(x.pollster), x.field_start, x.field_end, int(float(x.sample_size)))
            files.setdefault(k, set()).update(h)
    s = pd.read_csv(SUPPLEMENT, dtype=str)
    for x in s.drop_duplicates(["poll_id", "source_hash"]).itertuples(index=False):
        k = (int(x.round), x.pollster, x.field_start, x.field_end, int(float(x.sample_size)))
        assert k in files, f"{x.poll_id}: no research file holds this poll"
        assert x.source_hash in files[k], f"{x.poll_id}: source_hash is not the SHA-256 of its research file"
    assert set(s["verification_source"]) == {"data/manual/research_2014/verification.csv"}


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


def test_first_round_date_correction_also_applies_to_the_same_polls_round2_rows(tmp_path, monkeypatch):
    from brfc import data
    from tests.conftest import poll_rows

    shares = {"Lula": 40, "Flávio Bolsonaro": 35}
    r1 = poll_rows("2026", 1, "Pollster A", "2026-09-04", "2026-09-12", 2000, shares)
    twin = poll_rows("2026", 2, "Pollster A", "2026-09-04", "2026-09-12", 2000, shares, scenario="h2h")
    other_n = poll_rows("2026", 2, "Pollster A", "2026-09-04", "2026-09-12", 1500, shares, scenario="h2h")
    other_pollster = poll_rows("2026", 2, "Pollster B", "2026-09-04", "2026-09-12", 2000, shares, scenario="h2h")
    other_election = poll_rows("2022", 2, "Pollster A", "2026-09-04", "2026-09-12", 2000, shares, scenario="h2h")
    d = pd.DataFrame(r1 + twin + other_n + other_pollster + other_election)
    d["tse_br_id"] = "BR-00001/2026"
    pid = r1[0]["poll_id"]
    corrections = pd.DataFrame(
        [
            (pid, "field_start", "2026-09-04", "2026-09-06"),
            (pid, "field_end", "2026-09-12", "2026-09-11"),
            (pid, "tse_br_id", "BR-00001/2026", "BR-00002/2026"),  # not a date: first-round rows only
        ],
        columns=["poll_id", "field", "wikipedia_value", "corrected_value"],
    ).assign(source_url="https://example.org/release", source_type="media_report", retrieved_utc="", reason="t")
    path = tmp_path / "corrections.csv"
    corrections.to_csv(path, index=False)
    monkeypatch.setattr(data, "CORRECTIONS_FILE", path)

    out = data.apply_corrections(d)
    ids = {"r1": r1[0]["poll_id"], "twin": twin[0]["poll_id"]}
    for k in ("r1", "twin"):
        g = out[out["poll_id"] == ids[k]]
        assert (g["field_start"] == "2026-09-06").all() and (g["field_end"] == "2026-09-11").all(), k
        assert (g["verification_status"] == "corrected").all()
        assert (g["verification_source"] == "https://example.org/release").all()
    assert (out.loc[out["poll_id"] == ids["r1"], "tse_br_id"] == "BR-00002/2026").all()
    assert (out.loc[out["poll_id"] == ids["twin"], "tse_br_id"] == "BR-00001/2026").all()
    untouched = out[~out["poll_id"].isin(ids.values())]
    assert len(untouched) == len(other_n + other_pollster + other_election)
    assert (untouched["field_start"] == "2026-09-04").all() and (untouched["field_end"] == "2026-09-12").all()
    assert (untouched["verification_status"] == "unverified").all()
    assert data.apply_corrections(out).equals(out)  # idempotent


def test_lock_records_stable_revision_identity():
    """The rendered HTML of a revision is not byte-stable; the wikitext SHA-1 is (see scripts/reparse_sources.py)."""
    lock = json.loads((ROOT / "data" / "SOURCES.lock.json").read_text(encoding="utf-8"))
    for x in lock:
        sha1 = x.get("revision_sha1", "")
        assert len(sha1) == 40 and all(ch in "0123456789abcdef" for ch in sha1), x["revision"]
        assert int(x["revision_size"]) > 0

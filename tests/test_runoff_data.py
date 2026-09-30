"""2026 runoff head-to-head poll table (round 2, fielded before the first round): parsing, schema, provenance,
and non-interference with the first-round table."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pandas as pd
import pytest

from brfc.ingest import wiki_2026
from brfc.ingest.wikipedia import RAW_DIR, fetch_revision
from brfc.provenance import SourceRecord
from brfc.schema import CANONICAL_COLUMNS, OTHERS, validate_polls

ROOT = Path(__file__).resolve().parents[1]
INTERIM = ROOT / "data" / "interim"
RUNOFF_CSV = INTERIM / "polls_wiki_2026_runoff.csv"
RUNOFF_CONFLICTS_CSV = INTERIM / "conflicts_wiki_2026_runoff.csv"
FIRST_ROUND_CSV = INTERIM / "polls_wiki_2026.csv"
REVISION_JSON = INTERIM / "polls_wiki_2026.revision.json"

# Documented source-side rounding (the parsed values match the source cells); see data/README.md.
KNOWN_SOURCE_ISSUES = ("total-basis poll-scenarios summing above 101.5%",)

# First-round outputs for the pinned revisions, hashed before the runoff parser was added. Hashes are of the
# LF-normalised text (the committed blobs; pandas writes os.linesep, git stores LF), so they hold on every OS.
PINNED_REVISIONS = {"pt_oldid": 73082572, "en_oldid": 1377478949}
FIRST_ROUND_SHA256 = "26e9ad911a97d1d26d2866afe86ab234bfd43b6a790cbff97503d6244f3c8c20"
FIRST_ROUND_CONFLICTS_SHA256 = "31c0222645ed885e24008b87aa1da7de707fe6bc77480b6097d919c7531d7f0f"
# SHA-256 of the rendered HTML the committed tables were parsed from. MediaWiki can re-render the same revision
# with different bytes, so a re-download reproduces the values but not these hashes (see DATA_SOURCES.md).
ORIGINAL_HTML_SHA256 = {
    "9ca942a568882bbd6b774a0d5553b585fd3c21e22108b84064e56abca54efbeb",
    "a78d03ad5aeb63fc824e003bf10c344af9bfe722bf36e79b3d9db13c7cca830b",
}
# columns that describe the download rather than the data (as in scripts/reparse_sources.py)
RETRIEVAL_COLUMNS = ("retrieval_timestamp", "source_hash")

needs_runoff_csv = pytest.mark.skipif(not RUNOFF_CSV.exists(), reason="runoff table not built")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def _csv(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False, lineterminator="\n").encode("utf-8")


def _values(data: bytes) -> pd.DataFrame:
    df = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False)
    return df.drop(columns=[c for c in RETRIEVAL_COLUMNS if c in df.columns])


def _original_download(pinned_html) -> bool:
    return {pinned_html[1].sha256, pinned_html[3].sha256} <= ORIGINAL_HTML_SHA256


def _current_revisions() -> dict:
    if not REVISION_JSON.exists():
        return {}
    rev = json.loads(REVISION_JSON.read_text(encoding="utf-8"))
    return {k: rev.get(k) for k in PINNED_REVISIONS}


def _cached(lang: str, oldid: int) -> bool:
    return (RAW_DIR / f"{lang}_{oldid}.html").exists() and (RAW_DIR / f"{lang}_{oldid}.provenance.json").exists()


# --------------------------------------------------------------------------------------------------
# the committed runoff table


@needs_runoff_csv
def test_runoff_table_schema_and_provenance():
    d = pd.read_csv(RUNOFF_CSV, dtype={"election": str})
    assert list(d.columns) == CANONICAL_COLUMNS
    problems = [p for p in validate_polls(d) if not p.startswith(KNOWN_SOURCE_ISSUES)]
    assert problems == []
    assert (d["election"] == "2026").all()
    assert (d["round"] == 2).all()
    assert d["poll_id"].str.startswith("2026-2-").all()
    assert d["sample_size"].notna().all()
    assert (d["source_type"] == "wikipedia_revision").all()
    for col in ("source_url", "source_revision", "retrieval_timestamp", "source_hash"):
        assert d[col].notna().all() and (d[col].astype(str) != "").all(), col
    # every row cites one of the two revisions the table was parsed from
    rev = _current_revisions()
    if rev:
        assert set(d["source_revision"].astype(str)) <= {str(rev["pt_oldid"]), str(rev["en_oldid"])}


@needs_runoff_csv
def test_runoff_every_scenario_is_one_pairing_of_two_named_candidates():
    d = pd.read_csv(RUNOFF_CSV, dtype={"election": str})
    assert not (d["candidate"] == OTHERS).any()
    for (_, lab), g in d.groupby(["poll_id", "scenario"]):
        cands = list(g["candidate"])
        assert len(cands) == 2 and len(set(cands)) == 2, (lab, cands)
        assert lab.split(" [")[0] == wiki_2026.pair_label(*cands)
        # blank/null (optional) and undecided are poll-level responses of the scenario, not candidates
        assert g["blank_null"].nunique(dropna=False) == 1 and g["undecided"].nunique(dropna=False) == 1
    # one poll holds each pairing at most once unless the source lists it twice with different values
    dup = d.drop_duplicates(["poll_id", "scenario"]).assign(
        base=lambda x: x["scenario"].str.split(" [", regex=False).str[0]
    )
    twice = dup[dup.duplicated(["poll_id", "base"], keep=False)]
    assert twice["notes"].str.contains("pairing_listed_twice_with_different_values", na=False).all()


@needs_runoff_csv
def test_runoff_gap_fill_rows_come_from_en_and_keep_pt_poll_metadata():
    d = pd.read_csv(RUNOFF_CSV, dtype={"election": str, "source_revision": str})
    rev = _current_revisions()
    notes = d["notes"].fillna("")
    gap = d[notes.str.contains("gap_fill_en")]
    if rev:
        assert (gap["source_revision"] == str(rev["en_oldid"])).all()
        assert (d.loc[~notes.str.contains("gap_fill_en"), "source_revision"] == str(rev["pt_oldid"])).all()
    meta = ["pollster", "field_start", "field_end", "sample_size"]
    assert (d.groupby("poll_id")[meta].nunique() == 1).all().all()  # scenario gap-fills reuse PT poll metadata


@needs_runoff_csv
def test_runoff_conflicts_table_columns():
    c = pd.read_csv(RUNOFF_CONFLICTS_CSV)
    assert list(c.columns) == wiki_2026.CONFLICT_COLUMNS
    d = pd.read_csv(RUNOFF_CSV, dtype={"election": str})
    assert set(c["poll_id"]) <= set(d["poll_id"])


@needs_runoff_csv
def test_load_polls_2026_includes_the_runoff_table():
    from brfc.data import load_polls

    d = load_polls(("2026",))
    r1 = pd.read_csv(FIRST_ROUND_CSV, dtype={"election": str}).dropna(
        subset=["sample_size", "share_reported", "field_end"]
    )
    r2 = pd.read_csv(RUNOFF_CSV, dtype={"election": str}).dropna(subset=["sample_size", "share_reported", "field_end"])
    assert len(d[d["round"] == 1]) == len(r1)
    assert len(d[d["round"] == 2]) == len(r2)
    assert not set(d.loc[d["round"] == 1, "poll_id"]) & set(d.loc[d["round"] == 2, "poll_id"])
    assert set(load_polls(("2022",))["election"]) == {"2022"}


# --------------------------------------------------------------------------------------------------
# the first-round table is unchanged by the extension


def test_first_round_table_hash_unchanged_for_pinned_revisions():
    if _current_revisions() != PINNED_REVISIONS:
        pytest.skip("2026 tables were refreshed to other revisions; the rebuild test below still applies")
    assert _sha(FIRST_ROUND_CSV.read_bytes()) == FIRST_ROUND_SHA256
    assert _sha((INTERIM / "conflicts_wiki_2026.csv").read_bytes()) == FIRST_ROUND_CONFLICTS_SHA256


@pytest.fixture(scope="module")
def pinned_html():
    pt, en = PINNED_REVISIONS["pt_oldid"], PINNED_REVISIONS["en_oldid"]
    if not (_cached("pt", pt) and _cached("en", en)):
        pytest.skip("pinned raw revisions not cached locally (data/raw is not redistributed)")
    pt_html, pt_rec = fetch_revision("pt", wiki_2026.PT_TITLE, pt)
    en_html, en_rec = fetch_revision("en", wiki_2026.EN_TITLE, en)
    return pt_html, pt_rec, en_html, en_rec


def test_first_round_rebuild_from_pinned_revisions(pinned_html):
    """Every value is reproduced; the bytes (including the HTML hash column) only from the original download."""
    polls, conflicts, _ = wiki_2026.build_tables(*pinned_html, round_=1)
    if _original_download(pinned_html):
        assert _sha(_csv(polls)) == FIRST_ROUND_SHA256
        assert _sha(_csv(conflicts)) == FIRST_ROUND_CONFLICTS_SHA256
    if _current_revisions() == PINNED_REVISIONS:
        pd.testing.assert_frame_equal(_values(_csv(polls)), _values(FIRST_ROUND_CSV.read_bytes()))
        conflicts_csv = (INTERIM / "conflicts_wiki_2026.csv").read_bytes()
        pd.testing.assert_frame_equal(_values(_csv(conflicts)), _values(conflicts_csv))


def test_runoff_rebuild_from_pinned_revisions_matches_committed_table(pinned_html):
    if _current_revisions() != PINNED_REVISIONS or not RUNOFF_CSV.exists():
        pytest.skip("committed runoff table comes from other revisions")
    polls, conflicts, _ = wiki_2026.build_tables(*pinned_html, round_=2)
    if _original_download(pinned_html):
        assert _sha(_csv(polls)) == _sha(RUNOFF_CSV.read_bytes())
        assert _sha(_csv(conflicts)) == _sha(RUNOFF_CONFLICTS_CSV.read_bytes())
    pd.testing.assert_frame_equal(_values(_csv(polls)), _values(RUNOFF_CSV.read_bytes()))
    pd.testing.assert_frame_equal(_values(_csv(conflicts)), _values(RUNOFF_CONFLICTS_CSV.read_bytes()))


# --------------------------------------------------------------------------------------------------
# parser and reconcile rules on a synthetic page (no network, no cache)


def _rec(lang: str) -> SourceRecord:
    return SourceRecord(
        source=f"wikipedia_{lang}",
        url=f"https://{lang}.wikipedia.org/w/index.php?oldid=1",
        retrieval_timestamp="2026-09-29T16:00:00Z",
        source_type="wikipedia_revision",
        revision="1" if lang == "pt" else "2",
        sha256=("a" if lang == "pt" else "b") * 64,
        local_path="",
    )


LULA = '<a href="/wiki/Lula" title="Luiz Inácio Lula da Silva">Lula</a>'
FLAVIO = '<a href="/wiki/Fl" title="Flávio Bolsonaro">Flávio</a>'
CAIADO = '<a href="/wiki/Ca" title="Ronaldo Caiado">Caiado</a>'
PT_HEAD = "<th>Contratante / Pesquisa</th><th>Data(s) de Pesquisa</th><th>Tamanho da Amostra</th>"


def _row(*cells: str) -> str:
    return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"


def _pt_pair_table(cand_b: str, rows: list[tuple]) -> str:
    head = f"<tr>{PT_HEAD}<th>{LULA}</th><th>{cand_b}</th><th>Indecisos e Absentos</th></tr>"
    return '<table class="wikitable">' + head + "".join(_row(*r) for r in rows) + "</table>"


PT_PAGE = (
    "<div><h2>Primeiro turno</h2><h3>2026</h3>"
    '<table class="wikitable">'
    f"<tr>{PT_HEAD}<th>{LULA}</th><th>{FLAVIO}</th><th>Outros</th><th>Indecisos e Absentos</th></tr>"
    + _row("Datafolha", "22 Set \u2013 24 Set", "2 002", "40%", "35%", "15%", "10%")
    + "</table>"
    "<h2>Segundo turno</h2><h3>Lula e Flávio Bolsonaro</h3><h4>2026</h4>"
    + _pt_pair_table(
        FLAVIO,
        [
            ("Datafolha", "22 Set \u2013 24 Set", "2 002", "47%", "45%", "8%"),
            ("Quaest", "24 Set \u2013 27 Set", "2 004", "42%", "42%", "16%"),
            ("Gerp", "24 Set \u2013 28 Set", "2 400", "43%", "—", "6%"),  # one candidate only -> skipped
        ],
    )
    + "<h3>Lula e Caiado</h3><h4>2026</h4>"
    + _pt_pair_table(CAIADO, [("Datafolha", "22 Set \u2013 24 Set", "2 002", "46%", "44%", "10%")])
    + "<h2>Referências</h2></div>"
)

EN_HEAD = (
    f"<tr><th>Pollster</th><th>Polling period</th><th>{LULA}</th>"
    f'<th><a href="/wiki/Fl" title="Flávio Bolsonaro">F. Bolsonaro</a></th><th>{CAIADO}</th>'
    "<th>Blank Null Undec.</th><th>Sample size</th></tr>"
)
EN_PAGE = (
    "<div><h2>Second round</h2><h3>2026</h3>"
    '<table class="wikitable">'
    + EN_HEAD
    + _row("Datafolha", "22\u201324 Sep", "47", "45", "—", "8", "2,002")
    + _row("Datafolha", "22\u201324 Sep", "46", "—", "44", "10", "2,002")
    + _row("Quaest", "24\u201327 Sep", "43", "42", "—", "15", "2,004")  # Lula 43 vs PT 42 -> conflict
    + _row("Quaest", "24\u201327 Sep", "43", "—", "40", "17", "2,004")  # pairing absent from PT -> scenario gap-fill
    + _row("AtlasIntel", "23\u201328 Sep", "47.6", "47.7", "—", "4.8", "5,005")  # poll absent from PT -> gap-fill
    + "</table><h2>References</h2></div>"
)


def test_synthetic_round_sections_are_kept_apart():
    r1 = wiki_2026.parse(PT_PAGE, _rec("pt"), "pt", round_=1)
    assert set(r1["round"]) == {1} and set(r1["scenario"]) == {"main"}
    assert set(r1["candidate"]) == {"Lula", "Flávio Bolsonaro", OTHERS}
    r2 = wiki_2026.parse(PT_PAGE, _rec("pt"), "pt", round_=2)
    assert set(r2["round"]) == {2}
    assert r2.attrs["parse_stats"]["skipped_not_two_candidates"] == 1
    datafolha = r2[r2["pollster"] == "Datafolha"]
    assert datafolha["poll_id"].nunique() == 1  # the same poll in two pairing tables -> one poll
    assert set(datafolha["scenario"]) == {"Flávio Bolsonaro vs Lula", "Lula vs Ronaldo Caiado"}
    assert not set(r1["poll_id"]) & set(r2["poll_id"])
    with pytest.raises(ValueError):
        wiki_2026.parse(PT_PAGE, _rec("pt"), "pt", round_=3)


def test_synthetic_runoff_reconcile_pt_primary_en_gap_fill_never_average():
    pt = wiki_2026.parse(PT_PAGE, _rec("pt"), "pt", round_=2)
    en = wiki_2026.parse(EN_PAGE, _rec("en"), "en", round_=2)
    assert set(en["scenario"]) == {"Flávio Bolsonaro vs Lula", "Lula vs Ronaldo Caiado"}
    merged, conflicts = wiki_2026.reconcile(pt, en, round_=2)
    assert validate_polls(merged) == []

    quaest = merged[merged["pollster"] == "Quaest"]
    assert quaest["poll_id"].nunique() == 1
    flav = quaest[quaest["scenario"] == "Flávio Bolsonaro vs Lula"].set_index("candidate")
    assert flav.loc["Lula", "share_reported"] == 42.0  # PT value kept, not averaged with EN 43
    assert (flav["source_revision"] == "1").all()
    cai = quaest[quaest["scenario"] == "Lula vs Ronaldo Caiado"]
    assert len(cai) == 2 and (cai["source_revision"] == "2").all()
    assert cai["notes"].str.contains("gap_fill_en_scenario").all()
    assert (cai["poll_id"] == flav["poll_id"].iloc[0]).all()

    atlas = merged[merged["pollster"] == "AtlasIntel"]
    assert len(atlas) == 2 and atlas["notes"].str.contains("gap_fill_en").all()
    assert (atlas["source_revision"] == "2").all()

    share = conflicts[conflicts["kind"] == "share"]
    assert len(share) == 1
    c = share.iloc[0]
    assert (c["candidate"], c["pt_value"], c["en_value"]) == ("Lula", 42.0, 43.0)
    assert c["pt_scenario"] == c["en_scenario"] == "Flávio Bolsonaro vs Lula"
    bn = conflicts[conflicts["kind"] == "blank_null"]
    assert list(zip(bn["pt_value"], bn["en_value"], strict=True)) == [(16.0, 15.0)]
    assert merged.attrs["reconcile_stats"]["en_gap_fill_scenarios_in_matched_polls"] == 1


def test_pair_label_is_sorted_and_symmetric():
    assert wiki_2026.pair_label("Lula", "Flávio Bolsonaro") == "Flávio Bolsonaro vs Lula"
    assert wiki_2026.pair_label("Flávio Bolsonaro", "Lula") == "Flávio Bolsonaro vs Lula"
    a, b = sorted(("Ronaldo Caiado", "Lula"))
    assert wiki_2026.pair_label("Ronaldo Caiado", "Lula") == f"{a} vs {b}"

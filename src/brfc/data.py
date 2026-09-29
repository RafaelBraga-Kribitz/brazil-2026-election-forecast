"""Loaders for the curated datasets. Poll loading and result loading are deliberately separate module-level
functions: forecasting code paths only ever call `load_polls`.

Poll sources combined by `load_polls` (all in the canonical schema, parsed tables never edited):
- data/interim/polls_wiki_{year}.csv: pinned Wikipedia revisions (PREREG s.2);
- data/interim/polls_wiki_2026_runoff.csv: 2026 runoff head-to-heads from the same revisions (Addendum 04 s.2);
- data/interim/polls_releases.csv: final polls missing from Wikipedia, from release records (Addendum 02);
- data/interim/polls_2014_supplement.csv: 2014 release research (Addendum 04). Its pre-first-round head-to-heads
  (round 2, tagged `pre_first_round_h2h`) always load; its first-round rows (tagged `revision_2014_r1`) are a
  post-result data revision and load only with `revision_2014=True` (Addendum 04 s.3).
"""

from __future__ import annotations

import pandas as pd

from brfc import config
from brfc.names import canonical_candidate, canonical_pollster

INTERIM = config.DATA / "interim"
POLL_FILES = {e: INTERIM / f"polls_wiki_{e}.csv" for e in ("2014", "2018", "2022", "2026")}
RUNOFF_2026_FILE = INTERIM / "polls_wiki_2026_runoff.csv"
RELEASES_FILE = INTERIM / "polls_releases.csv"
SUPPLEMENT_2014_FILE = INTERIM / "polls_2014_supplement.csv"
RESULTS_FILE = config.DATA / "manual" / "results_secondary.csv"

# notes prefix of each round's rows in the 2014 supplement (set by scripts/build_2014_supplement.py)
SUPPLEMENT_TAGS = {2: "pre_first_round_h2h", 1: "revision_2014_r1"}
_DTYPES = {"election": str, "tse_br_id": str, "publication_date": str}


def _read(path) -> pd.DataFrame:
    d = pd.read_csv(path, dtype=_DTYPES)
    d["election"] = d["election"].astype(str)
    return d


def supplement_2014(revision_2014: bool = False) -> pd.DataFrame:
    """Rows of the 2014 supplement that `load_polls` adds: the round-2 pre-first-round head-to-heads always, the
    round-1 post-result revision rows only when `revision_2014` is True. Empty when the file is absent.

    Raises ValueError for a row that carries neither tag with its round, so no untagged row can enter."""
    if not SUPPLEMENT_2014_FILE.exists():
        return pd.DataFrame()
    s = _read(SUPPLEMENT_2014_FILE)
    rnd = pd.to_numeric(s["round"], errors="coerce")
    notes = s["notes"].fillna("").astype(str)
    h2h = (rnd == 2) & notes.str.startswith(SUPPLEMENT_TAGS[2])
    r1 = (rnd == 1) & notes.str.startswith(SUPPLEMENT_TAGS[1])
    bad = ~(h2h | r1) | (s["election"] != "2014")
    if bad.any():
        raise ValueError(f"{SUPPLEMENT_2014_FILE.name}: {int(bad.sum())} rows without a valid round tag or not 2014")
    return s[h2h | (r1 & revision_2014)]


def load_polls(elections: tuple[str, ...] = ("2014", "2018", "2022"), *, revision_2014: bool = False) -> pd.DataFrame:
    """Canonical poll rows (both rounds) for `elections`.

    For 2026 the runoff head-to-head table (round 2 rows parsed by brfc.ingest.wiki_2026) is included when present.
    For 2014 the supplement's pre-first-round head-to-heads are included; its first-round rows only with
    `revision_2014=True` (post-result data revision, PREREG_ADDENDUM_04 s.3). The default output for every
    registered information set is unchanged by the supplement (tests/test_data_supplement.py)."""
    extra = {"2026": [RUNOFF_2026_FILE]}
    frames = []
    for e in elections:
        for path in [POLL_FILES[e], *(p for p in extra.get(e, []) if p.exists())]:
            frames.append(_read(path))
    if RELEASES_FILE.exists():  # final polls missing from Wikipedia, added from release records (PREREG Addendum 02)
        r = _read(RELEASES_FILE)
        frames.append(r[r["election"].isin(elections)])
    if "2014" in elections:
        s = supplement_2014(revision_2014)
        if not s.empty:
            clash = sorted(set(s["poll_id"]) & set(pd.concat(frames)["poll_id"]))
            if clash:
                raise ValueError(f"{SUPPLEMENT_2014_FILE.name}: poll_id also present in another table: {clash[:5]}")
            frames.append(s)
    d = pd.concat(frames, ignore_index=True)
    d["round"] = d["round"].astype(int)
    d["sample_size"] = pd.to_numeric(d["sample_size"], errors="coerce")
    for c in ("share_reported", "blank_null", "undecided"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d["publication_date"] = d["publication_date"].fillna("")
    d = d.dropna(subset=["sample_size", "share_reported", "field_end"])
    d["pollster"] = d["pollster"].map(canonical_pollster)
    d["candidate"] = d["candidate"].map(canonical_candidate)
    return apply_corrections(d)


CORRECTIONS_FILE = config.DATA / "manual" / "corrections.csv"
DATE_FIELDS = ("field_start", "field_end", "publication_date")
_SAME_POLL = ("election", "pollster", "field_start", "field_end", "sample_size")


def _same_poll_round2(d: pd.DataFrame, poll_id: str) -> pd.Series:
    """Round-2 rows of the poll whose round-1 rows carry `poll_id`: same election, pollster, fieldwork dates and
    sample size (one fieldwork can publish first-round and head-to-head tables under two poll_ids)."""
    out = pd.Series(False, index=d.index)
    first = d[(d["poll_id"] == poll_id) & (d["round"].astype(int) == 1)]
    if first.empty:
        return out
    n = pd.to_numeric(d["sample_size"], errors="coerce")
    for key in first[list(_SAME_POLL)].drop_duplicates().itertuples(index=False):
        m = d["round"].astype(int) == 2
        for col, v in zip(_SAME_POLL, key, strict=True):
            m &= (n == pd.to_numeric(v, errors="coerce")) if col == "sample_size" else (d[col].astype(str) == str(v))
        out |= m
    return out


def apply_corrections(d: pd.DataFrame) -> pd.DataFrame:
    """Apply logged field corrections from verification against releases (precedence: release > Wikipedia).

    A date correction logged for a first-round poll is also applied to the same poll's round-2 rows (same election,
    pollster, fieldwork dates and sample size), so both tables of one fieldwork share one information time.
    Parsed source tables stay untouched; corrected rows are marked `verification_status = corrected`."""
    if not CORRECTIONS_FILE.exists():
        return d
    c = pd.read_csv(CORRECTIONS_FILE, dtype=str)
    d = d.copy()
    for x in c.itertuples():
        m = (d["poll_id"] == x.poll_id) & (d[x.field].astype(str) == x.wikipedia_value)
        if x.field in DATE_FIELDS:  # key taken before this correction; earlier ones were applied to both rounds
            m |= _same_poll_round2(d, x.poll_id) & (d[x.field].astype(str) == x.wikipedia_value)
        d.loc[m, x.field] = x.corrected_value
        d.loc[m, "verification_status"] = "corrected"
        d.loc[m, "verification_source"] = x.source_url
    return d


def load_results() -> pd.DataFrame:
    """Official historical results (valid-vote shares recomputed from vote counts). Evaluation-only."""
    r = pd.read_csv(RESULTS_FILE, dtype={"election": str})
    r["round"] = r["round"].astype(int)
    r["valid_share"] = 100.0 * r["votes"] / r.groupby(["election", "round"])["votes"].transform("sum")
    return r[["election", "round", "candidate", "votes", "valid_share", "source_url", "source_revision"]]


def actual_shares(results: pd.DataFrame, election: str, round_: int, categories: list[str]) -> dict[str, float]:
    """Map forecast categories (named candidates [+ Others]) to observed valid-vote shares."""
    r = results[(results["election"] == election) & (results["round"] == round_)].set_index("candidate")["valid_share"]
    out = {c: float(r.get(c, 0.0)) for c in categories if c != config.OTHERS_LABEL}
    if config.OTHERS_LABEL in categories:
        out[config.OTHERS_LABEL] = 100.0 - sum(out.values())
    return out

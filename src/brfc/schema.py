"""Canonical poll schema (long format: one row per poll x scenario x candidate response).

Poll *responses* (candidate, blank/null intention, undecided) are kept distinct from ballot
*outcomes*. `undecided` is never a ballot category. `valid_vote_share` is derived later by
brfc.transform.valid_votes and is empty in parser output.
"""

from __future__ import annotations

import pandas as pd

CANONICAL_COLUMNS: list[str] = [
    "poll_id",  # deterministic hash of (election, round, pollster, field dates, n) - see make_poll_id
    "election",  # "2014" | "2018" | "2022" | "2026"
    "round",  # 1 | 2
    "tse_br_id",  # e.g. "BR-01234/2026"; empty if unknown
    "pollster",  # canonical pollster name (see brfc.pollsters)
    "contractor",
    "field_start",  # ISO date
    "field_end",  # ISO date
    "publication_date",  # ISO date or empty (unknown)
    "sample_size",  # int
    "methodology",  # e.g. "face-to-face", "phone", "online", "IVR"; empty if unknown
    "scenario",  # scenario label within the poll ("main" if only one)
    "candidate",  # canonical candidate name, or "__others__" for an aggregated other-candidates column
    "share_reported",  # float, percent, as reported
    "share_basis",  # "total" (incl. blank/null/undecided) | "valid" (valid votes as reported)
    "blank_null",  # float percent: stated blank/null/none intention (poll response); NaN if absent
    "undecided",  # float percent: don't know / no answer (poll response); NaN if absent
    "valid_vote_share",  # float percent, derived (not filled by parsers)
    "source_url",
    "source_type",  # "wikipedia_revision" | "pollster_release" | "pesqele" | "news"
    "source_revision",  # Wikipedia oldid
    "retrieval_timestamp",  # UTC ISO
    "verification_status",  # "unverified" | "verified_match" | "verified_conflict" | "corrected"
    "verification_source",
    "source_hash",  # SHA-256 of the raw artefact the row was parsed from
    "notes",
]

OTHERS = "__others__"


def make_poll_id(election: str, round_: int, pollster: str, field_start: str, field_end: str, sample_size: int) -> str:
    """Deterministic poll identifier; scenario is NOT part of it (one poll can hold several scenarios)."""
    import hashlib

    key = f"{election}|{int(round_)}|{pollster.strip().lower()}|{field_start}|{field_end}|{int(sample_size)}"
    return f"{election}-{int(round_)}-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]
BASIS_VALUES = {"total", "valid"}
VERIFICATION_VALUES = {"unverified", "verified_match", "verified_conflict", "corrected"}


class SchemaError(ValueError):
    pass


def validate_polls(df: pd.DataFrame, *, require_valid_share: bool = False) -> list[str]:
    """Return a list of human-readable violations (empty list == valid)."""
    problems: list[str] = []
    missing = [c for c in CANONICAL_COLUMNS if c not in df.columns]
    if missing:
        return [f"missing columns: {missing}"]

    def bad(mask: pd.Series, msg: str) -> None:
        if mask.any():
            problems.append(f"{msg}: {int(mask.sum())} rows (e.g. poll_id={df.loc[mask, 'poll_id'].iloc[0]})")

    bad(~df["round"].isin([1, 2]), "round not in {1,2}")
    bad(~df["share_basis"].isin(BASIS_VALUES), "share_basis invalid")
    bad(~df["verification_status"].isin(VERIFICATION_VALUES), "verification_status invalid")
    bad(df["field_end"].isna() | (df["field_end"].astype(str) == ""), "missing field_end")
    fs = pd.to_datetime(df["field_start"], errors="coerce")
    fe = pd.to_datetime(df["field_end"], errors="coerce")
    bad(fe.isna(), "unparseable field_end")
    bad(fs.notna() & fe.notna() & (fs > fe), "field_start after field_end")
    n = pd.to_numeric(df["sample_size"], errors="coerce")
    bad(n.isna() | (n < 100) | (n > 200_000), "invalid sample_size")
    s = pd.to_numeric(df["share_reported"], errors="coerce")
    bad(s.isna() | (s < 0) | (s > 100), "share_reported outside [0,100]")
    for col in ("blank_null", "undecided"):
        v = pd.to_numeric(df[col], errors="coerce")
        bad(v.notna() & ((v < 0) | (v > 100)), f"{col} outside [0,100]")
    bad(df["source_url"].isna() | (df["source_url"].astype(str) == ""), "missing source_url")
    bad(df["source_hash"].isna() | (df["source_hash"].astype(str).str.len() != 64), "missing/invalid source_hash")
    bad(df["retrieval_timestamp"].isna() | (df["retrieval_timestamp"].astype(str) == ""), "missing retrieval_timestamp")

    key = ["poll_id", "scenario", "candidate"]
    bad(df.duplicated(key, keep=False), "duplicate (poll_id, scenario, candidate)")

    # per poll-scenario totals: candidates + blank/null + undecided should be ~100 on total basis
    tot = df[df["share_basis"] == "total"].groupby(["poll_id", "scenario"]).agg(
        cand=("share_reported", "sum"), bn=("blank_null", "first"), und=("undecided", "first")
    )
    total = tot["cand"] + tot["bn"].fillna(0) + tot["und"].fillna(0)
    over = total > 101.5
    if over.any():
        problems.append(f"total-basis poll-scenarios summing above 101.5%: {int(over.sum())}")
    val = df[df["share_basis"] == "valid"].groupby(["poll_id", "scenario"])["share_reported"].sum()
    off = (val - 100).abs() > 2.0
    if off.any():
        problems.append(f"valid-basis poll-scenarios not summing to 100 +/- 2: {int(off.sum())}")

    if require_valid_share:
        v = pd.to_numeric(df["valid_vote_share"], errors="coerce")
        bad(v.isna() | (v < 0) | (v > 100), "valid_vote_share missing or outside [0,100]")
    return problems


def assert_valid(df: pd.DataFrame, **kw) -> None:
    problems = validate_polls(df, **kw)
    if problems:
        raise SchemaError("; ".join(problems))

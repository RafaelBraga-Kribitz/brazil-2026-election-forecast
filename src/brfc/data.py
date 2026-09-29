"""Loaders for the curated datasets. Poll loading and result loading are deliberately separate modules-level
functions: forecasting code paths only ever call `load_polls`."""

from __future__ import annotations

import pandas as pd

from brfc import config
from brfc.names import canonical_candidate, canonical_pollster

POLL_FILES = {e: config.DATA / "interim" / f"polls_wiki_{e}.csv" for e in ("2014", "2018", "2022", "2026")}
RESULTS_FILE = config.DATA / "manual" / "results_secondary.csv"


def load_polls(elections: tuple[str, ...] = ("2014", "2018", "2022")) -> pd.DataFrame:
    frames = []
    for e in elections:
        d = pd.read_csv(POLL_FILES[e], dtype={"election": str, "tse_br_id": str, "publication_date": str})
        d["election"] = d["election"].astype(str)
        frames.append(d)
    rel = config.DATA / "interim" / "polls_releases.csv"
    if rel.exists():  # final polls missing from Wikipedia, added from release records (PREREG Addendum 02)
        r = pd.read_csv(rel, dtype={"election": str, "tse_br_id": str, "publication_date": str})
        frames.append(r[r["election"].astype(str).isin(elections)])
    d = pd.concat(frames, ignore_index=True)
    d["round"] = d["round"].astype(int)
    d["sample_size"] = pd.to_numeric(d["sample_size"], errors="coerce")
    for c in ("share_reported", "blank_null", "undecided"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d["publication_date"] = d["publication_date"].fillna("")
    d = d.dropna(subset=["sample_size", "share_reported", "field_end"])
    d["pollster"] = d["pollster"].map(canonical_pollster)
    d["candidate"] = d["candidate"].map(canonical_candidate)
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

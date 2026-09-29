"""Deterministic poll transformations: valid-vote conversion, scenario selection, dependence handling,
information-time filtering and candidate grouping. Every function is pure (DataFrame in, DataFrame out)."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from brfc import config
from brfc.names import canonical_candidate, canonical_pollster
from brfc.schema import OTHERS


def normalise_names(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["pollster"] = out["pollster"].map(canonical_pollster)
    out["candidate"] = out["candidate"].map(canonical_candidate)
    return out


def to_valid_votes(df: pd.DataFrame) -> pd.DataFrame:
    """Valid-vote share = candidate share / sum of all candidate shares in the same poll-scenario.

    Modelling assumption (not an observed fact): undecided respondents are allocated proportionally to the
    stated candidate shares, and stated blank/null intentions are excluded, mirroring how the TSE excludes
    blank and null ballots from valid votes. `share_reported` is preserved unchanged.
    Rows already on a valid basis are rescaled to sum to exactly 100 (removes rounding drift only).
    """
    out = df.copy()
    denom = out.groupby(["poll_id", "scenario"])["share_reported"].transform("sum")
    out["valid_vote_share"] = np.where(denom > 0, 100.0 * out["share_reported"] / denom, np.nan)
    return out


def select_scenarios(df: pd.DataFrame, election: str, round_: int) -> pd.DataFrame:
    """Keep one scenario per poll: the one matching the registered ballot (round 1) or the actual runoff pair.

    Round 1: drop scenarios that include a material (>= 3%) candidate not on the registered ballot; among the rest prefer
    total-basis tables (so one conversion rule applies to all rows), then fewest off-ballot names, then the
    scenario with most ballot candidates,
    then the lexicographically first label (deterministic tie-break).
    Round 2: keep only the scenario whose candidate set equals the runoff pair.
    """
    d = df[(df["election"].astype(str) == election) & (df["round"] == round_)].copy()
    if d.empty:
        return d
    if round_ == 1:
        ballot = set(config.BALLOTS[election])
        if not ballot:
            raise ValueError(f"ballot for {election} not configured")
        named = d[d["candidate"] != OTHERS]
        # an off-ballot name disqualifies a scenario only if it is material (>= NAMED_THRESHOLD_PCT); smaller
        # off-ballot shares (e.g. a late-withdrawn minor candidacy) fall into the residual "Others" category
        material = ~named["candidate"].isin(ballot) & (named["share_reported"] >= config.NAMED_THRESHOLD_PCT)
        off_ballot = named.assign(off=material).groupby(["poll_id", "scenario"])["off"].any()
        n_ballot = named[named["candidate"].isin(ballot)].groupby(["poll_id", "scenario"])["candidate"].nunique()
        n_off = named[~named["candidate"].isin(ballot)].groupby(["poll_id", "scenario"])["candidate"].nunique()
        basis = d.groupby(["poll_id", "scenario"])["share_basis"].first()
        cand = pd.DataFrame({"off": off_ballot, "n": n_ballot, "n_off": n_off, "basis": basis})
        cand = cand.fillna({"off": True, "n": 0, "n_off": 0})
        cand = cand[~cand["off"].astype(bool)].reset_index()
        cand["basis_rank"] = (cand["basis"] != "total").astype(int)
        # PREREG_ADDENDUM_01: fewest off-ballot names first (e.g. prefer "without X" when X's candidacy was revoked)
        cand = cand.sort_values(["poll_id", "basis_rank", "n_off", "n", "scenario"],
                                ascending=[True, True, True, False, True])
        keep = cand.drop_duplicates("poll_id")[["poll_id", "scenario"]]
    else:
        pair = set(config.RUNOFF_PAIRS[election]) if election in config.RUNOFF_PAIRS else None
        if pair is None:
            raise ValueError(f"runoff pair for {election} not configured")
        sets = d.groupby(["poll_id", "scenario"])["candidate"].agg(lambda s: frozenset(s) - {OTHERS})
        basis = d.groupby(["poll_id", "scenario"])["share_basis"].first()
        cand = pd.DataFrame({"set": sets, "basis": basis}).reset_index()
        cand = cand[cand["set"] == frozenset(pair)]
        cand["basis_rank"] = (cand["basis"] != "total").astype(int)
        keep = cand.sort_values(["poll_id", "basis_rank", "scenario"]).drop_duplicates("poll_id")[["poll_id", "scenario"]]
    return d.merge(keep, on=["poll_id", "scenario"], how="inner")


def drop_overlapping_waves(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Poll-dependence rule: within a pollster, fieldwork windows must not overlap.

    Walking backwards from each pollster's most recent poll, an earlier poll is dropped when its fieldwork
    ends on or after the start of the most recent *kept* poll (rolling/tracking waves share respondents).
    Returns (kept_rows, dropped_polls).
    """
    polls = df.drop_duplicates("poll_id")[["poll_id", "pollster", "field_start", "field_end"]].copy()
    polls["fs"] = pd.to_datetime(polls["field_start"]).fillna(pd.to_datetime(polls["field_end"]))
    polls["fe"] = pd.to_datetime(polls["field_end"])
    keep_ids, drop_ids = [], []
    for _, g in polls.sort_values(["pollster", "fe", "fs", "poll_id"], ascending=[True, False, False, True]).groupby(
        "pollster", sort=True
    ):
        last_start = None
        for row in g.itertuples():
            if last_start is not None and row.fe >= last_start:
                drop_ids.append(row.poll_id)
                continue
            keep_ids.append(row.poll_id)
            last_start = row.fs
    kept = df[df["poll_id"].isin(keep_ids)].copy()
    dropped = polls[polls["poll_id"].isin(drop_ids)][["poll_id", "pollster", "field_start", "field_end"]]
    return kept, dropped


def available_at(df: pd.DataFrame, cutoff: date) -> pd.DataFrame:
    """Information-time rule: a poll is usable at `cutoff` only if its fieldwork ended on or before `cutoff`
    and, when a publication date is recorded, it was published on or before `cutoff`."""
    c = pd.Timestamp(cutoff)
    fe = pd.to_datetime(df["field_end"])
    pub = pd.to_datetime(df["publication_date"].astype(str).replace({"": None, "nan": None}), errors="coerce")
    ok = (fe <= c) & (pub.isna() | (pub <= c))
    return df[ok.to_numpy()].copy()


def cutoff_for(election: str, round_: int, horizon_days: int) -> date:
    """Last calendar day whose information may be used for a forecast made `horizon_days` before election day."""
    return config.ELECTION_DATES[(election, round_)] - timedelta(days=horizon_days)


def window_start(election: str, round_: int) -> date:
    e = config.ELECTION_DATES[(election, round_)]
    if round_ == 1:
        return e - timedelta(days=config.WINDOW_DAYS_R1)
    return config.ELECTION_DATES[(election, 1)] + timedelta(days=1)


def choose_named(df_valid: pd.DataFrame, cutoff: date) -> list[str]:
    """Candidates modelled individually, chosen only from information available at `cutoff`."""
    lo = cutoff - timedelta(days=config.NAMED_LOOKBACK_DAYS - 1)
    fe = pd.to_datetime(df_valid["field_end"]).dt.date
    recent = df_valid[(fe >= lo) & (fe <= cutoff) & (df_valid["candidate"] != OTHERS)]
    if recent.empty:
        recent = df_valid[(fe <= cutoff) & (df_valid["candidate"] != OTHERS)]
    n_polls = recent["poll_id"].nunique()
    mean_share = recent.groupby("candidate")["valid_vote_share"].sum() / max(n_polls, 1)
    named = mean_share[mean_share >= config.NAMED_THRESHOLD_PCT].sort_values(ascending=False)
    return list(named.index[: config.MAX_NAMED])


def series_table(df_valid: pd.DataFrame, named: list[str]) -> pd.DataFrame:
    """Wide table, one row per poll: valid shares of each named candidate plus 'Others' (= 100 - named).

    A named candidate absent from a poll gets NaN (that poll then carries no information about them)."""
    meta_cols = ["poll_id", "pollster", "field_start", "field_end", "sample_size"]
    meta = df_valid.drop_duplicates("poll_id")[meta_cols].set_index("poll_id")
    wide = df_valid[df_valid["candidate"].isin(named)].pivot_table(
        index="poll_id", columns="candidate", values="valid_vote_share", aggfunc="first"
    )
    wide = wide.reindex(index=meta.index, columns=named)
    complete = wide.notna().all(axis=1)
    others = (100.0 - wide.sum(axis=1)).where(complete)
    if len(named) > 1:  # round 1: residual category
        wide[config.OTHERS_LABEL] = others
    out = meta.join(wide).reset_index()
    mid = pd.to_datetime(out["field_start"]).fillna(pd.to_datetime(out["field_end"]))
    fe = pd.to_datetime(out["field_end"])
    out["field_mid"] = (mid + (fe - mid) / 2).dt.normalize().dt.date
    return out.sort_values(["field_end", "pollster", "poll_id"]).reset_index(drop=True)


def prepare(df_all: pd.DataFrame, election: str, round_: int) -> pd.DataFrame:
    """Deterministic path from canonical parser rows to valid-vote rows for one round (whole window).

    Information-time filtering and the dependence rule are applied later, per forecast cutoff
    (see `polls_for_forecast`), so that no future poll can influence which earlier polls are used.
    """
    d = normalise_names(df_all)
    d = select_scenarios(d, election, round_)
    lo = window_start(election, round_)
    hi = config.ELECTION_DATES[(election, round_)] - timedelta(days=1)
    fe = pd.to_datetime(d["field_end"]).dt.date
    d = d[(fe >= lo) & (fe <= hi)]
    return to_valid_votes(d).reset_index(drop=True)


def polls_for_forecast(valid_rows: pd.DataFrame, cutoff: date, named: list[str] | None = None):
    """Information set for one forecast: available polls -> dependence rule -> candidate grouping -> wide table.

    Returns (series_wide, named, dropped_overlapping_polls, long_rows_used)."""
    avail = available_at(valid_rows, cutoff)
    kept, dropped = drop_overlapping_waves(avail)
    if named is None:
        named = choose_named(kept, cutoff)
    return series_table(kept, named), named, dropped, kept

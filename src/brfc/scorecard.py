"""2026 scorecard (PREREG_ADDENDUM_06): scores the frozen packages against the entered TSE result.

Evaluation only. It reads a frozen package (outputs/freeze/ or outputs/freeze_runoff/) and
data/manual/results_2026.csv; no forecast code imports it. Model scores use brfc.scoring, the functions the
backtest used; baselines keep the backtest rule (point value for MAE and margin, conversion draws for the rest).
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

from brfc import config, scoring
from brfc.conditional import score_president

LOG_FLOOR = 1e-4
OTHER = "other"
RESULTS_2026 = config.DATA / "manual" / "results_2026.csv"
MODELS = ("F", "E", "E0")
POINT_TOL = 2.0  # pp: displayed probabilities may exceed 100 by rounding only
POINT_METRICS = ("mae", "margin_pred", "margin_error", "margin_abs_error")


# ---------------------------------------------------------------- entered result
def load_results_2026(path: Path = RESULTS_2026, round_: int = 1) -> dict:
    """Vote counts of one 2026 round, checked as Addendum 06 s.2 requires. Raises ValueError on any problem."""
    r = pd.read_csv(path, dtype={"election": str})
    r = r[(r["election"] == "2026") & (r["round"].astype(int) == round_)]
    if r.empty:
        raise ValueError(f"{path}: no 2026 round-{round_} rows")
    names = r["candidate"].tolist()
    ballot = config.BALLOTS["2026"]
    problems = []
    if len(set(names)) != len(names):
        problems.append(f"duplicate candidates: {sorted({n for n in names if names.count(n) > 1})}")
    if unknown := sorted(set(names) - set(ballot)):
        problems.append(f"names not on the 2026 ballot: {unknown}")
    if round_ == 1 and (missing := [c for c in ballot if c not in names]):
        problems.append(f"ballot candidates without a row: {missing}")
    if round_ == 2 and len(names) != 2:
        problems.append(f"a runoff has two candidates, found {len(names)}")
    votes = pd.to_numeric(r["votes"], errors="coerce")
    if votes.isna().any() or (votes < 0).any() or (votes != votes.round()).any():
        problems.append("votes must be non-negative integers")
    totals = pd.to_numeric(r["total_valid_votes"], errors="coerce").unique()
    if len(totals) != 1 or not np.isfinite(totals[0]):
        problems.append(f"total_valid_votes must be one value per round, found {list(totals)}")
    elif not votes.isna().any() and int(votes.sum()) != int(totals[0]):
        problems.append(f"candidate votes sum to {int(votes.sum())}, total_valid_votes is {int(totals[0])}")
    if problems:
        raise ValueError(f"{path} round {round_}: " + "; ".join(problems))
    first = r.iloc[0]
    return {
        "round": round_,
        "votes": {c: int(v) for c, v in zip(names, votes, strict=True)},
        "total_valid_votes": int(totals[0]),
        "blank_votes": None if pd.isna(first.get("blank_votes")) else int(first["blank_votes"]),
        "null_votes": None if pd.isna(first.get("null_votes")) else int(first["null_votes"]),
        "source_url": first.get("source_url"),
        "retrieved_utc": first.get("retrieved_utc"),
    }


def valid_shares(result: dict) -> dict[str, float]:
    total = result["total_valid_votes"]
    return {c: 100.0 * v / total for c, v in result["votes"].items()}


def actual_categories(shares: dict[str, float], categories: list[str]) -> dict[str, float]:
    """Observed share per forecast category: named candidates by name, Others = 100 minus the named shares."""
    named = [c for c in categories if c != config.OTHERS_LABEL]
    if missing := [c for c in named if c not in shares]:
        raise ValueError(f"forecast categories without a result row: {missing}")
    out = {c: shares[c] for c in named}
    if config.OTHERS_LABEL in categories:
        out[config.OTHERS_LABEL] = 100.0 - sum(out.values())
    return {c: out[c] for c in categories}


# ---------------------------------------------------------------- share forecasts
def baseline_points(snapshot: pd.DataFrame, categories: list[str]) -> dict[str, dict[str, float]]:
    """Point values of the baselines B, C, D in a package's baseline_snapshot.csv (rows with status 'ok')."""
    if "baseline" not in snapshot:
        return {}
    out = {}
    for _, row in snapshot[snapshot["baseline"].notna() & (snapshot["status"] == "ok")].iterrows():
        cols = [f"point_{c}" for c in categories]
        if all(c in row and pd.notna(row[c]) for c in cols):
            out[str(row["baseline"])] = {c: float(row[f"point_{c}"]) for c in categories}
    return out


def score_share_models(
    draws: dict[str, np.ndarray],
    points: dict[str, dict[str, float]],
    categories: list[str],
    actual: dict[str, float],
    round_: int,
    baselines: tuple[str, ...],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Per-model and per-category scores. Models on draws; baselines with the backtest rule."""
    rows, cats = [], []
    for m in MODELS:
        if m not in draws:
            continue
        rows.append({"model": m, "status": "ok"} | scoring.score(draws[m], None, categories, actual, round_))
        cats += [{"model": m} | x for x in scoring.category_rows(draws[m], None, categories, actual)]
    for b in baselines:
        p = points.get(b)
        if p is None:
            rows.append({"model": b, "status": "N/A: no point value in the frozen baseline snapshot"})
            continue
        pm = scoring.score(None, p, categories, actual, round_)
        if b not in draws:
            rows.append({"model": b, "status": "point only: no conversion draws in the package"} | pm)
            cats += [{"model": b} | x for x in scoring.category_rows(None, p, categories, actual)]
            continue
        s = scoring.score(draws[b], None, categories, actual, round_) | {k: pm[k] for k in POINT_METRICS}
        rows.append({"model": b, "status": "calibrated probabilistic conversion of point baseline"} | s)
        for x in scoring.category_rows(draws[b], None, categories, actual):
            x["mean"], x["error"] = p[x["category"]], p[x["category"]] - actual[x["category"]]
            cats.append({"model": b} | x)
    return pd.DataFrame(rows), pd.DataFrame(cats)


def pollingdata_point(snapshot: pd.DataFrame, categories: list[str]) -> tuple[dict[str, float] | None, str]:
    """Baseline A: PollingData valid shares for the categories it displayed (Addendum 06 s.3)."""
    if "benchmark" not in snapshot:
        return None, "N/A: no PollingData reading in the package"
    rows = snapshot[(snapshot["benchmark"] == "PollingData") & (snapshot["event_kind"] == "share")]
    shown = {
        str(r["candidate"]): float(r["valid_share_pct"])
        for _, r in rows.iterrows()
        if isinstance(r["candidate"], str) and r["candidate"]
    }
    if not shown:
        return None, "N/A: no PollingData reading in the package"
    named = [c for c in categories if c != config.OTHERS_LABEL]
    point = {c: shown[c] for c in named if c in shown}
    rest = [c for c in config.BALLOTS["2026"] if c not in named]
    note = "named candidates as displayed"
    if config.OTHERS_LABEL in categories:
        if all(c in shown for c in rest):
            point[config.OTHERS_LABEL] = sum(shown[c] for c in rest)
            note += "; Others = sum of the displayed ballot candidates outside the named set"
        else:
            note += "; Others not scored (not every other ballot candidate was displayed)"
    if missing := [c for c in named if c not in point]:
        note += f"; not displayed: {missing}"
    return point, note


def _subset_mae(estimate: dict[str, float], actual: dict[str, float], subset: list[str]) -> float:
    return float(np.mean([abs(estimate[c] - actual[c]) for c in subset]))


def score_pollingdata(
    point: dict[str, float] | None, draws_f: np.ndarray, categories: list[str], actual: dict[str, float]
) -> dict:
    if not point:
        return {"model": "A", "status": "N/A: no PollingData reading in the package"}
    subset = [c for c in categories if c in point]
    f_mean = dict(zip(categories, draws_f.mean(axis=0), strict=True))
    named = [c for c in categories if c != config.OTHERS_LABEL]
    top = sorted(named, key=lambda c: -actual[c])[:2]
    out = {
        "model": "A",
        "status": "point only (PollingData published average, valid-vote basis)",
        "categories_scored": "|".join(subset),
        "mae_subset": _subset_mae(point, actual, subset),
        "F_mae_subset": _subset_mae(f_mean, actual, subset),
    }
    if all(c in point for c in top):
        out["margin_pred"] = point[top[0]] - point[top[1]]
        out["margin_actual"] = actual[top[0]] - actual[top[1]]
        out["margin_error"] = out["margin_pred"] - out["margin_actual"]
        out["margin_abs_error"] = abs(out["margin_error"])
    if len(subset) == len(categories):
        out["mae"] = out["mae_subset"]
    return out


def criteria_r1(scores: pd.DataFrame, a: dict) -> dict:
    """L1-L3 (PREREG s.12), each reported as met / not met / N/A."""
    s = scores.set_index("model")

    def val(m, col):
        return float(s.loc[m, col]) if m in s.index and col in s and pd.notna(s.loc[m, col]) else None

    f_mae, b_mae = val("F", "mae"), val("B", "mae")
    l1 = {"F_mae": f_mae, "B_mae": b_mae, "met_vs_B": None if b_mae is None else f_mae <= b_mae}
    if "mae_subset" in a:
        l1 |= {
            "A_categories": a["categories_scored"].split("|"),
            "A_mae_subset": a["mae_subset"],
            "F_mae_subset": a["F_mae_subset"],
            "met_vs_A": a["F_mae_subset"] <= a["mae_subset"],
            "assessed_against": "A (same categories) and B (all categories)",
        }
        l1["met"] = None if l1["met_vs_B"] is None else bool(l1["met_vs_A"] and l1["met_vs_B"])
    else:
        l1 |= {"assessed_against": "B only (no PollingData reading at the freeze)", "met": l1["met_vs_B"]}
    f_m, b_m = val("F", "margin_abs_error"), val("B", "margin_abs_error")
    l2 = {"F_margin_abs_error": f_m, "B_margin_abs_error": b_m, "met": None if b_m is None else f_m <= b_m}
    return {"L1": l1, "L2": l2}


def coverage_counts(categories_df: pd.DataFrame, model: str = "F") -> dict:
    d = categories_df[categories_df["model"] == model]
    return {
        "model": model,
        "n_categories": len(d),
        "inside_94": int(d["in94"].sum()),
        "inside_80": int(d["in80"].sum()),
        "outside_94": d.loc[~d["in94"].astype(bool), "category"].tolist(),
    }


# ---------------------------------------------------------------- benchmarks (priced events only)
def _candidate_or_none(x) -> str | None:
    return x if isinstance(x, str) and x else None


def market_probabilities(snapshot: pd.DataFrame, event_kind: str, named: list[str]) -> dict[str, float] | None:
    """Polymarket yes prices of every priced market of an event, normalised to 1; non-named outcomes -> 'other'."""
    if "benchmark" not in snapshot:
        return None
    rows = snapshot[
        (snapshot["benchmark"] == "Polymarket") & (snapshot["event_kind"] == event_kind) & snapshot["yes_price"].notna()
    ]
    total = float(rows["yes_price"].sum()) if len(rows) else 0.0
    if total <= 0.0:
        return None
    p = dict.fromkeys([*named, OTHER], 0.0)
    for _, r in rows.iterrows():
        c = _candidate_or_none(r["candidate"])
        p[c if c in named else OTHER] += float(r["yes_price"]) / total
    return p


def market_binary(snapshot: pd.DataFrame, event_kind: str) -> float | None:
    """Yes price of a single-market (binary) event."""
    if "benchmark" not in snapshot:
        return None
    rows = snapshot[
        (snapshot["benchmark"] == "Polymarket") & (snapshot["event_kind"] == event_kind) & snapshot["yes_price"].notna()
    ]
    if len(rows) != 1:
        return None
    return float(rows["yes_price"].iloc[0])


def displayed_probabilities(snapshot: pd.DataFrame, probability_event: str, named: list[str]) -> dict | None:
    """PollingData probabilities as displayed (percent -> 0..1); remainder below 100 -> 'other' (Addendum 06 s.3)."""
    if "benchmark" not in snapshot or "probability_event" not in snapshot:
        return None
    rows = snapshot[
        (snapshot["benchmark"] == "PollingData")
        & (snapshot["event_kind"] == "pollingdata_win_probability")
        & (snapshot["probability_event"] == probability_event)
    ]
    if rows.empty:
        return None
    p = dict.fromkeys([*named, OTHER], 0.0)
    for _, r in rows.iterrows():
        c = _candidate_or_none(r["candidate"])
        p[c if c in named else OTHER] += float(r["value_displayed_pct"]) / 100.0
    total = sum(p.values())
    if total > 1.0 + POINT_TOL / 100.0:
        raise ValueError(f"displayed {probability_event} probabilities sum to {100 * total:.1f}%")
    if total > 1.0:
        p = {c: v / total for c, v in p.items()}
    else:
        p[OTHER] += 1.0 - total
    return p


def displayed_binary(snapshot: pd.DataFrame, probability_event: str) -> float | None:
    """A single probability PollingData displayed for a yes/no event (percent -> 0..1)."""
    if "benchmark" not in snapshot or "probability_event" not in snapshot:
        return None
    rows = snapshot[
        (snapshot["benchmark"] == "PollingData")
        & (snapshot["event_kind"] == "pollingdata_win_probability")
        & (snapshot["probability_event"] == probability_event)
    ]
    return None if len(rows) != 1 else float(rows["value_displayed_pct"].iloc[0]) / 100.0


def multiclass_brier(p: dict[str, float], outcome: str) -> float:
    return float(sum((p.get(k, 0.0) - (k == outcome)) ** 2 for k in set(p) | {outcome}))


def log_score(p: dict[str, float], outcome: str) -> float:
    return math.log(max(p.get(outcome, 0.0), LOG_FLOOR))


def score_categorical(name: str, event: str, p: dict[str, float] | None, outcome: str) -> dict:
    base = {"benchmark": name, "event": event, "outcome": outcome}
    if p is None:
        return base | {"status": "N/A: event not priced or not displayed at the freeze"}
    return base | {
        "status": "ok",
        "p_actual": p.get(outcome, 0.0),
        "brier": multiclass_brier(p, outcome),
        "log": log_score(p, outcome),
        "probabilities": p,
    }


def score_binary(name: str, event: str, p: float | None, outcome: bool) -> dict:
    base = {"benchmark": name, "event": event, "outcome": bool(outcome)}
    if p is None:
        return base | {"status": "N/A: event not priced or not displayed at the freeze"}
    return base | {"status": "ok", "p_yes": p, "brier": (p - float(outcome)) ** 2}


# ---------------------------------------------------------------- who was elected
def score_president_doc(doc: dict, elected: str) -> pd.DataFrame:
    """Addendum 04 s.5 scores of every combination in a frozen president.json."""
    rows = []
    for label, alt in doc["alternatives"].items():
        s = score_president(alt["probabilities"], alt["unmodelled"], elected)
        rows.append(
            {
                "combination": label,
                "r1_model": alt["r1_model"],
                "h2h_model": alt["h2h_model"],
                "primary": bool(alt.get("primary")),
                "unmodelled": alt["unmodelled"],
                "elected": elected,
            }
            | s
        )
    return pd.DataFrame(rows)

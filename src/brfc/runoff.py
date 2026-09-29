"""Pre-first-round runoff forecasts from head-to-head (h2h) polls, and the probability of being elected president.

Design (recorded in PREREG_ADDENDUM_04):

- Information. A head-to-head poll is a round-2 poll scenario whose fieldwork ended BEFORE the first-round election
  day E1 (national, stimulated) and whose candidate set is exactly the pair. Window: fieldwork ending in the
  WINDOW_DAYS_R1 days before E1. At each cutoff the valid-vote conversion, the information-time filter and the
  dependence rule of brfc.transform are applied as in `transform.polls_for_forecast` (dependence rule after the
  filter, so a later poll can never decide which earlier poll is used).
- Pairs. Key = alphabetical pair (brfc.conditional.pair_key); the modelled series is pair[0]'s valid share and
  pair[1] = 100 - pair[0]. A pair of registered ballot candidates is fitted only if at least MIN_POLLS_PER_FIT of
  its polls are available at the cutoff.
- Random walk. brfc.model.fit with window_start = E1 - WINDOW_DAYS_R1, cutoff = E1 - r1_horizon, election_day = E2
  (runoff day) and the pre-registered retry loop (model.ATTEMPTS). r1_horizon 1 = first-round eve, 7 = T-7.
- Election-day term. deviation = official runoff valid share of the forecast-rank-1 candidate of the actual pair
  minus its eve h2h forecast mean (latent, projected), in a separate deviation table (round label "h2h"),
  estimated leave-one-election-out with brfc.election_day. Primary variant E (zero mean); F and E0 reported.
- Combination. brfc.conditional: P(X elected) = P(X outright) + sum over pairs of P(pair) * P(X wins | pair), with
  first-round paths from first-round model draws at the same cutoff. First-round and h2h draws are treated as
  independent. A pair without an h2h fit is "unmodelled" and never reallocated. Combinations (first-round model x
  h2h model): F x E (primary), F x F and E x E (Addendum 04 s.4).
- Cache. h2h fits live in their own directory H2H_CACHE (data/cache/fits_h2h/), never in pipeline.CACHE, so the
  first-round/runoff repair and diagnostics scripts cannot pick them up (Addendum 05 s.5). Every fit function
  returns the stored (float32) draws, so fresh and cached runs are bit-identical (Addendum 05 s.6).

Forecast functions (`h2h_rows`, `h2h_pairs`, `fit_h2h`, `h2h_forecast`, `first_round_forecast_draws`) never receive
the actual runoff pair, first-round results or runoff results. The only results they touch are the historical
results passed to `first_round_forecast_draws` for its leave-one-election-out term; evaluation passes only the OTHER
elections' results, and the target election is removed again before any computation. Evaluation code
(`h2h_deviations`, `evaluate_backtest`, `elected_candidate`, `success_criteria`) is the only code here that reads the
actual runoff pairs and the target election's results.
"""

from __future__ import annotations

import json
import re
import unicodedata
import zlib
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from brfc import baselines, conditional, config, election_day, model, pipeline, scoring
from brfc.schema import OTHERS
from brfc.transform import (
    available_at,
    cutoff_for,
    drop_overlapping_waves,
    normalise_names,
    series_table,
    to_valid_votes,
)

H2H_CACHE = config.DATA / "cache" / "fits_h2h"  # h2h fits only (never pipeline.CACHE)
ROUND_LABEL = "h2h"  # round label of the h2h deviation table and of h2h baseline errors
H2H_HORIZONS = (1, 7)  # first-round horizons (days before E1) of the h2h backtest
H2H_ROLES = ["rank1"]
PRIMARY_H2H_MODEL = "E"
H2H_MODELS = ("E", "F", "E0")
H2H_BASELINES = {"B": None, "C": "Datafolha"}
# (first-round model, h2h model) combinations of the probability of being elected (Addendum 04 s.4)
COMBINATIONS = (("F", "E"), ("F", "F"), ("E", "E"))
PRIMARY_COMBINATION = ("F", "E")
# error-model scale priors of the h2h term in the sensitivity loop (Addendum 04 s.5; registered default 3)
SENSITIVITY_PRIORS = {"sig1.5": {"sigma_scale": 1.5}, "sig6": {"sigma_scale": 6.0}}
SEED_2026 = 20261004
DEVIATION_COLUMNS = [
    "election",
    "round",
    "pair",
    "category",
    "role",
    "forecast",
    "actual",
    "deviation",
    "cutoff",
    "r1_horizon",
    "n_polls",
    "fit_key",
    "converged",
]
BASELINE_ERROR_COLUMNS = ["baseline", "election", "round", "horizon", "category", "error"]


# ---------------------------------------------------------------------------------------------------------------
# information set (polls only)
# ---------------------------------------------------------------------------------------------------------------


def first_round_day(election: str) -> date:
    return config.ELECTION_DATES[(election, 1)]


def runoff_day(election: str) -> date:
    return config.ELECTION_DATES[(election, 2)]


def h2h_window_start(election: str) -> date:
    return first_round_day(election) - timedelta(days=config.WINDOW_DAYS_R1)


def h2h_cutoff(election: str, r1_horizon: int) -> date:
    """Information cutoff of an h2h forecast made `r1_horizon` days before the first round."""
    return cutoff_for(election, 1, r1_horizon)


def _window_rows(polls: pd.DataFrame, election: str) -> pd.DataFrame:
    """Normalised round-2 rows of `election` with fieldwork ending in the first-round window, strictly before E1."""
    d = polls[(polls["election"].astype(str) == election) & (polls["round"].astype(int) == 2)]
    if d.empty:
        return d.copy()
    d = normalise_names(d)
    fe = pd.to_datetime(d["field_end"]).dt.date
    return d[(fe < first_round_day(election)) & (fe >= h2h_window_start(election))].copy()


def _scenario_sets(d: pd.DataFrame) -> pd.DataFrame:
    """One row per poll scenario: its set of named candidates and its share basis."""
    g = d.groupby(["poll_id", "scenario"])
    sets = g["candidate"].agg(lambda s: frozenset(s) - {OTHERS})
    return pd.DataFrame({"set": sets, "basis": g["share_basis"].first()}).reset_index()


def _select_pair(d: pd.DataFrame, pair: tuple[str, str]) -> pd.DataFrame:
    """One scenario per poll whose candidate set equals the pair: total-basis tables first, then the first label
    (the round-2 tie-break of transform.select_scenarios)."""
    if d.empty:
        return d.copy()
    target = frozenset(pair)
    cand = _scenario_sets(d)
    cand = cand[cand["set"].map(lambda s: s == target)].copy()
    cand["basis_rank"] = (cand["basis"] != "total").astype(int)
    keep = cand.sort_values(["poll_id", "basis_rank", "scenario"]).drop_duplicates("poll_id")[["poll_id", "scenario"]]
    return d.merge(keep, on=["poll_id", "scenario"], how="inner")


def _pair_information_set(d_window: pd.DataFrame, pair: tuple[str, str], cutoff: date):
    """(kept_rows, dropped_polls): valid-vote conversion -> information-time filter -> dependence rule."""
    valid = to_valid_votes(_select_pair(d_window, pair)).reset_index(drop=True)
    if valid.empty:
        return valid, pd.DataFrame(columns=["poll_id", "pollster", "field_start", "field_end"])
    kept, dropped = drop_overlapping_waves(available_at(valid, cutoff))
    return kept.reset_index(drop=True), dropped


def h2h_rows(polls: pd.DataFrame, election: str, pair: tuple[str, str], cutoff: date) -> pd.DataFrame:
    """Long valid-vote rows of the pair's head-to-head polls in the information set at `cutoff`.

    Round-2 scenarios whose named-candidate set equals set(pair), fieldwork ending in [E1 - WINDOW_DAYS_R1, E1),
    names normalised, valid-vote conversion, information-time filter at `cutoff`, then the dependence rule."""
    key = conditional.pair_key(*pair)
    kept, _ = _pair_information_set(_window_rows(polls, election), key, cutoff)
    return kept


def h2h_pairs(polls: pd.DataFrame, election: str, cutoff: date) -> list[tuple[str, str]]:
    """Alphabetical pairs of registered ballot candidates (config.BALLOTS) with at least MIN_POLLS_PER_FIT
    head-to-head polls in the information set at `cutoff`."""
    ballot = set(config.BALLOTS[election])
    d = _window_rows(polls, election)
    if d.empty:
        return []
    found = sorted({conditional.pair_key(*sorted(s)) for s in _scenario_sets(d)["set"] if len(s) == 2 and s <= ballot})
    out = []
    for key in found:
        kept, _ = _pair_information_set(d, key, cutoff)
        if kept["poll_id"].nunique() >= config.MIN_POLLS_PER_FIT:
            out.append(key)
    return out


# ---------------------------------------------------------------------------------------------------------------
# h2h random walk (stage 1, polls only)
# ---------------------------------------------------------------------------------------------------------------


def _slug(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def pair_slug(pair: tuple[str, str]) -> str:
    """File-name form of a pair key, e.g. "fernando-haddad_vs_jair-bolsonaro"."""
    return "_vs_".join(_slug(c) for c in conditional.pair_key(*pair))


def h2h_key(election: str, pair: tuple[str, str], r1_horizon: int, variant: str, tag: str = "") -> str:
    return f"{election}_h2h_{pair_slug(pair)}_r1h{r1_horizon:02d}_{variant}{('_' + tag) if tag else ''}"


def load_h2h(
    election: str, pair: tuple[str, str], r1_horizon: int, variant: str, *, tag: str = "", cache: Path = H2H_CACHE
) -> pipeline.CachedFit | None:
    """Cached h2h fit, or None when it is missing or was skipped (too few polls)."""
    key = h2h_key(election, pair, r1_horizon, variant, tag)
    return pipeline.load_fit(key, Path(cache)) if (Path(cache) / f"{key}.json").exists() else None


def fit_h2h(
    polls: pd.DataFrame,
    election: str,
    pair: tuple[str, str],
    r1_horizon: int,
    variant: str,
    *,
    tag: str = "",
    force: bool = False,
    cache: Path = H2H_CACHE,
    sampler: dict | None = None,
    start_attempt: int = 0,
) -> pipeline.CachedFit | None:
    """Fit (or load from the cache) the h2h random walk of `pair` at cutoff E1 - r1_horizon. None if skipped.

    `start_attempt` (0-based index into model.ATTEMPTS) starts the retry loop later: 2 = the third attempt of
    Addendum 03 (repair of a cached fit). Earlier attempts recorded for the same information set are kept in the
    metadata. The returned fit is always read back from the cache, so a fresh fit and a cached fit are identical."""
    if not 0 <= start_attempt < len(model.ATTEMPTS):
        raise ValueError(f"start_attempt must be in [0, {len(model.ATTEMPTS) - 1}], got {start_attempt}")
    key_pair = conditional.pair_key(*pair)
    key = h2h_key(election, key_pair, r1_horizon, variant, tag)
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    if not force and (cache / f"{key}.json").exists():
        return pipeline.load_fit(key, cache)
    previous = {}
    if start_attempt and (cache / f"{key}.json").exists():
        previous = json.loads((cache / f"{key}.json").read_text(encoding="utf-8"))
    cutoff = h2h_cutoff(election, r1_horizon)
    kept, dropped = _pair_information_set(_window_rows(polls, election), key_pair, cutoff)
    series = [key_pair[0]]
    wide = series_table(kept, series) if not kept.empty else pd.DataFrame(columns=["poll_id", "pollster", "field_end"])
    n_polls = int(wide["poll_id"].nunique())
    meta = {
        "key": key,
        "kind": "h2h",
        "election": election,
        "round": 2,
        "horizon": r1_horizon,
        "r1_horizon": r1_horizon,
        "variant": variant,
        "tag": tag,
        "pair": list(key_pair),
        "cutoff": str(cutoff),
        "window_start": str(h2h_window_start(election)),
        "election_day": str(runoff_day(election)),
        "named": series,
        "series": series,
        "n_polls": n_polls,
        "n_pollsters": int(wide["pollster"].nunique()),
        "n_dropped_overlap": len(dropped),
        "max_field_end": str(wide["field_end"].max()) if n_polls else None,
        "poll_ids": sorted(wide["poll_id"].tolist()),
        "priors": {},
        "options": {"dependence_rule": True, "min_polls_per_pollster": 1, "allocation": "proportional"},
        "status": "ok",
    }
    if n_polls < config.MIN_POLLS_PER_FIT:
        meta["status"] = f"skipped: {n_polls} polls < {config.MIN_POLLS_PER_FIT}"
        (cache / f"{key}.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
        return None
    kw = {
        "window_start": h2h_window_start(election),
        "election_day": runoff_day(election),
        "cutoff": cutoff,
        "two_regime": pipeline.VARIANTS[variant],
    }
    attempts = []
    if previous.get("poll_ids") == meta["poll_ids"]:  # same information set: keep the earlier attempts' diagnostics
        attempts = [a for a in previous.get("attempts", []) if int(a.get("attempt", 0)) <= start_attempt]
    for k in range(start_attempt, len(model.ATTEMPTS)):  # pre-registered retries (PREREG s.4, Addendum 03)
        fr = model.fit(wide, series, sampler={**(sampler or {}), **model.ATTEMPTS[k]}, **kw)
        attempts.append({"attempt": k + 1, **fr.diagnostics})
        if fr.diagnostics["converged"]:
            break
    meta["attempts"] = attempts
    meta["diagnostics"] = fr.diagnostics
    np.save(cache / f"{key}.npy", np.asarray(fr.election_day_draws).astype(np.float32))
    fr.path.to_csv(cache / f"{key}.path.csv", index=False)
    fr.house.to_csv(cache / f"{key}.house.csv", index=False)
    fr.params.to_csv(cache / f"{key}.params.csv", index=False)
    kept.to_csv(cache / f"{key}.rows.csv", index=False)
    (cache / f"{key}.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
    return pipeline.load_fit(key, cache)  # stored float32 draws: a fresh run equals a cached run bit for bit


def converged(fit: pipeline.CachedFit | None) -> bool | None:
    if fit is None:
        return None
    return bool((fit.meta.get("diagnostics") or {}).get("converged", False))


def h2h_forecast(
    fit: pipeline.CachedFit,
    post: election_day.ErrorPosterior | None = None,
    roles: dict[str, str] | None = None,
    seed: int = 0,
) -> np.ndarray:
    """(N, 2) runoff valid-share draws for (pair[0], pair[1]): latent walk + optional election-day term, projected.

    Without `roles`, the rank-1 role goes to the candidate with the higher latent forecast mean (forecast ranking,
    never the outcome)."""
    cats = list(fit.meta["pair"])
    latent = election_day.apply(np.asarray(fit.draws, dtype=float), cats, {}, None, 2)
    if post is None:
        return latent
    if roles is None:
        roles = election_day.assign_roles(cats, dict(zip(cats, latent.mean(axis=0), strict=True)), 2)
    return election_day.apply(np.asarray(fit.draws, dtype=float), cats, roles, post, 2, seed=seed)


def share_summary(draws: np.ndarray, pair: tuple[str, str]) -> dict:
    """Runoff valid-share summary of pair[0] (equal-tailed quantiles)."""
    x = np.asarray(draws, dtype=float)[:, 0]
    q = np.quantile(x, [0.03, 0.10, 0.5, 0.90, 0.97])
    return {
        "candidate": pair[0],
        "mean": float(x.mean()),
        "q03": float(q[0]),
        "q10": float(q[1]),
        "median": float(q[2]),
        "q90": float(q[3]),
        "q97": float(q[4]),
    }


# ---------------------------------------------------------------------------------------------------------------
# first-round draws (cached fits; LOEO election-day term exactly as pipeline.evaluate_backtest / forecast.run)
# ---------------------------------------------------------------------------------------------------------------


def _load_r1_fit(election: str, horizon: int, variant: str, tag: str, cache: Path) -> pipeline.CachedFit | None:
    key = pipeline.fit_key(election, 1, horizon, variant, tag)
    return pipeline.load_fit(key, cache) if (cache / f"{key}.json").exists() else None


def _r1_training_set(election: str, variant: str, results_hist: pd.DataFrame, tag: str, cache: Path) -> pd.DataFrame:
    """Round-1 eve deviations of the OTHER historical elections; the target's results are dropped first."""
    others = [e for e in config.HISTORICAL if e != election]
    eve = {}
    for e in others:
        f = _load_r1_fit(e, 1, variant, tag, cache)
        if f is not None:
            eve[(e, 1)] = f
    hist = results_hist[results_hist["election"].astype(str).isin(others)]
    devs = pipeline.eve_deviations(eve, hist)
    if devs.empty:
        return pd.DataFrame(columns=["election", "round", "category", "role", "forecast", "actual", "deviation"])
    return election_day.loeo_training_set(devs, election, 1)


def first_round_forecast(
    election: str,
    r1_horizon: int,
    variant: str,
    results_hist: pd.DataFrame,
    ed_model: str = "F",
    *,
    tag: str = "",
    cache: Path = pipeline.CACHE,
    error_prior: dict | None = None,
) -> dict:
    """`first_round_forecast_draws` with its metadata: {"draws", "categories", "fit", "train_elections",
    "error_model" (ErrorPosterior or None for E0), "roles"}."""
    cache = Path(cache)
    fit = _load_r1_fit(election, r1_horizon, variant, tag, cache)
    if fit is None:
        key = pipeline.fit_key(election, 1, r1_horizon, variant, tag)
        raise FileNotFoundError(f"no cached first-round fit {key} in {cache}")
    series = fit.meta["series"]
    cats = list(series)
    latent = election_day.apply(fit.draws, series, {}, None, 1)
    out = {"fit": fit, "categories": cats, "train_elections": [], "error_model": None, "roles": {}}
    if ed_model == "E0":
        return out | {"draws": latent}
    if ed_model not in ("E", "F"):
        raise ValueError(f"unknown first-round model {ed_model!r}")
    roles = election_day.assign_roles(cats, dict(zip(cats, latent.mean(axis=0), strict=True)), 1)
    historical = election in config.HISTORICAL
    # historical targets: the backtest's own tag; the 2026 forecast trains on the untagged historical fits
    train = _r1_training_set(election, variant, results_hist, tag if historical else "", cache)
    if train.empty:
        raise ValueError(f"no historical first-round eve deviations available for {election}")
    if historical:  # seeds as pipeline.evaluate_backtest
        seed, apply_seed = zlib.crc32(f"{election}|1|{r1_horizon}|{ed_model}".encode()), 1
    else:  # seeds as forecast.run
        seed, apply_seed = SEED_2026 + (ed_model == "F"), SEED_2026
    post = election_day.fit_error_model(train, ed_model, ["rank1", "rank2", "rest"], seed=seed, prior=error_prior)
    draws = election_day.apply(fit.draws, series, roles, post, 1, seed=apply_seed)
    return out | {"draws": draws, "roles": roles, "train_elections": post.train_elections, "error_model": post}


def first_round_forecast_draws(
    election: str,
    r1_horizon: int,
    variant: str,
    results_hist: pd.DataFrame,
    model: str = "F",
    *,
    tag: str = "",
    cache: Path = pipeline.CACHE,
    error_prior: dict | None = None,
) -> tuple[np.ndarray, list[str]]:
    """First-round forecast draws (N, K valid shares, categories incl. "Others") from the cached fit at
    E1 - r1_horizon, with the leave-one-election-out election-day term `model` ("F", "E" or "E0").

    Historical targets reproduce pipeline.evaluate_backtest for that cell; 2026 reproduces forecast.run (trained on
    all three historical elections; pass the status/revision `tag` of the 2026 fit). `results_hist` is used only for
    the election-day term, after the target election's rows are removed."""
    out = first_round_forecast(
        election, r1_horizon, variant, results_hist, model, tag=tag, cache=cache, error_prior=error_prior
    )
    return out["draws"], out["categories"]


# ---------------------------------------------------------------------------------------------------------------
# combination
# ---------------------------------------------------------------------------------------------------------------


def h2h_error_model(
    devs: pd.DataFrame, target: str, variant: str, *, seed: int, prior: dict | None = None
) -> election_day.ErrorPosterior:
    """LOEO h2h error model for `target`: deviations of every OTHER election (round label "h2h")."""
    if devs.empty:
        devs = pd.DataFrame(columns=DEVIATION_COLUMNS)
    train = election_day.loeo_training_set(devs, target, ROUND_LABEL)
    return election_day.fit_error_model(train, variant, H2H_ROLES, seed=seed, prior=prior)


def pair_win_probabilities(
    fits: dict[tuple[str, str], pipeline.CachedFit | None],
    post: election_day.ErrorPosterior | None = None,
    seed: int = 0,
) -> tuple[dict[tuple[str, str], dict[str, float]], dict[tuple[str, str], np.ndarray]]:
    """P(win | pair) and the (N, 2) h2h draws for every fitted pair (pairs mapped to None are skipped)."""
    wins, draws = {}, {}
    for p, f in fits.items():
        if f is None:
            continue
        key = conditional.pair_key(*p)
        if tuple(f.meta["pair"]) != key:
            raise ValueError(f"fit {f.key} is for {f.meta['pair']}, not {list(key)}")
        d = h2h_forecast(f, post, seed=seed)
        wins[key], draws[key] = conditional.win_given_pair(d, key), d
    return wins, draws


def validation_label(n_elections: int) -> str:
    """The "label" of outputs/president_2026.json (read by brfc.figures.n_validated_elections)."""
    noun = "election" if n_elections == 1 else "elections"
    return f"pre-registered; validated on {n_elections} {noun} only"


def president_forecast(
    r1_draws: np.ndarray,
    categories: list[str],
    fits: dict[tuple[str, str], pipeline.CachedFit | None],
    post: election_day.ErrorPosterior | None = None,
    *,
    seed: int = 0,
    n_polls: dict[tuple[str, str], int] | None = None,
) -> dict:
    """conditional.president_probabilities from first-round draws and h2h fits, with per-pair details
    (n_polls, fit key, convergence flag and the runoff share interval of pair[0]) for modelled pairs."""
    wins, draws = pair_win_probabilities(fits, post, seed)
    out = conditional.president_probabilities(r1_draws, categories, wins)
    fitted = {conditional.pair_key(*p): f for p, f in fits.items() if f is not None}
    counts = {conditional.pair_key(*p): int(v) for p, v in (n_polls or {}).items()}
    for p in out["pairs"]:
        key = tuple(p["candidates"])
        f = fitted.get(key)
        p["n_polls"] = int(f.meta["n_polls"]) if f is not None else counts.get(key, 0)
        p["fit_key"] = f.key if f is not None else None
        p["converged"] = converged(f)
        p["share_first"] = share_summary(draws[key], key) if key in draws else None
    return out


def combination_label(r1_model: str, h2h_model: str) -> str:
    """Label of a (first-round model, h2h model) combination, e.g. "F_E"."""
    return f"{r1_model}_{h2h_model}"


def president_combinations(
    r1: dict[str, dict],
    fits: dict[tuple[str, str], pipeline.CachedFit | None],
    posts: dict[str, election_day.ErrorPosterior | None],
    *,
    seed: int = 0,
    n_polls: dict[tuple[str, str], int] | None = None,
) -> dict[str, dict]:
    """`president_forecast` of every combination in COMBINATIONS whose first-round draws (`r1`: first-round model ->
    `first_round_forecast` output) and h2h error model (`posts`: h2h model -> ErrorPosterior) are given, keyed by
    `combination_label`. Combinations with a missing input are left out."""
    out = {}
    for r1_model, h2h_model in COMBINATIONS:
        if r1_model not in r1 or h2h_model not in posts:
            continue
        d = r1[r1_model]
        out[combination_label(r1_model, h2h_model)] = president_forecast(
            d["draws"], d["categories"], fits, posts[h2h_model], seed=seed, n_polls=n_polls
        )
    return out


# ---------------------------------------------------------------------------------------------------------------
# evaluation (reads official results and the actual runoff pairs)
# ---------------------------------------------------------------------------------------------------------------


def elected_candidate(results: pd.DataFrame, election: str) -> str:
    """Elected candidate: runoff winner, or the first-round leader above 50% of valid votes. Evaluation only."""
    r = results[results["election"].astype(str) == election]
    r2 = r[r["round"].astype(int) == 2]
    if not r2.empty:
        return str(r2.sort_values(["valid_share", "candidate"]).iloc[-1]["candidate"])
    r1 = r[r["round"].astype(int) == 1].sort_values(["valid_share", "candidate"])
    if r1.empty or float(r1.iloc[-1]["valid_share"]) <= 50.0:
        raise ValueError(f"no elected candidate recorded for {election}")
    return str(r1.iloc[-1]["candidate"])


def h2h_deviations(eve_fits: dict[str, pipeline.CachedFit | None], results: pd.DataFrame) -> pd.DataFrame:
    """Eve deviations of the ACTUAL runoff pair: runoff valid share of the forecast-rank-1 candidate minus its eve
    h2h forecast mean (latent, projected). Evaluation only; round label "h2h"."""
    from brfc.data import actual_shares

    rows = []
    for e, f in sorted(eve_fits.items()):
        if f is None:
            continue
        pair = tuple(f.meta["pair"])
        if e not in config.RUNOFF_PAIRS or set(pair) != set(config.RUNOFF_PAIRS[e]):
            raise ValueError(f"{f.key}: deviations are defined for the actual runoff pair of {e} only")
        if int(f.meta["r1_horizon"]) != 1:
            raise ValueError(f"{f.key}: deviations use the first-round eve fit (r1_horizon 1)")
        cats = list(pair)
        mean = dict(zip(cats, h2h_forecast(f).mean(axis=0), strict=True))
        roles = election_day.assign_roles(cats, mean, 2)
        act = actual_shares(results, e, 2, cats)
        for c in cats:
            if c in roles:
                rows.append(
                    {
                        "election": e,
                        "round": ROUND_LABEL,
                        "pair": conditional.pair_label(pair),
                        "category": c,
                        "role": roles[c],
                        "forecast": mean[c],
                        "actual": act[c],
                        "deviation": act[c] - mean[c],
                        "cutoff": f.meta["cutoff"],
                        "r1_horizon": 1,
                        "n_polls": f.meta["n_polls"],
                        "fit_key": f.key,
                        "converged": converged(f),
                    }
                )
    return pd.DataFrame(rows, columns=DEVIATION_COLUMNS)


def _h2h_point(kind: str, rows: pd.DataFrame, pair: tuple[str, str], cutoff: date) -> dict[str, float] | None:
    """Baseline B or C point for a pair from its h2h rows (named = [pair[0]], round 2)."""
    if rows is None or rows.empty:
        return None
    if kind == "B":
        p = baselines.latest_per_pollster(rows, [pair[0]], cutoff, 2)
    else:
        p = baselines.final_poll_of(rows, [pair[0]], cutoff, H2H_BASELINES[kind], 2)
    return baselines.as_categories(p, list(pair), 2) if p else None


def _train_elections(errors: pd.DataFrame, kind: str, target: str, round_, horizon: int) -> str:
    e = errors[
        (errors["baseline"] == kind)
        & (errors["round"] == round_)
        & (errors["horizon"] == horizon)
        & (errors["election"] != target)
    ]
    return "+".join(sorted(e["election"].unique()))


def _point_metrics(point: dict, cats: list[str], act: dict, round_: int) -> dict:
    pm = scoring.score(None, point, cats, act, round_)
    return {k: pm[k] for k in ("mae", "margin_pred", "margin_error", "margin_abs_error")}


def _fit_status(election: str, pair: tuple[str, str], r1_horizon: int, variant: str, tag: str, cache: Path) -> str:
    path = cache / f"{h2h_key(election, pair, r1_horizon, variant, tag)}.json"
    if not path.exists():
        return "N/A: h2h fit not in the cache (run scripts/run_runoff_backtest.py --stage fits)"
    status = json.loads(path.read_text(encoding="utf-8"))["status"]
    return f"N/A: h2h fit {status}"


def _h2h_baseline_errors(act_fits: dict, actual_pair: dict, results: pd.DataFrame) -> pd.DataFrame:
    """Observed-minus-point errors of B and C for the actual pair in every cell with an actual-pair fit."""
    from brfc.data import actual_shares

    rows = []
    for (e, h), f in act_fits.items():
        if f is None:
            continue
        pair = actual_pair[e]
        act = actual_shares(results, e, 2, list(pair))
        for b in H2H_BASELINES:
            p = _h2h_point(b, f.kept_rows, pair, date.fromisoformat(f.meta["cutoff"]))
            if p:
                rows += [
                    {
                        "baseline": b,
                        "election": e,
                        "round": ROUND_LABEL,
                        "horizon": h,
                        "category": c,
                        "error": act[c] - p[c],
                    }
                    for c in pair
                ]
    return pd.DataFrame(rows, columns=BASELINE_ERROR_COLUMNS)


def _score_actual_pair(
    base: dict, f: pipeline.CachedFit, act: dict, posts: dict, berr_h2h: pd.DataFrame
) -> tuple[list[dict], list[dict]]:
    """h2h scores and category rows of the actual pair for models E (primary), F, E0 and baselines B, C."""
    e, h = base["election"], base["r1_horizon"]
    pair = tuple(f.meta["pair"])
    cats = list(pair)
    cutoff = date.fromisoformat(f.meta["cutoff"])
    scores, cat_rows = [], []
    latent = h2h_forecast(f)
    roles = election_day.assign_roles(cats, dict(zip(cats, latent.mean(axis=0), strict=True)), 2)
    forecasts = {"E": h2h_forecast(f, posts["E"], roles, seed=1), "F": h2h_forecast(f, posts["F"], roles, seed=1)}
    forecasts["E0"] = latent
    for m in H2H_MODELS:
        d = forecasts[m]
        extra = {"primary": m == PRIMARY_H2H_MODEL, "status": "ok"}
        if m != "E0":
            extra |= {"train_elections": "+".join(posts[m].train_elections), "n_train": posts[m].n_train}
            if posts[m].n_train == 0:
                extra["status"] = "prior-only error model: no LOEO h2h deviation"
        scores.append(base | {"model": m} | extra | scoring.score(d, None, cats, act, 2))
        cat_rows += [base | {"model": m} | x for x in scoring.category_rows(d, None, cats, act)]
    for b in H2H_BASELINES:
        p = _h2h_point(b, f.kept_rows, pair, cutoff)
        if not p:
            scores.append(base | {"model": b, "status": "N/A: no qualifying poll in the 14 days to cutoff"})
            continue
        rmse = baselines.loeo_rmse(berr_h2h, b, e, ROUND_LABEL, h)
        if rmse is None:
            s = scoring.score(None, p, cats, act, 2) | {"status": "point only: no LOEO error history"}
            scores.append(base | {"model": b} | s)
            cat_rows += [base | {"model": b} | x for x in scoring.category_rows(None, p, cats, act)]
            continue
        d = baselines.probabilistic(p, cats, rmse, 2, seed=2)
        info = {
            "model": b,
            "loeo_rmse": rmse,
            "train_elections": _train_elections(berr_h2h, b, e, ROUND_LABEL, h),
            "status": "calibrated probabilistic conversion of point baseline",
        }
        scores.append(base | info | scoring.score(d, None, cats, act, 2) | _point_metrics(p, cats, act, 2))
        for x in scoring.category_rows(d, None, cats, act):  # point value as the estimate, conversion intervals
            x["mean"], x["error"] = p[x["category"]], p[x["category"]] - act[x["category"]]
            cat_rows.append(base | {"model": b} | x)
    return scores, cat_rows


def _baseline_president(
    b: str,
    r1: dict,
    pair_rows: dict,
    cutoff: date,
    berr_r1: pd.DataFrame,
    berr_h2h: pd.DataFrame,
    e: str,
    h: int,
) -> tuple[dict | None, dict]:
    """End-to-end baseline: first-round calibrated conversion for the paths, h2h point conversion for P(win | pair)."""
    p1 = pipeline.baseline_point(b, r1["fit"], e, cutoff)
    rmse1 = baselines.loeo_rmse(berr_r1, b, e, 1, h) if p1 else None
    rmse2 = baselines.loeo_rmse(berr_h2h, b, e, ROUND_LABEL, h)
    info = {
        "r1_model": b,
        "h2h_model": b,
        "r1_loeo_rmse": rmse1,
        "h2h_loeo_rmse": rmse2,
        "r1_train_elections": _train_elections(berr_r1, b, e, 1, h),
        "h2h_train_elections": _train_elections(berr_h2h, b, e, ROUND_LABEL, h),
    }
    if p1 is None or rmse1 is None:
        return None, info | {"status": "N/A: no first-round calibrated conversion at this cutoff"}
    d1 = baselines.probabilistic(p1, r1["categories"], rmse1, 1, seed=2)
    wins = {}
    if rmse2 is not None:
        for p, rows in pair_rows.items():
            pt = _h2h_point(b, rows, p, cutoff)
            if pt:
                wins[p] = conditional.win_given_pair(baselines.probabilistic(pt, list(p), rmse2, 2, seed=2), p)
    return conditional.president_probabilities(d1, r1["categories"], wins), info


def _president_row(pbase: dict, m: str, res: dict, info: dict, actual: tuple[str, str], winner: str) -> dict:
    modelled = [q["pair"] for q in res["pairs"] if q["modelled"]]
    row = pbase | {"model": m, "primary": m == combination_label(*PRIMARY_COMBINATION)} | info
    row |= {
        "unmodelled": res["unmodelled"],
        "p_outright_total": float(sum(res["outright"].values())),
        "p_actual_pair": float(sum(q["mass"] for q in res["pairs"] if tuple(q["candidates"]) == actual)),
        "n_pairs_modelled": len(modelled),
        "pairs_modelled": "|".join(modelled),
        "probabilities_json": json.dumps(res["probabilities"], ensure_ascii=False),
    }
    if not modelled:
        return row | {"status": "N/A: no modelled runoff pair at this cutoff"}
    return row | conditional.score_president(res["probabilities"], res["unmodelled"], winner) | {"status": "ok"}


def evaluate_backtest(
    variant: str,
    results: pd.DataFrame,
    *,
    polls: pd.DataFrame | None = None,
    cache: Path = pipeline.CACHE,
    h2h_cache: Path = H2H_CACHE,
    elections: tuple[str, ...] = config.HISTORICAL,
    horizons: tuple[int, ...] = H2H_HORIZONS,
    tag: str = "",
    error_prior: dict | None = None,
) -> dict[str, pd.DataFrame]:
    """Historical h2h and president backtest from cached fits (stage 2; reads official results).

    First-round fits are read from `cache`, h2h fits from `h2h_cache`. `error_prior` overrides the h2h error-model
    prior only (sensitivity); the first-round term keeps the registered prior.

    Returns {"runoff": h2h scores of the actual pair (E primary, F, E0, B, C), "runoff_categories": per-category
    rows, "president": probability-of-being-elected scores (one row per combination F_E primary, F_F, E_E, and
    B and C end-to-end), "deviations": the h2h eve deviation table, "error_models": LOEO h2h error-model
    summaries}. Every row records the elections that trained its leave-one-election-out terms. Forecast functions
    only ever receive the other elections' results."""
    from brfc.data import actual_shares, load_polls

    cache, h2h_cache = Path(cache), Path(h2h_cache)
    polls = load_polls(config.HISTORICAL) if polls is None else polls
    actual_pair = {e: conditional.pair_key(*config.RUNOFF_PAIRS[e]) for e in elections}
    act_fits = {
        (e, h): load_h2h(e, actual_pair[e], h, variant, tag=tag, cache=h2h_cache) for e in elections for h in horizons
    }
    devs = h2h_deviations({e: act_fits.get((e, 1)) for e in elections}, results)
    berr_h2h = _h2h_baseline_errors(act_fits, actual_pair, results)
    r1_fits = {}
    for e in elections:
        for h in horizons:
            f = _load_r1_fit(e, h, variant, tag, cache)
            if f is not None:
                r1_fits[(e, 1, h)] = f
    berr_r1 = pipeline.baseline_errors(r1_fits, results)

    scores, cat_rows, pres_rows, err_summ = [], [], [], []
    for e in elections:
        pair = actual_pair[e]
        winner = elected_candidate(results, e)
        act = actual_shares(results, e, 2, list(pair))
        others = results[results["election"].astype(str) != e]  # the only results a forecast function receives
        for h in horizons:
            cutoff = h2h_cutoff(e, h)
            f = act_fits[(e, h)]
            seeds = {v: zlib.crc32(f"{e}|{ROUND_LABEL}|{h}|{v}".encode()) for v in ("E", "F")}
            devs_other = devs[devs["election"] != e]  # Addendum 05 s.8: call site passes other elections only
            posts = {v: h2h_error_model(devs_other, e, v, seed=seeds[v], prior=error_prior) for v in ("E", "F")}
            n_actual = f.meta["n_polls"] if f is not None else h2h_rows(polls, e, pair, cutoff)["poll_id"].nunique()
            base = {
                "election": e,
                "r1_horizon": h,
                "cutoff": str(cutoff),
                "variant": variant,
                "pair": conditional.pair_label(pair),
                "n_polls": int(n_actual),
                "fit_key": h2h_key(e, pair, h, variant, tag),
                "converged": converged(f),
            }
            err_summ += [base | {"model": v} | post.summary() for v, post in posts.items()]
            if f is None:
                status = _fit_status(e, pair, h, variant, tag, h2h_cache)
                scores += [base | {"model": m, "status": status} for m in (*H2H_MODELS, *H2H_BASELINES)]
            else:
                s, c = _score_actual_pair(base, f, act, posts, berr_h2h)
                scores += s
                cat_rows += c

            # probability of being elected, end to end
            pairs = h2h_pairs(polls, e, cutoff)
            pair_rows = {p: h2h_rows(polls, e, p, cutoff) for p in pairs}
            n_polls = {p: int(r["poll_id"].nunique()) for p, r in pair_rows.items()}
            pbase = {
                "election": e,
                "r1_horizon": h,
                "cutoff": str(cutoff),
                "variant": variant,
                "winner": winner,
                "n_pairs_h2h": len(pairs),
                "pairs_h2h": "|".join(conditional.pair_label(p) for p in pairs),
            }
            r1_fit = _load_r1_fit(e, h, variant, tag, cache)
            if r1_fit is None:
                key = pipeline.fit_key(e, 1, h, variant, tag)
                status = f"N/A: no cached first-round fit {key}"
                labels = [combination_label(*c) for c in COMBINATIONS]
                pres_rows += [pbase | {"model": m, "status": status} for m in (*labels, *H2H_BASELINES)]
                continue
            r1, r1_errors = {}, {}
            for m in dict.fromkeys(c[0] for c in COMBINATIONS):
                try:
                    r1[m] = first_round_forecast(e, h, variant, others, m, tag=tag, cache=cache)
                except (FileNotFoundError, ValueError) as err:
                    r1_errors[m] = f"N/A: {err}"
            fits = {p: load_h2h(e, p, h, variant, tag=tag, cache=h2h_cache) for p in pairs}
            combos = president_combinations(r1, fits, posts, seed=1, n_polls=n_polls)
            for r1_model, h2h_model in COMBINATIONS:
                label = combination_label(r1_model, h2h_model)
                info = {"r1_model": r1_model, "h2h_model": h2h_model}
                if label not in combos:
                    primary = label == combination_label(*PRIMARY_COMBINATION)
                    pres_rows.append(
                        pbase | {"model": label, "primary": primary} | info | {"status": r1_errors[r1_model]}
                    )
                    continue
                info |= {
                    "r1_train_elections": "+".join(r1[r1_model]["train_elections"]),
                    "h2h_train_elections": "+".join(posts[h2h_model].train_elections),
                }
                pres_rows.append(_president_row(pbase, label, combos[label], info, pair, winner))
            r1_paths = {"fit": r1_fit, "categories": list(r1_fit.meta["series"])}
            for b in H2H_BASELINES:
                res, info = _baseline_president(b, r1_paths, pair_rows, cutoff, berr_r1, berr_h2h, e, h)
                if res is None:
                    pres_rows.append(pbase | {"model": b} | info)
                else:
                    pres_rows.append(_president_row(pbase, b, res, info, pair, winner))
    return {
        "runoff": pd.DataFrame(scores),
        "runoff_categories": pd.DataFrame(cat_rows),
        "president": pd.DataFrame(pres_rows),
        "deviations": devs.assign(variant=variant),
        "error_models": pd.DataFrame(err_summ),
    }


# ---------------------------------------------------------------------------------------------------------------
# pre-registered criteria and sensitivity (Addendum 04 s.5; evaluation only)
# ---------------------------------------------------------------------------------------------------------------

EVE = 1  # first-round horizon of the primary cutoff
H2H_SUMMARY = {  # sensitivity column -> evaluate_backtest()["runoff"] column
    "mae": "mae",
    "margin_abs_error": "margin_abs_error",
    "coverage_80": "coverage_80",
    "coverage_94": "coverage_94",
    "width_94": "width_94_mean",
    "brier": "brier_first",
    "log": "log_first",
}
PRESIDENT_SUMMARY = {"brier": "brier", "log": "log", "p_winner": "p_winner", "unmodelled": "unmodelled"}
SENSITIVITY_COLUMNS = [
    "setting",
    "sigma_scale",
    "variant",
    "table",
    "model",
    "r1_horizon",
    "n_elections",
    *dict.fromkeys([*H2H_SUMMARY, *PRESIDENT_SUMMARY]),
]


def _result(met: bool | None) -> str:
    return "not evaluable" if met is None else ("met" if met else "not met")


def _eve_values(df: pd.DataFrame, model_name: str, column: str) -> pd.Series:
    """election -> `column` of `model_name` at the first-round eve cutoff, rows without a value dropped."""
    if df.empty or column not in df.columns:
        return pd.Series(dtype=float)
    d = df[(df["model"] == model_name) & (df["r1_horizon"] == EVE) & df[column].notna()]
    return d.groupby(d["election"].astype(str))[column].mean()


def _paired_le(a: pd.Series, b: pd.Series, name_a: str, name_b: str) -> dict:
    """mean(a) <= mean(b) over the elections where both were scored."""
    common = sorted(set(a.index) & set(b.index))
    if not common:
        return {name_a: None, name_b: None, "elections": [], "met": None, "result": _result(None)}
    va, vb = float(a[common].mean()), float(b[common].mean())
    return {name_a: va, name_b: vb, "elections": common, "met": bool(va <= vb), "result": _result(va <= vb)}


def success_criteria(out: dict[str, pd.DataFrame]) -> dict:
    """Criteria HR1-HR3 of Addendum 04 s.5 from an `evaluate_backtest` output, each reported as met / not met with
    the values behind it. HR1 and HR3 compare means over the elections where both forecasts were scored."""
    runoff_scores, cats, pres = out["runoff"], out["runoff_categories"], out["president"]
    hr1 = _paired_le(_eve_values(runoff_scores, "E", "mae"), _eve_values(runoff_scores, "B", "mae"), "E", "B")
    inside: dict[str, bool] = {}
    if not cats.empty and "in94" in cats.columns:
        c = cats[(cats["model"] == "E") & (cats["r1_horizon"] == EVE) & cats["in94"].notna()]
        inside = {str(e): bool(g["in94"].astype(bool).all()) for e, g in c.groupby(c["election"].astype(str))}
    n_inside = int(sum(inside.values()))
    met2 = bool(n_inside >= 2) if inside else None
    hr2 = {
        "inside_94": n_inside,
        "n_elections": len(inside),
        "required": 2,
        "by_election": inside,
        "met": met2,
        "result": _result(met2),
    }
    if inside and len(inside) < len(config.HISTORICAL):
        hr2["note"] = f"evaluated on {len(inside)} of {len(config.HISTORICAL)} elections"
    primary = combination_label(*PRIMARY_COMBINATION)
    hr3 = _paired_le(_eve_values(pres, primary, "brier"), _eve_values(pres, "B", "brier"), primary, "B")
    variants = sorted(set(out["deviations"].get("variant", pd.Series(dtype=str)).dropna().astype(str)))
    return {
        "rule": "PREREG_ADDENDUM_04.md section 5",
        "HR1_E_h2h_mae_le_B": {
            "criterion": "head-to-head model E share MAE <= baseline B at the first-round eve cutoff (actual pair)",
            **hr1,
        },
        "HR2_E_94_interval": {
            "criterion": "actual runoff share inside model E's 94% interval in at least 2 of 3 elections (eve)",
            **hr2,
        },
        "HR3_FxE_brier_le_B": {
            "criterion": 'Brier of "who was elected", first-round F x head-to-head E <= baseline B (eve)',
            **hr3,
        },
        "variant": variants[0] if len(variants) == 1 else variants,
        "scope": "historical elections, first-round eve cutoff, production random-walk variant",
        "note": "three elections is very weak evidence",
    }


def _means(df: pd.DataFrame, table: str, columns: dict[str, str], key: str) -> pd.DataFrame:
    d = df.reindex(columns=["election", "model", "r1_horizon", *columns.values()])
    d = d[d[key].notna()]
    if d.empty:
        return pd.DataFrame(columns=["table", "model", "r1_horizon", "n_elections", *columns])
    agg = d.groupby(["model", "r1_horizon"]).agg(
        n_elections=("election", "nunique"), **{k: (v, "mean") for k, v in columns.items()}
    )
    return agg.reset_index().assign(table=table)


def sensitivity_summary(out: dict[str, pd.DataFrame], setting: str) -> pd.DataFrame:
    """Means by (table, model, r1_horizon) of one `evaluate_backtest` run: table "h2h" (share scores of the actual
    pair) and table "president" (who was elected)."""
    frames = [
        _means(out["runoff"], "h2h", H2H_SUMMARY, "mae"),
        _means(out["president"], "president", PRESIDENT_SUMMARY, "brier"),
    ]
    frames = [f for f in frames if not f.empty]
    s = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return s.assign(setting=setting)


def sensitivity_backtest(
    variant: str,
    results: pd.DataFrame,
    *,
    base: dict[str, pd.DataFrame] | None = None,
    priors: dict[str, dict] = SENSITIVITY_PRIORS,
    **kw,
) -> pd.DataFrame:
    """Error-model scale sensitivity of the h2h term (Addendum 04 s.5): `evaluate_backtest` with every prior in
    `priors` (h2h term only), beside the registered prior ("primary"; `base` reuses an existing primary run).
    Reported only; never used to choose a model."""
    runs = {"primary": (base if base is not None else evaluate_backtest(variant, results, **kw), {})}
    for name, prior in priors.items():
        runs[name] = (evaluate_backtest(variant, results, error_prior=prior, **kw), prior)
    frames = [
        sensitivity_summary(o, name).assign(
            sigma_scale=float({**election_day.PRIOR, **prior}["sigma_scale"]), variant=variant
        )
        for name, (o, prior) in runs.items()
    ]
    return pd.concat(frames, ignore_index=True).reindex(columns=SENSITIVITY_COLUMNS)

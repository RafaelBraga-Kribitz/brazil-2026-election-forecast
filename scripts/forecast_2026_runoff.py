"""2026 runoff forecast after the first round (PRELIMINARY or FINAL; PREREG_ADDENDUM_04 section 1B).

Usage:
  python scripts/forecast_2026_runoff.py --status PRELIMINARY --cutoff 2026-10-15 --pair "<first>" "<second>"
  python scripts/forecast_2026_runoff.py --status FINAL --cutoff 2026-10-24 --pair "<first>" "<second>" \
      --freeze-dir outputs/freeze_runoff        (after refresh_2026_polls.py --at 2026-10-25T01:00:00Z)

`--pair` is required: the two candidates who advanced, typed from the official TSE first-round count, first place
first, spelled as in config.BALLOTS["2026"] (accents and case are not significant). The script refuses to run
without it. It never reads a 2026 result: the only results it loads are the 2014, 2018 and 2022 official results,
for the leave-one-election-out election-day term and the baseline errors.

Registered round-2 design (PREREG s.2 round-2 rule, s.4 model, s.5 round-2 term, s.6 horizons):
  - information set: round-2 poll scenarios of the pair with fieldwork ending after the first round, available at
    the cutoff, dependence rule as registered (brfc.pipeline.fit_one, round 2);
  - one random walk on the valid share of the first-listed candidate; the other share is its complement;
  - election-day term: round-2 model F (primary) and E, plus E0 (latent only), trained on the 2014, 2018 and 2022
    round-2 eve deviations of the cached historical fits (brfc.pipeline.eve_deviations);
  - baselines B and C with the calibrated probabilistic conversion, using their historical round-2 errors at the
    registered round-2 horizon nearest to this forecast's horizon.

For this run only, config.RUNOFF_PAIRS["2026"] is set to the supplied pair (the round-2 scenario rule reads it) and
then restored; the pair is recorded in the output. The 2026 fit is cached in data/cache/fits/ under a key holding the
status, the poll revision and the pair; a cached fit is reused only if its poll ids equal the current information
set, otherwise it is refitted.

Writes outputs/runoff_2026.json. With --freeze-dir (FINAL only) also writes runoff.json, runoff.csv,
poll_snapshot.csv, draws.npz (every model's draws, PREREG_ADDENDUM_06 s.1), baseline_snapshot.csv (baselines B
and C plus data/manual/benchmarks_2026_runoff_freeze.csv when present) and MODEL_VERSION.txt into that directory,
then rewrites its forecast_hash.txt over every file in it.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from brfc import baselines, conditional, config, election_day, freeze, pipeline, runoff
from brfc.data import load_polls, load_results
from brfc.names import _key, canonical_candidate
from brfc.provenance import sha256_file, utc_now_iso
from brfc.transform import window_start

ELECTION = "2026"
ROUND = 2
FREEZE_CUTOFF = date(2026, 10, 24)
FREEZE_UTC = "2026-10-25T01:00:00Z"
MODELS = ("F", "E", "E0")
BASELINES = ("B", "C")
PRIMARY_MODEL = "F"
SEED = 20261004  # error-model and election-day seeds, as the 2026 first-round forecast (brfc.forecast.run)
BASELINE_SEED = 7  # calibrated-conversion seed, as brfc.forecast.run
MODEL_VERSION = "brfc-0.1.0 independent valid-share random walks + LOEO round-specific election-day term"
HASH_FILE = "forecast_hash.txt"

CACHE = pipeline.CACHE
REVISION = config.DATA / "interim" / "polls_wiki_2026.revision.json"
REGIME = config.OUTPUTS / "regime_selection.json"
OUT_JSON = config.OUTPUTS / "runoff_2026.json"
BENCHMARKS = config.DATA / "manual" / "benchmarks_2026_runoff_freeze.csv"
FIRST_ROUND_PACKAGE = config.OUTPUTS / "freeze"

PAIR_HELP = (
    "the two candidates who advanced, from the official TSE first-round count, first place first "
    '(e.g. --pair "<first>" "<second>")'
)


def git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=config.ROOT, text=True).strip()
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------------------------------------------
# the advancing pair (typed by the user; never read from a results file)
# ---------------------------------------------------------------------------------------------------------------


def resolve_candidate(name: str) -> str:
    """Registered 2026 ballot name matching `name` (accent- and case-insensitive), else ValueError."""
    key = _key(canonical_candidate(name))
    hits = [b for b in config.BALLOTS[ELECTION] if _key(b) == key]
    if len(hits) != 1:
        raise ValueError(
            f"{name!r} is not a registered {ELECTION} candidate; expected one of: {', '.join(config.BALLOTS[ELECTION])}"
        )
    return hits[0]


def resolve_pair(names) -> tuple[str, str]:
    """(first, second) ballot names of the advancing pair, in the order given."""
    if names is None or len(names) != 2:
        raise ValueError(f"--pair needs exactly two names: {PAIR_HELP}")
    a, b = (resolve_candidate(n) for n in names)
    if a == b:
        raise ValueError(f"--pair names the same candidate twice ({a!r})")
    return a, b


@contextmanager
def runoff_pair(pair: tuple[str, str]) -> Iterator[None]:
    """Set config.RUNOFF_PAIRS["2026"] to `pair` inside the block only; the previous state is always restored."""
    had = ELECTION in config.RUNOFF_PAIRS
    previous = config.RUNOFF_PAIRS.get(ELECTION)
    config.RUNOFF_PAIRS[ELECTION] = tuple(pair)
    try:
        yield
    finally:
        if had:
            config.RUNOFF_PAIRS[ELECTION] = previous
        else:
            config.RUNOFF_PAIRS.pop(ELECTION, None)


def _slug(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def pair_tag(status: str, pt_oldid, pair: tuple[str, str]) -> str:
    """Cache tag of the 2026 round-2 fit: status, poll revision and the pair in the order given."""
    return f"{status.lower()}_pt{pt_oldid}_{'_vs_'.join(_slug(c) for c in pair)}"


# ---------------------------------------------------------------------------------------------------------------
# forecast (2026 polls + historical results only)
# ---------------------------------------------------------------------------------------------------------------


def nearest_r2_horizon(h: int) -> int:
    return min(config.HORIZONS_R2, key=lambda x: (abs(x - h), -x))


def historical_r2_fits(variant: str, cache: Path) -> dict[tuple[str, int, int], pipeline.CachedFit]:
    """Cached historical round-2 fits of `variant` (untagged, as the backtest), keyed (election, 2, horizon)."""
    out = {}
    for e in config.HISTORICAL:
        for h in config.HORIZONS_R2:
            key = pipeline.fit_key(e, ROUND, h, variant)
            if (cache / f"{key}.json").exists():
                f = pipeline.load_fit(key, cache)
                if f is not None:
                    out[(e, ROUND, h)] = f
    return out


def summarise(draws: np.ndarray, cats: list[str]) -> list[dict]:
    """Per candidate: mean, equal-tailed quantiles and P(elected) = share of draws above the opponent."""
    win = conditional.win_given_pair(draws, (cats[0], cats[1]))
    rows = []
    for k, c in enumerate(cats):
        x = np.asarray(draws, dtype=float)[:, k]
        q = np.quantile(x, [0.03, 0.10, 0.5, 0.90, 0.97])
        rows.append(
            {
                "category": c,
                "mean": float(x.mean()),
                "median": float(q[2]),
                "q03": float(q[0]),
                "q10": float(q[1]),
                "q90": float(q[3]),
                "q97": float(q[4]),
                "p_win": float(win[c]),
            }
        )
    return rows


def _fit_status(key: str, cache: Path) -> str:
    path = cache / f"{key}.json"
    return json.loads(path.read_text(encoding="utf-8"))["status"] if path.exists() else "not fitted"


def forecast_runoff(
    polls_2026: pd.DataFrame,
    results_hist: pd.DataFrame,
    pair: tuple[str, str],
    cutoff: date,
    *,
    variant: str,
    tag: str,
    cache: Path = CACHE,
) -> dict:
    """Registered round-2 forecast of the 2026 runoff for `pair` at `cutoff`. Returns the draws and metadata."""
    if set(results_hist["election"].astype(str)) - set(config.HISTORICAL):
        raise ValueError("only historical results may be passed to the 2026 runoff forecast")
    cache = Path(cache)
    e1, e2 = config.ELECTION_DATES[(ELECTION, 1)], config.ELECTION_DATES[(ELECTION, ROUND)]
    if not e1 < cutoff < e2:
        raise ValueError(f"the cutoff must fall after the first round ({e1}) and before the runoff ({e2})")
    h = (e2 - cutoff).days

    hist = historical_r2_fits(variant, cache)
    missing = [e for e in config.HISTORICAL if (e, ROUND, 1) not in hist]
    if missing:
        raise FileNotFoundError(
            f"missing historical round-2 eve fits ({variant}) for {', '.join(missing)} in {cache}: run make backtest"
        )
    devs = pipeline.eve_deviations({(e, r): f for (e, r, hh), f in hist.items() if hh == 1}, results_hist)
    train = election_day.loeo_training_set(devs, ELECTION, ROUND)
    berr = pipeline.baseline_errors(hist, results_hist)

    with runoff_pair(pair):
        key = pipeline.fit_key(ELECTION, ROUND, h, variant, tag)
        wide, *_ = pipeline.information_set(polls_2026, ELECTION, ROUND, h)
        cached = cache / f"{key}.json"
        # a cached fit is reused only if it was fitted on exactly the current information set
        stale = cached.exists() and json.loads(cached.read_text(encoding="utf-8"))["poll_ids"] != sorted(
            wide["poll_id"].tolist()
        )
        fitted = pipeline.fit_one(polls_2026, ELECTION, ROUND, h, variant, tag=tag, cache=cache, force=stale)
        if fitted is None:
            raise RuntimeError(f"2026 runoff fit {key} not available: {_fit_status(key, cache)}")
        fit = pipeline.load_fit(key, cache)  # cached float32 draws: a re-run from the cache gives the same output
        latent, cats = pipeline.latent_forecast(fit, ELECTION)
        points = {b: pipeline.baseline_point(b, fit, ELECTION, cutoff) for b in BASELINES}
    if cats != list(pair):
        raise RuntimeError(f"fit categories {cats} differ from the pair {list(pair)}")

    roles = election_day.assign_roles(cats, dict(zip(cats, latent.mean(axis=0), strict=True)), ROUND)
    draws, error_models, train_elections = {"E0": latent}, {}, {}
    for v in ("E", "F"):
        post = election_day.fit_error_model(train, v, ["rank1"], seed=SEED + (v == "F"))
        error_models[v] = post.summary()
        train_elections[v] = post.train_elections
        draws[v] = election_day.apply(fit.draws, fit.meta["series"], roles, post, ROUND, seed=SEED)

    hh = nearest_r2_horizon(h)
    base_rows = []
    for b in BASELINES:
        p, rmse = points[b], baselines.loeo_rmse(berr, b, ELECTION, ROUND, hh)
        trained = berr[
            (berr["baseline"] == b)
            & (berr["round"] == ROUND)
            & (berr["horizon"] == hh)
            & (berr["election"] != ELECTION)
        ]
        row = {
            "baseline": b,
            "cutoff": str(cutoff),
            "error_horizon_used": hh,
            "loeo_rmse": rmse,
            "train_elections": "+".join(sorted(trained["election"].astype(str).unique())),
        }
        if p is None:
            row["status"] = "N/A: no qualifying poll in the 14 days to cutoff"
        elif rmse is None:
            row["status"] = "point only: no historical round-2 error at this horizon"
        else:
            row["status"] = "calibrated probabilistic conversion of point baseline"
            draws[b] = baselines.probabilistic(p, cats, rmse, ROUND, seed=BASELINE_SEED)
        row["point"] = {c: float(p[c]) for c in cats} if p is not None else None
        base_rows.append(row)

    return {
        "fit": fit,
        "pair": tuple(pair),
        "categories": cats,
        "cutoff": cutoff,
        "horizon": h,
        "variant": variant,
        "roles": roles,
        "draws": draws,
        "error_models": error_models,
        "train_elections": train_elections["F"],
        "baselines": base_rows,
    }


# ---------------------------------------------------------------------------------------------------------------
# outputs
# ---------------------------------------------------------------------------------------------------------------


def document(res: dict, status: str, rev: dict, generated: str, commit: str) -> dict:
    fit, cats = res["fit"], res["categories"]
    summaries = {m: summarise(d, cats) for m, d in res["draws"].items()}
    models = {
        m: {"label": pipeline.MODEL_LABELS[m], "primary": m == PRIMARY_MODEL, "categories": summaries[m]}
        for m in MODELS
    }
    for row in res["baselines"]:
        b = row["baseline"]
        models[b] = {
            "label": pipeline.MODEL_LABELS[b],
            "primary": False,
            "status": row["status"],
            "point": row["point"],
            "loeo_rmse": row["loeo_rmse"],
            "error_horizon_used": row["error_horizon_used"],
            "train_elections": row["train_elections"],
            "categories": summaries.get(b),
        }
    primary = {r["category"]: r["p_win"] for r in summaries[PRIMARY_MODEL]}
    return {
        "status": status,
        "election": "Brazil 2026 presidential election, runoff (2026-10-25)",
        "quantity": "probability of being elected under this model",
        "label": runoff.validation_label(len(res["train_elections"])),
        "design": "PREREG_ADDENDUM_04 s.1B: registered round-2 design (PREREG s.2 round-2 rule, s.4 model, "
        "s.5 round-2 election-day term, s.6 horizons)",
        "cutoff": str(res["cutoff"]),
        "information_cutoff_date": str(res["cutoff"]),
        "registered_freeze_utc": FREEZE_UTC if status == "FINAL" else None,
        "horizon_days": res["horizon"],
        "generated_utc": generated,
        "git_commit": commit,
        "model_version": MODEL_VERSION,
        "rw_variant": res["variant"],
        "primary_model": PRIMARY_MODEL,
        "runoff_pair": list(res["pair"]),
        "runoff_pair_source": "supplied with --pair from the official TSE first-round count (first place first); "
        "set as config.RUNOFF_PAIRS['2026'] for this run only; no 2026 result file is read",
        "modelled_series": fit.meta["series"],
        "probabilities": primary,
        "alternatives": {m: {r["category"]: r["p_win"] for r in summaries[m]} for m in MODELS if m != PRIMARY_MODEL},
        "interval_type": "equal-tailed posterior predictive intervals (q10-q90 = 80%, q03-q97 = 94%)",
        "share_basis": "valid votes (blank and null excluded; undecided allocated proportionally - an assumption)",
        "poll_source": rev,
        "poll_window_start": str(window_start(ELECTION, ROUND)),
        "n_polls": fit.meta["n_polls"],
        "n_pollsters": fit.meta["n_pollsters"],
        "n_dropped_overlap": fit.meta["n_dropped_overlap"],
        "fit_key": fit.key,
        "fit_diagnostics": fit.meta.get("diagnostics"),
        "fit_attempts": fit.meta.get("attempts"),
        "roles": res["roles"],
        "election_day_term_trained_on": res["train_elections"],
        "error_models": res["error_models"],
        "models": models,
        "baselines_note": "B/C probabilities are a calibrated probabilistic conversion of point baselines "
        "(point + Normal(0, historical round-2 RMSE of the other elections)); the baselines did not publish them.",
        "assumptions": [
            "the advancing pair is the one supplied with --pair; it is known information once the first round is "
            "counted",
            "only round-2 polls of that pair with fieldwork ending after the first round are used",
            "valid votes exclude blank and null ballots; undecided respondents are allocated proportionally",
        ],
        "neutrality_note": "An aggregation of publicly registered polls; not a poll, not a recommendation.",
    }


def table(doc: dict) -> pd.DataFrame:
    rows = []
    for m, spec in doc["models"].items():
        base = {
            "status": doc["status"],
            "cutoff": doc["cutoff"],
            "pair": conditional.pair_label(tuple(doc["runoff_pair"])),
            "model": m,
            "primary": spec["primary"],
            "model_status": spec.get("status", "ok"),
        }
        if spec["categories"]:
            point = spec.get("point") or {}
            rows += [base | r | {"point": point.get(r["category"])} for r in spec["categories"]]
        else:
            rows.append(base)
    return pd.DataFrame(rows)


def _check_freeze_dir(fz: Path) -> None:
    if fz.resolve() == FIRST_ROUND_PACKAGE.resolve() or (fz / "forecast.json").exists():
        raise ValueError(f"{fz} holds the frozen first-round package; the runoff package needs its own directory")


def write_hashes(fz: Path) -> Path:
    """forecast_hash.txt over every file in `fz` (relative POSIX paths), the format of the first-round package."""
    files = sorted(p for p in fz.rglob("*") if p.is_file() and p.name != HASH_FILE)
    out = fz / HASH_FILE
    out.write_text(
        "".join(f"{sha256_file(p)}  {p.relative_to(fz).as_posix()}\n" for p in files), encoding="utf-8", newline="\n"
    )
    return out


def write_freeze(fz: Path, doc: dict, res: dict, commit: str) -> None:
    _check_freeze_dir(fz)
    fz.mkdir(parents=True, exist_ok=True)
    (fz / "runoff.json").write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    table(doc).to_csv(fz / "runoff.csv", index=False)
    res["fit"].kept_rows.to_csv(fz / "poll_snapshot.csv", index=False)
    freeze.save_draws(fz / freeze.DRAWS_FILE, res["draws"], res["categories"])  # Addendum 06 s.1
    bsnap = pd.DataFrame(
        [
            {k: v for k, v in r.items() if k != "point"} | {f"point_{c}": p for c, p in (r["point"] or {}).items()}
            for r in res["baselines"]
        ]
    )
    if BENCHMARKS.exists():
        bsnap = pd.concat([bsnap, pd.read_csv(BENCHMARKS)], ignore_index=True)
    bsnap.to_csv(fz / "baseline_snapshot.csv", index=False)
    (fz / "MODEL_VERSION.txt").write_text(
        f"{MODEL_VERSION}\ngit {commit}\nvariant {res['variant']}\nrunoff pair {' | '.join(res['pair'])}\n",
        encoding="utf-8",
    )
    write_hashes(fz)


def main(argv: list[str] | None = None) -> dict:
    ap = argparse.ArgumentParser(description="2026 runoff forecast after the first round (PREREG_ADDENDUM_04 s.1B)")
    ap.add_argument("--status", choices=["PRELIMINARY", "FINAL"], required=True)
    ap.add_argument("--cutoff", required=True, help="information cutoff date; FINAL uses 2026-10-24")
    ap.add_argument("--pair", nargs=2, metavar=("FIRST", "SECOND"), default=None, help=PAIR_HELP)
    ap.add_argument("--variant", default=None, help="default: outputs/regime_selection.json production variant")
    ap.add_argument("--freeze-dir", type=Path, default=None, help="FINAL only: runoff package directory")
    a = ap.parse_args(argv)
    if a.pair is None:
        ap.error(f"--pair is required: {PAIR_HELP}; the runoff forecast is not run without the first-round result")
    try:
        pair = resolve_pair(a.pair)
        cutoff = date.fromisoformat(a.cutoff)
    except ValueError as err:
        ap.error(str(err))
    e1, e2 = config.ELECTION_DATES[(ELECTION, 1)], config.ELECTION_DATES[(ELECTION, ROUND)]
    if not e1 < cutoff < e2:
        ap.error(f"--cutoff must fall after the first round ({e1}) and before the runoff ({e2})")
    if a.status == "FINAL" and cutoff != FREEZE_CUTOFF:
        ap.error(f"a FINAL runoff forecast uses the registered cutoff {FREEZE_CUTOFF}")
    if a.freeze_dir is not None:
        if a.status != "FINAL":
            ap.error("--freeze-dir is for --status FINAL only")
        try:
            _check_freeze_dir(a.freeze_dir)
        except ValueError as err:
            ap.error(str(err))

    variant = a.variant or json.loads(REGIME.read_text(encoding="utf-8"))["production_variant"]
    rev = json.loads(REVISION.read_text(encoding="utf-8")) if REVISION.exists() else {"pt_oldid": 73082572}
    tag = pair_tag(a.status, rev["pt_oldid"], pair)
    polls = load_polls((ELECTION,))
    results = load_results()
    results_hist = results[results["election"].astype(str).isin(config.HISTORICAL)]  # no 2026 result is used
    try:
        res = forecast_runoff(polls, results_hist, pair, cutoff, variant=variant, tag=tag, cache=CACHE)
    except (FileNotFoundError, RuntimeError, ValueError) as err:
        raise SystemExit(str(err)) from err

    commit = git_commit()
    doc = document(res, a.status, rev, utc_now_iso(), commit)
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    if a.freeze_dir is not None:
        write_freeze(a.freeze_dir, doc, res, commit)
    print(
        json.dumps(
            {
                "status": doc["status"],
                "cutoff": doc["cutoff"],
                "runoff_pair": doc["runoff_pair"],
                "label": doc["label"],
                "n_polls": doc["n_polls"],
                "probabilities": {c: round(p, 4) for c, p in doc["probabilities"].items()},
            },
            indent=1,
            ensure_ascii=False,
        )
    )
    return doc


if __name__ == "__main__":
    main()

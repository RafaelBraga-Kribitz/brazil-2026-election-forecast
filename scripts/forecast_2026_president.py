"""2026: probability of being elected president under this model (PRELIMINARY or FINAL; PREREG_ADDENDUM_04).

Usage:
  python scripts/forecast_2026_president.py --status PRELIMINARY --cutoff 2026-09-29 [--workers 1]
  python scripts/forecast_2026_president.py --status FINAL --cutoff 2026-10-03 --freeze-dir outputs/freeze

Needs, for the same status, cutoff and poll revision (data/interim/polls_wiki_2026.revision.json):
  - the cached 2026 first-round fit written by scripts/forecast_2026.py;
  - outputs/h2h_deviations.csv written by scripts/run_runoff_backtest.py --stage evaluate.

Steps: first-round model F and E draws (same code path as forecast_2026.py) -> path masses; head-to-head random walk
of every 2026 pair with at least MIN_POLLS_PER_FIT h2h polls at the cutoff (fitted here into data/cache/fits_h2h/
unless cached); h2h election-day error models E and F trained on all historical h2h deviations; combination under
the independence assumption. Primary combination: first-round F x head-to-head E; F x F, E x E and the diagnostic E0
(first-round F x h2h latent walk only) are reported under "alternatives". Writes outputs/president_2026.json (read by
brfc.figures.president_probability) and appends outputs/president_history.csv. With --freeze-dir (FINAL only) also
writes president.json and president.csv into that directory and rewrites its forecast_hash.txt over every file in it.
Only historical results are read; 2026 results never are.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from pathlib import Path

import pandas as pd

from brfc import conditional, config, pipeline, runoff
from brfc.data import load_polls, load_results
from brfc.provenance import sha256_file, utc_now_iso

ELECTION = "2026"
DEVIATIONS = config.OUTPUTS / "h2h_deviations.csv"
REVISION = config.DATA / "interim" / "polls_wiki_2026.revision.json"
HASH_FILE = "forecast_hash.txt"
FREEZE_FILES = ("president.json", "president.csv")
DIAGNOSTIC = "E0"  # first-round F x h2h latent walk only (no h2h election-day term)
TABLE_COLUMNS = [
    "status",
    "cutoff",
    "combination",
    "r1_model",
    "h2h_model",
    "primary",
    "candidate",
    "component",
    "pair",
    "pair_mass",
    "p_win_given_pair",
    "probability",
]


def git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=config.ROOT, text=True).strip()
    except Exception:
        return "unknown"


def _fit_job(args) -> pipeline.CachedFit | None:
    pair, h, variant, tag = args
    return runoff.fit_h2h(load_polls((ELECTION,)), ELECTION, pair, h, variant, tag=tag)


def fit_pairs(pairs: list[tuple[str, str]], h: int, variant: str, tag: str, workers: int) -> dict:
    todo = [(p, h, variant, tag) for p in pairs]
    if workers > 1 and len(todo) > 1:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            return dict(zip(pairs, ex.map(_fit_job, todo), strict=True))
    return {p: _fit_job(j) for p, j in zip(pairs, todo, strict=True)}


def combination_models(label: str) -> tuple[str, str]:
    """(first-round model, h2h model) of an alternatives key."""
    if label == DIAGNOSTIC:
        return "F", "E0"
    r1_model, h2h_model = label.split("_", 1)
    return r1_model, h2h_model


def alternatives_block(combos: dict[str, dict]) -> dict:
    """JSON "alternatives": every reported combination (F_E primary, F_F, E_E, and E0 when present)."""
    primary = runoff.combination_label(*runoff.PRIMARY_COMBINATION)
    out = {}
    for label, r in combos.items():
        r1_model, h2h_model = combination_models(label)
        out[label] = {
            "r1_model": r1_model,
            "h2h_model": h2h_model,
            "primary": label == primary,
            "probabilities": r["probabilities"],
            "unmodelled": r["unmodelled"],
            "outright": r["outright"],
            "pairs": r["pairs"],
        }
    return out


def president_table(combos: dict[str, dict], status: str, cutoff: str) -> pd.DataFrame:
    """Per-candidate probability of being elected and its components (outright win, each modelled runoff pair),
    plus the unmodelled mass, for every combination."""
    primary = runoff.combination_label(*runoff.PRIMARY_COMBINATION)
    rows = []
    for label, r in combos.items():
        r1_model, h2h_model = combination_models(label)
        base = {
            "status": status,
            "cutoff": cutoff,
            "combination": label,
            "r1_model": r1_model,
            "h2h_model": h2h_model,
            "primary": label == primary,
        }
        for c, p in r["probabilities"].items():
            rows.append(base | {"candidate": c, "component": "elected", "probability": p})
            rows.append(base | {"candidate": c, "component": "outright", "probability": r["outright"].get(c, 0.0)})
            for q in r["pairs"]:
                if q["modelled"] and c in q["candidates"]:
                    rows.append(
                        base
                        | {
                            "candidate": c,
                            "component": "runoff",
                            "pair": q["pair"],
                            "pair_mass": q["mass"],
                            "p_win_given_pair": q["win"][c],
                            "probability": q["mass"] * q["win"][c],
                        }
                    )
        rows.append(base | {"candidate": "", "component": "unmodelled", "probability": r["unmodelled"]})
    return pd.DataFrame(rows).reindex(columns=TABLE_COLUMNS)


def _hash_entries(freeze_dir: Path) -> list[tuple[str, str]]:
    path = freeze_dir / HASH_FILE
    if not path.exists():
        return []
    return [tuple(x.split("  ", 1)) for x in path.read_text(encoding="utf-8").splitlines() if "  " in x]


def write_freeze(freeze_dir: Path, doc: dict, table: pd.DataFrame) -> list[str]:
    """Write president.json and president.csv into `freeze_dir`, then rewrite forecast_hash.txt ("sha  name", name
    relative to the directory) over every file in it except forecast_hash.txt. Refuses to run if a file already
    listed in forecast_hash.txt (other than the two written here) is missing or no longer matches its hash."""
    freeze_dir = Path(freeze_dir)
    freeze_dir.mkdir(parents=True, exist_ok=True)
    stale = [
        name
        for sha, name in _hash_entries(freeze_dir)
        if name not in FREEZE_FILES and (not (freeze_dir / name).is_file() or sha256_file(freeze_dir / name) != sha)
    ]
    if stale:
        raise SystemExit(
            f"{freeze_dir / HASH_FILE} lists files that are missing or changed since they were hashed: "
            f"{', '.join(stale)}; regenerate the first-round freeze package before adding the president files"
        )
    (freeze_dir / "president.json").write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    table.to_csv(freeze_dir / "president.csv", index=False)
    files = sorted(p for p in freeze_dir.rglob("*") if p.is_file() and p.name != HASH_FILE)
    lines = [f"{sha256_file(p)}  {p.relative_to(freeze_dir).as_posix()}" for p in files]
    (freeze_dir / HASH_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return lines


def history_rows(doc: dict, pt_oldid) -> pd.DataFrame:
    base = {
        "status": doc["status"],
        "cutoff": doc["cutoff"],
        "generated_utc": doc["generated_utc"],
        "pt_oldid": pt_oldid,
        "rw_variant": doc["first_round"]["rw_variant"],
        "r1_model": doc["first_round"]["model"],
        "h2h_model": doc["head_to_head"]["model"],
        "n_validation_elections": len(doc["validation_elections"]),
    }
    rows = [
        base | {"kind": "candidate", "name": c, "p_elected": p, "p_outright": doc["outright"].get(c, 0.0)}
        for c, p in doc["probabilities"].items()
    ]
    rows.append(base | {"kind": "unmodelled", "name": "pairs without an h2h fit", "p_elected": doc["unmodelled"]})
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", choices=["PRELIMINARY", "FINAL"], required=True)
    ap.add_argument("--cutoff", required=True)
    ap.add_argument("--variant", default=None, help="default: outputs/regime_selection.json production variant")
    ap.add_argument("--workers", type=int, default=1, help="parallel h2h fits (default 1: sequential)")
    ap.add_argument(
        "--freeze-dir",
        type=Path,
        default=None,
        help="FINAL only: also write president.json and president.csv into DIR and rewrite DIR/forecast_hash.txt",
    )
    a = ap.parse_args()
    # pre-flight checks, before any fit or file is written
    if a.freeze_dir is not None and a.status != "FINAL":
        raise SystemExit("--freeze-dir is only valid with --status FINAL")
    if not DEVIATIONS.exists():
        raise SystemExit(
            f"missing {DEVIATIONS}: run scripts/run_runoff_backtest.py --stage fits and --stage evaluate first"
        )
    cutoff = date.fromisoformat(a.cutoff)
    variant = a.variant or json.loads((config.OUTPUTS / "regime_selection.json").read_text())["production_variant"]
    rev = json.loads(REVISION.read_text(encoding="utf-8")) if REVISION.exists() else {"pt_oldid": 73082572}
    tag = f"{a.status.lower()}_pt{rev['pt_oldid']}"
    h = (config.ELECTION_DATES[(ELECTION, 1)] - cutoff).days
    if h < 1:
        raise SystemExit("the cutoff must be at least one day before the first round")
    r1_key = pipeline.fit_key(ELECTION, 1, h, variant, tag)
    if not (pipeline.CACHE / f"{r1_key}.json").exists():
        raise SystemExit(
            f"missing first-round fit {r1_key}: run scripts/forecast_2026.py --status {a.status} --cutoff {cutoff}"
        )
    devs = pd.read_csv(DEVIATIONS, dtype={"election": str, "round": str})
    if "variant" in devs.columns:
        devs = devs[devs["variant"] == variant]
    devs = devs[devs["election"].isin(config.HISTORICAL)]
    if devs.empty:
        raise SystemExit(f"no historical h2h deviations for variant {variant}")

    results_hist = load_results()
    results_hist = results_hist[results_hist["election"].isin(config.HISTORICAL)]
    r1 = {
        m: runoff.first_round_forecast(ELECTION, h, variant, results_hist, m, tag=tag)
        for m in dict.fromkeys(c[0] for c in runoff.COMBINATIONS)
    }
    draws, cats = r1["F"]["draws"], r1["F"]["categories"]

    polls = load_polls((ELECTION,))
    pairs = runoff.h2h_pairs(polls, ELECTION, cutoff)
    fits = fit_pairs(pairs, h, variant, tag, a.workers)
    path_pairs = set()
    for d in r1.values():
        path_pairs |= set(conditional.path_masses(d["draws"], d["categories"])["pairs"])
    n_polls = {p: int(runoff.h2h_rows(polls, ELECTION, p, cutoff)["poll_id"].nunique()) for p in {*pairs, *path_pairs}}

    posts = {v: runoff.h2h_error_model(devs, ELECTION, v, seed=runoff.SEED_2026 + (v == "F")) for v in ("E", "F")}
    h2h_train = posts[runoff.PRIMARY_H2H_MODEL].train_elections
    if any(p.train_elections != h2h_train for p in posts.values()):
        raise SystemExit("the h2h error models E and F were trained on different elections")
    combos = runoff.president_combinations(r1, fits, posts, seed=runoff.SEED_2026, n_polls=n_polls)
    combos[DIAGNOSTIC] = runoff.president_forecast(draws, cats, fits, None, n_polls=n_polls)
    primary_label = runoff.combination_label(*runoff.PRIMARY_COMBINATION)
    primary = combos[primary_label]
    wins, _ = runoff.pair_win_probabilities(fits, posts[runoff.PRIMARY_H2H_MODEL], seed=runoff.SEED_2026)
    r1_fit = r1["F"]["fit"]
    doc = {
        "status": a.status,
        "election": "Brazil 2026 presidential election (first round 2026-10-04; runoff, if any, 2026-10-25)",
        "quantity": "probability of being elected under this model",
        "cutoff": str(cutoff),
        "information_cutoff_date": str(cutoff),
        "horizon_days_first_round": h,
        "generated_utc": utc_now_iso(),
        "git_commit": git_commit(),
        "label": runoff.validation_label(len(h2h_train)),
        "validation_elections": h2h_train,
        "h2h_term_trained_on": h2h_train,
        "combination": {
            "label": primary_label,
            "r1_model": runoff.PRIMARY_COMBINATION[0],
            "h2h_model": runoff.PRIMARY_COMBINATION[1],
        },
        "probabilities": primary["probabilities"],
        "unmodelled": primary["unmodelled"],
        "outright": primary["outright"],
        "pairs": primary["pairs"],
        "first_round": {
            "model": runoff.PRIMARY_COMBINATION[0],
            "rw_variant": variant,
            "fit_key": r1_fit.key,
            "categories": cats,
            "n_polls": r1_fit.meta["n_polls"],
            "election_day_term_trained_on": r1["F"]["train_elections"],
            "fit_diagnostics": r1_fit.meta.get("diagnostics"),
        },
        "head_to_head": {
            "model": runoff.PRIMARY_H2H_MODEL,
            "definition": "round-2 poll scenarios fielded before the first round; pair[0] valid share modelled, "
            "pair[1] = 100 - pair[0]",
            "error_model": posts[runoff.PRIMARY_H2H_MODEL].summary(),
            "error_model_trained_on": h2h_train,
            "error_models": {v: p.summary() for v, p in posts.items()},
            "fits": [
                {
                    "pair": conditional.pair_label(p),
                    "candidates": list(p),
                    "fit_key": runoff.h2h_key(ELECTION, p, h, variant, tag),
                    "n_polls": n_polls[p],
                    "status": "ok" if fits[p] is not None else "skipped",
                    "converged": runoff.converged(fits[p]),
                    "win": wins.get(p),
                }
                for p in pairs
            ],
        },
        "alternatives": alternatives_block(combos),
        "assumptions": [
            "first-round and head-to-head forecasts are treated as independent",
            "an outright first-round win needs more than 50% of valid votes in a first-round draw",
            "the probability of runoff pairs without a head-to-head fit is reported as unmodelled, never reallocated",
            "valid votes exclude blank and null ballots; undecided respondents are allocated proportionally",
        ],
        "poll_source": rev,
        "neutrality_note": "An aggregation of publicly registered polls; not a poll, not a recommendation.",
    }
    config.OUTPUTS.mkdir(exist_ok=True)
    (config.OUTPUTS / "president_2026.json").write_text(
        json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    hist_path = config.OUTPUTS / "president_history.csv"
    new = history_rows(doc, rev["pt_oldid"])
    hist = pd.concat([pd.read_csv(hist_path), new], ignore_index=True) if hist_path.exists() else new
    hist.to_csv(hist_path, index=False)
    if a.freeze_dir is not None:
        lines = write_freeze(a.freeze_dir, doc, president_table(combos, a.status, str(cutoff)))
        print(f"{a.freeze_dir / HASH_FILE}: {len(lines)} files hashed")
    print(
        json.dumps(
            {
                "status": a.status,
                "cutoff": str(cutoff),
                "label": doc["label"],
                "probabilities": {c: round(p, 4) for c, p in doc["probabilities"].items()},
                "unmodelled": round(doc["unmodelled"], 4),
            },
            indent=1,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

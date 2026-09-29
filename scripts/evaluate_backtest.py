"""Stage 2 of the historical backtest: LOEO election-day term, baselines, scoring, regime selection (PREREG s.9).

Reads cached fits (stage 1) and official results. Writes outputs/:
  historical_backtest.csv             one row per (election, round, horizon, model), production RW variant
  historical_backtest_all_variants.csv same for both RW variants
  historical_backtest_categories.csv  per-category forecasts, intervals and errors
  election_day_deviations.csv         eve deviations used by the LOEO term
  election_day_error_models.csv       LOEO error-model posteriors (which elections trained each)
  backtest_summary.csv                means by model x round type x horizon
  calibration.csv                     pooled interval coverage by model x horizon
  regime_selection.json               outcome of the pre-registered volatility-regime rule
  model_diagnostics.csv               convergence diagnostics for every fit
"""

from __future__ import annotations

import json

import pandas as pd

from brfc import config
from brfc.data import load_results
from brfc.pipeline import CACHE, evaluate_backtest

OUT = config.OUTPUTS


def summarise(scores: pd.DataFrame) -> pd.DataFrame:
    s = scores[scores.get("mae").notna()].copy()
    s["round_type"] = s["round"].map({1: "first round", 2: "runoff"})
    agg = s.groupby(["variant", "model", "round_type", "horizon"]).agg(
        n_rounds=("mae", "size"),
        mae=("mae", "mean"),
        margin_abs_error=("margin_abs_error", "mean"),
        brier_first=("brier_first", "mean"),
        log_first=("log_first", "mean"),
        coverage_80=("coverage_80", "mean"),
        coverage_94=("coverage_94", "mean"),
    )
    return agg.reset_index()


def calibration(cats: pd.DataFrame) -> pd.DataFrame:
    c = cats[cats["in80"].notna()].copy()
    c["in80"] = c["in80"].astype(bool)
    c["in94"] = c["in94"].astype(bool)
    c["round_type"] = c["round"].map({1: "first round", 2: "runoff"})
    return (
        c.groupby(["variant", "model", "round_type", "horizon"])
        .agg(
            n_categories=("in80", "size"),
            coverage_80=("in80", "mean"),
            coverage_94=("in94", "mean"),
            width_80=("hi80", lambda x: float((x - c.loc[x.index, "lo80"]).mean())),
            width_94=("hi94", lambda x: float((x - c.loc[x.index, "lo94"]).mean())),
        )
        .reset_index()
    )


def regime_rule(all_scores: pd.DataFrame) -> dict:
    """PREREG 9.1: two-regime only if F's eve mean log score on first place beats single-regime by > 0.05 nats and
    its eve share MAE is not worse by more than 0.1 pp. Otherwise the simpler single-regime variant."""
    f = all_scores[(all_scores["model"] == "F") & (all_scores["horizon"] == 1)]
    m = f.groupby("variant").agg(log_first=("log_first", "mean"), mae=("mae", "mean"), n=("mae", "size"))
    two, one = m.loc["two_regime"], m.loc["single_regime"]
    choose_two = (two["log_first"] - one["log_first"] > 0.05) and (two["mae"] - one["mae"] <= 0.1)
    return {
        "rule": "PREREG.md section 9.1",
        "n_rounds": int(two["n"]),
        "two_regime": {"mean_log_first": float(two["log_first"]), "mean_mae": float(two["mae"])},
        "single_regime": {"mean_log_first": float(one["log_first"]), "mean_mae": float(one["mae"])},
        "production_variant": "two_regime" if choose_two else "single_regime",
        "note": "in-sample structural choice across all three historical elections; both variants published",
    }


def diagnostics() -> pd.DataFrame:
    rows = []
    for p in sorted(CACHE.glob("*.json")):
        m = json.loads(p.read_text(encoding="utf-8"))
        if m.get("tag"):
            continue
        d = m.get("diagnostics") or {}
        rows.append(
            {
                "key": m["key"],
                "election": m["election"],
                "round": m["round"],
                "horizon": m["horizon"],
                "variant": m["variant"],
                "status": m["status"],
                "n_polls": m["n_polls"],
                "n_pollsters": m["n_pollsters"],
                "n_dropped_overlap": m["n_dropped_overlap"],
                "named": "|".join(m["named"]),
                "retried": "first_attempt_diagnostics" in m,
                **d,
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    results = load_results()
    frames, cats, devs, errs = [], [], [], []
    for variant in ("two_regime", "single_regime"):
        s, c, d, e = evaluate_backtest(variant, results)
        frames.append(s)
        cats.append(c)
        devs.append(d.assign(variant=variant))
        errs.append(e)
    scores = pd.concat(frames, ignore_index=True)
    rule = regime_rule(scores)
    prod = rule["production_variant"]
    scores.to_csv(OUT / "historical_backtest_all_variants.csv", index=False)
    scores[scores["variant"] == prod].to_csv(OUT / "historical_backtest.csv", index=False)
    cat = pd.concat(cats, ignore_index=True)
    cat.to_csv(OUT / "historical_backtest_categories.csv", index=False)
    pd.concat(devs, ignore_index=True).to_csv(OUT / "election_day_deviations.csv", index=False)
    pd.concat(errs, ignore_index=True).to_csv(OUT / "election_day_error_models.csv", index=False)
    summarise(scores).to_csv(OUT / "backtest_summary.csv", index=False)
    calibration(cat).to_csv(OUT / "calibration.csv", index=False)
    diagnostics().to_csv(OUT / "model_diagnostics.csv", index=False)
    (OUT / "regime_selection.json").write_text(json.dumps(rule, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(rule, indent=1))


if __name__ == "__main__":
    main()

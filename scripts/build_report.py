"""Render docs/historical-backtest.md from outputs/ (no number is typed by hand).

Also writes outputs/success_criteria_historical.json: the pre-registered criteria H1-H4 (PREREG.md s.12),
evaluated mechanically.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from brfc import config

OUT = config.OUTPUTS
LABEL = {
    "F": "F: RW + election-day term (primary)",
    "E": "E: RW, zero-mean day error",
    "E0": "E0: RW latent only",
    "B": "B: 14-day latest per pollster",
    "C": "C: final Datafolha",
    "D": "D: final AtlasIntel",
}
HZ = {30: "T-30", 14: "T-14", 7: "T-7", 1: "eve"}


def f(x, nd=2, pct=False):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "N/A"
    return f"{100 * x:.0f}%" if pct else f"{x:.{nd}f}"


def md_table(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    lines += ["| " + " | ".join(str(v) for v in row) + " |" for row in df.itertuples(index=False)]
    return "\n".join(lines)


def comparison_matrix(s: pd.DataFrame, cats: pd.DataFrame) -> pd.DataFrame:
    eve = s[(s["horizon"] == 1) & s["mae"].notna()]
    c = cats[(cats["horizon"] == 1) & cats["in80"].notna()]
    rows = []
    for m in ("E0", "E", "F", "B", "C", "D"):
        g = eve[eve["model"] == m]
        if g.empty:
            continue
        cc = c[c["model"] == m]
        prob = bool(g["probabilistic"].astype("boolean").fillna(False).any())
        rows.append(
            {
                "Model": LABEL[m],
                "Probabilistic": "yes" if m in ("E0", "E", "F") else ("conversion" if prob else "no"),
                "Rounds": len(g),
                "Share MAE (pp)": f(g["mae"].mean()),
                "Top-two margin abs. error (pp)": f(g["margin_abs_error"].mean()),
                "Brier, first place": f(g["brier_first"].mean(), 3),
                "Log score, first place": f(g["log_first"].mean(), 3),
                "80% coverage": f(cc["in80"].astype(bool).mean(), pct=True) + f" (n={len(cc)})" if len(cc) else "N/A",
                "94% coverage": f(cc["in94"].astype(bool).mean(), pct=True) + f" (n={len(cc)})" if len(cc) else "N/A",
            }
        )
    return pd.DataFrame(rows)


def backtest_table(s: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for r in s.sort_values(["election", "round", "horizon", "model"], ascending=[True, True, False, True]).itertuples():
        if pd.isna(getattr(r, "mae", np.nan)):
            continue
        rows.append(
            {
                "Election": r.election,
                "Round": r.round,
                "Horizon": HZ.get(r.horizon, r.horizon),
                "Model": r.model,
                "Polls": r.n_polls,
                "MAE (pp)": f(r.mae),
                "Margin error (pp)": f(r.margin_error),
                "80% cov.": f(getattr(r, "coverage_80", np.nan), pct=True),
                "94% cov.": f(getattr(r, "coverage_94", np.nan), pct=True),
                "Trained on (LOEO)": r.train_elections if isinstance(r.train_elections, str) else "",
            }
        )
    return pd.DataFrame(rows)


def criteria(s: pd.DataFrame, cats: pd.DataFrame) -> dict:
    eve = s[(s["horizon"] == 1) & s["mae"].notna()]
    c = cats[(cats["horizon"] == 1) & cats["in80"].notna()]
    mf = eve[eve["model"] == "F"]
    mb = eve[eve["model"] == "B"]
    me = eve[eve["model"] == "E"]
    cf = c[c["model"] == "F"]
    cov94, cov80 = cf["in94"].astype(bool).mean(), cf["in80"].astype(bool).mean()
    out = {
        "H1_F_mae_le_B": {
            "F": mf["mae"].mean(),
            "B": mb["mae"].mean(),
            "met": bool(mf["mae"].mean() <= mb["mae"].mean()),
        },
        "H2_F_coverage": {
            "cov94": cov94,
            "cov80": cov80,
            "n": len(cf),
            "met": bool(0.85 <= cov94 <= 1.0 and 0.65 <= cov80 <= 0.95),
        },
        "H3_F_brier_le_B": {
            "F": mf["brier_first"].mean(),
            "B": mb["brier_first"].mean(),
            "met": bool(mf["brier_first"].mean() <= mb["brier_first"].mean()),
        },
        "H4_F_mae_le_E": {
            "F": mf["mae"].mean(),
            "E": me["mae"].mean(),
            "met": bool(mf["mae"].mean() <= me["mae"].mean()),
        },
        "scope": "eve horizon, historical rounds with a fitted eve forecast, production RW variant",
    }
    return out


def main() -> None:
    rule = json.loads((OUT / "regime_selection.json").read_text())
    prod = rule["production_variant"]
    s = pd.read_csv(OUT / "historical_backtest.csv", dtype={"election": str})
    cats = pd.read_csv(OUT / "historical_backtest_categories.csv", dtype={"election": str})
    cats = cats[cats["variant"] == prod]
    devs = pd.read_csv(OUT / "election_day_deviations.csv", dtype={"election": str})
    devs = devs[devs["variant"] == prod]
    errs = pd.read_csv(OUT / "final_poll_error_table.csv", dtype={"election": str})
    diag = pd.read_csv(OUT / "model_diagnostics.csv", dtype={"election": str})
    crit = criteria(s, cats)
    (OUT / "success_criteria_historical.json").write_text(json.dumps(crit, indent=1, default=float) + "\n")

    dv = (
        devs.assign(dev=devs["deviation"].round(2))
        .pivot_table(index=["round", "role"], columns="election", values="dev", aggfunc="mean")
        .round(2)
    )
    dv = dv.reset_index()
    dat = errs[errs["pollster"] == "Datafolha"].copy()
    dat = dat[dat["official_valid_share"] >= 2.0]
    dat = dat.assign(
        **{
            "Release (valid %)": dat["release_valid_share"].map(f),
            "Official (valid %)": dat["official_valid_share"].map(f),
            "Release − official (pp)": dat["release_minus_official_pp"].map(lambda x: f"{x:+.2f}"),
        }
    )
    dat = dat[["election", "round", "candidate", "Release (valid %)", "Official (valid %)", "Release − official (pp)"]]
    conv = diag[diag["status"] == "ok"]
    n_conv = int(conv["converged"].astype(bool).sum())

    parts = [
        "# Historical backtest (retrodiction), 2014-2022",
        "",
        "*Generated by `scripts/build_report.py` from `outputs/`; do not edit by hand.*",
        "",
        "## Design",
        "Every forecast uses only polls whose fieldwork ended by its cutoff (election day minus the horizon). The "
        "election-day term and the baseline error bands for election *k* are learned from the other two elections "
        "only (leave-one-election-out); the 'Trained on' column shows which. Official results: "
        "`data/manual/results_secondary.csv` (TSE-citing secondary sources, pending TSE-file reconciliation).",
        "",
        f"Production random-walk variant: **{prod}** (pre-registered rule, PREREG s.9.1: two-regime mean eve log score "
        f"{rule['two_regime']['mean_log_first']:.3f} vs single {rule['single_regime']['mean_log_first']:.3f}; share MAE "
        f"{rule['two_regime']['mean_mae']:.2f} vs {rule['single_regime']['mean_mae']:.2f} pp). This choice is in-sample "
        "across the three elections; both variants are in `outputs/historical_backtest_all_variants.csv`.",
        "",
        f"Convergence: {n_conv} of {len(conv)} fitted cells meet the pre-registered criteria (R-hat <= 1.01, ESS >= 400, "
        "divergences <= 1%) after up to three attempts; non-converged cells are listed at the end and kept in every table.",
        "",
        "## Model comparison at the eve horizon (pooled over rounds)",
        md_table(comparison_matrix(s, cats)),
        "",
        "Baselines B-D have no published probabilities; their Brier/log/coverage use the *calibrated probabilistic "
        "conversion of point baseline* (point + Normal(0, LOEO RMSE)). Coverage counts candidate categories; categories "
        "within one election are correlated, so the effective sample is smaller than n.",
        "",
        "## Pre-registered historical criteria (PREREG s.12)",
        md_table(
            pd.DataFrame(
                [
                    {
                        "Criterion": "H1 F share MAE <= B",
                        "Value": f"F {crit['H1_F_mae_le_B']['F']:.2f} vs B {crit['H1_F_mae_le_B']['B']:.2f} pp",
                        "Met": "yes" if crit["H1_F_mae_le_B"]["met"] else "no",
                    },
                    {
                        "Criterion": "H2 F 94% cov. in [85,100]%, 80% in [65,95]%",
                        "Value": f"94%: {100 * crit['H2_F_coverage']['cov94']:.0f}%, 80%: {100 * crit['H2_F_coverage']['cov80']:.0f}% (n={crit['H2_F_coverage']['n']})",
                        "Met": "yes" if crit["H2_F_coverage"]["met"] else "no",
                    },
                    {
                        "Criterion": "H3 F first-place Brier <= B conversion",
                        "Value": f"F {crit['H3_F_brier_le_B']['F']:.3f} vs B {crit['H3_F_brier_le_B']['B']:.3f}",
                        "Met": "yes" if crit["H3_F_brier_le_B"]["met"] else "no",
                    },
                    {
                        "Criterion": "H4 F share MAE <= E",
                        "Value": f"F {crit['H4_F_mae_le_E']['F']:.2f} vs E {crit['H4_F_mae_le_E']['E']:.2f} pp",
                        "Met": "yes" if crit["H4_F_mae_le_E"]["met"] else "no",
                    },
                ]
            )
        ),
        "",
        "## Eve deviation by forecast-rank role (observed − eve forecast, pp)",
        "These are the inputs of the election-day term. Roles are forecast ranks, not candidates.",
        md_table(dv),
        "",
        "## Verification of the planning-material pattern: final Datafolha vs official (valid votes)",
        "Recomputed from `data/manual/final_poll_verification.csv` and official results (candidates >= 2%).",
        md_table(dat),
        "",
        "## All cells",
        md_table(backtest_table(s)),
        "",
    ]
    bad = conv[~conv["converged"].astype(bool)]
    if len(bad):
        parts += [
            "## Non-converged cells (kept, flagged)",
            md_table(bad[["key", "n_polls", "rhat_max", "ess_bulk_min", "ess_tail_min", "divergences"]].round(4)),
            "",
        ]
    (config.ROOT / "docs" / "historical-backtest.md").write_text("\n".join(parts), encoding="utf-8", newline="\n")
    print(json.dumps(crit, indent=1, default=float))


if __name__ == "__main__":
    main()

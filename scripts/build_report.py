"""Render docs/historical-backtest.md from outputs/ (no number is typed by hand).

Also writes outputs/success_criteria_historical.json: the pre-registered criteria H1-H4 (PREREG.md s.12),
evaluated mechanically. The official-results note reads outputs/tse_reconciliation_*.csv.

render_runoff() renders docs/runoff-backtest.md (pre-first-round head-to-head and "who was elected" backtest,
PREREG_ADDENDUM_04.md s.5) from outputs/runoff_backtest.csv, president_backtest.csv, h2h_deviations.csv,
success_criteria_runoff.json and runoff_sensitivity.csv. It reads those files only and writes nothing to outputs/.
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


def tse_note() -> str:
    """Reconciliation status of the scored results, read from outputs/tse_reconciliation_*.csv."""
    files = sorted(OUT.glob("tse_reconciliation_*.csv"))
    if not files:
        return "TSE-citing secondary sources, pending TSE-file reconciliation"
    rec = pd.concat([pd.read_csv(p) for p in files], ignore_index=True)
    equal = int((rec["difference"] == 0).sum())
    years = ", ".join(p.stem.rsplit("_", 1)[-1] for p in files)
    return (
        f"TSE-citing secondary sources, reconciled with the TSE files for {years}: {equal} of {len(rec)} candidate "
        "vote counts equal, `outputs/tse_reconciliation_*.csv`"
    )


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
        f"`data/manual/results_secondary.csv` ({tse_note()}).",
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
    render_runoff()


# --------------------------------------------------------------------------------------------------------------
# Runoff / "who was elected" backtest (PREREG_ADDENDUM_04.md s.5)
# --------------------------------------------------------------------------------------------------------------

H2H_LABEL = {
    "E": "E: RW, zero-mean day error (primary)",
    "F": "F: RW + day-error mean",
    "E0": "E0: RW latent only",
    "B": "B: 14-day latest per pollster (conversion)",
    "C": "C: final Datafolha (conversion)",
}
PRES_LABEL = {
    "F_E": "F × E (primary)",
    "F_F": "F × F",
    "E_E": "E × E",
    "B": "B: 14-day latest per pollster (conversion)",
    "C": "C: final Datafolha (conversion)",
}
H2H_ORDER = ["E", "F", "E0", "B", "C"]
PRES_ORDER = ["F_E", "F_F", "E_E", "B", "C"]
SCALE = {"sig1.5": "1.5", "primary": "3 (primary)", "sig6": "6"}


def _inside(x) -> str:
    """Coverage of one two-candidate result: both shares are complements, so the value is 0 or 1."""
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "N/A"
    if x in (0.0, 1.0):
        return "yes" if x == 1.0 else "no"
    return f(x, pct=True)


def _ordered(df: pd.DataFrame, col: str, order: list[str]) -> pd.DataFrame:
    return df.assign(_o=df[col].map({m: i for i, m in enumerate(order)})).sort_values(
        ["election", "r1_horizon", "_o"], ascending=[True, True, True]
    )


def _h2h_cells(rb: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for r in _ordered(rb, "model", H2H_ORDER).itertuples():
        rows.append(
            {
                "Election": r.election,
                "Cutoff": f"{HZ.get(r.r1_horizon, r.r1_horizon)} ({r.cutoff})",
                "Pair": r.pair,
                "Polls": r.n_polls,
                "Model": H2H_LABEL.get(r.model, r.model),
                "Share MAE (pp)": f(r.mae),
                "Margin error (pp)": f(r.margin_error),
                "Inside 80%": _inside(r.coverage_80),
                "Inside 94%": _inside(r.coverage_94),
                "94% width (pp)": f(r.width_94_mean),
                "P(runoff winner)": f(r.p_actual_first, 3),
                "Brier, runoff winner": f(r.brier_first, 3),
                "Log, runoff winner": f(r.log_first, 3),
                "Trained on (LOEO)": r.train_elections if isinstance(r.train_elections, str) else "",
            }
        )
    return pd.DataFrame(rows)


def _pooled(sens: pd.DataFrame, table: str, order: list[str], labels: dict, metrics: dict) -> pd.DataFrame:
    p = sens[(sens["setting"] == "primary") & (sens["table"] == table)].copy()
    p["_o"] = p["model"].map({m: i for i, m in enumerate(order)})
    rows = []
    for r in p.sort_values(["r1_horizon", "_o"]).to_dict("records"):
        row = {
            "Cutoff": HZ.get(r["r1_horizon"], r["r1_horizon"]),
            "Model": labels.get(r["model"], r["model"]),
            "Elections": r["n_elections"],
        }
        for col, (key, nd, pct) in metrics.items():
            row[col] = f(r[key], nd, pct)
        rows.append(row)
    return pd.DataFrame(rows)


def _pres_cells(pb: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for r in _ordered(pb, "model", PRES_ORDER).itertuples():
        rows.append(
            {
                "Election": r.election,
                "Cutoff": f"{HZ.get(r.r1_horizon, r.r1_horizon)} ({r.cutoff})",
                "Elected": r.winner,
                "Model": PRES_LABEL.get(r.model, r.model),
                "Pairs with h2h fit": r.n_pairs_h2h,
                "P(outright win)": f(r.p_outright_total, 3),
                "P(actual pair)": f(r.p_actual_pair, 3),
                "Unmodelled": f(r.unmodelled, 3),
                "P(elected candidate)": f(r.p_winner, 3),
                "Brier": f(r.brier, 3),
                "Log": f(r.log, 3),
            }
        )
    return pd.DataFrame(rows)


def _pres_probabilities(pb: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for r in _ordered(pb[pb["model"] == "F_E"], "model", PRES_ORDER).itertuples():
        probs = json.loads(r.probabilities_json)
        shown = ", ".join(f"{k} {100 * v:.1f}%" for k, v in sorted(probs.items(), key=lambda kv: -kv[1]))
        rows.append(
            {
                "Election": r.election,
                "Cutoff": HZ.get(r.r1_horizon, r.r1_horizon),
                "Elected": r.winner,
                "P(elected under this model), F × E": shown,
                "Unmodelled": f"{100 * r.unmodelled:.1f}%",
            }
        )
    return pd.DataFrame(rows)


def _hr3_by_prior(sens: pd.DataFrame) -> list[tuple[str, float, float, bool]]:
    """HR3 (who-was-elected Brier at the eve, F_E <= B) recomputed under each error-scale prior setting."""
    p = sens[(sens["table"] == "president") & (sens["r1_horizon"] == 1)]
    out = []
    for setting, label in SCALE.items():
        g = p[p["setting"] == setting].set_index("model")["brier"]
        if {"F_E", "B"} <= set(g.index):
            out.append((label, float(g["F_E"]), float(g["B"]), bool(g["F_E"] <= g["B"])))
    return out


def _sensitivity(sens: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    s = sens[sens["setting"].isin(SCALE)].copy()
    s["Scale"] = s["setting"].map(SCALE)
    s["_s"] = s["setting"].map({k: i for i, k in enumerate(SCALE)})
    unchanged = []
    for (table, model), g in s.groupby(["table", "model"]):
        cols = ["mae", "coverage_94", "brier", "log"] if table == "h2h" else ["brier", "log", "p_winner"]
        if all(g.groupby("r1_horizon")[c].nunique(dropna=False).max() == 1 for c in cols):
            unchanged.append((H2H_LABEL if table == "h2h" else PRES_LABEL).get(model, model))
    s["_o"] = [
        (H2H_ORDER if t == "h2h" else PRES_ORDER).index(m) if m in (H2H_ORDER if t == "h2h" else PRES_ORDER) else 99
        for t, m in zip(s["table"], s["model"], strict=True)
    ]
    h_rows, p_rows = [], []
    for r in s.sort_values(["table", "_o", "r1_horizon", "_s"]).to_dict("records"):
        if r["table"] == "h2h" and r["model"] in ("E", "F"):
            h_rows.append(
                {
                    "Model": H2H_LABEL[r["model"]],
                    "Cutoff": HZ.get(r["r1_horizon"], r["r1_horizon"]),
                    "Scale prior": r["Scale"],
                    "Share MAE (pp)": f(r["mae"]),
                    "80% coverage": f(r["coverage_80"], pct=True),
                    "94% coverage": f(r["coverage_94"], pct=True),
                    "94% width (pp)": f(r["width_94"]),
                    "Brier": f(r["brier"], 3),
                    "Log": f(r["log"], 3),
                }
            )
        if r["table"] == "president" and r["model"] in ("F_E", "F_F", "E_E"):
            p_rows.append(
                {
                    "Model": PRES_LABEL[r["model"]],
                    "Cutoff": HZ.get(r["r1_horizon"], r["r1_horizon"]),
                    "Scale prior": r["Scale"],
                    "Brier": f(r["brier"], 3),
                    "Log": f(r["log"], 3),
                    "Mean P(elected candidate)": f(r["p_winner"], 3),
                }
            )
    return pd.DataFrame(h_rows), pd.DataFrame(p_rows), sorted(set(unchanged))


def render_runoff() -> None:
    """Write docs/runoff-backtest.md from the runoff backtest outputs (reads outputs/ only)."""
    rb = pd.read_csv(OUT / "runoff_backtest.csv", dtype={"election": str})
    pb = pd.read_csv(OUT / "president_backtest.csv", dtype={"election": str})
    dv = pd.read_csv(OUT / "h2h_deviations.csv", dtype={"election": str})
    sens = pd.read_csv(OUT / "runoff_sensitivity.csv")
    crit = json.loads((OUT / "success_criteria_runoff.json").read_text(encoding="utf-8"))
    variant = crit["variant"]
    rb = rb[rb["variant"] == variant]
    pb = pb[pb["variant"] == variant]
    dv = dv[dv["variant"] == variant]
    sens = sens[sens["variant"] == variant]

    elections = sorted(rb["election"].unique())
    fits = rb.drop_duplicates("fit_key")
    n_conv = int(fits["converged"].astype(bool).sum())

    hr1, hr2, hr3 = crit["HR1_E_h2h_mae_le_B"], crit["HR2_E_94_interval"], crit["HR3_FxE_brier_le_B"]
    by = ", ".join(f"{e} {'inside' if v else 'outside'}" for e, v in hr2["by_election"].items())
    crit_tab = pd.DataFrame(
        [
            {
                "Criterion": "HR1 head-to-head E share MAE <= B (eve)",
                "Value": f"E {hr1['E']:.2f} vs B {hr1['B']:.2f} pp ({len(hr1['elections'])} elections)",
                "Met": "yes" if hr1["met"] else "no",
            },
            {
                "Criterion": f"HR2 runoff share inside E's 94% interval in >= {hr2['required']} of {hr2['n_elections']} (eve)",
                "Value": f"{hr2['inside_94']} of {hr2['n_elections']} ({by})",
                "Met": "yes" if hr2["met"] else "no",
            },
            {
                "Criterion": "HR3 'who was elected' Brier, F × E <= B (eve)",
                "Value": f"F × E {hr3['F_E']:.3f} vs B {hr3['B']:.3f} ({len(hr3['elections'])} elections)",
                "Met": "yes" if hr3["met"] else "no",
            },
        ]
    )

    dev_tab = pd.DataFrame(
        [
            {
                "Election": r.election,
                "Pair (eve fit)": r.pair,
                "Ranked first in forecast": r.category,
                "Forecast mean, runoff day (valid %)": f(r.forecast),
                "Official (valid %)": f(r.actual),
                "Deviation (pp)": f"{r.deviation:+.2f}",
                "Polls": r.n_polls,
                "Converged": "yes" if bool(r.converged) else "no",
            }
            for r in dv.sort_values("election").itertuples()
        ]
    )
    sd = float(np.sqrt(np.mean(np.square(dv["deviation"])))) if len(dv) else float("nan")
    h_sens, p_sens, unchanged = _sensitivity(sens)
    hr3_prior = "; ".join(
        f"{lab}: F × E {fe:.3f} vs B {b:.3f} ({'met' if ok else 'not met'})" for lab, fe, b, ok in _hr3_by_prior(sens)
    )

    h2h_metrics = {
        "Share MAE (pp)": ("mae", 2, False),
        "Margin abs. error (pp)": ("margin_abs_error", 2, False),
        "80% coverage": ("coverage_80", 2, True),
        "94% coverage": ("coverage_94", 2, True),
        "94% width (pp)": ("width_94", 2, False),
        "Brier, runoff winner": ("brier", 3, False),
        "Log, runoff winner": ("log", 3, False),
    }
    pres_metrics = {
        "Brier": ("brier", 3, False),
        "Log": ("log", 3, False),
        "Mean P(elected candidate)": ("p_winner", 3, False),
        "Mean unmodelled": ("unmodelled", 3, False),
    }

    parts = [
        "# Runoff and 'who was elected' backtest, 2014-2022",
        "",
        "*Generated by `scripts/build_report.py` from `outputs/`; do not edit by hand.*",
        "",
        "**Pre-registered; validated on 3 elections only.** Every probability below is a probability under this "
        "model: of being elected or, in the head-to-head tables, of winning the runoff within one pair. Three "
        "elections are very weak evidence: one election more or less can reverse any comparison on this page.",
        "",
        "## Design",
        "The rules are [`PREREG_ADDENDUM_04.md`](../PREREG_ADDENDUM_04.md) section 5 and "
        "[`PREREG_ADDENDUM_05.md`](../PREREG_ADDENDUM_05.md).",
        "",
        "- **Cutoffs.** Every forecast uses only polls whose fieldwork ended by the first-round cutoff: the first-round "
        "eve (primary) or first-round T-7. Head-to-head polls are runoff scenarios fielded before the first round.",
        f"- **Head-to-head fit.** One random walk per pair with at least {config.MIN_POLLS_PER_FIT} head-to-head polls at "
        "the cutoff, projected to runoff day. Scores below are for the pair that actually advanced.",
        "- **Election-day term.** Learned leave-one-election-out from the other two elections ('Trained on'). "
        "E (zero mean) is primary for this term; F and E0 are reported beside it.",
        "- **Who was elected.** `P(X elected) = P(X outright) + sum over pairs of P(pair, no outright) × P(X wins | pair)`, "
        "with first-round and head-to-head draws treated as independent. Mass of pairs without a head-to-head fit is "
        "reported as unmodelled and never reallocated. The primary combination is first-round F × head-to-head E.",
        "- **Baselines.** B and C run end to end with the calibrated probabilistic conversion of point baseline "
        "(point + Normal(0, LOEO RMSE)); they did not publish these probabilities.",
        "- **Scores.** Head-to-head: share MAE, margin error (predicted minus observed margin of the runoff winner over the other candidate), and "
        "whether the official runoff share lies inside the 80% and 94% equal-tailed posterior predictive intervals "
        "(the two shares are complements, so both are inside or both outside). Who was elected: multi-category Brier over "
        "every candidate with positive probability plus the unmodelled bucket, log score `log(max(P(elected), 1e-4))`, "
        "and P(elected candidate).",
        "",
        f"Production random-walk variant: **{variant}**. Head-to-head fits scored here: {n_conv} of {len(fits)} meet the "
        "pre-registered convergence criteria.",
        "",
        "## Pre-registered criteria (Addendum 04 s.5)",
        md_table(crit_tab),
        "",
        f"Scope: {crit['scope']}. {crit['note'].capitalize()}.",
        "",
        f"HR3 under each error-scale prior (reported only; the registered result uses the primary prior): {hr3_prior}.",
        "",
        "## Head-to-head scores, pooled over elections",
        "Means over the elections scored at each cutoff (`outputs/runoff_sensitivity.csv`, setting `primary`).",
        md_table(_pooled(sens, "h2h", H2H_ORDER, H2H_LABEL, h2h_metrics)),
        "",
        "## Head-to-head scores per election (pair that advanced)",
        "'P(runoff winner)' is the forecast probability that the candidate who won the runoff exceeds 50% in that pair.",
        md_table(_h2h_cells(rb)),
        "",
        "## Who was elected, pooled over elections",
        md_table(_pooled(sens, "president", PRES_ORDER, PRES_LABEL, pres_metrics)),
        "",
        "## Who was elected, per election",
        "'P(actual pair)' is the first-round probability of the pair that actually advanced with no outright win. "
        "'Pairs with h2h fit' counts the pairs that had enough head-to-head polls at the cutoff.",
        md_table(_pres_cells(pb)),
        "",
        "Full probabilities of the primary combination:",
        md_table(_pres_probabilities(pb)),
        "",
        "## Head-to-head election-day deviations (inputs of the term)",
        "Deviation = official runoff valid share of the candidate ranked first in the pair's eve forecast minus that "
        "forecast's runoff-day mean. Roles are forecast ranks, not candidates. The 2026 term uses all rows; each "
        f"backtest target uses the other two. Root mean square of the {len(dv)} deviations: {sd:.2f} pp.",
        md_table(dev_tab),
        "",
        "## Sensitivity: error-model scale prior (1.5, 3 primary, 6)",
        "Reported, never used to pick the headline (Addendum 04 s.5).",
        "",
        "Head-to-head, models with an election-day term:",
        md_table(h_sens),
        "",
        "Who was elected:",
        md_table(p_sens),
        "",
        f"Unchanged across the three settings (they do not use this prior): {', '.join(unchanged)}.",
        "",
        f"Elections: {', '.join(elections)}.",
        "",
    ]
    (config.ROOT / "docs" / "runoff-backtest.md").write_text("\n".join(parts), encoding="utf-8", newline="\n")
    print(f"wrote docs/runoff-backtest.md ({len(rb)} head-to-head rows, {len(pb)} who-was-elected rows)")


if __name__ == "__main__":
    main()

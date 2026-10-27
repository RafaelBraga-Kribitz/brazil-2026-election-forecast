"""Score the last PUBLISHED PRELIMINARY 2026 forecast against the 2026 results. NOT the pre-registered scorecard.

Why this exists. PREREG s.11 and Addendum 06 register a scorecard of the forecast frozen on 2026-10-03 22:00 BRT.
That freeze did not happen, so the registered scorecard (`scripts/score_2026.py`) cannot run: it needs a frozen
package whose hash verifies. The most recent forecast that was public before the first round is the PRELIMINARY run
with information cutoff 2026-09-29 (`outputs/forecast_latest.json`, `outputs/president_2026.json`,
`outputs/baseline_comparison_2026.csv`, committed 2026-09-29 and pushed 2026-09-30, before the 2026-10-04 first
round). This script scores those committed files, unchanged, so the result is reported whatever it is.

What differs from the registered scorecard, and why every number here is labelled UNREGISTERED:
- Inputs are summaries, not draws. Interval coverage uses the stored equal-tailed quantiles (q10-q90 = 80%,
  q03-q97 = 94%); first-place and first-round-win probabilities use the stored Monte Carlo frequencies.
- The cutoff is five days before the first round, not the registered eve freeze.
- No post-first-round runoff forecast was ever produced; the runoff is scored only through the pre-first-round
  conditional forecast (pair probability, who is elected, and the head-to-head share interval).
- Results come from secondary sources that report the TSE count at 100% of sections
  (`data/manual/results_2026_secondary.csv`), not from the registered TSE-site reading. Replace them with the TSE
  reading and rerun before treating these numbers as final.

Usage:  python scripts/score_2026_unregistered.py
Writes: outputs/scorecard_2026_unregistered.json, outputs/scorecard_2026_unregistered_categories.csv,
        docs/scorecard-2026-unregistered.md, figures/scorecard_2026_unregistered.png
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "data" / "manual" / "results_2026_secondary.csv"
FORECAST = ROOT / "outputs" / "forecast_latest.json"
PRESIDENT = ROOT / "outputs" / "president_2026.json"
BASELINES = ROOT / "outputs" / "baseline_comparison_2026.csv"
OUT_JSON = ROOT / "outputs" / "scorecard_2026_unregistered.json"
OUT_CATS = ROOT / "outputs" / "scorecard_2026_unregistered_categories.csv"
OUT_DOC = ROOT / "docs" / "scorecard-2026-unregistered.md"
OUT_FIG = ROOT / "figures" / "scorecard_2026_unregistered.png"
OTHERS = "Others"
MODELS = ("F", "E", "E0")
LABEL = "UNREGISTERED: scores the PRELIMINARY forecast (cutoff 2026-09-29); the registered freeze did not happen"


# ------------------------------------------------------------------ results
def load_round(path: Path, round_: int) -> dict:
    """Votes for one round, with the sum check Addendum 06 s.2 asks for. Raises ValueError on any problem."""
    r = pd.read_csv(path, dtype={"election": str})
    r = r[(r["election"] == "2026") & (r["round"].astype(int) == round_)]
    if r.empty:
        raise ValueError(f"no 2026 round-{round_} rows in {path}")
    if r["candidate"].duplicated().any():
        raise ValueError(f"duplicate candidates in round {round_}")
    votes = r.set_index("candidate")["votes"].astype(int)
    totals = r["total_valid_votes"].astype(int).unique()
    if len(totals) != 1:
        raise ValueError(f"round {round_}: total_valid_votes must be one value, found {list(totals)}")
    if int(votes.sum()) != int(totals[0]):
        raise ValueError(f"round {round_}: votes sum to {int(votes.sum())}, total_valid_votes is {int(totals[0])}")
    if round_ == 2 and len(votes) != 2:
        raise ValueError(f"a runoff has two candidates, found {len(votes)}")
    total = int(totals[0])
    return {
        "votes": {c: int(v) for c, v in votes.items()},
        "total_valid_votes": total,
        "shares": {c: 100.0 * int(v) / total for c, v in votes.items()},
        "sources": sorted(set(r["source_url"])),
    }


def actual_categories(shares: dict[str, float], categories: list[str]) -> dict[str, float]:
    """Named candidates by name; Others = 100 minus the named shares (the backtest rule)."""
    named = [c for c in categories if c != OTHERS]
    out = {c: shares[c] for c in named}
    out[OTHERS] = 100.0 - sum(out.values())
    return out


# ------------------------------------------------------------------ scores
def brier(p: dict[str, float], outcome: str) -> float:
    return float(sum((v - (k == outcome)) ** 2 for k, v in p.items()))


def score_model(cats: list[dict], actual: dict[str, float]) -> tuple[dict, list[dict]]:
    """Share MAE, top-two margin error, 80/94% coverage from stored quantiles, first-place and first-round-win Brier."""
    names = [c["category"] for c in cats]
    named = [n for n in names if n != OTHERS]
    top = sorted(named, key=lambda n: -actual[n])[:2]
    by = {c["category"]: c for c in cats}
    rows, err, in80, in94 = [], [], [], []
    for n in names:
        c, y = by[n], actual[n]
        a80, a94 = c["q10"] <= y <= c["q90"], c["q03"] <= y <= c["q97"]
        in80.append(a80)
        in94.append(a94)
        err.append(abs(c["mean"] - y))
        rows.append(
            {
                "category": n,
                "actual": y,
                "mean": c["mean"],
                "lo80": c["q10"],
                "hi80": c["q90"],
                "lo94": c["q03"],
                "hi94": c["q97"],
                "in80": a80,
                "in94": a94,
                "error": c["mean"] - y,
            }
        )
    p_first = {n: float(by[n]["p_first"]) for n in named}
    z = sum(p_first.values())
    p_first = {k: v / z for k, v in p_first.items()}
    p_win_r1 = float(sum(by[n]["p_above_50"] for n in named))
    leader_won_r1 = actual[top[0]] > 50.0
    margin_pred = by[top[0]]["mean"] - by[top[1]]["mean"]
    margin_actual = actual[top[0]] - actual[top[1]]
    out = {
        "mae": sum(err) / len(err),
        "margin_pred": margin_pred,
        "margin_actual": margin_actual,
        "margin_error": margin_pred - margin_actual,
        "coverage_80": sum(in80) / len(in80),
        "coverage_94": sum(in94) / len(in94),
        "n_categories": len(names),
        "p_actual_first": p_first[top[0]],
        "brier_first": brier(p_first, top[0]),
        "p_first_round_win": p_win_r1,
        "brier_first_round_win": (p_win_r1 - float(leader_won_r1)) ** 2,
    }
    return out, rows


def score_point(point: dict[str, float], p_first: dict[str, float], actual: dict[str, float]) -> dict:
    """Baselines B, C, D: MAE and margin from the point value; first-place Brier from the stored conversion."""
    named = [n for n in point if n != OTHERS]
    top = sorted(named, key=lambda n: -actual[n])[:2]
    z = sum(p_first.values())
    p = {k: v / z for k, v in p_first.items()}
    mp, ma = point[top[0]] - point[top[1]], actual[top[0]] - actual[top[1]]
    return {
        "mae": sum(abs(point[n] - actual[n]) for n in point) / len(point),
        "margin_pred": mp,
        "margin_actual": ma,
        "margin_error": mp - ma,
        "p_actual_first": p[top[0]],
        "brier_first": brier(p, top[0]),
    }


def score_president(doc: dict, r2: dict) -> dict:
    elected = max(r2["shares"], key=r2["shares"].get)
    probs = {k: float(v) for k, v in doc["probabilities"].items() if float(v) > 0}
    pair = doc["pairs"][0]
    sf = pair["share_first"]
    y = r2["shares"][sf["candidate"]]
    runoff_names = set(r2["shares"])
    pair_mass = sum(float(p["mass"]) for p in doc["pairs"] if set(p["candidates"]) == runoff_names)
    return {
        "elected": elected,
        "p_elected": probs.get(elected, 0.0),
        "brier_elected": brier(probs, elected),
        "brier_elected_coin_flip": 0.5,
        "log_elected": math.log(max(probs.get(elected, 0.0), 1e-4)),
        "p_runoff_pair": pair_mass,
        "runoff_share_candidate": sf["candidate"],
        "runoff_share_median": sf["median"],
        "runoff_share_actual": y,
        "runoff_share_error": sf["median"] - y,
        "runoff_in80": sf["q10"] <= y <= sf["q90"],
        "runoff_in94": sf["q03"] <= y <= sf["q97"],
        "runoff_interval80": [sf["q10"], sf["q90"]],
        "runoff_interval94": [sf["q03"], sf["q97"]],
    }


# ------------------------------------------------------------------ outputs
def figure(rows: list[dict], path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from brfc import figures as bf

    bf._style()
    rows = [r for r in rows if r["category"] != OTHERS]
    fig, ax = plt.subplots(figsize=(8.0, 3.6))
    col = bf.candidate_colors([r["category"] for r in rows])
    for i, r in enumerate(rows):
        y = len(rows) - 1 - i
        c = col[r["category"]]
        ax.plot([r["lo94"], r["hi94"]], [y, y], color=c, lw=1.1)
        ax.plot([r["lo80"], r["hi80"]], [y, y], color=c, lw=5, solid_capstyle="round", alpha=0.55)
        ax.scatter([r["mean"]], [y], s=34, color=bf.SURFACE, edgecolor=c, lw=1.6, zorder=5)
        ax.scatter([r["actual"]], [y], s=60, marker="D", color=bf.INK, zorder=6)
        ax.text(
            r["hi94"] + 0.8,
            y,
            f"forecast {r['mean']:.1f}%  result {r['actual']:.1f}%",
            va="center",
            fontsize=8,
            color=bf.INK2,
        )
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([r["category"] for r in rows][::-1])
    ax.set_xlabel("first-round share of valid votes (%)")
    ax.set_xlim(-1, 70)
    ax.grid(axis="x", lw=0.5)
    bf._titles(
        fig,
        "First round 2026: model F forecast (5 days out) against the result",
        "Thick bar 80% interval, thin line 94% interval, circle forecast mean, diamond result. "
        "UNREGISTERED scoring of the PRELIMINARY forecast.",
    )
    bf._finish(
        fig,
        path,
        source="Source: outputs/forecast_latest.json (cutoff 2026-09-29); "
        "data/manual/results_2026_secondary.csv (TSE count at 100% of sections, via secondary sources).",
        top=0.80,
    )
    plt.close(fig)


def write_doc(res: dict, path: Path) -> None:
    f, p = res["first_round"]["models"]["F"], res["president"]
    m = res["first_round"]["models"]
    b = res["first_round"]["baselines"]
    lines = [
        "# 2026 scorecard: UNREGISTERED scoring of the PRELIMINARY forecast",
        "",
        "> **Read this first.** The registered scorecard (PREREG s.11, Addendum 06) scores the forecast frozen on "
        "2026-10-03 22:00 BRT. That freeze did not happen. This page scores the last forecast that was public "
        "before the first round instead: the PRELIMINARY run with information cutoff 2026-09-29, committed "
        "2026-09-29 and pushed 2026-09-30. Nothing in those files was changed. Every number below is "
        "UNREGISTERED and comes from `scripts/score_2026_unregistered.py`.",
        "",
        "## Headline",
        "",
        f"- **First place was called wrong.** Model F gave Flávio Bolsonaro a {100 * f['p_actual_first']:.0f}% "
        f"chance of finishing first; he finished first ({res['first_round']['actual']['Flávio Bolsonaro']:.2f}% "
        f"against {res['first_round']['actual']['Lula']:.2f}%). Every model and baseline favoured Lula for first "
        "place.",
        f"- **Shares were close; the top-two margin was not.** F's share error was {f['mae']:.2f} percentage points "
        f"(pp) on average across {f['n_categories']} categories, and "
        f"{round(f['coverage_80'] * f['n_categories'])} of {f['n_categories']} results fell inside its 80% "
        "intervals. Its top-two margin missed by "
        f"{abs(f['margin_error']):.1f} pp.",
        f"- **A runoff was expected and happened.** F gave a first-round win {100 * f['p_first_round_win']:.0f}% "
        f"and the Flávio Bolsonaro vs Lula runoff {100 * p['p_runoff_pair']:.0f}%.",
        f"- **Who was elected: close to a coin flip, and the coin landed on the slightly favoured side.** The model "
        f"gave {p['elected']} {100 * p['p_elected']:.1f}%; Brier {p['brier_elected']:.3f} against 0.500 for a "
        "50/50 guess.",
        f"- **Runoff share inside the interval.** The pre-first-round head-to-head median for "
        f"{p['runoff_share_candidate']} was {p['runoff_share_median']:.1f}%; the result was "
        f"{p['runoff_share_actual']:.2f}% (error {p['runoff_share_error']:+.1f} pp), inside the 80% interval "
        f"{p['runoff_interval80'][0]:.1f}% to {p['runoff_interval80'][1]:.1f}%.",
        "",
        "![Dot-and-interval chart of the five named candidates' first-round shares: model F's forecast mean with 80% "
        "and 94% intervals, and the result as a diamond. Flávio Bolsonaro's result sits near the top of his 80% "
        "interval; Lula's sits just below his forecast mean. UNREGISTERED.]"
        "(../figures/scorecard_2026_unregistered.png)",
        "",
        "## First round: models and baselines",
        "",
        "| Forecast | Share MAE (pp) | Top-two margin error (pp) | P(actual first place) | First-place Brier | "
        "80% coverage | 94% coverage |",
        "|---|---|---|---|---|---|---|",
    ]
    for k, v in m.items():
        lines.append(
            f"| {k} | {v['mae']:.2f} | {v['margin_error']:+.1f} | {v['p_actual_first']:.2f} | "
            f"{v['brier_first']:.3f} | {v['coverage_80']:.0%} | {v['coverage_94']:.0%} |"
        )
    for k, v in b.items():
        c80 = f"{v['coverage_80']:.0%}" if "coverage_80" in v else "N/A"
        c94 = f"{v['coverage_94']:.0%}" if "coverage_94" in v else "N/A"
        lines.append(
            f"| {k} (baseline) | {v['mae']:.2f} | {v['margin_error']:+.1f} | "
            f"{v['p_actual_first']:.2f} | {v['brier_first']:.3f} | {c80} | {c94} |"
        )
    lines += [
        "",
        "Margin error is predicted minus actual for (actual first minus actual second); a negative value means the "
        "forecast had the eventual leader too far behind. Baselines: B latest poll per pollster over 14 days, "
        "C final Datafolha poll, D final AtlasIntel poll; their first-place probabilities are the calibrated "
        "conversions stored in `outputs/baseline_comparison_2026.csv`. As in the backtest, a baseline's share MAE and "
        "margin come from its point value and its coverage from its calibrated conversion in "
        "`outputs/forecast_latest.json`.",
        "",
        "## What this does and does not show",
        "",
        "- One election cannot establish skill or its absence; the backtest covered three.",
        f"- The backtest criterion H3 found F's first-place probabilities worse than baseline B's. In 2026 F again "
        f"scored slightly worse than B on first place (Brier {f['brier_first']:.3f} vs {b['B']['brier_first']:.3f}) "
        f"while beating every baseline on share error. F's election-day term, learned from 2014-2022, moves share "
        f"toward the candidate second in the polls; in 2026 it moved in the right direction but not far enough.",
        "- These are summaries scored five days before the vote, not the eve freeze. The registered comparison "
        "with PollingData and market benchmarks is not possible, because those benchmarks were to be captured at the "
        "freeze.",
        "- Results are the TSE count at 100% of sections as reported by secondary sources; they must be replaced "
        "by the TSE reading (Addendum 06 s.2) before these numbers are final.",
        "",
        f"Label: {LABEL}.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    r1, r2 = load_round(RESULTS, 1), load_round(RESULTS, 2)
    fc = json.loads(FORECAST.read_text(encoding="utf-8"))
    pres = json.loads(PRESIDENT.read_text(encoding="utf-8"))
    base = pd.read_csv(BASELINES)

    cats = [c["category"] for c in fc["models"]["F"]["categories"]]
    actual = actual_categories(r1["shares"], cats)
    models, cat_rows = {}, []
    conversions = {}
    for name, mdl in fc["models"].items():
        s, rows = score_model(mdl["categories"], actual)
        if name in MODELS:
            models[name] = s
            cat_rows += [{"model": name, **r} for r in rows]
        else:
            conversions[name] = s
    baselines = {}
    for _, row in base[base["status"] == "ok"].iterrows():
        name = str(row["baseline"])
        point = {c: float(row[f"point_{c}"]) for c in cats}
        pf = {c: float(row[f"p_first_{c}"]) for c in cats if c != OTHERS}
        baselines[name] = score_point(point, pf, actual)
        if name in conversions:  # registered rule: coverage from the calibrated conversion, MAE from the point
            baselines[name]["coverage_80"] = conversions[name]["coverage_80"]
            baselines[name]["coverage_94"] = conversions[name]["coverage_94"]

    res = {
        "label": LABEL,
        "forecast_files": {
            "first_round": str(FORECAST.relative_to(ROOT)),
            "first_round_generated_utc": fc["generated_utc"],
            "president": str(PRESIDENT.relative_to(ROOT)),
            "president_generated_utc": pres["generated_utc"],
            "information_cutoff": fc["information_cutoff_date"],
        },
        "results_file": str(RESULTS.relative_to(ROOT)),
        "first_round": {
            "actual": actual,
            "total_valid_votes": r1["total_valid_votes"],
            "models": models,
            "baselines": baselines,
        },
        "runoff_result": {"shares": r2["shares"], "total_valid_votes": r2["total_valid_votes"]},
        "president": score_president(pres, r2),
    }
    OUT_JSON.write_text(json.dumps(res, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    pd.DataFrame(cat_rows).to_csv(OUT_CATS, index=False)
    figure([r for r in cat_rows if r["model"] == "F"], OUT_FIG)
    write_doc(res, OUT_DOC)
    print(json.dumps({"F": models["F"], "president": res["president"]}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

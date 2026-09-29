"""Static figures for the README. Every figure is rebuilt from files in outputs/ (no model code imported).

Colour rules: candidate colours are assigned in alphabetical order of candidate name from a validated
categorical palette (never party colours); "Others" is neutral grey; model colours are fixed by model ID.
"""

from __future__ import annotations

import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from brfc import config

SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3de"
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
GREY = "#8c8b86"
MODEL_COLORS = {"F": "#2a78d6", "E": "#eb6834", "E0": "#1baf7a", "B": "#52514e", "C": "#4a3aa7", "D": "#e87ba4"}
MODEL_SHORT = {
    "F": "F: RW + election-day term",
    "E": "E: RW, zero-mean day error",
    "E0": "E0: RW latent only",
    "B": "B: 14-day latest per pollster",
    "C": "C: final Datafolha",
    "D": "D: final AtlasIntel",
}
SOURCE = "Source: Wikipedia poll tables (pinned revisions); official results via TSE-citing secondary sources."


def _style() -> None:
    plt.rcParams.update(
        {
            "figure.facecolor": SURFACE,
            "axes.facecolor": SURFACE,
            "savefig.facecolor": SURFACE,
            "axes.edgecolor": GRID,
            "axes.labelcolor": INK2,
            "xtick.color": INK2,
            "ytick.color": INK2,
            "text.color": INK,
            "axes.grid": True,
            "grid.color": GRID,
            "grid.linewidth": 0.8,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "font.size": 10,
            "axes.titlesize": 12,
            "axes.titleweight": "bold",
            "axes.titlelocation": "left",
            "legend.frameon": False,
            "lines.linewidth": 1.8,
        }
    )


def candidate_colors(names: list[str]) -> dict[str, str]:
    named = sorted([n for n in names if n != config.OTHERS_LABEL])
    out = {n: PALETTE[i % len(PALETTE)] for i, n in enumerate(named)}
    out[config.OTHERS_LABEL] = GREY
    return out


def _finish(fig, path, source: str = SOURCE) -> None:
    fig.text(0.01, 0.01, source, fontsize=7.5, color=INK2, ha="left", va="bottom")
    fig.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(fig)


def posterior_forecast(out_dir=config.FIGURES) -> None:
    """2026: poll dots, posterior latent path (median, 80% and 94% bands), election-day forecast intervals (F)."""
    _style()
    doc = json.loads((config.OUTPUTS / "forecast_latest.json").read_text(encoding="utf-8"))
    path = pd.read_csv(config.OUTPUTS / "posterior_path_2026.csv", parse_dates=["date"])
    polls = pd.read_csv(config.OUTPUTS / "poll_snapshot.csv")
    cats = [c["category"] for c in doc["models"]["F"]["categories"]]
    col = candidate_colors(cats)
    cutoff = pd.Timestamp(doc["information_cutoff_date"])
    eday = pd.Timestamp(config.ELECTION_DATES[("2026", 1)])
    fig, ax = plt.subplots(figsize=(10, 5.6))
    from brfc.transform import series_table

    wide = series_table(polls, [c for c in cats if c != config.OTHERS_LABEL])
    wide["mid"] = pd.to_datetime(wide["field_mid"])
    for c in cats:
        p = path[path["series"] == c]
        obs = p[p["date"] <= cutoff]
        ax.fill_between(obs["date"], obs["q03"], obs["q97"], color=col[c], alpha=0.12, lw=0)
        ax.fill_between(obs["date"], obs["q10"], obs["q90"], color=col[c], alpha=0.25, lw=0)
        ax.plot(obs["date"], obs["median"], color=col[c], lw=1.8)
        fut = p[p["date"] >= cutoff]
        ax.plot(fut["date"], fut["median"], color=col[c], lw=1.4, ls=(0, (3, 2)))
        ax.scatter(wide["mid"], wide[c], s=12, color=col[c], alpha=0.35, lw=0)
    f = {r["category"]: r for r in doc["models"]["F"]["categories"]}
    for k, c in enumerate(cats):
        x = eday + pd.Timedelta(hours=10 * (k - len(cats) / 2))
        ax.plot([x, x], [f[c]["q03"], f[c]["q97"]], color=col[c], lw=1.2)
        ax.plot([x, x], [f[c]["q10"], f[c]["q90"]], color=col[c], lw=4, solid_capstyle="round")
        ax.scatter([x], [f[c]["median"]], s=36, color=SURFACE, edgecolor=col[c], zorder=5, lw=1.6)
        ax.annotate(
            f"{c}  {f[c]['median']:.1f}%",
            (eday + pd.Timedelta(days=1.2), f[c]["median"]),
            va="center",
            fontsize=9,
            color=INK,
            annotation_clip=False,
        )
    ax.axvline(cutoff, color=INK2, lw=0.8, ls=":")
    ax.text(cutoff, ax.get_ylim()[1], " information cutoff", fontsize=8, color=INK2, va="top")
    ax.set_xlim(path["date"].min(), eday + pd.Timedelta(days=1))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    ax.set_ylabel("Valid-vote share (%)")
    ax.set_title(f"2026 first round: poll aggregation and election-day forecast ({doc['status']})")
    ax.text(
        0,
        1.01,
        f"Lines: posterior median; bands: 80% and 94% intervals; dots: polls (valid-vote basis). "
        f"Election-day markers: model F, 80% (thick) and 94% (thin). Cutoff {doc['information_cutoff_date']}, "
        f"{doc['n_polls']} polls.",
        transform=ax.transAxes,
        fontsize=8,
        color=INK2,
        va="bottom",
    )
    _finish(fig, out_dir / "posterior_forecast.png")


def uncertainty_intervals(out_dir=config.FIGURES) -> None:
    """2026: election-day forecast per category for every model; dot = median (point for point-only)."""
    _style()
    doc = json.loads((config.OUTPUTS / "forecast_latest.json").read_text(encoding="utf-8"))
    cats = [c["category"] for c in doc["models"]["F"]["categories"]]
    models = [m for m in ("F", "E", "E0", "B", "C", "D") if m in doc["models"]]
    fig, ax = plt.subplots(figsize=(10, 0.9 + 0.62 * len(cats) * len(models) / 3))
    ys, labels = [], []
    for i, c in enumerate(cats):
        for j, m in enumerate(models):
            y = i * (len(models) + 1) + j
            r = next(x for x in doc["models"][m]["categories"] if x["category"] == c)
            ax.plot([r["q03"], r["q97"]], [y, y], color=MODEL_COLORS[m], lw=1.1)
            ax.plot([r["q10"], r["q90"]], [y, y], color=MODEL_COLORS[m], lw=4, solid_capstyle="round")
            ax.scatter([r["median"]], [y], s=30, color=SURFACE, edgecolor=MODEL_COLORS[m], lw=1.5, zorder=5)
            ys.append(y)
            labels.append(f"{c} · {m}" if j == 0 else m)
    ax.set_yticks(ys, labels, fontsize=8)
    ax.invert_yaxis()
    ax.set_xlabel("Valid-vote share on election day (%)")
    ax.set_title(f"2026 first-round forecast by model ({doc['status']}, cutoff {doc['information_cutoff_date']})")
    handles = [plt.Line2D([], [], color=MODEL_COLORS[m], lw=4, label=MODEL_SHORT[m]) for m in models]
    ax.legend(handles=handles, loc="lower right", fontsize=8)
    ax.text(
        0,
        1.01,
        "80% (thick) and 94% (thin) equal-tailed intervals. B/C/D intervals are a calibrated "
        "probabilistic conversion of point baselines (not published by them).",
        transform=ax.transAxes,
        fontsize=8,
        color=INK2,
        va="bottom",
    )
    _finish(fig, out_dir / "uncertainty_intervals.png")


def historical_backtest(out_dir=config.FIGURES) -> None:
    """Mean share MAE by horizon, first round and runoff, production RW variant; LOEO throughout."""
    _style()
    s = pd.read_csv(config.OUTPUTS / "backtest_summary.csv")
    prod = json.loads((config.OUTPUTS / "regime_selection.json").read_text())["production_variant"]
    s = s[s["variant"] == prod]
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.2), sharey=True)
    for ax, rt in zip(axes, ("first round", "runoff"), strict=True):
        d = s[s["round_type"] == rt]
        for m in ("F", "E", "B", "C", "D"):
            g = d[d["model"] == m].sort_values("horizon", ascending=False)
            if g.empty:
                continue
            ax.plot(g["horizon"], g["mae"], marker="o", ms=5, color=MODEL_COLORS[m], label=MODEL_SHORT[m])
            last = g.iloc[-1]
            ax.annotate(
                f"{m}",
                (last["horizon"], last["mae"]),
                xytext=(4, 0),
                textcoords="offset points",
                fontsize=8,
                va="center",
                color=INK,
            )
        ax.set_xscale("symlog", linthresh=2)
        ax.set_xticks([30, 14, 7, 1], ["T-30", "T-14", "T-7", "eve"])
        ax.invert_xaxis()
        n = d.groupby("horizon")["n_rounds"].max().to_dict()
        ax.set_title(f"{rt.capitalize()}")
        ax.set_xlabel(
            "Forecast horizon (rounds scored: "
            + ", ".join(f"{k}d={v}" for k, v in sorted(n.items(), reverse=True))
            + ")",
            fontsize=8,
        )
    axes[0].set_ylabel("Mean absolute share error (pp)")
    axes[1].legend(fontsize=8, loc="upper right")
    fig.suptitle("Historical retrodiction 2014-2022, leave-one-election-out", x=0.01, ha="left", fontweight="bold")
    _finish(fig, out_dir / "historical_backtest.png")


def calibration(out_dir=config.FIGURES) -> None:
    """Pooled interval coverage vs nominal, with Wilson 95% intervals for the small number of categories."""
    _style()
    c = pd.read_csv(config.OUTPUTS / "historical_backtest_categories.csv")
    prod = json.loads((config.OUTPUTS / "regime_selection.json").read_text())["production_variant"]
    c = c[(c["variant"] == prod) & c["in80"].notna()]
    models = [m for m in ("F", "E", "E0", "B", "C", "D") if m in set(c["model"])]
    fig, ax = plt.subplots(figsize=(9, 3.8))
    for lvl, off in ((80, -0.15), (94, 0.15)):
        for i, m in enumerate(models):
            x = c.loc[c["model"] == m, f"in{lvl}"].astype(bool)
            n, k = len(x), int(x.sum())
            p = k / n
            z = 1.96
            den = 1 + z**2 / n
            mid = (p + z**2 / (2 * n)) / den
            half = z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / den
            ax.plot([i + off, i + off], [100 * (mid - half), 100 * (mid + half)], color=MODEL_COLORS[m], lw=1.2)
            ax.scatter(
                [i + off],
                [100 * p],
                s=40,
                color=MODEL_COLORS[m] if lvl == 94 else SURFACE,
                edgecolor=MODEL_COLORS[m],
                lw=1.5,
                zorder=5,
            )
            ax.annotate(
                f"{100 * p:.0f}%",
                (i + off, 100 * p),
                xytext=(5, 0),
                textcoords="offset points",
                fontsize=7.5,
                va="center",
            )
        ax.axhline(lvl, color=INK2, lw=0.8, ls=(0, (4, 3)))
        ax.text(len(models) - 0.5, lvl, f" nominal {lvl}%", fontsize=8, color=INK2, va="center")
    ax.set_xticks(range(len(models)), [MODEL_SHORT[m] for m in models], fontsize=8, rotation=12)
    ax.set_ylabel("Share of outcomes inside interval (%)")
    ax.set_ylim(0, 105)
    n_all = int((c["model"] == "F").sum())
    ax.set_title("Interval coverage in the historical retrodiction (open = 80%, filled = 94%)")
    ax.text(
        0,
        1.01,
        f"Pooled over rounds, horizons and candidates (n = {n_all} per model; outcomes within an election are "
        "correlated, so the effective sample is smaller). Whiskers: Wilson 95%.",
        transform=ax.transAxes,
        fontsize=8,
        color=INK2,
        va="bottom",
    )
    _finish(fig, out_dir / "calibration.png")


def house_effects(out_dir=config.FIGURES) -> None:
    """2026: estimated house effects (relative deviation from model consensus) for the top two candidates."""
    _style()
    doc = json.loads((config.OUTPUTS / "forecast_latest.json").read_text(encoding="utf-8"))
    tag = f"{doc['status'].lower()}_pt{doc['poll_source']['pt_oldid']}"
    key = f"2026_r1_h{doc['horizon_days']:02d}_{doc['rw_variant']}_{tag}"
    h = pd.read_csv(config.DATA / "cache" / "fits" / f"{key}.house.csv")
    f = sorted(doc["models"]["F"]["categories"], key=lambda r: -r["mean"])
    top = [r["category"] for r in f if r["category"] != config.OTHERS_LABEL][:2]
    col = candidate_colors([r["category"] for r in f])
    order = h[h["series"] == top[0]].sort_values("mean")["pollster"].tolist()
    fig, ax = plt.subplots(figsize=(9, 0.6 + 0.34 * len(order)))
    for j, c in enumerate(top):
        d = h[h["series"] == c].set_index("pollster").loc[order]
        y = np.arange(len(order)) + (j - 0.5) * 0.3
        ax.hlines(y, d["q03"], d["q97"], color=col[c], lw=1.4)
        ax.scatter(d["mean"], y, s=30, color=col[c], zorder=5, label=c)
    ax.axvline(0, color=INK2, lw=0.8)
    ax.set_yticks(
        range(len(order)), [f"{p} (n={int(h[(h.pollster == p)]['n_polls'].iloc[0])})" for p in order], fontsize=8
    )
    ax.set_xlabel("Estimated house effect: deviation from the model consensus (pp, valid votes)")
    ax.set_title("2026 estimated house effects, relative to the all-pollster consensus")
    ax.text(
        0,
        1.01,
        "Mean and 94% posterior interval. Effects sum to zero across pollsters by construction: they measure "
        "relative deviation, not accuracy.",
        transform=ax.transAxes,
        fontsize=8,
        color=INK2,
        va="bottom",
    )
    ax.legend(fontsize=8, loc="lower right")
    _finish(fig, out_dir / "house_effects.png")


def election_day_deviation(out_dir=config.FIGURES) -> None:
    """Eve deviations (observed - eve forecast) by forecast-rank role, first round vs runoff."""
    _style()
    d = pd.read_csv(config.OUTPUTS / "election_day_deviations.csv", dtype={"election": str})
    prod = json.loads((config.OUTPUTS / "regime_selection.json").read_text())["production_variant"]
    d = d[d["variant"] == prod]
    marks = {"2014": "o", "2018": "s", "2022": "D"}
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.6), sharex=True, gridspec_kw={"width_ratios": [3, 1.3]})
    for ax, r in zip(axes, (1, 2), strict=True):
        g = d[d["round"] == r]
        roles = ["rank1", "rank2", "rest"] if r == 1 else ["rank1"]
        for i, role in enumerate(roles):
            for e, mk in marks.items():
                x = g[(g["role"] == role) & (g["election"] == e)]
                ax.scatter(
                    x["deviation"],
                    np.full(len(x), i) + {"2014": -0.15, "2018": 0, "2022": 0.15}[e],
                    marker=mk,
                    s=40,
                    color=PALETTE[list(marks).index(e)],
                    label=e if i == 0 else None,
                    edgecolor=SURFACE,
                    lw=0.8,
                    zorder=5,
                )
        ax.axvline(0, color=INK2, lw=0.8)
        role_label = {"rank1": "forecast #1", "rank2": "forecast #2", "rest": "all others"}
        ax.set_yticks(range(len(roles)), [role_label[x] for x in roles])
        ax.invert_yaxis()
        ax.set_title("First round" if r == 1 else "Runoff")
        ax.set_xlabel("Observed minus eve forecast (pp)")
    axes[0].legend(fontsize=8, loc="lower right", title="election", title_fontsize=8)
    fig.suptitle(
        "Election-day deviation of the eve forecast, by forecast rank (not by candidate)",
        x=0.01,
        ha="left",
        fontweight="bold",
    )
    _finish(fig, out_dir / "election_day_deviation.png")


def all_historical() -> None:
    config.FIGURES.mkdir(exist_ok=True)
    historical_backtest()
    calibration()
    election_day_deviation()


def all_2026() -> None:
    config.FIGURES.mkdir(exist_ok=True)
    posterior_forecast()
    uncertainty_intervals()
    house_effects()

"""Static figures for the README. Every figure is rebuilt from files in outputs/ (no model code imported).

Colour rules: candidate colours are assigned in alphabetical order of candidate name from a validated
categorical palette (never party colours); "Others" is neutral grey; model colours are fixed by model ID.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

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


def _finish(fig, path, source: str = SOURCE, top: float = 0.90) -> None:
    fig.tight_layout(rect=(0, 0.06, 1, top))
    fig.text(0.01, 0.005, source, fontsize=7.5, color=INK2, ha="left", va="bottom", wrap=True)
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
    """Mean share MAE by horizon, production RW variant, LOEO. A model is drawn at a horizon only if it was scored on
    the same rounds as F there (a baseline available in fewer rounds would not be comparable)."""
    _style()
    s = pd.read_csv(config.OUTPUTS / "historical_backtest.csv", dtype={"election": str})
    s = s[s["mae"].notna()]
    s["round_type"] = s["round"].map({1: "First round", 2: "Runoff"})
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.4), sharey=True, gridspec_kw={"width_ratios": [4, 3]})
    for ax, rt in zip(axes, ("First round", "Runoff"), strict=True):
        d = s[s["round_type"] == rt]
        ref = d[d["model"] == "F"].groupby("horizon")["election"].apply(frozenset)
        hs = sorted(ref.index, reverse=True)
        for m in ("F", "E", "B", "C", "D"):
            pts = []
            for h in hs:
                g = d[(d["model"] == m) & (d["horizon"] == h)]
                if frozenset(g["election"]) == ref[h]:
                    pts.append((hs.index(h), g["mae"].mean()))
            if not pts:
                continue
            ax.plot(
                [x for x, _ in pts], [v for _, v in pts], marker="o", ms=5, color=MODEL_COLORS[m], label=MODEL_SHORT[m]
            )
            ax.annotate(m, pts[-1], xytext=(5, 0), textcoords="offset points", fontsize=8, va="center")
        labels = [("eve" if h == 1 else f"T-{h}") + f"\n{len(ref[h])} rounds" for h in hs]
        ax.set_xticks(range(len(hs)), labels)
        ax.set_xlim(-0.3, len(hs) - 0.6)
        ax.set_title(rt)
    axes[0].set_ylabel("Mean absolute share error (pp)")
    axes[1].legend(fontsize=8, loc="upper right")
    fig.suptitle(
        "Historical retrodiction 2014-2022, leave-one-election-out: share error by horizon",
        x=0.01,
        ha="left",
        fontweight="bold",
        fontsize=12,
    )
    _finish(
        fig,
        out_dir / "historical_backtest.png",
        SOURCE + " A model is drawn only where it was scored on the same rounds as F.",
    )


def _wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float, float]:
    p = k / n
    den = 1 + z**2 / n
    mid = (p + z**2 / (2 * n)) / den
    half = z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / den
    return p, mid - half, mid + half


def calibration(out_dir=config.FIGURES) -> None:
    """Interval coverage vs nominal: eve horizon (pre-registered summary) and all horizons pooled."""
    _style()
    c = pd.read_csv(config.OUTPUTS / "historical_backtest_categories.csv")
    prod = json.loads((config.OUTPUTS / "regime_selection.json").read_text())["production_variant"]
    c = c[(c["variant"] == prod) & c["in80"].notna()]
    models = [m for m in ("F", "E", "E0", "B", "C") if m in set(c["model"])]
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.0), sharey=True)
    panels = (("Eve horizon (pre-registered summary)", c[c["horizon"] == 1]), ("All horizons pooled", c))
    for ax, (title, sub) in zip(axes, panels, strict=True):
        for lvl, off in ((80, -0.17), (94, 0.17)):
            for i, m in enumerate(models):
                x = sub.loc[sub["model"] == m, f"in{lvl}"].astype(bool)
                p, lo, hi = _wilson(int(x.sum()), len(x))
                ax.plot([i + off, i + off], [100 * lo, 100 * hi], color=MODEL_COLORS[m], lw=1.2)
                ax.scatter(
                    [i + off],
                    [100 * p],
                    s=36,
                    color=MODEL_COLORS[m] if lvl == 94 else SURFACE,
                    edgecolor=MODEL_COLORS[m],
                    lw=1.5,
                    zorder=5,
                )
                ax.annotate(
                    f"{100 * p:.0f}",
                    (i + off, 100 * p),
                    xytext=(4, 0),
                    textcoords="offset points",
                    fontsize=7,
                    va="center",
                )
            ax.axhline(lvl, color=INK2, lw=0.8, ls=(0, (4, 3)))
        ax.set_xticks(range(len(models)), models)
        ax.set_title(f"{title}, n = {int((sub['model'] == 'F').sum())}", fontsize=10)
        ax.set_ylim(0, 105)
    axes[0].set_ylabel("Observed values inside the interval (%)")
    fig.suptitle(
        "Interval coverage vs nominal 80% (open) and 94% (filled); whiskers: Wilson 95%",
        x=0.01,
        ha="left",
        fontweight="bold",
        fontsize=12,
    )
    _finish(
        fig,
        out_dir / "calibration.png",
        "F: RW + election-day term; E: zero-mean day error; E0: latent only; B: 14-day average; C: final "
        "Datafolha (B, C: calibrated conversion). n counts candidate shares, correlated within an election.",
    )


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
    """Every 2026 figure; the president figure only once outputs/president_2026.json has been written."""
    config.FIGURES.mkdir(exist_ok=True)
    posterior_forecast()
    uncertainty_intervals()
    house_effects()
    if (config.OUTPUTS / "president_2026.json").exists():
        president_probability()


PRESIDENT_CAPTION = "probability of being elected under this model; pre-registered; validated on {n} {noun} only"


def n_validated_elections(label) -> int:
    """Number of validation elections named by the president JSON "label".

    Accepted forms: an integer, "<N> elections" anywhere in the text, or the historical election years
    (e.g. "2014+2018+2022"), which are counted."""
    if isinstance(label, int) and not isinstance(label, bool):
        return label
    text = str(label)
    m = re.search(r"(\d+)\s*elections?\b", text)
    if m:
        return int(m.group(1))
    years = [y for y in config.HISTORICAL if re.search(rf"(?<!\d){y}(?!\d)", text)]
    if years:
        return len(years)
    if text.strip().isdigit():
        return int(text.strip())
    raise ValueError(f"cannot read the number of validation elections from label {label!r}")


def _pair_candidates(p: dict) -> list[str]:
    return list(p.get("candidates") or p["pair"].split(" vs "))


def president_probability(out_dir=config.FIGURES, json_path=None) -> Path:
    """2026: probability of being elected under this model, by path (outright or via each modelled runoff pair),
    and the runoff valid-share interval of each modelled pair. Reads outputs/president_2026.json."""
    from matplotlib.patches import Patch

    _style()
    src = Path(json_path) if json_path is not None else config.OUTPUTS / "president_2026.json"
    doc = json.loads(src.read_text(encoding="utf-8"))
    n_val = n_validated_elections(doc["label"])
    prob = {c: float(v) for c, v in doc["probabilities"].items()}
    outright = {c: float(v) for c, v in (doc.get("outright") or {}).items()}
    unmodelled = float(doc["unmodelled"])
    col = candidate_colors(list(dict.fromkeys([*prob, *(c for p in doc["pairs"] for c in _pair_candidates(p))])))
    modelled = sorted(
        [p for p in doc["pairs"] if p.get("modelled") and p.get("win")], key=lambda p: (-p["mass"], p["pair"])
    )
    cands = sorted([c for c, v in prob.items() if v > 0], key=lambda c: (-prob[c], c))

    segments: dict[str, list[tuple[float, str | None]]] = {}
    for c in cands:
        seg = [(outright.get(c, 0.0), None)]
        for p in modelled:
            if c in p["win"]:
                opp = next(x for x in _pair_candidates(p) if x != c)
                seg.append((float(p["mass"]) * float(p["win"][c]), opp))
        if abs(sum(v for v, _ in seg) - prob[c]) > 0.005:
            raise ValueError(f"path contributions for {c} do not add up to its probability")
        segments[c] = seg

    ranged = [p for p in modelled if p.get("share_first")]
    rows = len(cands) + 1
    height_in = 2.6 + 0.42 * max(rows, len(ranged), 3)
    top = 1 - 0.68 / height_in  # room for the title and a two-line subtitle
    bottom = 0.6 / height_in  # room for the path legend and the source line
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(11, height_in), gridspec_kw={"width_ratios": [3, 2]})

    ax.set_xlim(0, 100 * min(1.0, max([*prob.values(), unmodelled]) * 1.15 + 0.03))
    bar_h, inline = 0.62, []
    for y, c in enumerate(cands):
        left = 0.0
        for v, opp in segments[c]:
            if v <= 0:
                continue
            ax.barh(
                y,
                100 * v,
                left=100 * left,
                height=bar_h,
                color=col[c],
                alpha=1.0 if opp is None else 0.42,
                edgecolor=SURFACE,
                linewidth=2,
            )
            if opp is not None:
                inline.append((100 * left, 100 * (left + v), y, f"vs {opp}"))
            left += v
        ax.annotate(
            f"{100 * prob[c]:.1f}%" if prob[c] >= 0.0005 else "<0.1%",
            (100 * left, y),
            xytext=(4, 0),
            textcoords="offset points",
            va="center",
            fontsize=8.5,
            color=INK,
        )
    yu = len(cands)
    ax.barh(yu, 100 * unmodelled, height=bar_h, color=GREY, edgecolor=SURFACE, linewidth=2)
    ax.annotate(
        f"{100 * unmodelled:.1f}%",
        (100 * unmodelled, yu),
        xytext=(4, 0),
        textcoords="offset points",
        va="center",
        fontsize=8.5,
        color=INK,
    )
    ax.set_yticks(range(rows), [*cands, "unmodelled pairs"], fontsize=9)
    ax.set_ylim(rows - 0.5, -0.5)
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("Probability of being elected under this model (%)")
    ax.set_title("By path: outright first-round win or via a runoff pair", fontsize=10)
    fig.legend(
        handles=[
            Patch(color=INK2, label="outright first-round win (above 50% of valid votes)"),
            Patch(color=INK2, alpha=0.42, label="via a modelled runoff pair (label: opponent)"),
            Patch(color=GREY, label="pairs without a head-to-head fit (not reallocated)"),
        ],
        fontsize=7.5,
        loc="lower left",
        bbox_to_anchor=(0.005, 0.2 / height_in),
        ncol=3,
    )

    if ranged:
        lo, hi = 50.0, 50.0
        for y, p in enumerate(ranged):
            s = p["share_first"]
            c = col.get(s["candidate"], INK2)
            ax2.plot([s["q03"], s["q97"]], [y, y], color=c, lw=1.2)
            ax2.plot([s["q10"], s["q90"]], [y, y], color=c, lw=5, solid_capstyle="round")
            ax2.scatter([s["median"]], [y], s=40, color=SURFACE, edgecolor=c, lw=1.6, zorder=5)
            ax2.annotate(
                f"P(pair) {100 * float(p['mass']):.1f}%",
                (1.02, y),
                xycoords=ax2.get_yaxis_transform(),
                va="center",
                ha="left",
                fontsize=8,
                color=INK2,
                annotation_clip=False,
            )
            lo, hi = min(lo, s["q03"]), max(hi, s["q97"])
        ax2.axvline(50, color=INK2, lw=0.9, ls=(0, (4, 3)))
        ax2.set_xlim(lo - 3, hi + 3)
        ax2.set_yticks(range(len(ranged)), [p["pair"] for p in ranged], fontsize=8.5)
        ax2.set_ylim(max(len(ranged), 3) - 0.5, -0.5)
        ax2.grid(axis="y", visible=False)
        ax2.set_xlabel("Runoff valid-vote share of the first-listed candidate (%)")
    else:
        ax2.set_axis_off()
        ax2.text(0.5, 0.5, "no modelled runoff pair", transform=ax2.transAxes, ha="center", color=INK2)
    ax2.set_title("Runoff intervals of modelled pairs", fontsize=10)

    fig.suptitle(
        f"2026 president: probability of being elected under this model ({doc['status']}, cutoff {doc['cutoff']})",
        x=0.01,
        y=1 - 0.12 / height_in,
        va="top",
        ha="left",
        fontweight="bold",
        fontsize=12,
    )
    caption = PRESIDENT_CAPTION.format(n=n_val, noun="election" if n_val == 1 else "elections")
    fig.text(
        0.01,
        top + 0.02 / height_in,
        caption[0].upper() + caption[1:] + ".\nRight: 80% (thick) and 94% (thin) equal-tailed intervals, dot = "
        "median, dashed line = 50%; P(pair) = probability of that runoff pair with no outright first-round win.",
        fontsize=8,
        color=INK2,
        ha="left",
        va="bottom",
        linespacing=1.5,
    )
    # inline opponent labels, kept only where they fit inside their segment at the final layout
    fig.tight_layout(rect=(0, bottom, 1, top))
    renderer = fig.canvas.get_renderer()
    for x0, x1, y, text in inline:
        t = ax.text((x0 + x1) / 2, y, text, ha="center", va="center", fontsize=7.5, color=INK)
        px0, px1 = ax.transData.transform([(x0, y), (x1, y)])[:, 0]
        if t.get_window_extent(renderer).width > 0.9 * (px1 - px0):
            t.remove()
    out = Path(out_dir) / "president_probability.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    source = SOURCE + " First-round and head-to-head forecasts are treated as independent."
    fig.text(0.01, 0.005, source, fontsize=7.5, color=INK2, ha="left", va="bottom", wrap=True)
    fig.savefig(out, dpi=160, bbox_inches="tight")
    plt.close(fig)
    return out

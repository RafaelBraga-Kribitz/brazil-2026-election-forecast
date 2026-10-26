"""Static figures for the README. Every figure is rebuilt from files in outputs/ (no model code imported).

Styling is the Braga design system v2.1 through bk-viz (`bk_theme`); this module holds no colour value of its own.
Colour rules: candidate colours are the design system's categorical slots, assigned in alphabetical order of
candidate name (never party colours; a sixth named candidate takes the overflow grey); "Others" is the overflow grey;
model colours are the categorical slots in the fixed model order F, E, E0, B, C, D (D takes the overflow grey).
Every label is ink or ink-2; reference lines are `mid` hairlines. Each figure carries a provenance footer.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import bk_theme as bk
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from brfc import config

MODEL_ORDER = ("F", "E", "E0", "B", "C", "D")
MODEL_SHORT = {
    "F": "F: RW + election-day term",
    "E": "E: RW, zero-mean day error",
    "E0": "E0: RW latent only",
    "B": "B: 14-day latest per pollster",
    "C": "C: final Datafolha",
    "D": "D: final AtlasIntel",
}
SOURCE = "Source: Wikipedia poll tables (pinned revisions); official results via TSE-citing secondary sources."
TAG = "CALIBRATED"  # epistemic tag in the provenance footer: model output scored against verified results

WIDTH = bk.figsize(12)[0]  # the design system's page content width (12 columns), in inches
SCALE = WIDTH / 10  # the figures were laid out 10 in wide; heights keep that aspect
MARGIN = 0.01  # left and right margin of the title block and the footer (figure fraction)
SM = bk.SIZE["mono-sm"]  # the 12px floor: dense tick labels, legends and in-plot annotations


def _style(grid: str = "y") -> None:
    """The design system as rcParams; `grid` names the axis whose hairline grid carries the reading."""
    bk.apply("light", grid=grid)
    plt.rcParams.update({"savefig.bbox": "tight", "savefig.pad_inches": 0.12, "legend.fontsize": SM})


def _px(v: float) -> float:
    return v / bk.PX_PER_IN


def _panel_title(ax, text: str) -> None:
    ax.set_title(text, loc="left", color=bk.c("ink"), fontproperties=bk.font("display", "bold", "body"))


def _ref_line(ax, value, *, orient: str = "v", ls="-", lw: float = 0.8) -> None:
    """A reference line (zero, nominal level, cutoff): a `mid` hairline, never a hue."""
    line = ax.axvline if orient == "v" else ax.axhline
    line(value, color=bk.c("mid"), lw=lw, ls=ls, zorder=1)


def model_colors() -> dict[str, str]:
    return dict(zip(MODEL_ORDER, bk.palette("categorical", len(MODEL_ORDER)), strict=True))


def candidate_colors(names: list[str]) -> dict[str, str]:
    named = sorted([n for n in names if n != config.OTHERS_LABEL])
    slots = bk.palette("categorical", len(named))
    out = {n: slots[i] for i, n in enumerate(named)}
    out[config.OTHERS_LABEL] = bk.c("cat-other")
    return out


def _lines(text: str) -> int:
    return text.count("\n") + 1 if text else 0


def _finish(
    fig,
    path,
    *,
    title: str,
    subtitle: str = "",
    source: str,
    note: str,
    run_id: str | None = None,
    legend: dict | None = None,
    after_layout: Callable | None = None,
) -> Path:
    """Title block, plot, provenance footer. The figure grows by the height of the title block and the footer, so
    the plot keeps the height it was given; nothing is placed by guesswork in figure fractions.

    `legend` (optional): keyword arguments of a figure legend set as one band under the subtitle (needs `handles`
    and `ncol`). `after_layout(fig)` runs once the final layout is fixed (e.g. to drop labels that do not fit)."""
    w_in, body_in = fig.get_size_inches()
    span_px = w_in * bk.PX_PER_IN * (1 - 2 * MARGIN)
    title_fp = bk.font("display", "bold", "project")
    sub_fp = bk.font("display", "regular", "body")
    title_w = bk.wrap_to_px(title, title_fp, span_px)
    sub_w = bk.wrap_to_px(subtitle, sub_fp, span_px) if subtitle else ""
    note_w = bk.wrap_to_px(note, sub_fp, span_px)
    title_in = _lines(title_w) * bk.SIZE["project"] / 72 * 1.2
    sub_in = _lines(sub_w) * bk.SIZE["body"] / 72 * 1.15
    legend_rows = -(-len(legend["handles"]) // legend["ncol"]) if legend else 0
    legend_in = legend_rows * SM / 72 * 1.7
    header_in = _px(12) + title_in + (_px(8) + sub_in if sub_w else 0) + (_px(12) + legend_in if legend else 0)
    header_in += _px(4)
    # bk.footer: source strip (mono) on top, the note under it, an ink rule 0.028 of the height above the strip
    strip_top_in = _px(8) + _lines(note_w) * bk.SIZE["body"] / 72 * 1.15 + bk.SIZE["mono"] / 72 + _px(8)
    h_in = (body_in + header_in + strip_top_in + _px(20)) / (1 - 0.028)
    fig.set_size_inches(w_in, h_in)

    y = 1 - _px(12) / h_in
    fig.text(MARGIN, y, title_w, ha="left", va="top", color=bk.c("ink"), linespacing=1.2, fontproperties=title_fp)
    y -= title_in / h_in
    if sub_w:
        y -= _px(8) / h_in
        fig.text(MARGIN, y, sub_w, ha="left", va="top", color=bk.c("ink-2"), linespacing=1.15, fontproperties=sub_fp)
        y -= sub_in / h_in
    if legend:
        y -= _px(12) / h_in
        fig.legend(loc="upper left", bbox_to_anchor=(MARGIN, y), borderaxespad=0, **legend)
    bk.footer(fig, source=source, run_id=run_id, tag=TAG, note=note, left=MARGIN, right=MARGIN, y=strip_top_in / h_in)
    bottom = strip_top_in / h_in + 0.028 + _px(20) / h_in
    fig.tight_layout(rect=(0, bottom, 1, 1 - header_in / h_in))
    if after_layout is not None:
        after_layout(fig)
    out = Path(path)
    bk.save(fig, out.with_suffix(""), formats=(out.suffix.lstrip("."),))
    return out


def _spread(values: list[float], min_gap: float) -> list[float]:
    """Nudge label positions apart (keeps order) so that direct labels never overlap."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    pos = [values[i] for i in order]
    for k in range(1, len(pos)):
        pos[k] = max(pos[k], pos[k - 1] + min_gap)
    out = [0.0] * len(values)
    for k, i in enumerate(order):
        out[i] = pos[k]
    return out


def posterior_forecast(out_dir=config.FIGURES) -> None:
    """2026: poll dots, posterior latent path (median, 80% and 94% bands), election-day forecast intervals (F)."""
    _style("y")
    doc = json.loads((config.OUTPUTS / "forecast_latest.json").read_text(encoding="utf-8"))
    path = pd.read_csv(config.OUTPUTS / "posterior_path_2026.csv", parse_dates=["date"])
    polls = pd.read_csv(config.OUTPUTS / "poll_snapshot.csv")
    cats = [c["category"] for c in doc["models"]["F"]["categories"]]
    col = candidate_colors(cats)
    surface = bk.c("surface")
    cutoff = pd.Timestamp(doc["information_cutoff_date"])
    eday = pd.Timestamp(config.ELECTION_DATES[("2026", 1)])
    fig, ax = plt.subplots(figsize=(WIDTH, 4.8 * SCALE))
    from brfc.transform import series_table

    wide = series_table(polls, [c for c in cats if c != config.OTHERS_LABEL])
    wide["mid"] = pd.to_datetime(wide["field_mid"])
    for c in cats:
        p = path[path["series"] == c]
        obs = p[p["date"] <= cutoff]
        # bands overlap between candidates, so they stay translucent: an opaque band would hide its neighbour
        ax.fill_between(obs["date"], obs["q03"], obs["q97"], color=col[c], alpha=0.12, lw=0)
        ax.fill_between(obs["date"], obs["q10"], obs["q90"], color=col[c], alpha=0.25, lw=0)
        ax.plot(obs["date"], obs["median"], color=col[c], lw=1.8)
        fut = p[p["date"] >= cutoff]
        ax.plot(fut["date"], fut["median"], color=col[c], lw=1.4, ls=(0, (3, 2)))
        ax.scatter(wide["mid"], wide[c], s=12, color=col[c], alpha=0.35, lw=0)
    f = {r["category"]: r for r in doc["models"]["F"]["categories"]}
    for k, c in enumerate(cats):
        x = eday + pd.Timedelta(hours=10 * (k + 1))
        ax.plot([x, x], [f[c]["q03"], f[c]["q97"]], color=col[c], lw=1.2)
        ax.plot([x, x], [f[c]["q10"], f[c]["q90"]], color=col[c], lw=4)
        ax.scatter([x], [f[c]["median"]], s=36, color=surface, edgecolor=col[c], zorder=5, lw=1.6)
    ys = _spread([f[c]["median"] for c in cats], min_gap=2.6)
    lx = eday + pd.Timedelta(hours=10 * (len(cats) + 1) + 14)
    for c, y in zip(cats, ys, strict=True):
        bk.direct_label(ax, lx, y, f"{c}  {f[c]['median']:.1f}%", dx=0)
    _ref_line(ax, cutoff, ls=":")
    _ref_line(ax, eday)
    top = max(r["q97"] for r in f.values()) + 4
    ink2 = bk.c("ink-2")
    ax.text(cutoff, top, "information cutoff ", fontsize=SM, color=ink2, va="top", ha="right")
    ax.text(eday, top, " election day", fontsize=SM, color=ink2, va="top", ha="left")
    ax.set_ylim(-1, top + 1)
    ax.set_xlim(path["date"].min(), eday + pd.Timedelta(hours=10 * (len(cats) + 1) + 6))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    ax.set_ylabel("Valid-vote share (%)")
    _finish(
        fig,
        out_dir / "posterior_forecast.png",
        title=f"2026 first round: poll aggregation and election-day forecast ({doc['status']})",
        subtitle=f"Lines: posterior median of the polling consensus; bands: 80% and 94% intervals; dots: polls "
        f"(valid-vote basis). Election-day markers: model F, 80% (thick) and 94% (thin). Cutoff "
        f"{doc['information_cutoff_date']}, {doc['n_polls']} polls. Not a poll; not a recommendation.",
        source="outputs/forecast_latest.json, posterior_path_2026.csv, poll_snapshot.csv",
        run_id=f"{doc['status']} {doc['information_cutoff_date']}",
        note=SOURCE,
    )


def uncertainty_intervals(out_dir=config.FIGURES) -> None:
    """2026: election-day forecast per category for every model; dot = median (point for point-only)."""
    _style("x")
    doc = json.loads((config.OUTPUTS / "forecast_latest.json").read_text(encoding="utf-8"))
    cats = [c["category"] for c in doc["models"]["F"]["categories"]]
    models = [m for m in MODEL_ORDER if m in doc["models"]]
    mcol, surface = model_colors(), bk.c("surface")
    fig, ax = plt.subplots(figsize=(WIDTH, (0.9 + 0.62 * len(cats) * len(models) / 3) * SCALE))
    ys, labels = [], []
    for i, c in enumerate(cats):
        for j, m in enumerate(models):
            y = i * (len(models) + 1) + j
            r = next(x for x in doc["models"][m]["categories"] if x["category"] == c)
            ax.plot([r["q03"], r["q97"]], [y, y], color=mcol[m], lw=1.1)
            ax.plot([r["q10"], r["q90"]], [y, y], color=mcol[m], lw=4)
            ax.scatter([r["median"]], [y], s=30, color=surface, edgecolor=mcol[m], lw=1.5, zorder=5)
            ys.append(y)
            labels.append(f"{c} · {m}" if j == 0 else m)
    ax.set_yticks(ys, labels, fontsize=SM)
    ax.invert_yaxis()
    ax.set_xlabel("Valid-vote share on election day (%)")
    handles = [plt.Line2D([], [], color=mcol[m], lw=4, label=MODEL_SHORT[m]) for m in models]
    ax.legend(handles=handles, loc="lower right")
    _finish(
        fig,
        out_dir / "uncertainty_intervals.png",
        title=f"2026 first-round forecast by model ({doc['status']}, cutoff {doc['information_cutoff_date']})",
        subtitle="80% (thick) and 94% (thin) equal-tailed intervals; dot = median. B/C/D intervals are a calibrated "
        "probabilistic conversion of point baselines (not published by them).",
        source="outputs/forecast_latest.json",
        run_id=f"{doc['status']} {doc['information_cutoff_date']}",
        note=SOURCE,
    )


def historical_backtest(out_dir=config.FIGURES) -> None:
    """Mean share MAE by horizon, production RW variant, LOEO. A model is drawn at a horizon only if it was scored on
    the same rounds as F there (a baseline available in fewer rounds would not be comparable)."""
    _style("y")
    mcol = model_colors()
    s = pd.read_csv(config.OUTPUTS / "historical_backtest.csv", dtype={"election": str})
    s = s[s["mae"].notna()]
    s["round_type"] = s["round"].map({1: "First round", 2: "Runoff"})
    fig, axes = plt.subplots(1, 2, figsize=(WIDTH, 3.7 * SCALE), sharey=True, gridspec_kw={"width_ratios": [4, 3]})
    for ax, rt in zip(axes, ("First round", "Runoff"), strict=True):
        d = s[s["round_type"] == rt]
        ref = d[d["model"] == "F"].groupby("horizon")["election"].apply(frozenset)
        hs = sorted(ref.index, reverse=True)
        ends = []
        for m in ("F", "E", "B", "C", "D"):
            pts = []
            for h in hs:
                g = d[(d["model"] == m) & (d["horizon"] == h)]
                if frozenset(g["election"]) == ref[h]:
                    pts.append((hs.index(h), g["mae"].mean()))
            if not pts:
                continue
            ax.plot([x for x, _ in pts], [v for _, v in pts], marker="o", ms=5, color=mcol[m], label=MODEL_SHORT[m])
            ends.append((m, *pts[-1]))
        for (m, x, _), y in zip(ends, _spread([v for _, _, v in ends], min_gap=0.3), strict=True):
            bk.direct_label(ax, x, y, m, dx=5, size=SM)  # end labels nudged apart so they never overlap
        labels = [("eve" if h == 1 else f"T-{h}") + f"\n{len(ref[h])} rounds" for h in hs]
        ax.set_xticks(range(len(hs)), labels)
        ax.set_xlim(-0.3, len(hs) - 0.6)
        _panel_title(ax, rt)
    axes[0].set_ylabel("Mean absolute share error (pp)")
    axes[1].legend(loc="upper right")
    _finish(
        fig,
        out_dir / "historical_backtest.png",
        title="Historical retrodiction 2014-2022, leave-one-election-out: share error by horizon",
        source="outputs/historical_backtest.csv",
        note=SOURCE + " A model is drawn only where it was scored on the same rounds as F.",
    )


def _wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float, float]:
    p = k / n
    den = 1 + z**2 / n
    mid = (p + z**2 / (2 * n)) / den
    half = z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / den
    return p, mid - half, mid + half


def calibration(out_dir=config.FIGURES) -> None:
    """Interval coverage vs nominal: eve horizon (pre-registered summary) and all horizons pooled."""
    _style("y")
    mcol, surface = model_colors(), bk.c("surface")
    c = pd.read_csv(config.OUTPUTS / "historical_backtest_categories.csv")
    prod = json.loads((config.OUTPUTS / "regime_selection.json").read_text())["production_variant"]
    c = c[(c["variant"] == prod) & c["in80"].notna()]
    models = [m for m in ("F", "E", "E0", "B", "C") if m in set(c["model"])]
    fig, axes = plt.subplots(1, 2, figsize=(WIDTH, 3.4 * SCALE), sharey=True)
    panels = (("Eve horizon (pre-registered summary)", c[c["horizon"] == 1]), ("All horizons pooled", c))
    for ax, (title, sub) in zip(axes, panels, strict=True):
        for lvl, off in ((80, -0.17), (94, 0.17)):
            for i, m in enumerate(models):
                x = sub.loc[sub["model"] == m, f"in{lvl}"].astype(bool)
                p, lo, hi = _wilson(int(x.sum()), len(x))
                ax.plot([i + off, i + off], [100 * lo, 100 * hi], color=mcol[m], lw=1.2)
                ax.scatter(
                    [i + off],
                    [100 * p],
                    s=36,
                    color=mcol[m] if lvl == 94 else surface,
                    edgecolor=mcol[m],
                    lw=1.5,
                    zorder=5,
                )
                bk.direct_label(ax, i + off, 100 * p, f"{100 * p:.0f}", dx=4, size=SM, weight="regular")
            _ref_line(ax, lvl, orient="h", ls=(0, (4, 3)))
        ax.set_xticks(range(len(models)), models)
        _panel_title(ax, f"{title}, n = {int((sub['model'] == 'F').sum())}")
        ax.set_ylim(0, 105)
    axes[0].set_ylabel("Observed values inside the interval (%)")
    _finish(
        fig,
        out_dir / "calibration.png",
        title="Interval coverage vs nominal 80% (open) and 94% (filled); whiskers: Wilson 95%",
        source="outputs/historical_backtest_categories.csv, regime_selection.json",
        run_id=prod,
        note="F: RW + election-day term; E: zero-mean day error; E0: latent only; B: 14-day average; C: final "
        "Datafolha (B, C: calibrated conversion). n counts candidate shares, correlated within an election.",
    )


def house_effects(out_dir=config.FIGURES) -> None:
    """2026: estimated house effects (relative deviation from model consensus) for the top two candidates."""
    _style("x")
    doc = json.loads((config.OUTPUTS / "forecast_latest.json").read_text(encoding="utf-8"))
    key = doc.get("fit_key") or (  # older documents: the tag format before forecast.forecast_tag
        f"2026_r1_h{doc['horizon_days']:02d}_{doc['rw_variant']}_{doc['status'].lower()}_pt{doc['poll_source']['pt_oldid']}"
    )
    h = pd.read_csv(config.DATA / "cache" / "fits" / f"{key}.house.csv")
    f = sorted(doc["models"]["F"]["categories"], key=lambda r: -r["mean"])
    top = [r["category"] for r in f if r["category"] != config.OTHERS_LABEL][:2]
    col = candidate_colors([r["category"] for r in f])
    order = h[h["series"] == top[0]].sort_values("mean")["pollster"].tolist()
    fig, ax = plt.subplots(figsize=(WIDTH, (0.6 + 0.34 * len(order)) * SCALE))
    for j, c in enumerate(top):
        d = h[h["series"] == c].set_index("pollster").loc[order]
        y = np.arange(len(order)) + (j - 0.5) * 0.3
        ax.hlines(y, d["q03"], d["q97"], color=col[c], lw=1.4)
        ax.scatter(d["mean"], y, s=30, color=col[c], zorder=5, label=c)
    _ref_line(ax, 0)
    ax.set_yticks(
        range(len(order)), [f"{p} (n={int(h[(h.pollster == p)]['n_polls'].iloc[0])})" for p in order], fontsize=SM
    )
    ax.set_xlabel("Estimated house effect: deviation from the model consensus (pp, valid votes)")
    ax.legend(loc="upper left", bbox_to_anchor=(1.0, 1.0))
    _finish(
        fig,
        out_dir / "house_effects.png",
        title="2026 estimated house effects, relative to the all-pollster consensus",
        subtitle="Mean and 94% posterior interval. Effects sum to zero across pollsters by construction: they "
        "measure relative deviation from the other pollsters, not accuracy.",
        source=f"data/cache/fits/{key}.house.csv",
        run_id=f"{doc['status']} {doc['information_cutoff_date']}",
        note=SOURCE,
    )


def election_day_deviation(out_dir=config.FIGURES) -> None:
    """Eve deviations (observed - eve forecast) by forecast-rank role, first round vs runoff."""
    _style("x")
    surface = bk.c("surface")
    d = pd.read_csv(config.OUTPUTS / "election_day_deviations.csv", dtype={"election": str})
    prod = json.loads((config.OUTPUTS / "regime_selection.json").read_text())["production_variant"]
    d = d[d["variant"] == prod]
    marks = {"2014": "o", "2018": "s", "2022": "D"}
    ecol = dict(zip(marks, bk.palette("categorical", len(marks)), strict=True))
    fig, axes = plt.subplots(1, 2, figsize=(WIDTH, 3.0 * SCALE), sharex=True, gridspec_kw={"width_ratios": [3, 1.3]})
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
                    color=ecol[e],
                    label=e if i == 0 else None,
                    edgecolor=surface,
                    lw=0.8,
                    zorder=5,
                )
        _ref_line(ax, 0)
        role_label = {"rank1": "forecast #1", "rank2": "forecast #2", "rest": "all others"}
        ax.set_yticks(range(len(roles)), [role_label[x] for x in roles])
        ax.invert_yaxis()
        _panel_title(ax, "First round" if r == 1 else "Runoff")
        ax.set_xlabel("Observed minus eve forecast (pp)")
    axes[0].legend(loc="lower right", title="election", title_fontsize=SM)
    _finish(
        fig,
        out_dir / "election_day_deviation.png",
        title="Election-day deviation of the eve forecast, by forecast rank (not by candidate)",
        source="outputs/election_day_deviations.csv, regime_selection.json",
        run_id=prod,
        note=SOURCE,
    )


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

    _style("x")
    surface, ink, ink2, other = bk.c("surface"), bk.c("ink"), bk.c("ink-2"), bk.c("cat-other")
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
    body_in = (1.6 + 0.42 * max(rows, len(ranged), 3)) * SCALE
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(WIDTH, body_in), gridspec_kw={"width_ratios": [3, 2]})

    ax.set_xlim(0, 100 * min(1.0, max([*prob.values(), unmodelled]) * 1.15 + 0.03))
    bar_h, inline = 0.62, []
    for y, c in enumerate(cands):
        left = 0.0
        for v, opp in segments[c]:
            if v <= 0:
                continue
            if opp is None:  # outright first-round win: solid
                ax.barh(y, 100 * v, left=100 * left, height=bar_h, color=col[c], edgecolor=surface, linewidth=2)
            else:  # via a runoff pair: the same hue as an outline (conditional, like an IBCS plan), no transparency
                ax.barh(y, 100 * v, left=100 * left, height=bar_h, facecolor=surface, edgecolor=col[c], linewidth=1.5)
                inline.append((100 * left, 100 * (left + v), y, f"vs {opp}"))
            left += v
        bk.direct_label(ax, 100 * left, y, f"{100 * prob[c]:.1f}%" if prob[c] >= 0.0005 else "<0.1%", dx=4, size=SM)
    yu = len(cands)
    ax.barh(yu, 100 * unmodelled, height=bar_h, color=other, edgecolor=surface, linewidth=2)
    bk.direct_label(ax, 100 * unmodelled, yu, f"{100 * unmodelled:.1f}%", dx=4, size=SM)
    ax.set_yticks(range(rows), [*cands, "unmodelled pairs"])
    ax.set_ylim(rows - 0.5, -0.5)
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("Probability of being elected under this model (%)")
    _panel_title(ax, "By path: outright first-round win or via a runoff pair")

    if ranged:
        lo, hi = 50.0, 50.0
        for y, p in enumerate(ranged):
            s = p["share_first"]
            c = col.get(s["candidate"], ink2)
            ax2.plot([s["q03"], s["q97"]], [y, y], color=c, lw=1.2)
            ax2.plot([s["q10"], s["q90"]], [y, y], color=c, lw=5)
            ax2.scatter([s["median"]], [y], s=40, color=surface, edgecolor=c, lw=1.6, zorder=5)
            ax2.annotate(
                f"P(pair) {100 * float(p['mass']):.1f}%",
                (1.02, y),
                xycoords=ax2.get_yaxis_transform(),
                va="center",
                ha="left",
                fontsize=SM,
                color=ink2,
                annotation_clip=False,
            )
            lo, hi = min(lo, s["q03"]), max(hi, s["q97"])
        _ref_line(ax2, 50, ls=(0, (4, 3)), lw=0.9)
        ax2.set_xlim(lo - 3, hi + 3)
        ax2.set_yticks(range(len(ranged)), [p["pair"] for p in ranged], fontsize=SM)
        ax2.set_ylim(max(len(ranged), 3) - 0.5, -0.5)
        ax2.grid(axis="y", visible=False)
        ax2.set_xlabel("Runoff valid-vote share of the first-listed candidate (%)")
    else:
        ax2.set_axis_off()
        ax2.text(0.5, 0.5, "no modelled runoff pair", transform=ax2.transAxes, ha="center", color=ink2)
    _panel_title(ax2, "Runoff intervals of modelled pairs")

    def inline_labels(fig) -> None:
        """Inline opponent labels, kept only where they fit inside their segment at the final layout."""
        renderer = fig.canvas.get_renderer()
        for x0, x1, y, text in inline:
            t = ax.text((x0 + x1) / 2, y, text, ha="center", va="center", fontsize=SM, color=ink)
            px0, px1 = ax.transData.transform([(x0, y), (x1, y)])[:, 0]
            if t.get_window_extent(renderer).width > 0.9 * (px1 - px0):
                t.remove()

    caption = PRESIDENT_CAPTION.format(n=n_val, noun="election" if n_val == 1 else "elections")
    return _finish(
        fig,
        Path(out_dir) / "president_probability.png",
        title=f"2026 president: probability of being elected under this model ({doc['status']}, cutoff "
        f"{doc['cutoff']})",
        subtitle=caption[0].upper() + caption[1:] + ". Right: 80% (thick) and 94% (thin) equal-tailed intervals, "
        "dot = median, dashed line = 50%; P(pair) = probability of that runoff pair with no outright first-round win.",
        legend={
            "handles": [
                Patch(facecolor=ink2, label="outright first-round win (above 50% of valid votes)"),
                Patch(
                    facecolor=surface,
                    edgecolor=ink2,
                    linewidth=1.5,
                    label="via a modelled runoff pair (label: opponent)",
                ),
                Patch(facecolor=other, label="pairs without a head-to-head fit (not reallocated)"),
            ],
            "ncol": 2,
        },
        source=src.name if json_path is not None else "outputs/president_2026.json",
        run_id=f"{doc['status']} {doc['cutoff']}",
        note=SOURCE + " First-round and head-to-head forecasts are treated as independent.",
        after_layout=inline_labels,
    )

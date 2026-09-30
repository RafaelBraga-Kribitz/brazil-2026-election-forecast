"""Score the frozen 2026 forecasts against the TSE count (PREREG_ADDENDUM_06).

Usage:
  python scripts/score_2026.py --stage r1          first round: outputs/freeze/ vs round-1 rows of results_2026.csv
  python scripts/score_2026.py --stage president   who was elected: outputs/freeze/president.json
  python scripts/score_2026.py --stage runoff      runoff: outputs/freeze_runoff/ vs round-2 rows

The result file is data/manual/results_2026.csv (columns of results_secondary.csv), typed from the TSE count once
100% of sections are totalled. Nothing is scored unless the package's forecast_hash.txt verifies. The elected
candidate is read from the result file: the round-1 leader if above 50% of valid votes, otherwise the round-2
winner. Writes outputs/scorecard_2026_<stage>.json and .csv (and _categories.csv for share forecasts), then
regenerates docs/scorecard-2026.md from every scorecard present. The frozen files are never modified.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from brfc import config, freeze
from brfc import scorecard as sc
from brfc.provenance import utc_now_iso

LABEL = "scorecard of the frozen forecast (PREREG s.11, Addendum 06); published whatever the result"
BENCH_NOTE = "market prices and displayed probabilities are benchmarks, not ground truth and not model forecasts"
DOC = config.ROOT / "docs" / "scorecard-2026.md"


def _read_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def _model_version(fz: Path) -> str:
    p = fz / "MODEL_VERSION.txt"
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""


def _check(fz: Path) -> None:
    problems = freeze.verify_package(fz)
    if problems:
        raise SystemExit(f"{fz}: the frozen package does not verify; nothing scored:\n  " + "\n  ".join(problems))


def _jsonable(x):
    if isinstance(x, dict):
        return {k: _jsonable(v) for k, v in x.items()}
    if isinstance(x, list | tuple):
        return [_jsonable(v) for v in x]
    if isinstance(x, np.bool_ | bool):
        return bool(x)
    if isinstance(x, np.integer):
        return int(x)
    if isinstance(x, np.floating | float):
        return None if not np.isfinite(x) else float(x)
    return x


def _records(df: pd.DataFrame) -> list[dict]:
    return [
        _jsonable({k: v for k, v in r.items() if not (isinstance(v, float) and np.isnan(v))})
        for r in df.to_dict("records")
    ]


def _source(result: dict) -> dict:
    keys = ("total_valid_votes", "blank_votes", "null_votes", "source_url", "retrieved_utc")
    return {k: result[k] for k in keys}


def elected_candidate(results_path: Path) -> tuple[str, str]:
    r1 = sc.valid_shares(sc.load_results_2026(results_path, 1))
    leader = max(r1, key=r1.get)
    if r1[leader] > 50.0:
        return leader, "elected in the first round (more than 50% of valid votes)"
    r2 = sc.load_results_2026(results_path, 2)["votes"]
    a, b = sorted(r2, key=lambda c: -r2[c])
    if r2[a] == r2[b]:
        raise SystemExit("runoff tie in the entered result; resolve it from the TSE record before scoring")
    return a, "elected in the runoff"


def score_r1(fz: Path, results_path: Path) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    _check(fz)
    cats, draws = freeze.load_draws(fz / freeze.DRAWS_FILE)
    doc = _read_json(fz / "forecast.json")
    snap = pd.read_csv(fz / "baseline_snapshot.csv")
    result = sc.load_results_2026(results_path, 1)
    shares = sc.valid_shares(result)
    actual = sc.actual_categories(shares, cats)
    first = max(shares, key=shares.get)  # among all ballot candidates
    outright = max(shares.values()) > 50.0
    scores, cat_rows = sc.score_share_models(
        draws,
        sc.baseline_points(snap, cats),
        cats,
        actual,
        1,
        baselines=("B", "C", "D"),
        first_place=first,
        outright=outright,
    )
    a_point, a_note = sc.pollingdata_point(snap, cats)
    a = sc.score_pollingdata(a_point, draws["F"], cats, actual) | {"note": a_note}
    scores = pd.concat([scores, pd.DataFrame([a])], ignore_index=True)
    named = [c for c in cats if c != config.OTHERS_LABEL]
    first_bucket = first if first in named else sc.OTHER
    bench = [
        sc.score_categorical(
            "Polymarket", "first_place", sc.market_probabilities(snap, "first_place", named), first_bucket
        ),
        sc.score_binary(
            "Polymarket", "first_round_outright_win", sc.market_binary(snap, "first_round_outright_win"), outright
        ),
    ]
    pd_out = sc.displayed_binary(snap, "first_round_outright_win")
    bench.append(sc.score_binary("PollingData", "first_round_outright_win", pd_out, outright))
    for cand, p in sc.displayed_first_place(snap).items():  # one displayed candidate: the yes/no event "X first"
        bench.append(sc.score_binary("PollingData", f"first_place: {cand}", p, first == cand) | {"candidate": cand})
    bench.append(sc.score_binary("PollingData", "runoff_held", sc.displayed_binary(snap, "runoff_held"), not outright))
    f = scores.set_index("model").loc["F"]
    p_f_out = sc.event_probability(draws["F"], cats, "leader_above_50")
    for b in bench:  # model F on the same event, next to each benchmark
        if b["event"] == "first_place":
            b |= {"F_p_actual": f["p_actual_first"], "F_brier": f["brier_first"]}
        elif b["event"] in ("first_round_outright_win", "runoff_held"):
            p = p_f_out if b["event"] == "first_round_outright_win" else 1.0 - p_f_out
            b |= {"F_p_yes": p, "F_brier": (p - float(b["outcome"])) ** 2}
        elif b["event"].startswith("first_place: "):
            p = sc.event_probability(draws["F"], cats, "first_place", b["candidate"])
            b |= {"F_p_yes": p, "F_brier": (p - float(b["outcome"])) ** 2}
    crit = sc.criteria_r1(scores, a)
    crit["L3"] = sc.coverage_counts(cat_rows, "F")
    out = {
        "stage": "r1",
        "label": LABEL,
        "scored_utc": utc_now_iso(),
        "package": fz.as_posix(),
        "package_status": doc["status"],
        "information_cutoff_date": doc["information_cutoff_date"],
        "model_version": _model_version(fz),
        "result": _source(result),
        "observed": {"categories": actual, "first_place": first, "leader_above_50": outright},
        "criteria": crit,
        "models": _records(scores),
        "benchmarks": bench,
        "benchmark_note": BENCH_NOTE,
    }
    if doc["status"] != "FINAL":
        out["warning"] = "the package is not a FINAL freeze; this is a rehearsal, not the registered scorecard"
    return _jsonable(out), scores, cat_rows


def score_president_stage(fz: Path, results_path: Path) -> tuple[dict, pd.DataFrame]:
    _check(fz)
    if not (fz / "president.json").exists():
        raise SystemExit(
            f"{fz} holds no president.json: the conditional forecast was not frozen with the first round "
            "(Addendum 04 s.7 fallback), so there is no frozen who-is-elected forecast to score"
        )
    doc = _read_json(fz / "president.json")
    elected, how = elected_candidate(results_path)
    table = sc.score_president_doc(doc, elected)
    snap = pd.read_csv(fz / "baseline_snapshot.csv")
    named = list(doc["probabilities"])
    bucket = elected if elected in named else sc.OTHER
    bench = [
        sc.score_categorical(
            "Polymarket", "election_winner", sc.market_probabilities(snap, "election_winner", named), bucket
        ),
        sc.score_categorical(
            "PollingData", "election_winner", sc.displayed_probabilities(snap, "election_winner", named), bucket
        ),
    ]
    primary = table[table["primary"]].iloc[0]
    for b in bench:  # the primary combination on the same event, next to each benchmark
        b |= {"F_p_actual": float(primary["p_winner"]), "F_brier": float(primary["brier"])}
    out = {
        "stage": "president",
        "label": LABEL,
        "quantity": "probability of being elected under this model",
        "scored_utc": utc_now_iso(),
        "package": fz.as_posix(),
        "package_status": doc["status"],
        "information_cutoff_date": doc["information_cutoff_date"],
        "model_version": _model_version(fz),
        "elected": elected,
        "how": how,
        "combinations": _records(table),
        "benchmarks": bench,
        "benchmark_note": BENCH_NOTE,
    }
    if doc["status"] != "FINAL":
        out["warning"] = "the package is not a FINAL freeze; this is a rehearsal, not the registered scorecard"
    return _jsonable(out), table


def score_runoff_stage(fz: Path, results_path: Path) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    _check(fz)
    cats, draws = freeze.load_draws(fz / freeze.DRAWS_FILE)
    doc = _read_json(fz / "runoff.json")
    result = sc.load_results_2026(results_path, 2)
    if sorted(result["votes"]) != sorted(cats):
        raise SystemExit(f"runoff result candidates {sorted(result['votes'])} differ from the frozen pair {cats}")
    shares = sc.valid_shares(result)
    actual = sc.actual_categories(shares, cats)
    snap = pd.read_csv(fz / "baseline_snapshot.csv")
    scores, cat_rows = sc.score_share_models(
        draws, sc.baseline_points(snap, cats), cats, actual, 2, baselines=("B", "C")
    )
    winner = max(shares, key=shares.get)
    bench = [
        sc.score_categorical(
            "Polymarket", "election_winner", sc.market_probabilities(snap, "election_winner", cats), winner
        ),
        sc.score_categorical(
            "PollingData", "election_winner", sc.displayed_probabilities(snap, "election_winner", cats), winner
        ),
    ]
    out = {
        "stage": "runoff",
        "label": LABEL,
        "scored_utc": utc_now_iso(),
        "package": fz.as_posix(),
        "package_status": doc["status"],
        "information_cutoff_date": doc["information_cutoff_date"],
        "model_version": _model_version(fz),
        "result": _source(result),
        "observed": {"categories": actual, "winner": winner},
        "coverage": sc.coverage_counts(cat_rows, doc.get("primary_model", "F")),
        "models": _records(scores),
        "benchmarks": bench,
        "benchmark_note": BENCH_NOTE,
    }
    if doc["status"] != "FINAL":
        out["warning"] = "the package is not a FINAL freeze; this is a rehearsal, not the registered scorecard"
    return _jsonable(out), scores, cat_rows


# ---------------------------------------------------------------- markdown report
def _fmt(x, nd=2) -> str:
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "N/A"
    if isinstance(x, bool):
        return "met" if x else "not met"
    return f"{x:.{nd}f}" if isinstance(x, float) else str(x)


def _table(rows: list[dict], cols: list[tuple[str, str]]) -> list[str]:
    out = ["| " + " | ".join(h for _, h in cols) + " |", "| " + " | ".join("---" for _ in cols) + " |"]
    out += ["| " + " | ".join(_fmt(r.get(k)) for k, _ in cols) + " |" for r in rows]
    return out


def render(out_dir: Path) -> str:
    lines = [
        "# 2026 scorecard",
        "",
        "Generated by `scripts/score_2026.py` from the frozen packages and the TSE count "
        "([`PREREG_ADDENDUM_06.md`](../PREREG_ADDENDUM_06.md)). Published whatever the result; the frozen files are "
        "never rewritten. Probabilities are probabilities under this model; market prices are benchmarks, not ground "
        "truth. One election is weak evidence about long-run skill.",
    ]
    model_cols = [
        ("model", "Model"),
        ("mae", "Share MAE (pp)"),
        ("margin_error", "Margin error (pp)"),
        ("coverage_80", "80% coverage"),
        ("coverage_94", "94% coverage"),
    ]
    for stage, title in (("r1", "First round"), ("president", "Who was elected"), ("runoff", "Runoff")):
        p = out_dir / f"scorecard_2026_{stage}.json"
        if not p.exists():
            continue
        d = _read_json(p)
        lines += [
            "",
            f"## {title}",
            "",
            f"Package `{d['package']}` ({d['package_status']}, cutoff "
            f"{d['information_cutoff_date']}); scored {d['scored_utc']}.",
        ]
        if "warning" in d:
            lines += ["", f"**{d['warning']}**"]
        if stage == "r1":
            c = d["criteria"]
            lines += [
                "",
                f"- L1 (share MAE: F {_fmt(c['L1']['F_mae'])} vs B {_fmt(c['L1']['B_mae'])}"
                + (
                    f"; on PollingData's categories ({', '.join(c['L1']['A_categories'])}): F "
                    f"{_fmt(c['L1']['F_mae_subset'])} vs A {_fmt(c['L1']['A_mae_subset'])}"
                    if "A_mae_subset" in c["L1"]
                    else "; A: N/A"
                )
                + f"; assessed against {c['L1']['assessed_against']}): **{_fmt(c['L1']['met'])}**",
                f"- L2 (absolute top-two margin error: F {_fmt(c['L2']['F_margin_abs_error'])} vs B "
                f"{_fmt(c['L2']['B_margin_abs_error'])}): **{_fmt(c['L2']['met'])}**",
                f"- L3: {c['L3']['inside_94']} of {c['L3']['n_categories']} categories inside F's 94% interval, "
                f"{c['L3']['inside_80']} inside its 80% interval",
                "",
            ]
            lines += _table(
                d["models"],
                [*model_cols, ("brier_first", "First-place Brier"), ("brier_first_round_win", "Leader > 50% Brier")],
            )
        elif stage == "president":
            lines += ["", f"Elected: {d['elected']} ({d['how']}).", ""]
            lines += _table(
                d["combinations"],
                [("combination", "Combination"), ("p_winner", "P(elected)"), ("brier", "Brier"), ("log", "Log score")],
            )
        else:
            lines += ["", f"Winner: {d['observed']['winner']}.", ""]
            lines += _table(d["models"], [*model_cols, ("brier_first", "Winner Brier"), ("log_first", "Winner log")])
        bench = [b for b in d["benchmarks"] if b.get("status") == "ok"]
        if bench:
            lines += ["", "Benchmarks (" + d["benchmark_note"] + "):", ""]
            lines += _table(
                bench,
                [
                    ("benchmark", "Benchmark"),
                    ("event", "Event"),
                    ("p_actual", "P(actual)"),
                    ("p_yes", "P(yes)"),
                    ("brier", "Brier"),
                    ("F_brier", "Primary model Brier"),
                ],
            )
    revisions = sorted(out_dir.glob("scorecard_2026_*_revision_*.json"))
    if revisions:
        lines += ["", "## Revisions", "", "Recomputations kept beside the first scorecards (Addendum 06 s.2):", ""]
        for p in revisions:
            d = _read_json(p)
            lines.append(f"- `{p.name}` ({d['stage']}, scored {d['scored_utc']}): {d.get('revision_note', '')}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stage", choices=["r1", "president", "runoff"], required=True)
    ap.add_argument("--freeze-dir", type=Path, default=None, help="default outputs/freeze (outputs/freeze_runoff)")
    ap.add_argument("--results", type=Path, default=sc.RESULTS_2026)
    ap.add_argument("--out-dir", type=Path, default=config.OUTPUTS)
    ap.add_argument("--doc", type=Path, default=DOC)
    ap.add_argument(
        "--revision",
        default=None,
        help="label of a recomputation (Addendum 06 s.2), e.g. tse-open-data; written beside the first scorecard",
    )
    ap.add_argument("--revision-note", default="", help="why the scorecard was recomputed (required with --revision)")
    a = ap.parse_args(argv)
    fz = a.freeze_dir or config.OUTPUTS / ("freeze_runoff" if a.stage == "runoff" else "freeze")
    a.out_dir.mkdir(parents=True, exist_ok=True)
    stem = a.out_dir / f"scorecard_2026_{a.stage}"
    if a.revision:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,40}", a.revision) or not a.revision_note.strip():
            raise SystemExit("--revision needs a short lowercase label (letters, digits, '-') and --revision-note")
        if not Path(f"{stem}.json").exists():
            raise SystemExit(f"no first scorecard {stem}.json to revise; score without --revision first")
        stem = a.out_dir / f"scorecard_2026_{a.stage}_revision_{a.revision}"
    if Path(f"{stem}.json").exists():
        raise SystemExit(
            f"{stem}.json exists; the first scorecard is never overwritten. Use --revision LABEL --revision-note TEXT "
            "for a recomputation (Addendum 06 s.2)"
        )
    if a.stage == "r1":
        out, scores, cats = score_r1(fz, a.results)
        cats.to_csv(f"{stem}_categories.csv", index=False)
    elif a.stage == "president":
        out, scores = score_president_stage(fz, a.results)
    else:
        out, scores, cats = score_runoff_stage(fz, a.results)
        cats.to_csv(f"{stem}_categories.csv", index=False)
    if a.revision:
        out |= {"revision": a.revision, "revision_note": a.revision_note, "label": f"REVISION: {out['label']}"}
    scores.to_csv(f"{stem}.csv", index=False)
    Path(f"{stem}.json").write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    a.doc.parent.mkdir(parents=True, exist_ok=True)
    a.doc.write_text(render(a.out_dir), encoding="utf-8", newline="\n")
    print(json.dumps({k: out[k] for k in ("stage", "package_status") if k in out}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

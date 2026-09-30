"""2026 first-round forecast (PRELIMINARY or FINAL).

Usage:
  python scripts/forecast_2026.py --status PRELIMINARY --cutoff 2026-09-29
  python scripts/forecast_2026.py --status FINAL --cutoff 2026-10-03      (after refresh_2026_polls.py --at <freeze>)

Writes outputs/forecast_latest.json, appends outputs/forecast_history.csv, writes outputs/poll_snapshot.csv and
outputs/baseline_comparison_2026.csv. With --status FINAL also writes the freeze package to outputs/freeze/,
including every model's draws (draws.npz, PREREG_ADDENDUM_06 s.1).
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import date
from pathlib import Path

import pandas as pd

from brfc import config, forecast, freeze
from brfc.data import load_polls, load_results
from brfc.pipeline import MODEL_LABELS
from brfc.provenance import sha256_file, utc_now_iso

MODEL_VERSION = "brfc-0.1.0 independent valid-share random walks + LOEO round-specific election-day term"


def git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=config.ROOT, text=True).strip()
    except Exception:
        return "unknown"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", choices=["PRELIMINARY", "FINAL"], required=True)
    ap.add_argument("--cutoff", required=True)
    ap.add_argument("--variant", default=None, help="default: outputs/regime_selection.json production variant")
    a = ap.parse_args()
    cutoff = date.fromisoformat(a.cutoff)
    variant = a.variant or json.loads((config.OUTPUTS / "regime_selection.json").read_text())["production_variant"]
    rev = (
        json.loads((config.DATA / "interim" / "polls_wiki_2026.revision.json").read_text())
        if (config.DATA / "interim" / "polls_wiki_2026.revision.json").exists()
        else {"pt_oldid": 73082572}
    )
    tag = f"{a.status.lower()}_pt{rev['pt_oldid']}"
    polls = load_polls(("2026",))
    res = forecast.run(polls, load_results(), cutoff, variant=variant, tag=tag)
    fit, cats = res["fit"], res["categories"]

    generated = utc_now_iso()
    models = {}
    for m in ("F", "E", "E0", "B", "C", "D"):
        if m in res["summaries"] or m in res["draws"]:
            s = res["summaries"].get(m)
            if s is None:
                s = forecast.summarise_draws(res["draws"][m], cats)
            models[m] = {"label": MODEL_LABELS[m], "categories": json.loads(s.to_json(orient="records"))}
    doc = {
        "status": a.status,
        "election": "Brazil 2026 presidential election, first round (2026-10-04)",
        "information_cutoff_date": str(cutoff),
        "horizon_days": res["horizon"],
        "generated_utc": generated,
        "git_commit": git_commit(),
        "model_version": MODEL_VERSION,
        "rw_variant": variant,
        "primary_model": "F",
        "interval_type": "equal-tailed posterior predictive intervals (q10-q90 = 80%, q03-q97 = 94%)",
        "share_basis": "valid votes (blank and null excluded; undecided allocated proportionally - an assumption)",
        "poll_source": rev,
        "n_polls": fit.meta["n_polls"],
        "n_pollsters": fit.meta["n_pollsters"],
        "named_candidates": fit.meta["named"],
        "roles": res["roles"],
        "election_day_term_trained_on": res["train_elections"],
        "error_models": res["error_models"],
        "fit_diagnostics": fit.meta["diagnostics"],
        "models": models,
        "baselines_note": "B/C/D probabilities are a calibrated probabilistic conversion of point baselines "
        "(point + Normal(0, LOEO historical RMSE)); the baselines did not publish them.",
        "neutrality_note": "An aggregation of publicly registered polls; not a poll, not a recommendation.",
    }
    config.OUTPUTS.mkdir(exist_ok=True)
    (config.OUTPUTS / "forecast_latest.json").write_text(
        json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    f_sum = res["summaries"]["F"].assign(
        model="F",
        status=a.status,
        cutoff=str(cutoff),
        generated_utc=generated,
        pt_oldid=rev["pt_oldid"],
        rw_variant=variant,
    )
    hist_path = config.OUTPUTS / "forecast_history.csv"
    hist = pd.concat([pd.read_csv(hist_path), f_sum]) if hist_path.exists() else f_sum
    hist.to_csv(hist_path, index=False)
    fit.kept_rows.to_csv(config.OUTPUTS / "poll_snapshot.csv", index=False)
    res["baselines"].to_csv(config.OUTPUTS / "baseline_comparison_2026.csv", index=False)
    fit_path = Path(config.DATA / "cache" / "fits" / f"{fit.key}.path.csv")
    pd.read_csv(fit_path).to_csv(config.OUTPUTS / "posterior_path_2026.csv", index=False)

    if a.status == "FINAL":
        fz = config.OUTPUTS / "freeze"
        fz.mkdir(exist_ok=True)
        (fz / "forecast.json").write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        pd.concat([res["summaries"][m].assign(model=m) for m in ("F", "E", "E0")]).to_csv(
            fz / "forecast.csv", index=False
        )
        fit.kept_rows.to_csv(fz / "poll_snapshot.csv", index=False)
        freeze.save_draws(fz / freeze.DRAWS_FILE, res["draws"], cats)  # Addendum 06 s.1
        bench = config.DATA / "manual" / "benchmarks_2026_freeze.csv"
        bsnap = res["baselines"]
        if bench.exists():
            bsnap = pd.concat([bsnap, pd.read_csv(bench)], ignore_index=True)
        bsnap.to_csv(fz / "baseline_snapshot.csv", index=False)
        (fz / "MODEL_VERSION.txt").write_text(
            f"{MODEL_VERSION}\ngit {git_commit()}\nvariant {variant}\n", encoding="utf-8"
        )
        hashes = [f"{sha256_file(p)}  {p.name}" for p in sorted(fz.iterdir()) if p.name != "forecast_hash.txt"]
        (fz / "forecast_hash.txt").write_text("\n".join(hashes) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": a.status,
                "cutoff": str(cutoff),
                "n_polls": fit.meta["n_polls"],
                "F": res["summaries"]["F"].round(2).to_dict(orient="records"),
            },
            indent=1,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

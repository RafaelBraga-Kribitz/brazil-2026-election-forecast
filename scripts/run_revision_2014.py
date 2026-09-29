"""Labelled POST-RESULT data revision of the 2014 first round (PREREG_ADDENDUM_04 s.3).

The 11 late 2014 first-round polls found in release research were added after the registered historical scores had
been computed. This script refits the 2014 first round with them (tag "rev2014"; registered fits untouched) and
re-evaluates the whole first-round backtest with the 2014 cells replaced, because the 2014 eve deviations also
enter the leave-one-election-out term of 2018 and 2022. The registered results stay the reference; this output is
reported beside them only.

Writes outputs/revision_2014_first_round.csv (registered vs revision, first-round eve and all horizons).
"""

from __future__ import annotations

import json

from brfc import config
from brfc.data import load_polls, load_results
from brfc.pipeline import evaluate_backtest, fit_one

TAG = "rev2014"


def main() -> None:
    variant = json.loads((config.OUTPUTS / "regime_selection.json").read_text())["production_variant"]
    polls = load_polls(config.HISTORICAL, revision_2014=True)
    for h in config.HORIZONS_R1:
        f = fit_one(polls, "2014", 1, h, variant, tag=TAG)
        print("2014 r1", h, "skipped" if f is None else (f.meta["n_polls"], f.meta["diagnostics"]["converged"]))
    res = load_results()
    reg = evaluate_backtest(variant, res)[0]
    rev = evaluate_backtest(variant, res, tag_by_cell={("2014", 1): TAG})[0]
    keys = ["election", "round", "horizon", "model"]
    cols = ["n_polls", "mae", "margin_error", "coverage_80", "coverage_94", "brier_first", "log_first"]
    reg = reg[reg["round"] == 1][keys + cols]
    rev = rev[rev["round"] == 1][keys + cols]
    out = reg.merge(rev, on=keys, suffixes=("_registered", "_revision"))
    out.insert(0, "label", "post-result data revision (Addendum 04 s.3); registered values are the reference")
    out.to_csv(config.OUTPUTS / "revision_2014_first_round.csv", index=False)
    eve = out[(out["horizon"] == 1) & out["model"].isin(["F", "E", "B", "C"])]
    print(
        eve[["election", "model", "n_polls_registered", "n_polls_revision", "mae_registered", "mae_revision"]]
        .round(2)
        .to_string(index=False)
    )


if __name__ == "__main__":
    main()

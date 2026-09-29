"""Verify 100% of historical final-poll rows and recompute the planning-material error table independently.

Inputs: parsed Wikipedia polls (data/interim), final-poll releases as reported by the pollster/contracting outlet
(data/manual/final_poll_verification.csv), official results (data/manual/results_secondary.csv).
Outputs:
  outputs/final_poll_verification_check.csv  parsed Wikipedia value vs release value, per candidate
  outputs/final_poll_error_table.csv         release valid share minus official valid share (pp)
"""

from __future__ import annotations

import pandas as pd

from brfc import config
from brfc.data import load_polls, load_results
from brfc.names import canonical_pollster


def main() -> None:
    rel = pd.read_csv(config.DATA / "manual" / "final_poll_verification.csv", dtype={"election": str})
    rel["pollster"] = rel["pollster"].map(canonical_pollster)
    polls = load_polls(config.HISTORICAL)
    polls = polls[polls["source_type"] == "wikipedia_revision"]  # release-added rows would match themselves
    res = load_results()
    rows = []
    for (e, r, p), g in rel.groupby(["election", "round", "pollster"]):
        fe = pd.Timestamp(g["field_end"].iloc[0])
        cand = polls[(polls["election"] == e) & (polls["round"] == r) & (polls["pollster"] == p)]
        cand = cand[(pd.to_datetime(cand["field_end"]) - fe).abs() <= pd.Timedelta(days=2)]
        if r == 2:
            cand = cand[cand["candidate"].isin(config.RUNOFF_PAIRS[e])]
        if len(cand):  # the single best-matching wave: closest field end, then closest sample size
            meta = cand.drop_duplicates("poll_id").copy()
            meta["d_end"] = (pd.to_datetime(meta["field_end"]) - fe).abs()
            meta["d_n"] = (meta["sample_size"] - float(g["sample_size"].iloc[0])).abs()
            best = meta.sort_values(["d_end", "d_n"]).iloc[0]["poll_id"]
            cand = cand[cand["poll_id"] == best]
        ids = cand["poll_id"].unique()
        for x in g.itertuples():
            w = cand[cand["candidate"] == x.candidate]
            if len(ids) == 0:
                status, wv, pid = "missing_in_wikipedia", None, ""
            elif w.empty:
                status, wv, pid = "candidate_not_in_parsed_row", None, ids[0]
            else:
                wv, pid = float(w["share_reported"].iloc[0]), w["poll_id"].iloc[0]
                if pd.isna(x.total_share_pct):
                    status = "release_has_valid_only"
                else:
                    status = "verified_match" if abs(wv - x.total_share_pct) <= 0.5 else "verified_conflict"
            rows.append(
                {
                    "election": e,
                    "round": r,
                    "pollster": p,
                    "release_field_end": x.field_end,
                    "poll_id": pid,
                    "candidate": x.candidate,
                    "wikipedia_total_share": wv,
                    "release_total_share": x.total_share_pct,
                    "release_valid_share": x.valid_share_pct,
                    "status": status,
                    "release_source": x.source_url,
                }
            )
    chk = pd.DataFrame(rows)
    chk.to_csv(config.OUTPUTS / "final_poll_verification_check.csv", index=False)

    err = rel.merge(res[["election", "round", "candidate", "valid_share"]], on=["election", "round", "candidate"])
    err["release_minus_official_pp"] = err["valid_share_pct"] - err["valid_share"]
    err = err[
        [
            "election",
            "round",
            "pollster",
            "field_end",
            "candidate",
            "valid_share_pct",
            "valid_share",
            "release_minus_official_pp",
        ]
    ].rename(columns={"valid_share_pct": "release_valid_share", "valid_share": "official_valid_share"})
    err.to_csv(config.OUTPUTS / "final_poll_error_table.csv", index=False)
    print(chk["status"].value_counts().to_string())
    print(err[err["pollster"] == "Datafolha"].round(2).to_string(index=False))


if __name__ == "__main__":
    main()

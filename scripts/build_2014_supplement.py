"""Build the 2014 manual supplement from verified release research (PREREG_ADDENDUM_04).

Inputs: data/manual/research_2014/{datafolha,ibope,mda_vox}.csv (values read on cited releases/articles) and
data/manual/research_2014/verification.csv (independent re-read of every poll). Output:
data/interim/polls_2014_supplement.csv in the canonical schema. The parsed Wikipedia tables are not edited.

Rows:
- round 2, fielded before the first round (pre-first-round head-to-heads): used by the conditional forecast
  backtest (registered in Addendum 04 before any such fit or score);
- round 1, the late first-round polls missing from the pinned Wikipedia revision: a POST-RESULT data revision of
  the 2014 first round (registered scores already existed). Loaded only on request (load_polls(..., revision_2014=True))
  and reported beside the registered results, never in place of them.

Exclusions (logged): polls without a confirmed sample size; polls whose verification reports a conflict or an
unreachable source; Sensus 2014-09-01..04 (the only source labels its runoff figures as valid votes, but they sum
to 80.4, so the basis cannot be determined); rows with a printed 0 (below 1%); first-round rows for polls already
present in data/interim/polls_releases.csv.
"""

from __future__ import annotations

import glob

import pandas as pd

from brfc import config
from brfc.names import canonical_candidate, canonical_pollster
from brfc.provenance import sha256_file
from brfc.schema import CANONICAL_COLUMNS, make_poll_id, validate_polls

RESEARCH = config.DATA / "manual" / "research_2014"
EXCLUDE = {("Sensus", "2014-09-04"): "basis undeterminable (source says valid votes; values sum to 80.4)"}


def main() -> None:
    files = sorted(p for p in glob.glob(str(RESEARCH / "*.csv")) if not p.endswith("verification.csv"))
    ver = pd.read_csv(RESEARCH / "verification.csv", dtype=str)
    ver["pollster"] = ver["pollster"].map(canonical_pollster)
    bad = ver[ver["status"] != "confirmed"][["pollster", "field_end"]].drop_duplicates()
    bad_keys = set(map(tuple, bad.to_numpy()))
    checked = set(map(tuple, ver[["pollster", "field_end"]].drop_duplicates().to_numpy()))
    rel = pd.read_csv(config.DATA / "interim" / "polls_releases.csv", dtype=str)
    rel_r1 = set(map(tuple, rel[(rel["election"] == "2014") & (rel["round"] == "1")][["pollster", "field_end"]]
                     .drop_duplicates().to_numpy()))
    rows, log = [], []
    for f in files:
        h = sha256_file(f)
        d = pd.read_csv(f, dtype=str)
        d["pollster"] = d["pollster"].map(canonical_pollster)
        for x in d.itertuples(index=False):
            key = (x.pollster, x.field_end)
            reason = None
            if pd.isna(x.sample_size) or not str(x.sample_size).strip():
                reason = "no confirmed sample size"
            elif key in EXCLUDE:
                reason = EXCLUDE[key]
            elif key in bad_keys or key not in checked:
                reason = "not confirmed by independent verification"
            elif x.round == "1" and key in rel_r1:
                reason = "first-round poll already added from releases (Addendum 02)"
            total = pd.to_numeric(x.share_total_pct, errors="coerce")
            valid = pd.to_numeric(x.share_valid_pct, errors="coerce")
            share, basis = (total, "total") if pd.notna(total) else (valid, "valid")
            if reason is None and (pd.isna(share) or share <= 0):
                reason = "no positive share printed"
            if reason:
                log.append({"pollster": x.pollster, "field_end": x.field_end, "round": x.round,
                            "scenario": x.scenario, "candidate": x.candidate, "reason": reason})
                continue
            r = dict.fromkeys(CANONICAL_COLUMNS, "")
            n = int(float(x.sample_size))
            r.update(
                poll_id=make_poll_id("2014", int(x.round), x.pollster, x.field_start, x.field_end, n),
                election="2014", round=int(x.round), tse_br_id=x.tse_br_id if pd.notna(x.tse_br_id) else "",
                pollster=x.pollster, contractor=x.contractor if pd.notna(x.contractor) else "",
                field_start=x.field_start, field_end=x.field_end,
                publication_date=x.publication_date if pd.notna(x.publication_date) else "", sample_size=n,
                methodology="", scenario=x.scenario, candidate=canonical_candidate(x.candidate),
                share_reported=float(share), share_basis=basis,
                blank_null=pd.to_numeric(x.blank_null_pct, errors="coerce") if basis == "total" else float("nan"),
                undecided=pd.to_numeric(x.undecided_pct, errors="coerce") if basis == "total" else float("nan"),
                valid_vote_share=float("nan"), source_url=x.source_url, source_type=x.source_type,
                source_revision="", retrieval_timestamp=x.retrieved_utc, verification_status="verified_match",
                verification_source="data/manual/research_2014/verification.csv", source_hash=h,
                notes=("revision_2014_r1; " if x.round == "1" else "pre_first_round_h2h; ")
                + (x.notes if pd.notna(x.notes) else ""),
            )
            rows.append(r)
    out = pd.DataFrame(rows, columns=CANONICAL_COLUMNS)
    out = out.drop_duplicates(["poll_id", "scenario", "candidate"])
    print(validate_polls(out) or "schema OK")
    out.to_csv(config.DATA / "interim" / "polls_2014_supplement.csv", index=False)
    pd.DataFrame(log).to_csv(RESEARCH / "supplement_exclusions.csv", index=False)
    g = out.drop_duplicates(["poll_id", "scenario"])
    print(g.groupby(["round", "scenario", "pollster"]).size().to_string())
    print(f"excluded rows: {len(log)}")


if __name__ == "__main__":
    main()

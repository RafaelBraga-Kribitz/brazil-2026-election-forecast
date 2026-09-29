"""Add final pre-election polls that are missing from the pinned Wikipedia tables, from release records.

Source precedence (PREREG.md s.2, Addendum 02): original pollster release > Wikipedia PT > Wikipedia EN. Rows are
built only for polls in data/manual/final_poll_verification.csv that outputs/final_poll_verification_check.csv
reports as `missing_in_wikipedia`. Output: data/interim/polls_releases.csv (canonical schema).
"""

from __future__ import annotations

import pandas as pd

from brfc import config
from brfc.names import canonical_pollster
from brfc.provenance import sha256_text_file_lf
from brfc.schema import CANONICAL_COLUMNS, make_poll_id, validate_polls


def main() -> None:
    src = config.DATA / "manual" / "final_poll_verification.csv"
    rel = pd.read_csv(src, dtype={"election": str, "tse_br_id": str})
    rel["pollster"] = rel["pollster"].map(canonical_pollster)
    chk = pd.read_csv(config.OUTPUTS / "final_poll_verification_check.csv", dtype={"election": str})
    missing = chk.loc[chk["status"] == "missing_in_wikipedia", ["election", "round", "pollster"]].drop_duplicates()
    rel = rel.merge(missing, on=["election", "round", "pollster"])
    h = sha256_text_file_lf(src)
    rows = []
    for x in rel.itertuples():
        total = pd.notna(x.total_share_pct)
        share = x.total_share_pct if total else x.valid_share_pct
        r = dict.fromkeys(CANONICAL_COLUMNS, "")
        r.update(
            poll_id=make_poll_id(x.election, x.round, x.pollster, x.field_start, x.field_end, x.sample_size),
            election=x.election,
            round=int(x.round),
            tse_br_id=x.tse_br_id if pd.notna(x.tse_br_id) else "",
            pollster=x.pollster,
            field_start=x.field_start,
            field_end=x.field_end,
            publication_date=x.publication_date if pd.notna(x.publication_date) else "",
            sample_size=int(x.sample_size),
            scenario="main",
            candidate=x.candidate,
            share_reported=float(share),
            share_basis="total" if total else "valid",
            blank_null=x.blank_null_pct if total else float("nan"),
            undecided=x.undecided_pct if total else float("nan"),
            valid_vote_share=float("nan"),
            source_url=x.source_url,
            source_type="media_report",
            source_revision="",
            retrieval_timestamp=x.retrieved_utc,
            verification_status="verified_match",
            verification_source=x.source_url,
            source_hash=h,
            notes="final poll missing from pinned Wikipedia revision; added from release record (Addendum 02)",
        )
        rows.append(r)
    out = pd.DataFrame(rows, columns=CANONICAL_COLUMNS)
    out = out[out["share_reported"] > 0]  # zero-share minor candidates carry no information for valid shares
    print(validate_polls(out) or "schema OK")
    out.to_csv(config.DATA / "interim" / "polls_releases.csv", index=False)
    print(out.groupby(["election", "round", "pollster", "field_end", "share_basis"]).size())


if __name__ == "__main__":
    main()

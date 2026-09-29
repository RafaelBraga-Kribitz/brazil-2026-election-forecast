from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from brfc.schema import CANONICAL_COLUMNS, make_poll_id

HASH = "0" * 64


def poll_rows(election, round_, pollster, fs, fe, n, shares, *, scenario="main", basis="total", blank_null=5.0):
    pid = make_poll_id(election, round_, pollster, fs, fe, n)
    rows = []
    for cand, s in shares.items():
        r = dict.fromkeys(CANONICAL_COLUMNS, "")
        r.update(poll_id=pid, election=election, round=round_, pollster=pollster, field_start=fs, field_end=fe,
                 sample_size=n, scenario=scenario, candidate=cand, share_reported=float(s), share_basis=basis,
                 blank_null=blank_null, undecided=float("nan"), valid_vote_share=float("nan"),
                 source_url="https://example.org/x", source_type="wikipedia_revision", source_revision="1",
                 retrieval_timestamp="2026-09-29T00:00:00Z", verification_status="unverified", source_hash=HASH,
                 publication_date="")
        rows.append(r)
    return rows


@pytest.fixture
def fixture_2022_r1() -> pd.DataFrame:
    """Synthetic 2022-like round-1 polls (ballot candidates from config), several pollsters, one tracking pair."""
    rows = []
    start = date(2022, 8, 5)
    pollsters = ["Datafolha", "Quaest", "AtlasIntel", "Ipec"]
    for k in range(40):
        fe = start + timedelta(days=int(k * 1.4))
        fs = fe - timedelta(days=2)
        p = pollsters[k % 4]
        rows += poll_rows("2022", 1, p, str(fs), str(fe), 2000 + 10 * k,
                          {"Lula": 44 + (k % 3), "Jair Bolsonaro": 34 + (k % 2), "Ciro Gomes": 7, "Simone Tebet": 4,
                           "Soraya Thronicke": 1, "__others__": 1})
    # overlapping tracking waves (same pollster, overlapping fieldwork)
    rows += poll_rows("2022", 1, "Tracker", "2022-09-20", "2022-09-24", 1500,
                      {"Lula": 45, "Jair Bolsonaro": 36, "Ciro Gomes": 6, "Simone Tebet": 5})
    rows += poll_rows("2022", 1, "Tracker", "2022-09-22", "2022-09-26", 1500,
                      {"Lula": 46, "Jair Bolsonaro": 35, "Ciro Gomes": 6, "Simone Tebet": 5})
    df = pd.DataFrame(rows)
    df["round"] = df["round"].astype(int)
    return df

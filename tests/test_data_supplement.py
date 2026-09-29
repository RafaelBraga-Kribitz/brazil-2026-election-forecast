"""2014 release-research supplement (PREREG_ADDENDUM_04): `load_polls` always adds its pre-first-round
head-to-heads, adds its first-round rows only with `revision_2014=True`, and leaves every registered information
set unchanged."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from brfc import config, data
from brfc.pipeline import information_set
from brfc.schema import make_poll_id

ROOT = Path(__file__).resolve().parents[1]
INTERIM = ROOT / "data" / "interim"
SUPPLEMENT = INTERIM / "polls_2014_supplement.csv"

pytestmark = pytest.mark.skipif(not SUPPLEMENT.exists(), reason="2014 supplement not built")

REGISTERED_CELLS = [
    (e, r, h) for e in config.HISTORICAL for r, hs in ((1, config.HORIZONS_R1), (2, config.HORIZONS_R2)) for h in hs
]
# registered sensitivity settings (PREREG s.10), run at the eve horizon
SENSITIVITY = {
    "dependence_off": {"dependence_rule": False},
    "min_3_polls": {"min_polls_per_pollster": 3},
    "leader_weighted": {"allocation": "leader_weighted"},
    "final_14_days": {"final_days": 14},
}


def _raw_supplement() -> pd.DataFrame:
    return pd.read_csv(SUPPLEMENT, dtype={"election": str, "tse_br_id": str, "publication_date": str})


def _loadable(d: pd.DataFrame) -> pd.DataFrame:
    """Rows that survive the loader's completeness filter."""
    x = d.assign(
        sample_size=pd.to_numeric(d["sample_size"], errors="coerce"),
        share_reported=pd.to_numeric(d["share_reported"], errors="coerce"),
    )
    return x.dropna(subset=["sample_size", "share_reported", "field_end"])


def _tag(d: pd.DataFrame) -> pd.Series:
    return d["notes"].fillna("").astype(str).str.split(";").str[0].str.strip()


@pytest.fixture(scope="module")
def loads(tmp_path_factory):
    """Default load, load without the supplement file, and the revision_2014 load (all historical elections)."""
    default = data.load_polls(config.HISTORICAL)
    revision = data.load_polls(config.HISTORICAL, revision_2014=True)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(data, "SUPPLEMENT_2014_FILE", tmp_path_factory.mktemp("none") / "absent.csv")
        without = data.load_polls(config.HISTORICAL)
    return {"default": default, "without": without, "revision": revision}


def _rows(d: pd.DataFrame, mask) -> pd.DataFrame:
    return d[mask].reset_index(drop=True)


# --------------------------------------------------------------------------------------------------
# the supplement file itself


def test_supplement_rows_carry_the_round_tag_and_precede_the_first_round():
    s = _raw_supplement()
    assert (s["election"] == "2014").all()
    tags = _tag(s)
    assert set(zip(s["round"], tags, strict=True)) == {(2, "pre_first_round_h2h"), (1, "revision_2014_r1")}
    fe = pd.to_datetime(s["field_end"]).dt.date
    assert (fe < config.ELECTION_DATES[("2014", 1)]).all()  # both kinds were fielded before the first round
    assert (s["source_type"] != "wikipedia_revision").all()


# --------------------------------------------------------------------------------------------------
# (a) default load: 2014 round-1 rows identical to the load without the supplement


def test_default_load_leaves_2014_round1_rows_unchanged(loads):
    d, w = loads["default"], loads["without"]
    pd.testing.assert_frame_equal(_rows(d, d["round"] == 1), _rows(w, w["round"] == 1))
    for e in ("2018", "2022"):
        pd.testing.assert_frame_equal(_rows(d, d["election"] == e), _rows(w, w["election"] == e))
    assert not (_tag(d) == data.SUPPLEMENT_TAGS[1]).any()


def test_default_load_adds_exactly_the_pre_first_round_h2h_rows(loads):
    d, w = loads["default"], loads["without"]
    s = _loadable(_raw_supplement())
    h2h = s[s["round"] == 2]
    added = d[~d["poll_id"].isin(w["poll_id"])]
    assert len(d) == len(w) + len(h2h)
    assert set(added["poll_id"]) == set(h2h["poll_id"])
    assert (added["round"] == 2).all() and (_tag(added) == data.SUPPLEMENT_TAGS[2]).all()
    # rows present without the supplement are unchanged, in the same order
    pd.testing.assert_frame_equal(_rows(d, d["poll_id"].isin(w["poll_id"])), w.reset_index(drop=True))


# --------------------------------------------------------------------------------------------------
# (b) every registered information set is identical with and without the supplement


def _assert_same_information_set(a, b):
    wide_a, named_a, dropped_a, kept_a, cutoff_a = a
    wide_b, named_b, dropped_b, kept_b, cutoff_b = b
    assert named_a == named_b and cutoff_a == cutoff_b
    pd.testing.assert_frame_equal(wide_a.reset_index(drop=True), wide_b.reset_index(drop=True))
    pd.testing.assert_frame_equal(dropped_a.reset_index(drop=True), dropped_b.reset_index(drop=True))
    pd.testing.assert_frame_equal(kept_a.reset_index(drop=True), kept_b.reset_index(drop=True))


@pytest.mark.parametrize(("election", "round_", "horizon"), REGISTERED_CELLS, ids=lambda x: str(x))
def test_registered_information_sets_unchanged_by_supplement(loads, election, round_, horizon):
    _assert_same_information_set(
        information_set(loads["default"], election, round_, horizon),
        information_set(loads["without"], election, round_, horizon),
    )


@pytest.mark.parametrize("election", config.HISTORICAL)
@pytest.mark.parametrize("round_", (1, 2))
@pytest.mark.parametrize("setting", sorted(SENSITIVITY))
def test_sensitivity_information_sets_unchanged_by_supplement(loads, election, round_, setting):
    kw = SENSITIVITY[setting]
    _assert_same_information_set(
        information_set(loads["default"], election, round_, 1, **kw),
        information_set(loads["without"], election, round_, 1, **kw),
    )


# --------------------------------------------------------------------------------------------------
# (c) revision_2014=True adds exactly the supplement's first-round polls


def test_revision_2014_adds_exactly_the_supplement_round1_polls(loads):
    d, rev = loads["default"], loads["revision"]
    s = _loadable(_raw_supplement())
    r1 = s[s["round"] == 1]
    added = rev[~rev["poll_id"].isin(d["poll_id"])]
    assert len(r1) > 0
    assert len(rev) == len(d) + len(r1)
    assert set(added["poll_id"]) == set(r1["poll_id"])
    assert (added["election"] == "2014").all() and (added["round"] == 1).all()
    assert (_tag(added) == data.SUPPLEMENT_TAGS[1]).all()
    pd.testing.assert_frame_equal(_rows(rev, rev["poll_id"].isin(d["poll_id"])), d.reset_index(drop=True))


def test_revision_2014_changes_only_2014_round1_information_sets(loads):
    eve = [information_set(loads[k], "2014", 1, 1)[0]["poll_id"].nunique() for k in ("revision", "default")]
    assert eve[0] > eve[1]  # the revision does reach the 2014 first-round eve set
    for e, r, h in REGISTERED_CELLS:
        if (e, r) == ("2014", 1):
            continue
        _assert_same_information_set(
            information_set(loads["revision"], e, r, h), information_set(loads["default"], e, r, h)
        )


def test_revision_2014_has_no_effect_without_2014():
    pd.testing.assert_frame_equal(
        data.load_polls(("2022",), revision_2014=True).reset_index(drop=True),
        data.load_polls(("2022",)).reset_index(drop=True),
    )


# --------------------------------------------------------------------------------------------------
# (d) no poll_id collisions


def test_supplement_poll_ids_do_not_collide():
    s = _raw_supplement()
    others = pd.concat(
        pd.read_csv(p, dtype=str) for p in (INTERIM / "polls_wiki_2014.csv", INTERIM / "polls_releases.csv")
    )
    assert not set(s["poll_id"]) & set(others["poll_id"])
    meta = ["round", "pollster", "field_start", "field_end", "sample_size"]
    per_poll = s.drop_duplicates(["poll_id", *meta])
    assert not per_poll["poll_id"].duplicated().any()  # one poll_id <-> one fieldwork
    for x in per_poll.itertuples(index=False):
        assert x.poll_id == make_poll_id("2014", x.round, x.pollster, x.field_start, x.field_end, x.sample_size)
    # the same fieldwork is not also present in another 2014 table under a different poll_id
    key = ["round", "pollster", "field_end"]
    o = others[others["election"] == "2014"].assign(round=lambda x: x["round"].astype(int))
    assert per_poll[key].merge(o[key].drop_duplicates(), on=key).empty


def test_loaded_rows_have_unique_keys(loads):
    for d in loads.values():
        assert not d.duplicated(["poll_id", "scenario", "candidate"]).any()


# --------------------------------------------------------------------------------------------------
# loader guards (synthetic copies of the supplement)


def test_loader_rejects_untagged_supplement_rows(tmp_path, monkeypatch):
    s = _raw_supplement()
    s.loc[s.index[0], "notes"] = "untagged row"
    bad = tmp_path / "supplement.csv"
    s.to_csv(bad, index=False)
    monkeypatch.setattr(data, "SUPPLEMENT_2014_FILE", bad)
    with pytest.raises(ValueError, match="round tag"):
        data.load_polls(("2014",))


def test_loader_rejects_supplement_poll_id_collisions(tmp_path, monkeypatch):
    s = _raw_supplement()
    wiki = pd.read_csv(INTERIM / "polls_wiki_2014.csv", dtype=str)
    s.loc[s["poll_id"] == s["poll_id"].iloc[0], "poll_id"] = wiki["poll_id"].iloc[0]
    bad = tmp_path / "supplement.csv"
    s.to_csv(bad, index=False)
    monkeypatch.setattr(data, "SUPPLEMENT_2014_FILE", bad)
    with pytest.raises(ValueError, match="poll_id also present"):
        data.load_polls(("2014",))

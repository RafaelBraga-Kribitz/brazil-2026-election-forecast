"""2014 national presidential polls from a pinned Wikipedia revision -> canonical long schema.

Source choice (checked 2026-09-29, see SOURCES):
- EN "2014 Brazilian general election" (section "Opinion polls") is the only Wikipedia table that reports
  sample sizes. First round: one row per poll-scenario (multi-scenario polls use rowspan). Runoff: one table
  split into "Valid votes" and "Total votes" sections.
- PT "Pesquisas de opinião para a eleição presidencial no Brasil em 2014" ("... da eleição ..." redirects to
  it) lists more first-round polls with a blank/null vs don't-know split, but has no sample-size column in any
  revision (nor did the PT article "Eleição presidencial no Brasil em 2014" in 2014-2018). Sample size is
  required, so it is used only as a coverage reference (coverage_gaps) and contributes no rows.

Rules: national stimulated vote intention only; election-result rows and the election-day exit poll are
excluded; "<1%" cells are not numbers and are omitted (listed in `notes`); the EN "Abst./Undec." column
combines blank/null and undecided -> blank_null with note "blank_null_includes_undecided".
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

import lxml.html as LH
import numpy as np
import pandas as pd

from brfc.ingest.wikipedia import fetch_revision
from brfc.provenance import SourceRecord
from brfc.schema import CANONICAL_COLUMNS, OTHERS, make_poll_id, validate_polls

ELECTION = "2014"
YEAR = 2014
SOURCES: list[tuple[str, str, int]] = [("en", "2014 Brazilian general election", 1369923991)]
COVERAGE_REFERENCE: tuple[str, str, int] = (
    "pt",
    "Pesquisas de opinião para a eleição presidencial no Brasil em 2014",
    73055945,
)
OUT_PATH = Path(__file__).resolve().parents[3] / "data" / "interim" / "polls_wiki_2014.csv"

# Header link title (Wikipedia article) -> canonical candidate name (matches brfc.config.BALLOTS["2014"]).
CANDIDATES: dict[str, str] = {
    "Dilma Rousseff": "Dilma Rousseff",
    "Luiz Inácio Lula da Silva": "Lula",
    "Aécio Neves": "Aécio Neves",
    "José Serra": "José Serra",
    "Marina Silva": "Marina Silva",
    "Eduardo Campos": "Eduardo Campos",
    "Luciana Genro": "Luciana Genro",
    "Randolfe Rodrigues": "Randolfe Rodrigues",
    "Plínio de Arruda Sampaio": "Plínio de Arruda Sampaio",
    "Everaldo Pereira": "Pastor Everaldo",
    "Eduardo Jorge": "Eduardo Jorge",
    "Levy Fidelix": "Levy Fidelix",
    "José Maria Eymael": "José Maria Eymael",
    "Zé Maria": "Zé Maria",
    "Mauro Iasi": "Mauro Iasi",
    "Rui Costa Pimenta": "Rui Costa Pimenta",
}
# Pollster cell text (lower-case, archive note stripped) -> (pollster, contractor). "Client/Pollster" form.
POLLSTERS: dict[str, tuple[str, str]] = {
    "datafolha": ("Datafolha", ""),
    "ibope": ("Ibope", ""),
    "cni/ibope": ("Ibope", "CNI"),
    "vox populi": ("Vox Populi", ""),
    "cnt/mda": ("MDA", "CNT"),
    "istoé/sensus": ("Sensus", "IstoÉ"),
}
EN_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
PT_MONTHS = {m: i for i, m in enumerate(
    ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
     "novembro", "dezembro"], start=1)}
EN_DASH, EM_DASH, MINUS = chr(0x2013), chr(0x2014), chr(0x2212)
DASHES = {"", EN_DASH, EM_DASH, "-", MINUS, "n/a"}


def _text(el) -> str:
    return re.sub(r"\s+", " ", el.text_content().replace("\xa0", " ")).strip()


def _clean_doc(html: str):
    doc = LH.fromstring(html)
    for bad in doc.xpath('//style|//script|//sup[contains(@class,"reference")]|//span[contains(@class,"sortkey")]'):
        bad.drop_tree()
    return doc


def _grid(table) -> list[list]:
    """Expand rowspan/colspan: grid[r][c] = the <td>/<th> element covering that position."""
    grid: list[list] = []
    pending: dict[tuple[int, int], object] = {}
    for r, tr in enumerate(table.xpath("./tr|./thead/tr|./tbody/tr|./tfoot/tr")):
        row: list = []
        c = 0
        cells = [x for x in tr if x.tag in ("td", "th")]
        i = 0
        while i < len(cells) or (r, c) in pending:
            if (r, c) in pending:
                row.append(pending.pop((r, c)))
                c += 1
                continue
            cell = cells[i]
            i += 1
            rs, cs = int(cell.get("rowspan", 1) or 1), int(cell.get("colspan", 1) or 1)
            for dc in range(cs):
                row.append(cell)
                for dr in range(1, rs):
                    pending[(r + dr, c + dc)] = cell
            c += cs
        grid.append(row)
    return grid


def _share(s: str) -> tuple[float | None, bool]:
    """Return (value, is_below_one). value None if absent/non-numeric."""
    s = s.replace("%", "").replace(",", ".").strip()
    if s.lower() in DASHES:
        return None, False
    if s.startswith("<"):
        return None, True
    try:
        return float(s), False
    except ValueError:
        return None, False


def _en_dates(s: str) -> tuple[str, str]:
    s = s.replace(EN_DASH, "-").replace(EM_DASH, "-").strip()
    m = re.fullmatch(r"(\d{1,2})\s*([A-Za-z]{3,})?\s*-\s*(\d{1,2})\s+([A-Za-z]{3,})(?:\s+(\d{4}))?", s)
    if m:
        d1, m1, d2, m2, y = m.groups()
        y = int(y or YEAR)
        mo2 = EN_MONTHS[m2[:3].lower()]
        mo1 = EN_MONTHS[m1[:3].lower()] if m1 else mo2
        return date(y, mo1, int(d1)).isoformat(), date(y, mo2, int(d2)).isoformat()
    m = re.fullmatch(r"(\d{1,2})\s+([A-Za-z]{3,})(?:\s+(\d{4}))?", s)
    if m:
        d, mo, y = m.groups()
        iso = date(int(y or YEAR), EN_MONTHS[mo[:3].lower()], int(d)).isoformat()
        return iso, iso
    raise ValueError(f"unparseable EN date range: {s!r}")


def _pt_dates(s: str) -> tuple[str, str]:
    s = s.lower().strip()
    m = re.fullmatch(r"(\d{1,2})(?: de (\w+))? (?:a|e) (\d{1,2}) de (\w+)", s)
    if m:
        d1, m1, d2, m2 = m.groups()
        mo2 = PT_MONTHS[m2]
        mo1 = PT_MONTHS[m1] if m1 else mo2
        return date(YEAR, mo1, int(d1)).isoformat(), date(YEAR, mo2, int(d2)).isoformat()
    m = re.fullmatch(r"(\d{1,2}) de (\w+)", s)
    if m:
        iso = date(YEAR, PT_MONTHS[m.group(2)], int(m.group(1))).isoformat()
        return iso, iso
    raise ValueError(f"unparseable PT date range: {s!r}")


def _pollster(raw: str) -> tuple[str, str]:
    key = re.sub(r"\s*Archived\b.*$", "", raw).strip()
    return POLLSTERS.get(key.lower(), (key, ""))


def _candidate_from_header(th) -> str | None:
    links = th.xpath(".//a[@title]")
    if not links:
        return None
    title = links[0].get("title")
    if title not in CANDIDATES:
        raise ValueError(f"unmapped candidate header: {title!r}")
    return CANDIDATES[title]


def _poll_tables(doc):
    """Yield (round, table) for the EN presidential poll tables (h4 'First round' / 'Second round')."""
    heading = ""
    for el in doc.iter():
        if el.tag in ("h2", "h3", "h4"):
            heading = _text(el).lower()
        elif el.tag == "table" and "wikitable" in (el.get("class") or ""):
            head = _text(el.xpath(".//tr")[0]) if el.xpath(".//tr") else ""
            if "Pollster" in head and "Sample" in head:
                if heading.startswith("first round"):
                    yield 1, el
                elif heading.startswith("second round"):
                    yield 2, el


def parse(html: str, rec: SourceRecord) -> pd.DataFrame:
    """Parse the EN article's poll tables. Skip counts are in df.attrs['skipped']."""
    doc = _clean_doc(html)
    rows: list[dict] = []
    skipped = {"result_rows": 0, "exit_poll_rows": 0, "missing_sample_size_polls": 0, "event_rows": 0}
    skipped_polls: list[str] = []
    for round_, table in _poll_tables(doc):
        grid = _grid(table)
        ncol = max(len(r) for r in grid)
        header = grid[0]
        cols: list[tuple[str, str | None]] = []  # (kind, candidate)
        for th in header:
            t = _text(th).lower()
            cand = _candidate_from_header(th)
            if cand:
                cols.append(("cand", cand))
            elif t.startswith("pollster"):
                cols.append(("pollster", None))
            elif t.startswith("date"):
                cols.append(("date", None))
            elif t.startswith("sample"):
                cols.append(("n", None))
            elif t == "others":
                cols.append(("cand", OTHERS))
            elif "abst" in t or "undec" in t:
                cols.append(("bn_und", None))
            elif t == "lead":
                cols.append(("lead", None))
            else:
                raise ValueError(f"unexpected header column: {t!r}")
        data_rows = [r for r in grid if not all(c.tag == "th" for c in r)]
        basis = "total"
        groups: list[tuple[str, list[list]]] = []  # (basis, rows) per poll
        for r in data_rows:
            distinct = list(dict.fromkeys(r))
            if len(distinct) <= 2 and any(int(c.get("colspan", 1) or 1) >= 5 for c in distinct):
                label = _text(distinct[-1]).lower()
                if label.startswith("valid votes"):
                    basis = "valid"
                elif label.startswith("total votes"):
                    basis = "total"
                else:
                    skipped["event_rows"] += 1
                continue
            if len(r) < ncol:
                raise ValueError(f"short row in round {round_} table: {[_text(c) for c in r]}")
            ip = [k for k, _ in cols].index("pollster")
            if groups and groups[-1][1][-1][ip] is r[ip] and groups[-1][0] == basis:
                groups[-1][1].append(r)
            else:
                groups.append((basis, [r]))
        for basis, grows in groups:
            first = grows[0]
            vals = {k: _text(first[i]) for i, (k, _) in enumerate(cols) if k in ("pollster", "date", "n")}
            raw_pollster = vals["pollster"]
            if "election" in raw_pollster.lower():
                skipped["result_rows"] += len(grows)
                continue
            if "exit poll" in raw_pollster.lower():
                skipped["exit_poll_rows"] += len(grows)
                continue
            pollster, contractor = _pollster(raw_pollster)
            fs, fe = _en_dates(vals["date"])
            n_txt = vals["n"].replace(",", "").replace(".", "").strip()
            if not n_txt.isdigit():
                skipped["missing_sample_size_polls"] += 1
                skipped_polls.append(f"r{round_} {raw_pollster} {vals['date']} n={vals['n']!r}")
                continue
            n = int(n_txt)
            poll_id = make_poll_id(ELECTION, round_, pollster, fs, fe, n)
            scen_rows = []
            for r in grows:
                cands, below = [], []
                bn = None
                for i, (k, cand) in enumerate(cols):
                    if k == "cand":
                        v, lt1 = _share(_text(r[i]))
                        if v is not None:
                            cands.append((cand, v))
                        elif lt1:
                            below.append(cand)
                    elif k == "bn_und":
                        bn, _ = _share(_text(r[i]))
                present = [c for c, _ in cands] + below
                named = [c for _, c in sorted((cols.index(("cand", c)), c) for c in present if c != OTHERS)]
                if OTHERS in present and round_ == 1:
                    named.append("others")
                scen_rows.append((cands, below, bn, named))
            labels: list[str] = []
            for *_, named in scen_rows:
                if round_ == 2:
                    lab = " vs ".join(named)
                    lab += " [valid]" if basis == "valid" else ""
                elif len(scen_rows) == 1:
                    lab = "main" + (" [valid]" if basis == "valid" else "")
                else:
                    lab = " / ".join(named) + (" [valid]" if basis == "valid" else "")
                if lab in labels:
                    lab = f"{lab} #{sum(x.startswith(lab) for x in labels) + 1}"
                labels.append(lab)
            for (cands, below, bn, _), lab in zip(scen_rows, labels, strict=True):
                notes = []
                if bn is not None:
                    notes.append("blank_null_includes_undecided")
                if below:
                    notes.append("lt1pct_omitted=" + "|".join(below))
                for cand, v in cands:
                    rows.append({
                        "poll_id": poll_id, "election": ELECTION, "round": round_, "tse_br_id": "",
                        "pollster": pollster, "contractor": contractor, "field_start": fs, "field_end": fe,
                        "publication_date": "", "sample_size": n, "methodology": "", "scenario": lab,
                        "candidate": cand, "share_reported": v, "share_basis": basis,
                        "blank_null": bn if bn is not None else np.nan, "undecided": np.nan,
                        "valid_vote_share": np.nan, "source_url": rec.url, "source_type": rec.source_type,
                        "source_revision": rec.revision, "retrieval_timestamp": rec.retrieval_timestamp,
                        "verification_status": "unverified", "verification_source": "",
                        "source_hash": rec.sha256, "notes": ";".join(notes),
                    })
    df = pd.DataFrame(rows, columns=CANONICAL_COLUMNS)
    # flag (do not alter) total-basis poll-scenarios whose reported numbers sum above 101.5%
    tot = df[df["share_basis"] == "total"].groupby(["poll_id", "scenario"])
    total = tot["share_reported"].transform("sum") + tot["blank_null"].transform("first").fillna(0)
    over = total.index[total > 101.5]
    df.loc[over, "notes"] = df.loc[over, "notes"].map(lambda x: ";".join(filter(None, [x, "total_sum_gt_101.5"])))
    df["round"] = df["round"].astype(int)
    df["sample_size"] = df["sample_size"].astype(int)
    df = df.sort_values(["round", "field_end", "pollster", "scenario", "candidate"], kind="stable")
    df = df.reset_index(drop=True)
    df.attrs["skipped"] = skipped
    df.attrs["skipped_polls"] = skipped_polls
    return df


def parse_pt_reference(html: str) -> pd.DataFrame:
    """Poll list (round, pollster, field dates) of the PT page. No sample sizes there -> diagnostic only."""
    doc = _clean_doc(html)
    out = []
    round_ = 0
    for el in doc.iter():
        if el.tag == "h2":
            t = _text(el).lower()
            round_ = 1 if t.startswith("primeiro turno") else 2 if t.startswith("segundo turno") else 0
        elif el.tag == "table" and "wikitable" in (el.get("class") or "") and round_:
            grid = _grid(el)
            head = [_text(c).lower() for c in grid[0]]
            i_date = next(i for i, h in enumerate(head) if h.startswith("período"))
            i_inst = next(i for i, h in enumerate(head) if h.startswith("instituto"))
            for r in grid:
                if all(c.tag == "th" for c in r) or len(r) <= i_inst:
                    continue
                fs, fe = _pt_dates(_text(r[i_date]))
                out.append({"round": round_, "pollster": _pollster(_text(r[i_inst]))[0], "field_start": fs,
                            "field_end": fe})
    return pd.DataFrame(out)


def coverage_gaps(df: pd.DataFrame, ref: pd.DataFrame, tol_days: int = 1) -> pd.DataFrame:
    """Reference polls with no parsed poll of the same round+pollster whose field_end is within tol_days."""
    polls = df.drop_duplicates("poll_id")
    miss = []
    for _, r in ref.iterrows():
        cand = polls[(polls["round"] == r["round"]) & (polls["pollster"] == r["pollster"])]
        fe = date.fromisoformat(r["field_end"])
        if not any(abs(date.fromisoformat(x) - fe) <= timedelta(days=tol_days) for x in cand["field_end"]):
            miss.append(r)
    return pd.DataFrame(miss)


def main() -> None:
    frames = []
    for lang, title, oldid in SOURCES:
        html, rec = fetch_revision(lang, title, oldid)
        d = parse(html, rec)
        print(f"{lang}:{title}@{oldid}: {len(d)} rows; skipped={d.attrs['skipped']}")
        for s in d.attrs["skipped_polls"]:
            print("  skipped (no sample size):", s)
        frames.append(d)
    df = pd.concat(frames, ignore_index=True)
    problems = validate_polls(df)
    print("validate_polls:", problems or "OK")
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_PATH, index=False, encoding="utf-8")
    print(f"wrote {OUT_PATH} ({len(df)} rows)")

    polls = df.drop_duplicates("poll_id")
    for rnd in (1, 2):
        p = polls[polls["round"] == rnd]
        print(f"round {rnd}: rows={int((df['round'] == rnd).sum())} polls={len(p)} "
              f"pollsters={sorted(p['pollster'].unique())} range={p['field_start'].min()}..{p['field_end'].max()}")
    r1_late = polls[(polls["round"] == 1) & (polls["field_start"] >= "2014-08-20")]
    r2_late = polls[(polls["round"] == 2) & (polls["field_start"] >= "2014-10-05")]
    print(f"round 1 polls with field_start >= 2014-08-20: {len(r1_late)}"
          + ("  ** THIN (<10) **" if len(r1_late) < 10 else ""))
    print(f"round 2 polls with field_start >= 2014-10-05: {len(r2_late)}"
          + ("  ** THIN (<5) **" if len(r2_late) < 5 else ""))
    cols = ["pollster", "field_start", "field_end", "sample_size", "scenario", "candidate", "share_reported",
            "share_basis", "blank_null"]
    for rnd, lo in ((1, "2014-09-29"), (2, "2014-10-22")):
        last = df[(df["round"] == rnd) & (df["field_end"] >= lo) & (df["candidate"] != OTHERS)]
        print(f"final round-{rnd} polls (field_end >= {lo}):")
        print(last[cols].to_string(index=False))

    lang, title, oldid = COVERAGE_REFERENCE
    html, _ = fetch_revision(lang, title, oldid)
    ref = parse_pt_reference(html)
    gaps = coverage_gaps(df, ref)
    ref_late = ref[((ref["round"] == 1) & (ref["field_start"] >= "2014-08-20"))
                   | ((ref["round"] == 2) & (ref["field_start"] >= "2014-10-05"))]
    print(f"coverage reference {lang}:{title}@{oldid}: {len(ref)} polls listed, none with sample size "
          f"(all would be skipped); {len(ref_late)} in the late windows")
    if len(gaps):
        print(f"reference polls absent from the parsed source ({len(gaps)}):")
        print(gaps.sort_values(["round", "field_end"]).to_string(index=False))


if __name__ == "__main__":
    main()

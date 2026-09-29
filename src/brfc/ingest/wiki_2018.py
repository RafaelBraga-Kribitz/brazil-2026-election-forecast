"""Parse 2018 Brazilian presidential opinion-poll tables from a pinned Wikipedia PT revision.

Source: "Pesquisas de opinião para a eleição presidencial no Brasil em 2018", oldid 73055947.

Tables used (national, stimulated vote intention for president):
  * "Primeiro turno" > "Pesquisas"                    -> round 1
  * "Segundo turno" > "Depois do primeiro turno"      -> round 2 (Haddad x Bolsonaro)
  * "Segundo turno" > "Antes do primeiro turno"       -> round 2 (hypothetical pairings)
Exit polls ("Pesquisa boca-de-urna") and the reference rows with the 2014 election results are excluded.

Structure of the PT tables
  * Columns are party slots (PT, PDT, ...); each cell holds "22%" + "(Surname)". A poll with several
    scenarios spans several rows via rowspan on the date/pollster/sample cells; every row is one scenario.
  * "Abst./Não decid." is a single column that combines blank/null and undecided -> stored in
    `blank_null` with `undecided` NaN and note "blank_null_includes_undecided".
  * All shares are of total respondents (no "votos válidos" tables outside the exit polls) -> basis "total".

Scenario labels
  * Round 1: "with <PT candidate>" ("with Lula", "with Haddad", "with Haddad (backed by Lula)",
    "without PT", ...). When two scenarios of one poll share that label, the candidates that differ
    between them are appended ("with Lula + Doria, Temer"; "(core)" if the scenario has none of them).
  * Round 2: alphabetical surnames joined by " vs " ("Bolsonaro vs Haddad").
  * Any remaining collision gets " #k" (k = row order within the poll).

Methodology is taken from the article's "Metodologias" section (per-institute description of the
2018 cycle) and is only applied to polls fielded in 2018; otherwise empty.
"""

from __future__ import annotations

import copy
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from lxml import html as lxml_html

from brfc.ingest.wikipedia import fetch_revision
from brfc.schema import CANONICAL_COLUMNS, OTHERS, make_poll_id, validate_polls

PT_TITLE = "Pesquisas de opinião para a eleição presidencial no Brasil em 2018"
PT_OLDID = 73055947
ELECTION = "2018"
OUT_PATH = Path(__file__).resolve().parents[3] / "data" / "interim" / "polls_wiki_2018.csv"

MONTHS = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4, "maio": 5, "junho": 6,
    "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
}  # fmt: skip

# surname as printed in the PT tables -> canonical short name
CANDIDATES = {
    "Haddad": "Fernando Haddad",
    "Lula": "Lula",
    "Rousseff": "Dilma Rousseff",
    "Wagner": "Jaques Wagner",
    "Gomes": "Ciro Gomes",
    "Barbosa": "Joaquim Barbosa",
    "Silva": "Marina Silva",
    "Meirelles": "Henrique Meirelles",
    "Temer": "Michel Temer",
    "Dias": "Alvaro Dias",
    "Alckmin": "Geraldo Alckmin",
    "Doria": "João Doria",
    "Neves": "Aécio Neves",
    "Serra": "José Serra",
    "Virgílio": "Arthur Virgílio",
    "Amoêdo": "João Amoêdo",
    "Bolsonaro": "Jair Bolsonaro",
    "Huck": "Luciano Huck",
    "Moro": "Sergio Moro",
    "Boulos": "Guilherme Boulos",
    "Daciolo": "Cabo Daciolo",
}
# special (non-surname) labels: raw text -> (canonical candidate, short label, note)
SPECIAL_CANDIDATES = {
    "Haddad, apoiado por Lula": ("Fernando Haddad", "Haddad (backed by Lula)", "presented_as_backed_by_Lula"),
    "algum candidato apoiado por Lula": (
        "Lula-backed candidate (unnamed)",
        "unnamed Lula-backed candidate",
        "hypothetical_unnamed_candidate_backed_by_Lula",
    ),
}

# "Metodologias" section of the same revision (describes the 2018 cycle per institute)
METHODOLOGY_2018 = {
    "amostra": "face-to-face",
    "brasilis": "phone",
    "datafolha": "face-to-face",
    "datapoder360": "IVR",
    "fsb pesquisa": "phone",
    "ibope": "face-to-face",
    "ipespe": "phone",
    "mda": "face-to-face",
    "paraná pesquisas": "face-to-face",
    "vox populi": "face-to-face",
}
# names that appear on the pollster side of "Contratante/Instituto" but are contractors
KNOWN_CONTRACTORS = {"veja"}

# non-ASCII dashes seen in the tables: hyphen U+2010, non-breaking hyphen U+2011, en/em dash, minus sign
_UNI_DASHES = "".join(map(chr, (0x2010, 0x2011, 0x2013, 0x2014, 0x2212)))
DASHES = {"", "-", *_UNI_DASHES, "n/d", "n/a"}
DASH_CLASS = f"[{_UNI_DASHES}-]"
_FOOTNOTE = re.compile(r"\[(?:\d+|[a-zA-Z]|nota \d+)\]")


# ----------------------------------------------------------------------------- low-level helpers


def _text(el) -> str:
    """Cell text with footnote <sup> markers removed and whitespace normalised (newlines kept)."""
    el = copy.deepcopy(el)
    for sup in el.xpath('.//sup[contains(@class,"reference")]'):
        sup.drop_tree()
    for br in el.iter("br"):
        br.tail = "\n" + (br.tail or "")
    txt = _FOOTNOTE.sub("", el.text_content())
    txt = txt.replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in txt.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def _expand(table) -> list[list]:
    """Row-major grid of cell *elements* with rowspan/colspan expanded (same element repeated)."""
    grid: list[list] = []
    pending: dict[int, tuple[int, object]] = {}
    for tr in table.iter("tr"):
        cells = [c for c in tr if c.tag in ("td", "th")]
        out: list = []
        col = 0
        it = iter(cells)
        while True:
            if col in pending:
                rem, c = pending[col]
                out.append(c)
                if rem <= 1:
                    del pending[col]
                else:
                    pending[col] = (rem - 1, c)
                col += 1
                continue
            c = next(it, None)
            if c is None:
                break
            rs = int(re.sub(r"\D", "", c.get("rowspan", "1")) or 1)
            cs = int(re.sub(r"\D", "", c.get("colspan", "1")) or 1)
            for _ in range(cs):
                out.append(c)
                if rs > 1:
                    pending[col] = (rs - 1, c)
                col += 1
        grid.append(out)
    return grid


def _parse_share(raw: str) -> tuple[float | None, str | None, list[str]]:
    """'22%(Haddad)' -> (22.0, 'Haddad', notes). Returns (None, None, []) for dash/empty cells."""
    notes: list[str] = []
    s = raw.replace("\n", "").strip()
    if s in DASHES:
        return None, None, notes
    name = None
    m = re.search(r"\((.+)\)\s*$", s)
    if m:
        name = m.group(1).strip()
        s = s[: m.start()].strip()
    if s.endswith("ref"):  # stray link text left in one source cell ("20,2%ref(Alckmin)")
        s = s[:-3].strip()
        notes.append("stray_text_removed")
    s = s.rstrip("%").strip()
    if s in DASHES:
        return None, name, notes
    if re.fullmatch(r"\d+(,\d+)?", s):
        return float(s.replace(",", ".")), name, notes
    if re.fullmatch(r"\d+[;.]\d+", s):  # source typos "25;5%" / "27.2%"
        notes.append("decimal_separator_typo_in_source")
        return float(re.sub(r"[;.]", ".", s)), name, notes
    raise ValueError(f"unparseable share cell {raw!r}")


def _parse_int(raw: str) -> int | None:
    s = raw.strip().replace(" ", "")
    if re.fullmatch(r"\d{1,3}(\.\d{3})+|\d+", s):
        return int(s.replace(".", ""))
    return None


def _parse_dates(raw: str) -> tuple[str, str, list[str]]:
    """Portuguese field-period text -> (ISO start, ISO end, notes)."""
    notes: list[str] = []
    s = raw.replace("\n", " ").strip()
    s = re.sub(rf"\s*{DASH_CLASS}\s*", "-", s)
    windows = [w.strip() for w in re.split(r"\s+e\s+", s)]
    if len(windows) > 1:
        notes.append(f"multiple_field_windows:{s}")
    # propagate trailing month/year of the last window to earlier windows
    tail = re.search(r"(de \w+ de \d{4})$", windows[-1])
    endpoints: list[date] = []
    for w in windows:
        if not re.search(r"\d{4}$", w) and tail:
            w = f"{w} {tail.group(1)}"
        parts = w.split("-")
        parsed: list[list] = []
        for p in parts:
            m = re.fullmatch(r"(\d{1,2})(?:º)?(?: de (\w+))?(?: de (\d{4}))?", p.strip())
            if not m:
                raise ValueError(f"unparseable date {raw!r}")
            parsed.append([int(m.group(1)), m.group(2), m.group(3)])
        # fill missing month/year from the right
        for i in range(len(parsed) - 2, -1, -1):
            if parsed[i][1] is None:
                parsed[i][1] = parsed[i + 1][1]
            if parsed[i][2] is None:
                parsed[i][2] = parsed[i + 1][2]
        for d, mon, yr in parsed:
            if mon is None or yr is None or mon.lower() not in MONTHS:
                raise ValueError(f"unparseable date {raw!r}")
            endpoints.append(date(int(yr), MONTHS[mon.lower()], d))
    return min(endpoints).isoformat(), max(endpoints).isoformat(), notes


def _norm_tse(lines: list[str]) -> tuple[str, list[str]]:
    notes: list[str] = []
    txt = " ".join(lines)
    txt = re.sub(DASH_CLASS, "-", txt)
    ids = re.findall(r"BR\s*-?\s*(\d+)\s*/\s*(\d{4})", txt)
    out = [f"BR-{a}/{b}" for a, b in ids]
    if len(out) > 1:
        notes.append("multiple_tse_ids")
    if any(len(a) != 5 for a, _ in ids):
        notes.append("tse_id_nonstandard_length")
    return ";".join(out), notes


def _split_institute(raw: str) -> tuple[str, str, str, list[str]]:
    """'Globo e Folha/Datafolha\\nBR-01584/2018' -> (pollster, contractor, tse_id, notes)."""
    lines = raw.split("\n")
    name = lines[0].strip()
    tse, notes = _norm_tse(lines[1:])
    if "/" in name:
        contractor, pollster = (x.strip() for x in name.rsplit("/", 1))
        if pollster.lower() in KNOWN_CONTRACTORS:
            contractor, pollster = pollster, contractor
            notes.append("contractor_pollster_order_swapped_in_source")
    else:
        contractor, pollster = "", name
    return pollster, contractor, tse, notes


def _candidate(raw_name: str) -> tuple[str, str, list[str]]:
    """raw surname/label -> (canonical name, short label, notes)."""
    if raw_name in SPECIAL_CANDIDATES:
        canon, label, note = SPECIAL_CANDIDATES[raw_name]
        return canon, label, [note]
    if raw_name in CANDIDATES:
        return CANDIDATES[raw_name], raw_name, []
    return raw_name, raw_name, ["unmapped_candidate_name"]


def _classify_header(label: str) -> tuple[str, str | None]:
    """Header text -> (kind, candidate surname or None). kind in {cand, others, bn_combined, bn, und}."""
    lab = label.strip()
    low = lab.lower()
    if low.startswith("outros"):
        return "others", None
    if "abst" in low and "decid" in low:
        return "bn_combined", None
    if "branco" in low or "nulo" in low or low.startswith("nenhum"):
        return "bn", None
    if "não sabe" in low or "ns/nr" in low or "indecis" in low or "não decid" in low:
        return "und", None
    m = re.fullmatch(r"(.+?)\s*\(([^)]+)\)", lab)  # "Haddad (PT)"
    if m:
        return "cand", m.group(1).strip()
    return "cand", None  # party column; candidate comes from the cell


# ----------------------------------------------------------------------------- table selection


def _select_tables(doc) -> list[tuple[int, str, object]]:
    """Return [(round, section_label, table)] for the relevant wikitables, walking headings in order."""
    h2 = h3 = h4 = ""
    picked: list[tuple[int, str, object]] = []
    for el in doc.iter():
        if not isinstance(el.tag, str):
            continue
        if el.tag == "h2":
            h2, h3, h4 = el.text_content().strip(), "", ""
        elif el.tag == "h3":
            h3, h4 = el.text_content().strip(), ""
        elif el.tag == "h4":
            h4 = el.text_content().strip()
        elif el.tag == "table" and "wikitable" in (el.get("class") or ""):
            sub = h4 or h3
            if "boca" in sub.lower():  # exit polls
                continue
            if h2 == "Primeiro turno" and h3 == "Pesquisas":
                picked.append((1, "Primeiro turno/Pesquisas", el))
            elif h2 == "Segundo turno" and h4 in ("Depois do primeiro turno", "Antes do primeiro turno"):
                picked.append((2, f"Segundo turno/{h4}", el))
    return picked


# ----------------------------------------------------------------------------- main parse


def _scenario_labels(rnd: int, scen: list[dict]) -> list[str]:
    """Distinguishing scenario labels for the scenarios (list of dicts with 'cands', 'pt') of one poll."""
    if rnd == 2:
        base = [" vs ".join(sorted(s["labels"])) for s in scen]
    else:
        base = [f"with {s['pt']}" if s["pt"] else "without PT" for s in scen]
        counts = Counter(base)
        for lab, k in counts.items():
            if k < 2:
                continue
            idx = [i for i, b in enumerate(base) if b == lab]
            sets = [set(scen[i]["labels"]) for i in idx]
            common = set.intersection(*sets)
            for i, st in zip(idx, sets, strict=True):
                extra = sorted(st - common)
                base[i] = f"{lab} + {', '.join(extra)}" if extra else f"{lab} (core)"
    final = list(base)
    counts = Counter(base)
    for i, b in enumerate(base):
        if counts[b] > 1:
            final[i] = f"{b} #{i + 1}"
    return final


def parse(html: str, rec) -> pd.DataFrame:
    doc = lxml_html.fromstring(html)
    stats: Counter = Counter(exact_duplicate_scenarios_dropped=0)
    skipped: list[str] = []
    polls: dict[tuple, dict] = {}  # poll key -> {meta, scenarios:[...]}

    for rnd, section, table in _select_tables(doc):
        grid = _expand(table)
        header = list(grid[0])
        width = len(header)
        header_txt = [_text(c) for c in header]
        kinds = [_classify_header(h) for h in header_txt]
        for r_i, row in enumerate(grid[1:], start=1):
            if len(row) != width:
                stats["skipped_row_width_mismatch"] += 1
                skipped.append(f"{section} row {r_i}: width {len(row)} != {width}")
                continue
            if row[0].tag == "th" or all(_text(c) == "" for c in row):
                stats["header_or_colour_rows"] += 1
                continue
            if len({id(c) for c in row}) == 1:  # full-width note row
                stats["note_rows"] += 1
                continue
            txt = [_text(c) for c in row]
            if txt[1].startswith("Eleições de"):
                stats["reference_result_rows_excluded"] += 1
                continue
            notes: list[str] = []
            try:
                fs, fe, n_dates = _parse_dates(txt[0])
                notes += n_dates
                pollster, contractor, tse, n_inst = _split_institute(txt[1])
                notes += n_inst
                n = _parse_int(txt[2])
                if (n is None or n < 100) and (_parse_int(txt[3]) or 0) >= 100:
                    n = _parse_int(txt[3])  # sample size and margin of error swapped in source
                    notes.append("sample_size_margin_columns_swapped_in_source")
                if n is None:
                    raise ValueError(f"no sample size ({txt[2]!r})")
                cands: list[tuple[str, str, float, list[str]]] = []
                others = bn = und = np.nan
                bn_combined = False
                pt_label = None
                for j in range(4, width):
                    kind, head_name = kinds[j]
                    val, cell_name, cnotes = _parse_share(txt[j])
                    if val is None:
                        continue
                    if kind == "others":
                        others = val
                        notes += [f"others_{x}" for x in cnotes]
                    elif kind in ("bn_combined", "bn"):
                        bn, bn_combined = val, bn_combined or kind == "bn_combined"
                        notes += [f"blank_null_{x}" for x in cnotes]
                    elif kind == "und":
                        und = val
                        notes += [f"undecided_{x}" for x in cnotes]
                    else:
                        raw_name = cell_name or head_name
                        if not raw_name:
                            raise ValueError(f"candidate name missing in column {header_txt[j]!r}")
                        canon, label, n_c = _candidate(raw_name)
                        cands.append((canon, label, val, cnotes + n_c))
                        if header_txt[j] == "PT" or (head_name and header_txt[j].endswith("(PT)")):
                            pt_label = label
                if not cands:
                    raise ValueError("no candidate shares")
            except ValueError as exc:
                stats["skipped_unparseable"] += 1
                skipped.append(f"{section} row {r_i}: {exc} | {' || '.join(t[:30] for t in txt[:4])}")
                continue
            if bn_combined:
                notes.append("blank_null_includes_undecided")
            if fs[:4] == "2018" and pollster.lower() in METHODOLOGY_2018:
                methodology = METHODOLOGY_2018[pollster.lower()]
                notes.append("methodology_from_article_section")
            else:
                methodology = ""
            pid = make_poll_id(ELECTION, rnd, pollster, fs, fe, n)
            key = (pid,)
            meta = {
                "poll_id": pid, "round": rnd, "tse_br_id": tse, "pollster": pollster, "contractor": contractor,
                "field_start": fs, "field_end": fe, "sample_size": n, "methodology": methodology,
            }  # fmt: skip
            total = sum(c[2] for c in cands) + np.nansum([others, bn, und])
            if total > 101.5:
                notes.append("scenario_sum_exceeds_101.5_in_source")
            entry = polls.setdefault(key, {"meta": meta, "scenarios": [], "seen": set()})
            sig = (tuple(sorted((c[0], c[2]) for c in cands)), others, bn, und)
            if sig in entry["seen"]:  # exact repeat of a scenario already recorded for this poll
                stats["exact_duplicate_scenarios_dropped"] += 1
                continue
            entry["seen"].add(sig)
            entry["scenarios"].append(
                {
                    "cands": cands,
                    "labels": [c[1] for c in cands],
                    "pt": pt_label,
                    "others": others,
                    "bn": bn,
                    "und": und,
                    "notes": notes,
                }
            )
            stats[f"scenario_rows_round{rnd}"] += 1

    records: list[dict] = []
    for entry in polls.values():
        meta, scen = entry["meta"], entry["scenarios"]
        labels = _scenario_labels(meta["round"], scen)
        for s, lab in zip(scen, labels, strict=True):
            rows = [(c[0], c[2], c[3]) for c in s["cands"]]
            if not np.isnan(s["others"]):
                rows.append((OTHERS, s["others"], []))
            for cand, share, cnotes in rows:
                records.append(
                    dict(
                        **meta,
                        election=ELECTION,
                        publication_date="",
                        scenario=lab,
                        candidate=cand,
                        share_reported=share,
                        share_basis="total",
                        blank_null=s["bn"],
                        undecided=s["und"],
                        valid_vote_share=np.nan,
                        source_url=rec.url,
                        source_type="wikipedia_revision",
                        source_revision=str(rec.revision),
                        retrieval_timestamp=rec.retrieval_timestamp,
                        verification_status="unverified",
                        verification_source="",
                        source_hash=rec.sha256,
                        notes=";".join(dict.fromkeys(s["notes"] + cnotes)),
                    )
                )
    df = pd.DataFrame.from_records(records)
    if df.empty:
        df = pd.DataFrame(columns=CANONICAL_COLUMNS)
    df = df[CANONICAL_COLUMNS]
    before = len(df)
    df = df.drop_duplicates().reset_index(drop=True)
    stats["exact_duplicate_rows_dropped"] = before - len(df)
    df["round"] = df["round"].astype(int)
    df["sample_size"] = df["sample_size"].astype(int)
    df = df.sort_values(["round", "field_end", "pollster", "poll_id", "scenario", "candidate"], kind="stable")
    df = df.reset_index(drop=True)
    df.attrs["stats"] = dict(stats)
    df.attrs["skipped"] = skipped
    return df


# ----------------------------------------------------------------------------- CLI


def _print_poll(df: pd.DataFrame, pid: str, scenario: str | None = None) -> None:
    sub = df[df["poll_id"] == pid]
    if scenario is not None:
        sub = sub[sub["scenario"] == scenario]
    for scen, g in sub.groupby("scenario", sort=False):
        r = g.iloc[0]
        top = g.sort_values("share_reported", ascending=False)
        shares = ", ".join(f"{c} {v:g}" for c, v in zip(top["candidate"], top["share_reported"], strict=True))
        print(
            f"  {r['pollster']:<18} {r['field_start']}..{r['field_end']} n={r['sample_size']:<6} "
            f"tse={r['tse_br_id'] or '-':<14} [{scen}] {shares} | blank_null={r['blank_null']} "
            f"undecided={r['undecided']}"
        )


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    html, rec = fetch_revision("pt", PT_TITLE, PT_OLDID)
    df = parse(html, rec)
    problems = validate_polls(df)
    print(f"source: {rec.url}  sha256={rec.sha256[:12]}...  retrieved={rec.retrieval_timestamp}")
    print("parse stats:", df.attrs.get("stats"))
    for s in df.attrs.get("skipped", []):
        print("  SKIPPED:", s)
    print("validate_polls:", problems if problems else "OK (no problems)")
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_PATH, index=False, encoding="utf-8")
    print(f"wrote {len(df)} rows -> {OUT_PATH}")

    for rnd in (1, 2):
        d = df[df["round"] == rnd]
        print(
            f"round {rnd}: rows={len(d)} polls={d['poll_id'].nunique()} "
            f"poll-scenarios={d.groupby(['poll_id', 'scenario']).ngroups} pollsters={d['pollster'].nunique()} "
            f"field_end {d['field_end'].min()}..{d['field_end'].max()}"
        )
    print("final first-round polls (field_end >= 2018-10-01):")
    r1 = df[(df["round"] == 1) & (df["field_end"] >= "2018-10-01")]
    for pid in r1.sort_values("field_end", ascending=False)["poll_id"].unique():
        _print_poll(df, pid)
    print("final runoff polls (field_end >= 2018-10-20):")
    r2 = df[(df["round"] == 2) & (df["field_end"] >= "2018-10-20")]
    for pid in r2.sort_values("field_end", ascending=False)["poll_id"].unique():
        _print_poll(df, pid)


if __name__ == "__main__":
    main()

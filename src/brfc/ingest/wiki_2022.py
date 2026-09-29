"""Parse the 2022 Brazilian presidential vote-intention tables from a pinned PT Wikipedia revision.

Source: "Pesquisas de opinião para a eleição presidencial no Brasil em 2022", oldid 73055949.

Page layout (as of that revision) and how it is mapped to the canonical long schema:

  * "Primeiro turno" > <year> > <quarter/semester>  -> round 1, national stimulated scenarios, 2019-2022.
    Dates in these tables carry no year; the year comes from the enclosing section heading.
    The "Gráfico" data table and the "Agregação de pesquisas" table are skipped (not polls).
  * "Segundo turno" > one table per pairing        -> round 2, every pairing (dates carry the year).
  * Pollster cell: "Contractor/Pollster[ref]" plus, in 2022 round-1 tables, the TSE id "BR-xxxxx/2022".
  * Candidate columns are identified via the header's Wikipedia person link. A cell that names a person
    (e.g. "0% Bivar" under the UNIÃO column, "13%Haddad" under "Candidato PT", "2%Janones(AVANTE)" under
    "Outros") is attributed to that person; a bare number under "Outros" -> "__others__".
  * Minor candidates of one scenario (in "Outros", or several PSDB names under "Candidato PSDB") are
    sometimes given one sub-row each while the other cells of the scenario span those sub-rows (rowspan).
    Adjacent rows of the same poll that share the blank/undecided cell and at least half of the candidate
    cells (same spanned element) are therefore merged into one scenario (checked: such scenarios sum ~100).
  * The same poll can be listed in two adjacent period tables; identical scenarios are dropped, and scenario
    labels are assigned after merging (round 1: "main", or "with X"/"without Y"/"all listed candidates"
    relative to the poll's other scenarios, plus note "widest_scenario"; round 2: "A vs B" in column order).
  * "Indecisos e Absentos" (undecided + blank/null combined) -> `blank_null`, `undecided` NaN,
    note "blank_null_includes_undecided". No table on the page reports valid votes -> share_basis "total".
  * Event rows (a full-width cell announcing a withdrawal etc.) and election-result reference rows are
    not polls and are skipped (counted separately from unparseable poll rows).

No value is imputed: cells that cannot be parsed are skipped and counted in `df.attrs["parse_stats"]`.
"""

from __future__ import annotations

import copy
import re
from collections import Counter
from datetime import date
from pathlib import Path

import lxml.html
import pandas as pd

from brfc.schema import CANONICAL_COLUMNS, OTHERS, make_poll_id, validate_polls

PT_TITLE = "Pesquisas de opinião para a eleição presidencial no Brasil em 2022"
PT_OLDID = 73055949
ELECTION = "2022"
OUT_PATH = Path(__file__).resolve().parents[3] / "data" / "interim" / "polls_wiki_2022.csv"

# Wikipedia (PT) article title of the candidate -> canonical short name.
PERSON: dict[str, str] = {
    "Jair Bolsonaro": "Jair Bolsonaro",
    "Luiz Inácio Lula da Silva": "Lula",
    "Ciro Gomes": "Ciro Gomes",
    "Simone Tebet": "Simone Tebet",
    "Soraya Thronicke": "Soraya Thronicke",
    "Luiz Felipe d'Avila": "Felipe d'Avila",
    "José Maria Eymael": "José Maria Eymael",
    "Sofia Manzano": "Sofia Manzano",
    "Léo Péricles": "Léo Péricles",
    "Leonardo Péricles": "Léo Péricles",
    "Vera Lúcia Salgado": "Vera Lúcia",
    "Pablo Marçal": "Pablo Marçal",
    "Padre Kelmon": "Padre Kelmon",
    "Roberto Jefferson": "Roberto Jefferson",
    "Luciano Bivar": "Luciano Bivar",
    "André Janones": "André Janones",
    "João Doria": "João Doria",
    "Eduardo Leite": "Eduardo Leite",
    "Sergio Moro": "Sergio Moro",
    "Alessandro Vieira": "Alessandro Vieira",
    "Rodrigo Pacheco": "Rodrigo Pacheco",
    "José Luiz Datena": "José Luiz Datena",
    "Datena": "José Luiz Datena",
    "Luiz Henrique Mandetta": "Luiz Henrique Mandetta",
    "João Amoêdo": "João Amoêdo",
    "Luciano Huck": "Luciano Huck",
    "Guilherme Boulos": "Guilherme Boulos",
    "Flávio Dino": "Flávio Dino",
    "Marina Silva": "Marina Silva",
    "Fernando Haddad": "Fernando Haddad",
    "Joaquim Barbosa": "Joaquim Barbosa",
    "Luiza Trajano": "Luiza Trajano",
    "Luiza Helena Trajano": "Luiza Trajano",
    "Tasso Jereissati": "Tasso Jereissati",
    "Geraldo Alckmin": "Geraldo Alckmin",
    "Cabo Daciolo": "Cabo Daciolo",
    "Danilo Gentili": "Danilo Gentili",
    "Alexandre Kalil": "Alexandre Kalil",
    "Hamilton Mourão": "Hamilton Mourão",
    "Marcelo Ramos Rodrigues": "Marcelo Ramos",
    "Aldo Rebelo": "Aldo Rebelo",
    "Dilma Rousseff": "Dilma Rousseff",
    "Carlos Alberto dos Santos Cruz": "Santos Cruz",
    "Michel Temer": "Michel Temer",
    "Arthur Virgílio Neto": "Arthur Virgílio",
    "Wilson Witzel": "Wilson Witzel",
    "Romeu Zema": "Romeu Zema",
}

MONTHS = {"jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
          "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12}  # fmt: skip
EMPTY = {"", "\u2014", "\u2013", "-", "\u2212", "n/a", "N/A", "?"}  # em dash, en dash, hyphen, minus
DASH = "[\u2013\u2014-]"  # en dash, em dash, hyphen


# --------------------------------------------------------------------------------------------- text helpers
def _clean(el) -> str:
    """Visible text of a cell without footnote markers, hidden sort keys or print-only spans."""
    el = copy.deepcopy(el)
    for bad in el.xpath(
        ".//sup | .//style | .//*[contains(@class,'printfooter')] | .//*[contains(@class,'sortkey')]"
        " | .//*[contains(translate(@style,' ',''),'display:none')]"
    ):
        bad.drop_tree()
    s = re.sub(r"\[[^\]]*\]", "", el.text_content())  # any residual "[12]" / "[a]"
    return re.sub(r"\s+", " ", s).strip()


def _person(el) -> str | None:
    """Canonical candidate of the first link in `el` that points to a known person."""
    for a in el.iter("a"):
        title = (a.get("title") or "").removeprefix("Predefinição:Info/")
        if title in PERSON:
            return PERSON[title]
    return None


def _colspan(c) -> int:
    return int(re.sub(r"\D", "", c.get("colspan", "1")) or 1)


def _grid(table) -> list[list[tuple]]:
    """Expand row/colspans: grid[r][c] = (cell element, column offset inside the cell)."""
    rows = []
    for ch in table:
        if ch.tag == "tr":
            rows.append(ch)
        elif ch.tag in ("tbody", "thead", "tfoot"):
            rows.extend(r for r in ch if r.tag == "tr")
    grid, pending = [], {}
    for ri, r in enumerate(rows):
        line, ci, k = [], 0, 0
        cells = [c for c in r if c.tag in ("td", "th")]
        while k < len(cells) or (ri, ci) in pending:
            if (ri, ci) in pending:
                line.append(pending.pop((ri, ci)))
                ci += 1
                continue
            c = cells[k]
            k += 1
            rs = int(re.sub(r"\D", "", c.get("rowspan", "1")) or 1)
            for dc in range(_colspan(c)):
                line.append((c, dc))
                for dr in range(1, rs):
                    pending[(ri + dr, ci)] = (c, dc)
                ci += 1
        grid.append(line)
    return grid


def _headings(doc) -> dict:
    """table element -> list of enclosing section headings (h2..h5) in document order."""
    cur: dict[int, str] = {}
    out = {}
    for el in doc.iter():
        if el.tag in ("h2", "h3", "h4", "h5"):
            lvl = int(el.tag[1])
            cur = {k: v for k, v in cur.items() if k < lvl}
            cur[lvl] = re.sub(r"\s+", " ", el.text_content()).strip()
        elif el.tag == "table":
            out[el] = [cur[k] for k in sorted(cur)]
    return out


# ------------------------------------------------------------------------------------------- value parsers
_NUM = re.compile(r"^(\d+(?:[.,]\d+)?)\s*(%?)\s*(.*)$")


def _value(el) -> tuple[str, float | None, str, list[str]]:
    """-> (status, value, trailing text, notes); status in {"empty", "ok", "bad"}."""
    s = _clean(el)
    notes = []
    if s in EMPTY:
        return "empty", None, "", notes
    if "*" in s:
        notes.append("asterisk_in_source")
        s = s.replace("*", "").strip()
    if re.match(rf"^\d+(?:[.,]\d+)?\s*%?\s*{DASH}\s*\d", s):
        return "bad", None, s, [*notes, "range_in_source"]
    m = _NUM.match(s)
    if not m:
        return "bad", None, s, notes
    return "ok", float(m.group(1).replace(",", ".")), m.group(3).strip(), notes


def _sample(s: str) -> int | None:
    s = re.sub(r"\s+", "", s)
    if re.fullmatch(r"\d{1,3}(\.\d{3})+|\d+", s):
        return int(s.replace(".", ""))
    return None


_DPART = re.compile(r"^(\d{1,2})(?:\s+([A-Za-zÀ-ÿ]+)\.?)?(?:\s+(?:de\s+)?(\d{4}))?$")


def _month(tok: str | None) -> int | None:
    return MONTHS.get(tok[:3].lower()) if tok else None


def _dates(text: str, default_year: int | None) -> tuple[str, str, list[str]] | None:
    """'30 Set-01 Out' / '28-30 Set 2022' / '30-4 Set 2021' / '7 Out 2018' -> ISO (start, end, notes)."""
    s = re.sub(r"\s+", " ", text).strip()
    parts = [p.strip() for p in re.split(DASH, s)]
    if len(parts) not in (1, 2):
        return None
    ms = [_DPART.match(p) for p in parts]
    if not all(ms):
        return None  # e.g. month-only "Mai-Jun 2021"
    (d2, mon2, y2) = ms[-1].groups()
    (d1, mon1, y1) = ms[0].groups()
    notes: list[str] = []
    m2 = _month(mon2)
    if m2 is None:
        return None
    yr2 = int(y2) if y2 else default_year
    if yr2 is None:
        return None
    m1 = _month(mon1) if mon1 else None
    if mon1 and m1 is None:
        return None
    if m1 is None:
        m1 = m2
        if len(parts) == 2 and int(d1) > int(d2):  # "30-4 Set" == 30 Aug - 4 Sep
            m1 = m2 - 1 if m2 > 1 else 12
            notes.append("field_start_month_inferred_from_range")
    yr1 = int(y1) if y1 else (yr2 - 1 if m1 > m2 else yr2)
    try:
        start, end = date(yr1, m1, int(d1)), date(yr2, m2, int(d2))
    except ValueError:
        return None
    if start > end:
        return None
    return start.isoformat(), end.isoformat(), notes


_TSE = re.compile("BR\\s*[-\u2013]\\s*(\\d{4,6})\\s*/\\s*(\\d{4})")  # hyphen or en dash


def _pollster(el) -> tuple[str, str, str]:
    """-> (pollster, contractor, tse_br_id) from 'Globo/Datafolha[28] BR-00245/2022'."""
    s = _clean(el)
    m = _TSE.search(s)
    tse = f"BR-{m.group(1)}/{m.group(2)}" if m else ""
    s = _TSE.sub("", s)
    s = re.sub(r"[\s>?]+$", "", s).strip()
    if "/" in s:
        contractor, pollster = (x.strip() for x in s.rsplit("/", 1))
    else:
        contractor, pollster = "", s
    return pollster, contractor, tse


# --------------------------------------------------------------------------------------------- table model
def _column_kinds(grid: list, nh: int) -> list[tuple[str, str | None]]:
    """Per column: (kind, header candidate). kind in pollster/date/n/margin/cand/others/blank/lead."""
    width = len(grid[0])
    kinds = []
    for ci in range(width):
        cells = []
        for line in grid[:nh]:
            if ci < len(line) and line[ci][0] not in cells:
                cells.append(line[ci][0])
        head = " ".join(_clean(c) for c in cells)
        cand = next((p for p in (_person(c) for c in cells) if p), None)
        if ci == 0:
            kinds.append(("pollster", None))
        elif re.search(r"Data", head):
            kinds.append(("date", None))
        elif re.search(r"Amostra", head):
            kinds.append(("n", None))
        elif re.search(r"Margem", head):
            kinds.append(("margin", None))
        elif re.search(r"Vantagem", head):
            kinds.append(("lead", None))
        elif re.search(r"Indecis|Absent|Absten|Brancos|Nulos", head):
            kinds.append(("blank", None))
        elif re.search(r"^Outros", head):
            kinds.append(("others", None))
        else:
            kinds.append(("cand", cand))
    return kinds


def _r1_labels(scen_cands: list[list[str]]) -> list[str]:
    """Poll-local labels naming what distinguishes each scenario of a multi-scenario round-1 poll."""
    named = [[c for c in cs if c != OTHERS] for cs in scen_cands]
    sets = [set(c) for c in named]
    inter = set.intersection(*sets)
    order: list[str] = []
    for cs in named:
        order += [c for c in cs if c not in order]
    labels = []
    for cs, st in zip(named, sets, strict=True):
        extra = [c for c in cs if c not in inter]
        missing = [c for c in order if c not in st]
        if not missing:
            lab = "all listed candidates"
        elif extra and len(extra) <= len(missing):
            lab = "with " + ", ".join(extra)
        else:
            lab = "without " + ", ".join(missing)
        labels.append(lab)
    seen: Counter = Counter()
    out = []
    for lab in labels:
        seen[lab] += 1
        out.append(lab if labels.count(lab) == 1 else f"{lab} (#{seen[lab]})")
    return out


def parse(html: str, rec) -> pd.DataFrame:
    doc = lxml.html.fromstring(html)
    heads = _headings(doc)
    stats: Counter = Counter()
    skipped: list[str] = []  # poll rows that could not be parsed (for the log)
    polls: list[dict] = []  # one dict per poll-scenario (before labelling)

    for table in doc.iter("table"):
        if "wikitable" not in (table.get("class") or ""):
            continue
        ctx = heads.get(table, [])
        if "Primeiro turno" in ctx:
            round_ = 1
        elif "Segundo turno" in ctx:
            round_ = 2
        else:
            continue
        grid = _grid(table)
        if not grid:
            continue
        nh = 0
        for line in grid:
            if all(c.tag == "th" for c, _ in line):
                nh += 1
            else:
                break
        head0 = _clean(grid[0][0][0])
        if not re.search(r"Instituto|Pesquisa|Contratante|Publica", head0) or "Agregador" in head0:
            stats["tables_skipped_not_poll_list"] += 1
            continue
        kinds = _column_kinds(grid, nh)
        if [k for k, _ in kinds[:3]] != ["pollster", "date", "n"]:
            raise ValueError(f"unexpected table layout under {ctx}: {kinds[:4]}")
        default_year = None
        if round_ == 1:
            yrs = [h for h in ctx if re.fullmatch(r"\d{4}", h)]
            default_year = int(yrs[-1]) if yrs else None
        stats["tables_parsed"] += 1
        table_no = stats["tables_parsed"]
        table_polls: list[dict] = []
        prev_line = None
        for line in grid[nh:]:
            if any(c.tag == "td" and _colspan(c) >= 4 for c, _ in line):
                stats["event_rows_skipped"] += 1
                prev_line = None
                continue
            if _clean(line[0][0]).lower().startswith("eleição"):
                stats["election_result_rows_skipped"] += 1
                prev_line = None
                continue
            if len(line) < len(kinds):
                stats["poll_rows_skipped_short"] += 1
                skipped.append(" | ".join(_clean(c) for c, _ in line))
                prev_line = None
                continue
            pollster, contractor, tse = _pollster(line[0][0])
            d = _dates(_clean(line[1][0]), default_year)
            n = _sample(_clean(line[2][0]))
            if not pollster or d is None or n is None:
                stats["poll_rows_skipped_unparseable_date_or_n"] += 1
                skipped.append(f"R{round_} {pollster} | {_clean(line[1][0])} | {_clean(line[2][0])}")
                prev_line = None
                continue
            field_start, field_end, date_notes = d
            key = (round_, pollster, contractor, field_start, field_end, n)

            # Sub-row of the previous scenario? Same poll, the blank/undecided cell is the same (rowspan) cell,
            # and at least half of the candidate cells are spanned from the previous row. Only the cells that
            # differ (typically "Outros" or a "Candidato PSDB" column) then add further named candidates.
            merge = False
            if prev_line is not None and table_polls and table_polls[-1]["key"] == key:
                cand_cols = [i for i, (k, _) in enumerate(kinds) if k == "cand"]
                blank_cols = [i for i, (k, _) in enumerate(kinds) if k == "blank"]
                shared = sum(line[i][0] is prev_line[i][0] for i in cand_cols)
                blank_same = all(line[i][0] is prev_line[i][0] for i in blank_cols)
                merge = blank_same and shared > 0 and 2 * shared >= len(cand_cols)
                if merge:
                    stats["scenario_subrows_merged"] += 1
            if merge:
                scen = table_polls[-1]
            else:
                scen = {
                    "key": key, "tse": tse, "cands": {}, "order": [], "blank": None, "notes": list(date_notes),
                    "seen_cells": set(),
                }  # fmt: skip
                table_polls.append(scen)
            prev_line = line

            for ci, (kind, hcand) in enumerate(kinds):
                if kind not in ("cand", "others", "blank"):
                    continue
                cell = line[ci][0]
                if id(cell) in scen["seen_cells"]:
                    continue  # spanned cell already read for this scenario
                scen["seen_cells"].add(id(cell))
                status, val, rest, vnotes = _value(cell)
                if status == "empty":
                    continue
                if status == "bad":
                    stats["values_skipped_unparseable"] += 1
                    skipped.append(f"value R{round_} {pollster} {field_end} col={hcand or kind}: {rest!r}")
                    scen["notes"] += vnotes
                    continue
                scen["notes"] += vnotes
                if val > 100:  # e.g. "244%" (source typo): not a percentage, do not guess the intended value
                    stats["values_skipped_out_of_range"] += 1
                    scen["notes"].append(
                        f"{'blank_null' if kind == 'blank' else 'candidate'}_value_{val:g}_in_source_dropped"
                    )
                    continue
                if kind == "blank":
                    scen["blank"] = val
                    continue
                name = _person(cell) if rest else None
                if kind == "others" and name is None:
                    if rest and not re.fullmatch(r"(?i)outros?", rest):
                        stats["values_skipped_unknown_name"] += 1
                        skipped.append(f"value R{round_} {pollster} {field_end}: unknown name {rest!r}")
                        continue
                    name = OTHERS
                elif kind == "cand":
                    if rest and name is None:
                        stats["values_skipped_unknown_name"] += 1
                        skipped.append(f"value R{round_} {pollster} {field_end}: unknown name {rest!r}")
                        continue
                    name = name or hcand
                    if name is None:
                        stats["values_skipped_unnamed_column"] += 1
                        scen["notes"].append(f"unattributed_value_{val:g}_in_source_dropped")
                        skipped.append(f"value R{round_} {pollster} {field_end}: {val}% in a column with no candidate")
                        continue
                if name in scen["cands"]:
                    if scen["cands"][name] != val:
                        scen["notes"].append(f"conflicting_values_for_{name}_kept_first")
                    stats["duplicate_candidate_in_scenario"] += 1
                    continue
                scen["cands"][name] = val
                scen["order"].append(name)

        for sc in table_polls:
            sc["table"] = table_no
        polls.extend(table_polls)

    # Merge listings of the same poll (a poll can sit in two adjacent period tables, possibly with its candidate
    # columns in a different order). Identical scenario content -> dropped; a round-2 pairing listed twice with
    # different numbers -> first kept and flagged. Labels are assigned afterwards, over the merged scenario set.
    by_poll: dict[tuple, list[dict]] = {}
    dups: list[str] = []
    for sc in polls:
        if not sc["cands"]:
            stats["poll_rows_skipped_no_values"] += 1
            continue
        sc["sig"] = (tuple(sorted(sc["cands"].items())), sc["blank"])
        sc["pair"] = tuple(c for c in sc["order"] if c != OTHERS)
        lst = by_poll.setdefault(sc["key"], [])
        desc = f"R{sc['key'][0]} {sc['key'][1]} {sc['key'][3]}..{sc['key'][4]} {'/'.join(sc['pair'])}"
        if any(o["sig"] == sc["sig"] for o in lst):
            stats["duplicate_scenarios_dropped_identical"] += 1
            dups.append(desc)
            continue
        if sc["key"][0] == 2 and (first := next((o for o in lst if o["pair"] == sc["pair"]), None)):
            first["notes"].append("duplicate_listing_conflict_kept_first")
            stats["duplicate_scenarios_dropped_conflicting"] += 1
            dups.append(desc + " (CONFLICT)")
            continue
        if sc["key"][0] == 1 and lst and sc["table"] not in {o["table"] for o in lst}:
            stats["scenarios_added_from_second_table"] += 1
            dups.append(desc + " (extra scenario from another table)")
        lst.append(sc)

    polls = []
    for key, scs in by_poll.items():
        if key[0] == 2:
            labels = [" vs ".join(sc["pair"]) for sc in scs]
        elif len(scs) == 1:
            labels = ["main"]
        else:
            labels = _r1_labels([sc["order"] for sc in scs])
            widest = max(range(len(scs)), key=lambda i: len(scs[i]["pair"]))
            scs[widest]["notes"].append("widest_scenario")
        for sc, lab in zip(scs, labels, strict=True):
            sc["scenario"] = lab
            polls.append(sc)

    rows = []
    for sc in polls:
        round_, pollster, contractor, fs, fe, n = sc["key"]
        pid = make_poll_id(ELECTION, round_, pollster, fs, fe, n)
        total = sum(sc["cands"].values()) + (sc["blank"] or 0.0)
        notes = ["blank_null_includes_undecided", *sc["notes"]]
        if total > 101.5 or total < 95:
            notes.append(f"source_total_{total:.1f}")
        for cand in sc["order"]:
            rows.append({
                "poll_id": pid, "election": ELECTION, "round": round_, "tse_br_id": sc["tse"],
                "pollster": pollster, "contractor": contractor, "field_start": fs, "field_end": fe,
                "publication_date": "", "sample_size": n, "methodology": "", "scenario": sc["scenario"],
                "candidate": cand, "share_reported": sc["cands"][cand], "share_basis": "total",
                "blank_null": sc["blank"], "undecided": float("nan"), "valid_vote_share": float("nan"),
                "source_url": rec.url, "source_type": "wikipedia_revision", "source_revision": str(rec.revision),
                "retrieval_timestamp": rec.retrieval_timestamp, "verification_status": "unverified",
                "verification_source": "", "source_hash": rec.sha256,
                "notes": ";".join(dict.fromkeys(notes)),
            })  # fmt: skip
    df = pd.DataFrame(rows, columns=CANONICAL_COLUMNS)

    # dedupe (same poll listed twice, e.g. at a table boundary): keep first, flag disagreements
    key = ["poll_id", "scenario", "candidate"]
    dup = df.duplicated(key, keep=False)
    if dup.any():
        for _, grp in df[dup].groupby(key, sort=False):
            vals = grp[["share_reported", "blank_null"]].astype(float).round(3).drop_duplicates()
            if len(vals) > 1:
                i = grp.index[0]
                df.loc[i, "notes"] = df.loc[i, "notes"] + ";duplicate_listing_conflict_kept_first"
                stats["duplicate_rows_dropped_conflicting"] += len(grp) - 1
            else:
                stats["duplicate_rows_dropped_identical"] += len(grp) - 1
        df = df[~df.duplicated(key, keep="first")].reset_index(drop=True)

    df["round"] = df["round"].astype(int)
    df["sample_size"] = df["sample_size"].astype(int)
    for c in ("share_reported", "blank_null", "undecided", "valid_vote_share"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    df.attrs["parse_stats"] = dict(stats)
    df.attrs["skipped_rows"] = skipped
    df.attrs["duplicate_listings"] = sorted(set(dups))
    return df


def main() -> None:
    from brfc.ingest.wikipedia import fetch_revision

    html, rec = fetch_revision("pt", PT_TITLE, PT_OLDID)
    df = parse(html, rec)
    print(f"rows={len(df)}  polls={df['poll_id'].nunique()}  pollsters={df['pollster'].nunique()}")
    for k, v in df.attrs.get("parse_stats", {}).items():
        print(f"  {k}: {v}")
    for s in df.attrs.get("skipped_rows", []):
        print(f"  skipped: {s}")
    for s in df.attrs.get("duplicate_listings", []):
        print(f"  duplicate listing: {s}")
    problems = validate_polls(df)
    print("validate_polls:", problems if problems else "OK")
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_PATH, index=False, encoding="utf-8")
    print(f"wrote {OUT_PATH}")


if __name__ == "__main__":
    main()

# Data sources

Every external observation carries its source URL, revision or version, retrieval timestamp (UTC), source type,
verification status and a SHA-256 hash. The machine-readable lock of every source revision used is
[`data/SOURCES.lock.json`](data/SOURCES.lock.json).

## Register

| Role | Source | What is used | Access | Status |
| --- | --- | --- | --- | --- |
| Poll toplines, primary (2018, 2022, 2026) | Wikipedia PT "Pesquisas de opinião para a eleição presidencial no Brasil em {year}" | National stimulated first-round and runoff tables | MediaWiki API, pinned `oldid` | Parsed: 2018 oldid 73055947, 2022 oldid 73055949, 2026 oldid 73082572 (refreshed at the freeze) |
| Poll toplines, gap-filler | Wikipedia EN "Opinion polling for the {year} Brazilian presidential election" | Conflict check (2018, 2022, 2026); gap-fill (2026) | MediaWiki API, pinned `oldid` | 2026 oldid 1377453715: 0 gap-fills, 22 recorded conflicts ([`conflicts_wiki_2026.csv`](data/interim/conflicts_wiki_2026.csv)) |
| Poll toplines, 2014 | Wikipedia EN "2014 Brazilian general election", section "Opinion polls" | First round and runoff | MediaWiki API, oldid 1369923991 | The only 2014 source with sample sizes; it lacks 12 late first-round polls that the PT page lists without sample sizes |
| Final-poll verification | Pollster releases as published by the contracting outlet (G1, Agência Brasil, Correio Braziliense, Estadão Conteúdo, CNN Brasil, Poder360, Terra, Exame) | Final Datafolha and Ibope/Ipec polls in all 6 historical rounds; Quaest and AtlasIntel in 2022 | Article text, URL per row | [`final_poll_verification.csv`](data/manual/final_poll_verification.csv). 71/71 checkable values match Wikipedia within 0.5 pp. Two missing final polls were added (Addendum 02) |
| Spot-check (10% random sample) | Same kinds of release sources | 29 seeded-random polls from the modelled windows | Article text / pollster PDF | [`spot_check_results.csv`](data/manual/spot_check_results.csv) |
| Official results, history | Secondary sources citing the TSE: Wikipedia EN results tables (2014 oldid 1369923991; 2018 oldid 1361748195; 2022 oldid 1376389749) and electionresources.org | National votes per candidate, both rounds | Pinned revisions | [`results_secondary.csv`](data/manual/results_secondary.csv). Votes sum to the valid total in all 6 rounds. **Reconciled against the TSE files: all 41 candidate vote counts match exactly** |
| Official results, authoritative | TSE Portal de Dados Abertos, "Votação nominal por município e zona" | Same national totals | **Manual browser download**: TSE endpoints return HTTP 403 to scripts, and this project does not work around that | Downloaded 2026-09-29 (`votacao_candidato_munzona_{2014,2018,2022}.zip`; SHA-256 in [`outputs/readiness.json`](outputs/readiness.json)). Reconciliation: [`outputs/tse_reconciliation_{year}.csv`](outputs/), 0 differences |
| Poll registration metadata (optional) | TSE PesqEle / "Pesquisas Eleitorais 2026" | BR-ID, sample size, dates, contractor | Manual browser download (403 to scripts) | Optional. PesqEle holds registration metadata, not toplines |
| 2026 ballot | Wikipedia PT "Eleição presidencial no Brasil em 2026", oldid 73078529 | 13 registered candidates; one revoked candidacy | Pinned revision | [`ballot_2026.csv`](data/manual/ballot_2026.csv) |
| Benchmark A | PollingData (pollingdata.com.br) | Published average at the freeze | Page snapshot at the freeze: timestamp, values, institutes, methodology note, archive URL | At freeze. Described only as far as its own methodology page documents |
| Market benchmark | Polymarket "Brazil presidential election" | Price at the freeze | Market ID, exact question, price, volume, UTC timestamp, archive URL | At freeze. A market price, not ground truth |
| Not used | Poder360 Agregador (login-gated), Base dos Dados (needs a GCP project), electionsBR (R) | — | — | Excluded from the pipeline |

## Revision identity

A Wikipedia revision is identified by its `oldid` and by MediaWiki's SHA-1 of the revision wikitext
(`revision_sha1` in `data/SOURCES.lock.json`), which never changes. The `sha256` / `source_hash` values are hashes of
the rendered HTML as retrieved. MediaWiki re-renders pages as templates and its parser change, so a later fetch of
the same revision can differ in bytes: the clean-clone test found this for three of the five pinned revisions.
`scripts/reparse_sources.py` (`make reparse`) therefore checks the wikitext SHA-1 against the API, re-parses the
pinned revisions and compares every data value with the committed tables. All values were identical.

## Conflict rule

Original pollster release > Wikipedia PT > Wikipedia EN. Conflicting values are recorded, never averaged.

## Redistribution and licensing

- **Wikipedia text** is CC BY-SA 4.0. The derived poll tables in `data/interim/` hold factual poll figures extracted
  from pinned revisions. They are shared under CC BY-SA 4.0 with attribution to the Wikipedia revisions listed in
  `data/SOURCES.lock.json`.
- **Raw page HTML and TSE files are not redistributed.** `data/raw/` is git-ignored; the acquisition instructions,
  expected filenames and hashes make them reproducible.
- **Pollster releases are cited by URL only.** No PDF or article text is stored.
- **No personal data.** The tables hold aggregate poll shares and public candidate names only.

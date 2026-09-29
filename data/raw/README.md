# Raw inputs (not redistributed)

This folder is git-ignored apart from this file. Its contents are re-created as follows.

## `wikipedia/`: created automatically
`brfc.ingest.wikipedia.fetch_revision` downloads each pinned revision through the MediaWiki API. It writes
`{lang}_{oldid}.html` plus a `.provenance.json` holding the URL, oldid, retrieval time and SHA-256. The revisions
used are listed in `data/SOURCES.lock.json`.

## `tse/`: manual browser download (optional; used for reconciliation)
TSE servers return HTTP 403 to scripted requests. Download these files **in a browser**:

| Expected filename | Where |
| --- | --- |
| `votacao_candidato_munzona_2014.zip` | https://dadosabertos.tse.jus.br/ → "Resultados 2014" → "Votação nominal por município e zona" |
| `votacao_candidato_munzona_2018.zip` | same, "Resultados 2018" |
| `votacao_candidato_munzona_2022.zip` | same, "Resultados 2022" |
| `pesquisas_eleitorais_2026*.zip` / `.csv` (optional) | https://dadosabertos.tse.jus.br/dataset/pesquisas-eleitorais-2026 → "pesquisas" |

Save them here without renaming the files, then run `python scripts/check_readiness.py`. It records each file's
SHA-256 in `outputs/readiness.json`. It also writes `outputs/tse_reconciliation_{year}.csv`, which compares the TSE
national totals with `data/manual/results_secondary.csv`.

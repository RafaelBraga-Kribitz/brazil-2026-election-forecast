# Poll spot-check: provenance verification (Agent A, 2026-09-29)

Sample: 29 polls (seeded 10%). 316 fields checked (dates, n, top-4 shares or both runoff candidates, plus minor candidates and blank_null wherever the source printed them). Row-level detail and URLs are in `spot_check_results.csv`.
Sources: 28 media reports and 1 pollster release (Nexus). Wikipedia was not used. G1, Estadão, UOL and R7 are blocked for the fetcher, and poder360.com.br returned 403, so reports syndicated elsewhere were used instead.

## Overall
- Fields found: 304/316. Match: 302/304 = **99.3%**. Conflict: 2 (both field dates of one Veritá poll). not_found: 12.
- Polls: 26 fully verified, 1 partly verified, 1 not found, 1 with a conflict. No share value conflicted.

## Per poll
| poll_id | pollster | status |
|---|---|---|
| 2014-1-d0f3f6f476 / b45eee4d99 / cd84978c85 / 3d4612918e | Ibope, Datafolha, Ibope/CNI, Ibope | verified |
| 2014-2-1475c049f4 / 74a4e84908 | Datafolha (runoff) | verified |
| 2018-1-9b9dddb2f8 / bc22fac03b | Ibope, Datafolha | verified |
| 2018-1-2be066d000 | Real Time Big Data | partial: dates, n, Bolsonaro and Haddad verified; Ciro, Alckmin and blank_null are only in image tables or on unreachable pages |
| 2022-1-b0e4d26a69 / 1b8b76875a / aac99ab2b0 / 260f2716be / be5b3a9427 / 4f7f9f09cd / 4322ca8a47 / 70c5bcdcd9 | Datafolha, AtlasIntel, Ipespe x2, MDA, Ideia, PoderData, FSB | verified |
| 2022-1-635b1dc0e4 | Veritá (24–29 Sep 2022, n=51,169) | **not found**: no release or media report found |
| 2022-2-8d7b718e89 / 137249edbc / 91ae9b1b6d | AtlasIntel, Brasmarket, Futura (runoff) | verified |
| 2026-1-40855b367a / 4dfdd759f8 / 235486b0b9 / d10f3a0f3c / fb0ad4acc8 / 7a0844b9da / f25b09cd46 | Nexus, Datafolha, Quaest, PoderData x2, Nexus, Datafolha | verified |
| 2026-1-de343fc6e2 | Veritá (4–12 Sep 2026, n=40,500) | **conflict** (dates) |

## Conflicts and systematic issues
1. **Veritá 2026 (de343fc6e2).** The parsed shares (43.9 / 41.5 / others 14.5) match the Folha de Patrocínio report (43.95 / 41.51 / 14.54). That report dates the fieldwork to 6–11 Sep. Classe Política gives 4–12 Sep, but with different shares (45.73 / 41.56). The parsed row seems to combine the dates of one Veritá release with the values of another. The three shares also add up to 100.00 with no blank/null/undecided, so the basis is probably valid-vote-equivalent even though it is not labelled. The model treats it as total-respondent.
2. **Veritá overall.** One Veritá poll could not be found and the other has inconsistent metadata. Veritá describes its 2026 figure as an "integrated estimate of 27 UFs" with no national margin of error. Recommend a manual PesqEle/TSE check (done by a person, since TSE blocks scripts) or down-weighting Veritá until then.
3. **blank_null definition.** The parsed blank_null always equals brancos/nulos + undecided. That holds for 2014 as well, not only 2018/2022/2026. For FSB 2022 and Brasmarket 2022 it also includes "won't vote / none" (5 and 2.3 pp). This is consistent across rows, but it should be documented in the codebook.
4. **Brasmarket 2022 runoff.** Brasmarket ran two overlapping polls: 21–25 Oct (48.0 / 41.5, BR-08584/2022) and 23–27 Oct (48.2 / 42.7, BR-07309/2022). The parse matches the later poll. Check the de-duplication and house-effect logic so that both are not double-counted.
5. **Secondary outlets disagree.** For Datafolha 18–20 Aug 2026, a gazetaweb headline gives Zema 4 and tvtnews gives blank/null 8 + undecided 3, while the full Gazeta do Povo table gives 3 and 6 + 4 (and matches the parse). When outlets disagree, prefer full tables.
6. **Rounding.** Most sources print whole numbers, and the parse matches them exactly. The only sub-0.5 pp difference is Veritá 2026, where the parse holds 43.9 and the source 43.95.

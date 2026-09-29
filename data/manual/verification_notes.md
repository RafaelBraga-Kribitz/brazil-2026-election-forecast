# Ground truth + final-poll verification (Agent C, retrieved 2026-09-29 UTC)

## What was found
- `results_secondary.csv` (41 rows): every candidate on the ballot, all 6 rounds (2014/2018/2022 x R1/R2). Candidate
  votes, % and valid totals come from pinned en.wikipedia oldids 1369923991 (2014, table cites Election Resources),
  1361748195 (2018, cites TSE) and 1376389749 (2022, cites TSE). Revision ids came from the MediaWiki API.
  Blank/null split: 2022 from the same en table; 2018 from pt.wiki oldid 73064323; 2014 from electionresources.org.
- `final_poll_verification.csv` (108 rows, 16 polls): the final pre-election national stimulated poll of Datafolha and
  Ibope/Ipec for all 6 rounds, plus Quaest and AtlasIntel for 2022 R1/R2. Every value was read in the cited article
  text (G1, Agencia Brasil, Correio Braziliense, Estadao Conteudo/Seu Dinheiro, CNN Brasil, Poder360, Terra, Exame).

## Validation performed
- For every round, candidate votes sum exactly to total_valid_votes. Published 2-decimal shares match votes/total
  within 0.005 pp and sum to 100.00 (2014 R1: 100.01, a rounding effect).
- blank+null equals en.wiki's combined "Invalid/blank votes" for 2014 and 2018. It equals the separate en rows for 2022.
- Poll valid shares sum to 98-100. 2014 R1 Datafolha: the four "0%*" names add up to 1%. 2022 R1 Datafolha lists
  valid shares only for the top 5; per G1/JN, "outros" add up to 2%.

## Discrepancies between sources (unresolved; TSE files are needed to settle them)
- 2014 R1: en.wiki/Election Resources give valid 104,023,802, blank 4,420,489, null 6,678,592. pt.wiki (oldid
  73048880) gives valid 104,023,543, blank 4,420,488, null 6,678,580, and its valid total does not match its own
  candidate rows.
- 2018 R1: pt.wiki lists Bolsonaro at 49,276,990 (plus 746 "pending" votes). en.wiki and Election Resources list
  49,277,010, which sums to the valid total. We use 49,277,010.
- Sample sizes: Datafolha 2018 R1 is 19,552 per Agencia Brasil and Correio, but 17,056 per Gazeta do Povo (same TSE
  id). Datafolha 2018 R2 is 18,371 per Agencia Brasil and Poder360, but 18,731 on the en.wiki polling page (a typo).
- Field dates: Ibope 2014 R1 ran 2-4 Oct per G1 (3-4 on pt.wiki). Ibope 2018 R1 ran 5-6 Oct per Agencia Brasil
  (4-6 per Gazeta). Ipec 2022 R1 ran 29 Sep-1 Oct per G1 (25 Sep-1 Oct on pt.wiki).
- Rounding: UOL gives Quaest 24-27 Sep Bolsonaro as both 36.3 and 36.2 valid. That poll is only the penultimate one.
- Ipec 2022 R2: G1 prints the TSE id as "BR-05256/12" (a typo), recorded verbatim.
- The Quaest final R1 poll (30 Sep-1 Oct, n=3,600, BR-02444/2022) is missing from both pinned Wikipedia polling
  pages. Those pages stop at 24-27 Sep.

## Gaps
- No national AtlasIntel poll for 2018 was found, either on the pinned 2018 en/pt polling pages or by web search.
  It is left out.
- Quaest 2022 R1 final: the article text gives only valid shares. Totals, blank/null and undecided shares are in the
  PDF report (static.poder360.com.br/2022/10/quaest-eleicoes-1-out-relatorio.pdf), which could not be read here.
- AtlasIntel publishes blank/null and don't-know as one combined figure (put in blank_null_pct; undecided left blank).
- The 2022 R2 Datafolha source is Terra, because the G1/Folha original was not found. None of the polls came from a
  pollster's own site (datafolha.folha.uol.com.br etc. were not checked). TSE was not used (403 to scripts).

## Error table: final Datafolha valid share minus official valid share (pp)
These are COMPUTED values: `valid_share_pct` (final_poll_verification.csv) minus `valid_vote_share_pct`
(results_secondary.csv). The poll figures are integer-rounded, so each error carries about +/-0.5 pp of rounding.

| Round | Candidate: poll - official = error |
|---|---|
| 2014 R1 | Dilma 44-41.59=+2.41; Aecio 26-33.55=-7.55; Marina 24-21.32=+2.68 |
| 2014 R2 | Dilma 52-51.64=+0.36; Aecio 48-48.36=-0.36 |
| 2018 R1 | Bolsonaro 40-46.03=-6.03; Haddad 25-29.28=-4.28; Ciro 15-12.47=+2.53; Alckmin 8-4.76=+3.24 |
| 2018 R2 | Bolsonaro 55-55.13=-0.13; Haddad 45-44.87=+0.13 |
| 2022 R1 | Lula 50-48.43=+1.57; Bolsonaro 36-43.20=-7.20; Tebet 6-4.16=+1.84; Ciro 5-3.04=+1.96 |
| 2022 R2 | Lula 52-50.90=+1.10; Bolsonaro 48-49.10=-1.10 |

Other pollsters, computed the same way: in 2022 R1 the Bolsonaro error was -6.20 for Ipec, -5.20 for Quaest and
-2.10 for AtlasIntel. In 2022 R2 the Lula error was +3.10 for Ipec, +1.10 for Quaest and +2.50 for AtlasIntel. Ibope
had Aecio at -6.55 in 2014 R1 and Bolsonaro at -5.03 in 2018 R1. Descriptive pattern only: in each cycle the final
Datafolha R1 poll under-stated one major candidate by 6-8 pp (Aecio 2014, Bolsonaro 2018 and 2022). Its final runoff
polls were within 1.1 pp of the result.

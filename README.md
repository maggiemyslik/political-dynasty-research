# POLITICAL DYNASTY RESEARCH

## Purpose
This repository builds two linked datasets on the UK House of Commons: a per-MP measure of political dynasties from 1832 to the present, and a record of how each MP voted in every recorded division from 1909 to 2003. Both use the same MP identifier, `politician_id` (a Wikidata item id), so voting behaviour can be analysed against family ties.

## Status
Complete, apart from matching the remaining unmatched names by hand, which has not been started (`manual_checks/manual_matches.csv` is empty).

| | Count |
|---|---|
| Divisions | 23,323 (1909-02-16 to 2003-11-06) |
| Printed names scraped | 8,368,166 |
| Votes in `votes_final.csv.gz` | 8,276,184 (98.9% of printed names) |
| Unmatched names, left out | 81,438 rows (28,120 distinct names) |
| Dropped: same MP on both sides of a division | 5,329 rows (2,650 cases) |
| Dropped: same MP twice on one side | 5,215 rows |
| MPs in the votes missing from the relationships file | 0 |

## Final datasets
Both are in `final/` and join on `politician_id`.

| File | Contents |
|---|---|
| `relationships_final.csv`, `relationships_final.dta` | One row per MP, all 10,508 Members since 1832: whether they had a relative who also sat as an MP, how close, and who. Copies of `data/dynasties/output/mp_relatives_coded.csv` and `.dta`; fields in the MP dynasties section below. |
| `votes_final.csv.gz` | One row per MP per division, 1909 to 2003: how they voted, with the division's details. Gzip-compressed CSV; fields below. |

`votes_final.csv.gz` is read directly by pandas (`pd.read_csv("final/votes_final.csv.gz")`) and R (`readr::read_csv`); in Stata, unzip it and use `import delimited`. There is no .dta version: at 8 million rows it would be several GB, and writing it needs more memory than a laptop has. It is too large for GitHub and for email, so share it by a cloud link (OneDrive, Google Drive); `final/votes_final.csv.gz` is gitignored.

### votes_final
| Field | Description |
|---|---|
| `division_id` | `S<series>CV<volume>-<date>-<division number>`, e.g. `S5CV0001-1909-02-16-1`. |
| `series`, `volume` | Hansard series (5 or 6) and volume. |
| `date` | Sitting date. |
| `division_number` | As printed; resets each session. |
| `time` | Time of the division, where printed. |
| `column_start` | Hansard column where the division starts. |
| `debate_title` | Heading of the debate. |
| `question_text` | The question put, where printed. |
| `ayes_declared`, `noes_declared` | Printed totals, excluding tellers; blank if not found. |
| `ayes_extracted`, `noes_extracted` | Names recovered per side, excluding tellers. |
| `division_flags` | Extraction warnings for the division (see [code/role_call_votes/README.md](code/role_call_votes/README.md)). |
| `politician_id` | The MP; join key to `relationships_final.csv`. |
| `mp_name` | The MP's name on Wikidata. |
| `constituency` | The MP's seat on that date. |
| `vote` | `aye` or `no`. |
| `is_teller` | 1 if the MP was a teller (counted the votes; not in the printed totals). |
| `raw_name` | The name exactly as printed. |
| `confidence` | Match score, 0.60 to 1. |
| `method` | Evidence for the match (see [code/name_resolution/README.md](code/name_resolution/README.md)). |

Each MP appears at most once per division. Where the same MP was matched twice on one side, the more confident match is kept; where they were matched on both sides (a fault in the source or the scrape), the side is unknown and both rows are dropped. Unmatched names are left out. Everything that needs checking by hand, including names matched by hand, is in `manual_checks/` (see [manual_checks/README.md](manual_checks/README.md)).

Presence is not preference: a division list records only MPs who voted, so an MP missing from a division may have been absent, abstaining or paired.

## Projects
| Project | Documentation |
|---|---|
| MP dynasties | [code/dynasties/README.md](code/dynasties/README.md) |
| Role call votes: data collection | [code/role_call_votes/README.md](code/role_call_votes/README.md) |
| Role call votes: name resolution | [code/name_resolution/README.md](code/name_resolution/README.md) |
| Manual checks | [manual_checks/README.md](manual_checks/README.md) |

## Repository layout
| Folder | Contents |
|---|---|
| `code/dynasties/` | Dynasty coding pipeline |
| `code/role_call_votes/` | Hansard scraper and its checks |
| `code/name_resolution/` | MP roster download, name resolution and its checks |
| `code/build_final.py` | Builds both final datasets |
| `data/` | Inputs and intermediate outputs of each stage (not pushed to GitHub) |
| `final/` | The two final datasets |
| `manual_checks/` | Unmatched names and MPs, dropped duplicates, low-confidence matches, problem divisions, and the file for matching names by hand |

## Running the scripts
Run everything from the repository root with the virtual environment active (`source .venv/bin/activate`). No script takes flags or parameters unless stated in its documentation.

1. Dynasties: `python code/dynasties/mp_relatives_pipeline.py`, then `python code/dynasties/fetch_rush_relationships.py`, then `python code/dynasties/merge_sources.py`
2. Scrape: `python code/role_call_votes/hansard_scraping.py` (several hours; resumes if interrupted)
3. MP roster: `python code/name_resolution/download_mps.py`
4. Names: `python code/name_resolution/resolve_names.py`
5. Final datasets and manual checks: `python code/build_final.py`

Checks for each stage are in each folder's README.

# MP DYNASTIES

## Purpose
For every Member of the UK House of Commons from 1832 to the present, this project codes whether they had a relative who also served in Parliament, and where they did, how close the tie was and whether there was more than one. It combines a Wikidata-derived biographical panel with the Rush database of MPs into a single per-MP dataset, a consistent long-run measure of political dynasties in the Commons. Full method, sources and limitations: [code/dynasties/README.md](code/dynasties/README.md).

## Scripts
- `mp_relatives_pipeline.py`: load, clean, collapse, build the Wikidata graph, code the first-pass fields, merge the bundled Rush cousins; writes `mp_relatives_coded.csv` (one row per MP) and `mp_relationship_edges.csv` (one row per directed relationship, labelled by relation and source)
- `fetch_rush_relationships.py`: pulls all 18 Rush relation types, reads each edge from both members' perspectives, restores gendered relations, codes the Rush fields, merges by `rushid`
- `merge_sources.py`: combines the relationship data from the two sources into one output

## Output: mp_relatives_coded
`mp_relatives_coded.csv` (and an identical Stata copy, `mp_relatives_coded.dta`) in `data/dynasties/output/`, one row per MP:

| Field | Description |
|---|---|
| `politician_id` | Unique MP identifier (Wikidata item id), carried from the source panel. |
| `politician_name` | MP's name. |
| `rushid` | The MP's id in the Rush database; blank where the MP is not matched to Rush. |
| `has_relative` | 1 if the MP had at least one relative who also served as an MP, else 0. |
| `n_relatives_detected` | Number of distinct relative-MPs identified. |
| `multiple_relatives` | 1 if `n_relatives_detected` is greater than 1, else 0. |
| `closeness` | Closest relationship type among the MP's relatives; blank when `has_relative` is 0. Ordered nearest to furthest: parent, child, sibling; spouse, civil partner, partner; grandparent, grandchild, aunt/uncle, niece/nephew; cousin and the great- variants; the great-great- variants; then `indirect` for ties Rush records without a specific type. |
| `relation_types` | Every distinct relationship type linking the MP to a relative, semicolon-separated. |
| `relative_chamber` | Chamber of the identified relatives. "HoC" throughout, since every coded relative is itself an MP; relatives who sat only in the Lords are not captured. Blank when there is no relative. |
| `peerage_linked_flag` | 1 if the MP had an aristocratic or peerage connection of their own. Flags the shortlist for the outstanding Commons-versus-Lords work; it is not a relative's chamber. |
| `relative_sources` | Where the MP's links came from: `panel`, `rush`, or both, semicolon-separated. |
| `relative_examples` | Names of the relative-MPs, semicolon-separated. |

## Coverage
- **Sources:** Wikidata (the MP universe and parent-child links) and the Rush database (History of Parliament Trust, CC BY 4.0), matched by `rushid`, for typed relationships across its eighteen categories.
- One row per MP for all 10,508 Members of the House of Commons, 1832 to the present.
- 2,890 MPs (27%) had at least one relative who also served as an MP; 1,469 had more than one.
- Every relative in the file is itself an MP. Relatives who sat only in the Lords are not covered; `peerage_linked_flag` marks the shortlist for that dimension.
- The counts are a lower bound: only ties between two MPs appear.

# ROLE CALL VOTES

## Purpose
For every division (recorded vote) in the House of Commons from 1909 to 2003, this project records how each individual MP voted. It scrapes the printed division lists from the digitised Hansard archive, then resolves each printed name to a `politician_id`. Names were only printed in division lists from 1909, which sets the start of coverage; the archive's 6th series stops at volume 412 (November 2003). Full method, outputs, checks and known problems: [data collection](code/role_call_votes/README.md) and [name resolution](code/name_resolution/README.md).

## Coverage
- 23,323 divisions, 1909-02-16 to 2003-11-06, from 1,209 Hansard volumes (219 volumes are missing from the archive).
- 8.37 million printed names, about 99% matched to an MP.
- Printed totals found for 92% of divisions; 84% of 5th series (1909 to 1981) and 95% of 6th series divisions are within 2 names of them on both sides.
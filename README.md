# POLITICAL DYNASTY RESEARCH

## Purpose
This repository builds two linked datasets on the UK House of Commons: a per-MP measure of political dynasties from 1832 to the present, and a member-level record of how each MP voted in every recorded division from 1909 to 2004. Both use the same MP identifier, `politician_id` (a Wikidata item id), so voting behaviour can be analysed against family ties.

## Projects
| Project | Status | Documentation |
|---|---|---|
| MP dynasties | Complete: one row per MP for all 10,508 Members since 1832 | [dynasties.md](dynasties.md) |
| Role call votes | Scraper complete, full run in progress; name resolution written, not yet run | [role_call_votes.md](role_call_votes.md) |

## Repository layout
| Folder | Contents |
|---|---|
| `code/dynasties/` | Dynasty coding pipeline |
| `code/role_call_votes/` | Hansard scraper and its checks |
| `code/name_resolution/` | MP roster download, name resolution and its checks |
| `data/dynasties/` | Dynasty inputs and outputs (`output/mp_relatives_coded.csv`) |
| `data/role_call_votes/` | Scraped division lists (`divisions_raw.csv`) |
| `data/name_resolution/` | MP roster and resolved names |

Data folders are not pushed to GitHub: `divisions_raw.csv` alone is about 2 GB, beyond the 100 MB file limit.

## Running the scripts
Run everything from the repository root with the virtual environment active (`source .venv/bin/activate`). No script takes flags or parameters unless stated in its documentation.

Dynasties:
1. `python code/dynasties/mp_relatives_pipeline.py`
2. `python code/dynasties/fetch_rush_relationships.py`
3. `python code/dynasties/merge_sources.py`

Role call votes:
1. `python code/role_call_votes/hansard_scraping.py`
2. `python code/name_resolution/download_mps.py`
3. `python code/name_resolution/resolve_names.py`

Checks for each stage are listed in [role_call_votes.md](role_call_votes.md).

# MP DYNASTIES

## Purpose
For every Member of the UK House of Commons from 1832 to the present, this project codes whether they had a relative who also served in Parliament, and where they did, how close the tie was and whether there was more than one. It combines a Wikidata-derived biographical panel with the Rush database of MPs into a single per-MP dataset, a consistent long-run measure of political dynasties in the Commons. Full method, sources and limitations: [dynasties.md](dynasties.md).

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
For every division (recorded vote) in the House of Commons from 1909 to 2004, this project records how each individual MP voted. It scrapes the printed division lists from the digitised Hansard archive, then resolves each printed name to a `politician_id`, so the voting record joins directly to `mp_relatives_coded.csv`. Names were only printed in division lists from 1909, which sets the start of coverage. Full method, outputs, checks and known problems: [role_call_votes.md](role_call_votes.md).

## Stages
1. **Scraping** (`code/role_call_votes/`): downloads each Hansard volume, extracts every division and writes one row per name per division, names stored exactly as printed (`divisions_raw.csv`).
2. **Name resolution** (`code/name_resolution/`): downloads a roster of MP seat spells from Wikidata and matches each printed name to the MP sitting on that date (`names_resolved.csv`, `names_unmatched.csv`).

## Current status (19 October 2026)
- A full re-scrape of both Hansard series with the latest parser is running. The previous 5th series run (older parser) produced 16,812 divisions and 5.8 million name rows, with 75% of divisions within 2 names of the printed totals.
- The 6th series (1981 to 2004) is being scraped for the first time.
- Name resolution is written but has not been run. A cross-check against the Rush database is planned after the first run.
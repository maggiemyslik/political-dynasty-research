# MP DYNASTIES RESEARCH
## Purpose
For every Member of the UK House of Commons from 1832 to the present, this project codes whether they had a relative who also served in Parliament, and where they did, how close the tie was and whether there was more than one. It combines a Wikidata-derived biographical panel with the Rush database of MPs into a single per-MP dataset, a consistent long-run measure of political dynasties in the Commons.

## Running the Scripts 
- `mp_relatives_pipeline.py: load, clean, collapse, build the Wikidata graph, code the first-pass fields, merge the bundled Rush cousins; writes `mp_relatives_coded.csv` (one row per MP) and `mp_relationship_edges.csv` (one row per directed relationship, labelled by relation and source)
- `fetch_rush_relationships.py`: pulls all 18 Rush relation types, reads each edge from both members' perspectives, restores gendered relations, codes the Rush fields, merges by `rushid`.

## Outputs  
### mp_relatives_coded

The output is:
`mp_relatives_coded.csv`(and an identical stata copy `mp_relatives_coded.dta`) in `data/dynasties/output/` 
 
It contains one row per MP with the folloing feilds:

| Field | Description |
|---|---|
| `politician_id` | Unique MP identifier, carried from the source panel. |
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

# ROLE CALL VOTES
[purpose]

## Running the Scripts 
- `mp_relatives_pipeline.py: load, clean, collapse, build the Wikidata graph, code the first-pass fields, merge the bundled Rush cousins; writes `mp_relatives_coded.csv` (one row per MP) and `mp_relationship_edges.csv` (one row per directed relationship, labelled by relation and source)
- `fetch_rush_relationships.py`: pulls all 18 Rush relation types, reads each edge from both members' perspectives, restores gendered relations, codes the Rush fields, merges by `rushid`
-`merge_sources.py`: combines the relationship data from the two souces into one output

### Coverage 

**Sources:**
- **Wikidata** the MP universe (every Member of the House of Commons, 1832 to the present) and the parent-child links between them.
- **Rush database** (History of Parliament Trust, CC BY 4.0), matched by `rushid` typed relationships across its eighteen categories: siblings, cousins, aunts and uncles, nieces and nephews, grandparents and grandchildren, spouses and partners, the great- and great-great- variants, and a catch-all "indirect".

Resulting Dataset: 
- One row per MP for all 10,508 Members of the House of Commons, 1832 to the present.
- 2,890 MPs (27%) had at least one relative who also served as an MP; 1,469 had more than one.
- The origin of each MP's links is recorded in `relative_sources` (`panel`, `rush`, or both).
- Relationship detail runs from immediate kin (parent, child, sibling) out through grandparents, aunts and uncles, cousins, and the great- and great-great- variants, to marriage ties; `closeness` gives the nearest and `relation_types` lists all of them.
- Every relative in the file is itself an MP, so `relative_chamber` is House of Commons throughout. Relatives who sat only in the House of Lords, meaning peers who were never MPs, are not covered; `peerage_linked_flag` marks the MPs with an aristocratic or peerage connection as the shortlist for that dimension.
- The counts are a lower bound: only ties between two MPs appear, so a relationship to anyone who never sat as an MP is absent rather than recorded as missing.

# ROLE CALL VOTES

## Purpose
For every division (recorded vote) in the UK House of Commons from 1909 to the present, this project codes how each individual Member voted. It extracts the printed division lists from the digitised Hansard XML and resolves each printed name to an MP identifier, producing a member-level voting record that joins to the dynasties dataset on `member_id`. Individual names are only printed in Hansard from 1909; before that the Official Report gives aye and no totals alone, which sets the start of coverage.

## Running the Scripts
- `hansard_scraping.py`: downloads each Hansard volume zip, extracts every division from every sitting day, and records one row per name-in-division with the name exactly as printed; writes `divisions_raw.csv`
- `hansard_resolve.py`: parses each printed name into surname, initials, honorific, rank and constituency, matches it against a spell-level roster of MPs, and applies the one-member-per-division constraint; writes `divisions_resolved.csv` (one row per member-division) and `unresolved.csv` (one row per name it declined to assign)

`hansard_resolve.py` requires `member_spells.csv`, a roster of MP service spells derived from Rush, with fields `member_id`, `surname`, `forenames`, `constituency`, `start`, `end`, `party`, one row per member per seat per continuous period of service.

## Outputs

### divisions_raw

`divisions_raw.csv` in `data/role_call_votes/`

One row per name-in-division, names verbatim and unparsed:

| Field | Description |
|---|---|
| `division_id` | Unique division identifier: series, volume, sitting date and division number. |
| `series` | Hansard series: 5 (1909 to March 1981) or 6 (March 1981 onward). |
| `volume` | Hansard volume number. |
| `sitting_file` | Source XML file within the volume archive. |
| `date` | Date of the sitting. |
| `division_number` | Division number as printed. Resets each session, so not unique on its own. |
| `time` | Time the division was called, where printed. |
| `column_start` | Hansard column at which the division begins. |
| `debate_title` | Heading of the enclosing debate. |
| `question_text` | The motion actually put, where printed. |
| `ayes_declared` | Aye total as declared by the tellers and printed by Hansard. |
| `noes_declared` | No total as declared. |
| `ayes_extracted` | Number of aye names recovered from the printed list, tellers included. |
| `noes_extracted` | Number of no names recovered. |
| `side` | `aye` or `no`. |
| `is_teller` | 1 where the name appears as a teller rather than in the lists. Tellers are votes. |
| `raw_name` | The name exactly as printed, no cleaning. |
| `division_flags` | Extraction problems, semicolon-separated: `no_date`, `no_name_lists`, `no_declared_totals`, `aye_mismatch`, `no_mismatch`. |
| `source_url` | Volume archive the row was extracted from. |

Declared and extracted totals are held separately on every row. The gap between them is the extraction error rate, measured division by division rather than estimated.

### divisions_resolved

`divisions_resolved.csv` in `data/role_call_votes/`

One row per member-division. Carries every field from `divisions_raw` except `sitting_file` and `source_url`, and adds:

| Field | Description |
|---|---|
| `member_id` | The MP's identifier in the roster, and the join key to the dynasties dataset; blank where the name could not be resolved. |
| `surname` | Surname as printed, particles retained. |
| `initials` | Initials as printed, concatenated. |
| `forenames` | Spelled-out forenames, where printed rather than initialled. |
| `honorific` | `Rt. Hon.`, `Sir`, `Mr.` and the like. A weak signal of office or title held. |
| `rank` | Military or professional rank, common from 1918 to 1955. |
| `constituency` | Constituency, where Hansard printed one to distinguish members sharing a surname. |
| `match_confidence` | Confidence in the assignment, 0 to 1. |
| `match_method` | Evidence the assignment rests on: `initials`, `initial_prefix`, `first_initial`, `forename`, `forename_prefix`, `surname`, any of these with `+seat`, `+unique_surname`, or `propagated`. |
| `candidates` | Number of roster spells sharing the surname and sitting on the date. |
| `name_flags` | Name parsing problems: `no_comma`, `no_forename`, `forename_particle`, `teller_forename_first`, `unparsed`. |

### unresolved

`unresolved.csv` in `data/role_call_votes/`

One row per name the resolver declined to assign, with the three best roster candidates and their scores, for manual adjudication. Names are left unassigned rather than guessed at where two or more sitting members fit the printed evidence equally well, or where no roster surname matches at all.

### Coverage

**Sources:**
- **Hansard Digitisation Project** (UK Parliament, Parliamentary copyright), the XML behind Historic Hansard: Commons 5th series volumes 1 to 1000 (January 1909 to March 1981) and 6th series from March 1981. One archive per volume, one file per sitting day.
- **Rush database** (History of Parliament Trust, CC BY 4.0), reshaped to spell level, supplying the MP universe, service dates and constituencies against which printed names are resolved.

Resulting Dataset:
- One row per member-division for divisions from 1909 onward, keyed to `member_id` and joinable to `mp_relatives_coded.csv`.
- Divisions before 1909 are out of scope: Hansard printed totals only, without names.
- Every row records both the printed totals and the number of names recovered, so extraction loss is visible per division rather than aggregate.
- Tellers are included as votes and flagged, since they are counted in the declared totals and omitting them puts every affected division two votes short.
- Divisions whose name lists could not be located are retained as a single flagged row, so they stay in the denominator instead of disappearing.
- Presence is not preference: a division list records only Members who voted. Abstention, absence, pairing and the Speaker's non-voting role are indistinguishable in the source, so a member's absence from a list is not evidence of a position.
- Resolution is a lower bound. Names Hansard printed ambiguously and names damaged by OCR are sent to `unresolved.csv` rather than assigned, so unmatched rows are recorded as unresolved rather than mis-attributed.
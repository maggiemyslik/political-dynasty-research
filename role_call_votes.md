# ROLE CALL VOTES

## Purpose
For every division (recorded vote) in the UK House of Commons from 1909 to the present, this project codes how each individual Member voted. It extracts the printed division lists from the digitised Hansard XML and resolves each printed name to an MP identifier, producing a member-level voting record that joins to the dynasties dataset on `member_id`. Individual names are only printed in Hansard from 1909; before that the Official Report gives aye and no totals alone, which sets the start of coverage.

## Running the Scripts
- `hansard_scraping.py`: downloads each Hansard volume zip, extracts every division from every sitting day, and records one row per name-in-division with the name exactly as printed; writes `divisions_raw.csv`
- `name_processing.py`: parses each printed name into surname, initials, honorific, rank and constituency, matches it against a spell-level roster of MPs, and applies the one-member-per-division constraint; writes `divisions_resolved.csv` (one row per member-division) and `unresolved.csv` (one row per name it declined to assign)

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

Resulting Dataset:
- One row per member-division for divisions from 1909 onward, keyed to `member_id` and joinable to `mp_relatives_coded.csv`.
- Divisions before 1909 are out of scope: Hansard printed totals only, without names.
- Every row records both the printed totals and the number of names recovered, so extraction loss is visible per division rather than aggregate.
- Tellers are included as votes and flagged, since they are counted in the declared totals and omitting them puts every affected division two votes short.
- Divisions whose name lists could not be located are retained as a single flagged row, so they stay in the denominator instead of disappearing.
- Presence is not preference: a division list records only Members who voted. Abstention, absence, pairing and the Speaker's non-voting role are indistinguishable in the source, so a member's absence from a list is not evidence of a position.
- Resolution is a lower bound. Names Hansard printed ambiguously and names damaged by OCR are sent to `unresolved.csv` rather than assigned, so unmatched rows are recorded as unresolved rather than mis-attributed.
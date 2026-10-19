# ROLE CALL VOTES

## Purpose
For every division (recorded vote) in the UK House of Commons from 1909 to 2004, this project records how each individual MP voted. It scrapes the printed division lists from the digitised Hansard archive, stores every name exactly as printed, then resolves each name to a `politician_id` (a Wikidata item id), the same identifier as the dynasties dataset. Names were only printed in division lists from 1909; before that the Official Report gives totals alone.

## Current status (19 October 2026)
- **Scraping:** a full re-scrape of both series with the latest parser started on 19 October 2026. Its results have not yet been checked. The parser has been tested on 12 real divisions from 1909 to 1980, but not on a whole volume, and not on the 6th series.
- **Name resolution:** written, not yet run. The roster comes from Wikidata; a cross-check against the Rush database is planned once the first results are in.

## Source: the Hansard archive
- UK Parliament's Hansard archive of digitised (OCR) debates, `https://www.hansard-archive.parliament.uk`, one zip of XML per volume.
- 5th series: volumes 1 to 1000, January 1909 to March 1981. 6th series: volumes 1 to 428, March 1981 to December 2004.
- About 165 of the 1,000 5th series volumes are missing from the archive. The archive answers a missing volume, and one past the end of a series, with the same HTML page (never a 404), so the last volume of each series is a fixed number rather than detected.
- Each zip held one XML file in every volume inspected. Section titles and printed column numbers are marked as elements.

### How a division is printed
- **Header** on its own line (`Division No. 1.]`), usually followed by the time.
- **Totals** (`The House divided` followed by `Ayes, 221; Noes, 142.`) printed before the header, after it, or mid-list, depending on the era. Totals exclude the four tellers.
- **Lists** headed `AYES.` and `NOES.` (the 1980 layout has no `AYES` heading), one name per line as `Surname, Forenames (Constituency)`, printed in three columns from 1909 to the 1970s and two in 1980, read across the page.
- **Tellers**, two per side, after each list (`TELLERS FOR THE AYES.` followed by `Mr. A and Mr. B.`), set in one column so the text appears every second or third line.
- Lists were typeset as blocks at the top of a page, so debate text can sit in the middle of a list. Nothing reliably marks the end of a division.

## Running the scripts
Run from the repository root with the virtual environment active. No flags or parameters except where shown.

### Scraping (`code/role_call_votes/`)
- `hansard_scraping.py`: walks every volume of both series, parses each in memory (zips are not stored), appends rows to `divisions_raw.csv` and logs each finished or absent volume in `divisions_raw_done.txt`. Resumes by itself: rows from a volume interrupted part-way are removed and redone, finished volumes are never fetched again, and failed downloads are retried during the run and once more at the end. The last line reads `All volumes done.` or lists volumes that still failed; re-running fetches only those.
- `summarise.py`: one-pass summary of `divisions_raw.csv`: completeness by series, size, data quality, impossible results, agreement with printed totals by series and decade, and the 10 worst divisions. Usage: `python code/role_call_votes/summarise.py > summary.txt`
- `check_volume.py`: downloads one volume and prints each division's extracted and printed counts, tellers and flags, with the lines the parser rejected in divisions more than 2 names short. Usage: `python code/role_call_votes/check_volume.py 5 864`. With a division number as a third argument (`5 268 289`) it writes that division's raw text to `dump_S5_vol268_div289.txt` instead.

### Name resolution (`code/name_resolution/`)
- `download_mps.py`: downloads every MP seat spell overlapping 1909 to 2004 from Wikidata and writes `mp_spells.csv`. Prints counts of MPs and spells, missing dates and constituencies, and how many MPs are missing from `mp_relatives_coded.csv`.
- `resolve_names.py`: matches every name in `divisions_raw.csv` to an MP; writes `names_resolved.csv` and `names_unmatched.csv`. One pass, no resuming needed.
- `check_names.py`: checks the resolution (see Checks). Usage: `python code/name_resolution/check_names.py > name_check.txt`

## Method

### Scraping
1. The XML is flattened to lines in document order, with titles and column numbers marked.
2. Division headers are found at the start of a line; OCR spellings (`Divison`, `N0.`) are accepted. Each division runs to the next header.
3. Two totals candidates are taken, the last before the header and the first after it; the one closer to the number of names counted is used. Totals above 700 (the Commons never exceeded 707 seats) are treated as OCR errors and left blank.
4. Each side's list is collected line by line. Constituencies printed on their own line are rejoined to their name; column numbers, time stamps and prose (speaker labels, speeches, tables) are skipped rather than ending the list, so lists interrupted by debate continue. The Noes end at the first prose line after their tellers.
5. Tellers are separated from names using the column count (2 or 3, worked out from the alphabetical jumps between lines).
6. Names are counted per side, compared with the printed totals and flagged where they disagree or where the result is impossible.

### Name resolution
1. **Roster:** every MP who held a "member of the nth Parliament" position overlapping 1909 to 2004, one row per seat spell with start and end dates and constituency. Matching uses every MP in the roster, not only those in the dynasty file, so an MP missing from that file cannot have their votes pushed onto someone else.
2. **Parsing:** list entries are read surname first (`Abraham, William (Rhondda)`), tellers forename first (`Mr. A. Smith`). Titles and ranks are dropped; the constituency in brackets is kept.
3. **Candidates:** only MPs sitting on the date of the division. Surnames are compared in a normalised form (accents removed; M', Mc and Mac treated alike); if no surname matches exactly, OCR-damaged spellings are matched by similarity (0.85 or above). Peerage titles are also matched, so `Viscount Valentia` finds Arthur Annesley.
4. **Scoring:** a surname match scores 0.60 (0.45 if fuzzy). Matching forenames or initials add up to 0.25 and conflicting initials subtract 0.25; a matching constituency adds 0.25 (abbreviations such as `B'ham` are allowed) and a conflicting one subtracts 0.15; being the only MP with that surname sitting adds 0.20.
5. **Decision:** the best candidate is accepted if it scores at least 0.60 and beats the runner-up by at least 0.10. Otherwise the name stays unmatched, with its reason and top candidates recorded. These thresholds are starting values, to be tuned from the check output.

## Outputs

### divisions_raw
`divisions_raw.csv` in `data/role_call_votes/`, one row per name per division (tellers included), names verbatim with OCR errors. Division-level fields repeat on every row; a division with no names keeps one row with blank name fields.

| Field | Description |
|---|---|
| `division_id` | `S<series>CV<volume>-<date>-<division number>`, e.g. `S5CV0001-1909-02-16-1`. |
| `series`, `volume` | Archive series (5 or 6) and volume. |
| `sitting_file` | XML file within the volume zip. |
| `date` | Sitting date, ISO format; blank if not found. |
| `division_number` | As printed. Resets each session, so not unique on its own. |
| `time` | Time of the division, where printed. |
| `column_start` | Last printed column number before the header. |
| `debate_title` | Last section title before the division. |
| `question_text` | The question put, where printed. |
| `ayes_declared`, `noes_declared` | Printed totals, excluding tellers; blank if not found or impossible. |
| `ayes_extracted`, `noes_extracted` | Names recovered per side, excluding tellers. |
| `side` | `aye` or `no`. |
| `is_teller` | 1 for tellers, 0 otherwise. |
| `raw_name` | The name exactly as printed. |
| `division_flags` | Semicolon-separated warnings, below. |
| `source_url` | Volume zip the row came from; also the resume key. |

| Flag | Meaning |
|---|---|
| `aye_mismatch:XvY`, `no_mismatch:XvY` | X names extracted against Y printed. |
| `no_declared_totals` | No printed totals found. |
| `implausible_totals:XvY` | Printed totals above 700, treated as an OCR error. |
| `no_name_lists` | No NOES heading found, so no names. |
| `no_ayes_heading` | Ayes taken from the line after the header. |
| `missing_aye_tellers`, `missing_no_tellers` | No tellers heading on that side. |
| `names_on_both_sides:N` | N names printed on both sides (impossible; usually a parsing fault). |
| `repeated_names:N` | N extra repeats of a name within one side. |
| `no_date` | No sitting date found. |

`divisions_raw_done.txt` in the same folder lists one zip URL per finished or absent volume.

### mp_spells
`mp_spells.csv` in `data/name_resolution/`, one row per MP per seat spell.

| Field | Description |
|---|---|
| `politician_id` | Wikidata item id; the join key to the dynasties dataset. |
| `name` | English label on Wikidata. |
| `family_names`, `given_names`, `aliases` | Name variants from Wikidata, `\|`-separated. |
| `constituency` | Seat for this spell. |
| `start`, `end` | Spell dates; taken from the parliament where the spell has none. |
| `in_dynasty_file` | 1 if the MP is in `mp_relatives_coded.csv`. |

### names_resolved
`names_resolved.csv` in `data/name_resolution/`, one row per name per division, in the same order as `divisions_raw.csv` and joinable to it on `division_id`, `side` and `raw_name`.

| Field | Description |
|---|---|
| `division_id`, `date`, `side`, `is_teller`, `raw_name` | Carried from `divisions_raw.csv`. |
| `politician_id` | The matched MP; blank if unmatched. |
| `mp_name` | The MP's name on Wikidata. |
| `constituency` | The MP's seat on that date, from the roster. |
| `confidence` | Match score, 0 to 1. |
| `method` | Evidence for the match, `+`-joined: `surname` or `fuzzy_surname`, then any of `forename`, `initials`, `initials_partial`, `first_initial`, `initials_conflict`, `seat`, `seat_conflict`, `only_one_sitting`. For unmatched names, the reason: `no_surname_match`, `below_threshold`, `ambiguous`, `unparsed` or `no_date`. |

### names_unmatched
`names_unmatched.csv` in `data/name_resolution/`, one row per distinct unmatched name, most frequent first, for manual matching where it is needed: `raw_name`, `is_teller`, `rows` (times it appears), `first_date`, `last_date`, `reason`, and `candidates` (the top three MPs with their scores).

## Checks
- **Scrape:** `summarise.py` after a run. A division can never have more than 707 voters, a name on both sides, or zero names, whatever the printed totals say. For a bad division or volume, `check_volume.py`.
- **Resolution:** `check_names.py` reports the match rate (overall, voters and tellers, by decade, by method and reason); MPs sitting on 1 July of each year, where counts outside 600 to 720 point to gaps or overlaps in the roster; MPs who sat in the period but were never matched; matches to someone not sitting that day; one MP matched to two different printed names in a division, or on both sides; and printed samples of random matches, the lowest-confidence matches and the most frequent unmatched names.

## Previous scrape (baseline, older parser)
The last full 5th series run, since deleted, gives the figures the current run must beat:
- 835 of 1,000 volumes with divisions; dates 1909-02-16 to 1981-03-02, no year without divisions.
- 16,812 divisions, 5,831,643 rows, 2,107 MB. Voters per division: median 337, maximum 638.
- Printed totals found for 89% of divisions. Within 2 names on both sides: 75%; within 10: 97%; exact: 22%. Within 2 fell from about 80% in the 1900s to 68% in the 1970s.
- 639 divisions with a name on both sides, 664 with a name repeated on one side, 145 with no names, 1,547 with fewer than 4 tellers. Most of these were traced to parsing faults fixed in the current parser.

## Limitations
- **Gaps in the archive:** about 165 5th series volumes are absent; whether the 6th series runs to volume 428 is not yet confirmed.
- **Names missing from the digitised text:** some printed lists are incomplete in the OCR (1980 volume 996, division 37 has 117 of 166 Aye names). These show as mismatch flags and cannot be recovered.
- **Small mismatches are normal:** printed lists and printed totals often disagree by 1 to 3 in the source itself.
- **OCR noise in names** (`Ralkes`, `Jonos`) and era-specific formats (`Abraham, William (Rhondda)` in 1909, `Benyon, Thomas (A'don)` in 1980) are handled in resolution, not the scrape.
- **Presence is not preference:** a division list records only MPs who voted. Abstention, absence, pairing and the Speaker's non-voting role cannot be told apart, so an MP's absence from a list is not evidence of a position.
- **Resolution is a lower bound:** ambiguous and badly damaged names are left unmatched rather than guessed, and the roster's accuracy depends on Wikidata's seat dates.

## Open questions
- Does the 6th series follow the 1980 two-column layout throughout?
- What causes the remaining divisions with a name on both sides?
- Volumes that download but give 0 divisions (5th series vols 57, 66 to 70, 79 and others): genuinely empty, or a header format not matched?
- Which filter to use for analysis (suggested: within 2 of the printed totals on both sides, without `names_on_both_sides`, `no_name_lists` or `implausible_totals`), and what to do with divisions lacking printed totals.
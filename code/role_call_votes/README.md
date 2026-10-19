# ROLE CALL VOTES: DATA COLLECTION

## Purpose
Scrapes every division (recorded vote) in the House of Commons from 1909 to 2003 from the digitised Hansard archive, storing every name in every division list exactly as printed. Names were only printed in division lists from 1909; before that the Official Report gives totals alone. Matching the printed names to MPs is the next stage: [code/name_resolution/README.md](../name_resolution/README.md).

## Results
- 23,323 divisions and 8,368,266 rows (8,368,166 printed names), 1909-02-16 to 2003-11-06, from 1,209 volumes.
- Printed totals found for 92% of divisions. Within 2 names of them on both sides: 84% of 5th series and 95% of 6th series divisions.
- 100 divisions with no names recovered; 1,066 with a name printed on both sides (mostly in the source itself).

## Source: the Hansard archive
- UK Parliament's Hansard archive of digitised (OCR) debates, `https://www.hansard-archive.parliament.uk`, one zip of XML per volume.
- 5th series: volumes 1 to 1000, January 1909 to March 1981. 6th series: volumes 1 to 412 in the archive, March 1981 to November 2003 (the series runs to volume 428, December 2004, but the archive stops at 412).
- 167 5th series and 52 6th series volumes are missing from the archive. The archive answers a missing volume, and one past the end of a series, with the same HTML page (never a 404), so the last volume of each series is a fixed number rather than detected.
- Each zip held one XML file in every volume inspected, covering several weeks of sittings. Sitting dates, section titles and printed column numbers are marked as elements.

### How a division is printed
- **Header** on its own line (`Division No. 1.]`), usually followed by the time.
- **Totals** (`The House divided` followed by `Ayes, 221; Noes, 142.`) printed before the header, after it, or mid-list, depending on the era. In the 1970s the lists were sometimes printed apart from the debate (`Division List 397 [See col. 695]`), with the totals back in the debate. Totals exclude the four tellers.
- **Lists** headed `AYES.` and `NOES.` (either heading is sometimes missing), one name per line as `Surname, Forenames (Constituency)`, printed in three columns from 1909 to the 1970s and two from 1980, read across the page.
- **Tellers**, two per side, after each list (`TELLERS FOR THE AYES.` followed by `Mr. A and Mr. B.`), set in one column so the text appears every second or third line.
- Lists were typeset as blocks at the top of a page, so debate text can sit in the middle of a list. Nothing reliably marks the end of a division.

## Running the scripts
Run from the repository root with the virtual environment active.

- `hansard_scraping.py`: walks every volume of both series, parses each in memory (zips are not stored), appends rows to `divisions_raw.csv` and logs each finished or absent volume in `divisions_raw_done.txt`. Resumes by itself: rows from a volume interrupted part-way are removed and redone, finished volumes are never fetched again, and failed downloads are retried during the run and once more at the end. The last line reads `All volumes done.` or lists volumes that still failed; re-running fetches only those. For a fresh scrape, delete both files first.
- `summarise.py`: one-pass summary of `divisions_raw.csv`: completeness by series, size, data quality, impossible results, agreement with printed totals by series and decade, and the 10 worst divisions. Usage: `python code/role_call_votes/summarise.py > summary.txt`
- `check_volume.py`: downloads one volume and prints each division's extracted and printed counts, tellers and flags, the names behind any both-sides or repeated flags, and the lines the parser rejected in divisions more than 2 names short. Usage: `python code/role_call_votes/check_volume.py 5 864`. With a division number as a third argument (`5 268 289`) it writes that division's raw text to `dump_S5_vol268_div289.txt` instead.

## Method
1. The XML is flattened to lines in document order, with titles, column numbers and the position of each sitting date recorded.
2. Division headers are found at the start of a line; OCR spellings (`Divison`, `N0.`) are accepted. Each division runs to the next header and takes the last sitting date before it; dates out of order with the sittings around them (OCR errors, cross-references) are ignored.
3. Totals candidates are the last totals before the header, the first after it, and, for lists printed apart from their debate, the totals at the referenced column. The one closest to the number of names counted is used. Totals above 700 (the Commons never exceeded 707 seats) are treated as OCR errors and left blank.
4. Each side's list is collected line by line. Constituencies printed on their own line are rejoined to their name; column numbers, time stamps and prose (speaker labels, speeches, tables) are skipped rather than ending the list, so lists interrupted by debate continue. The Noes end at the first prose line after their tellers; without a NOES heading they start after the Ayes tellers.
5. Tellers are separated from names using the column count (2 or 3, from the alphabetical jumps between lines); a `Surname, Forename` line is never taken as a teller.
6. A division headed twice (in the debate and again over its list) keeps only the copy with names. Names are counted per side, compared with the printed totals and flagged where they disagree or where the result is impossible.

## Output: divisions_raw
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
| `no_name_lists` | No lists found, so no names. |
| `no_ayes_heading` | No AYES heading; Ayes taken from the line after the header. |
| `no_noes_heading` | No NOES heading; Noes taken from after the Ayes tellers. |
| `missing_aye_tellers`, `missing_no_tellers` | No tellers heading on that side. |
| `names_on_both_sides:N` | N names printed on both sides (impossible; usually in the source itself). |
| `repeated_names:N` | N extra repeats of a name within one side. |
| `no_date` | No sitting date found. |

`divisions_raw_done.txt` in the same folder lists one zip URL per finished or absent volume.

## Checks
Run `summarise.py` after a scrape. Whatever the printed totals say, a division can never have more than 707 voters, a name on both sides, or zero names. For a bad division or volume, use `check_volume.py`.

## Limitations
- **Gaps in the archive:** 219 volumes are absent, including everything after 6th series volume 412 (November 2003). Years with under 50 divisions are the war years (1915 to 1917, 1941 to 1944) and 1921 (volumes 139 to 147 missing).
- **Names missing from the digitised text:** some printed lists are incomplete in the OCR (1980 volume 996, division 37 has 117 of 166 Aye names). These show as mismatch flags and cannot be recovered.
- **Small mismatches are normal:** printed lists and printed totals often disagree by 1 to 3 in the source itself.
- **Lost headers:** where a division's header is missing from the OCR, its names are merged into the previous division and flagged `repeated_names` (e.g. 6th series volume 59, divisions 263 and 264).
- **Presence is not preference:** a division list records only MPs who voted. Abstention, absence, pairing and the Speaker's non-voting role cannot be told apart.

## Open questions
- Volumes that download but give 0 divisions (5th series vols 57, 66 to 70, 79 and others): genuinely empty, or a header format not matched?
- Which filter to use for analysis (suggested: within 2 of the printed totals on both sides, without `names_on_both_sides`, `no_name_lists` or `implausible_totals`), and what to do with divisions lacking printed totals.
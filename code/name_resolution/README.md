# ROLE CALL VOTES: NAME RESOLUTION

## Purpose
Matches every name printed in the division lists ([code/role_call_votes/README.md](../role_call_votes/README.md)) to the MP it refers to, as a `politician_id` (a Wikidata item id), the same identifier as the dynasties dataset.

## Results
- 8,368,166 printed names, 8,286,728 matched (99.0%): 99.1% of voters, 94.0% of tellers. Every decade is above 98%.
- 81,438 names (28,120 distinct) are unmatched: no sitting MP with that surname (0.6%), best match too weak (0.2%), two MPs fit equally well (0.2%), or the name could not be read (under 0.1%). They are listed in `manual_checks/unmatched_names.csv`; hand matching has not been started.
- No match is to an MP who was not sitting on the date of the division.

## Running the scripts
Run from the repository root with the virtual environment active.

- `download_mps.py`: downloads every MP seat spell overlapping 1909 to 2004 from Wikidata, with name variants and noble titles, and writes `mp_spells.csv`. Prints counts of MPs and spells, missing dates and constituencies, and how many MPs are missing from `mp_relatives_coded.csv` (0).
- `resolve_names.py`: matches every name in `divisions_raw.csv` to an MP in one pass; writes `names_resolved.csv` and `names_unmatched.csv`.
- `courtesy_titles.csv`: courtesy titles Wikidata doesn't record (`Dalkeith, Earl of`), one row per title holder with their `politician_id`. Compiled by Claude (AI), not from a primary source; each id was checked against the holder's seat and dates in `mp_spells.csv`. Read by `resolve_names.py`.
- `check_names.py`: checks the resolution (see Checks). Usage: `python code/name_resolution/check_names.py > name_check.txt`

## Method
1. **Roster:** every MP who held a "member of the nth Parliament" position overlapping 1909 to 2004, one row per seat spell with start and end dates and constituency (5,217 MPs, 17,979 spells). Matching uses every MP in the roster, not only those in the dynasty file, so an MP missing from that file cannot have their votes pushed onto someone else.
2. **Parsing:** list entries are read surname first (`Abraham, William (Rhondda)`), tellers forename first (`Mr. A. Smith`). Titles and ranks are dropped unless they are the surname (`Lord, Michael`); the constituency in brackets is kept. Entries whose comma was lost to OCR (`Robertson. J. M.`) are still read surname first.
3. **Candidates:** only MPs sitting on the date of the division. Surnames are compared in a normalised form (accents removed; M', Mc and Mac treated alike), including each part of a double-barrelled name, surnames with particles (`De la Bère`), peerage and courtesy titles. If no surname matches exactly, common OCR confusions are allowed (`rn`/`m`, `l`/`i`, `f`/`t`, `g`/`c`: `Thumham` for Thurnham), then similar spellings (0.85 or above).
4. **Scoring:**

| Evidence | Score |
|---|---|
| Surname matches exactly / one part of a double-barrelled name / by OCR or similarity | 0.60 / 0.50 / 0.45 |
| Rest of a double-barrelled surname printed as a forename (`Stewart, Henderson`) | +0.25 |
| First printed forename matches (nicknames count: `Tony` for Anthony) | +0.25, +0.10 more if it is the name they went by |
| Only a later printed forename matches | +0.15 |
| Initials all among the MP's forenames / some / none | +0.25 / +0.15 / −0.25 |
| Spelled forename with no initials: first letter matches / conflicts | +0.05 / −0.25 |
| Printed title matches one of the MP's (`Astor, Viscountess`) | +0.10 |
| Constituency agrees (abbreviations such as `B'ham` allowed) / conflicts | +0.25 / −0.15 |
| Only MP with that surname sitting | +0.20 |

5. **Decision:** the best candidate is accepted if it scores at least 0.60 and beats the runner-up by at least 0.10. Otherwise the name stays unmatched, with its reason and top three candidates recorded.

## Outputs

### mp_spells
`mp_spells.csv` in `data/name_resolution/`, one row per MP per seat spell.

| Field | Description |
|---|---|
| `politician_id` | Wikidata item id; the join key to the dynasties dataset. |
| `name` | English label on Wikidata. |
| `family_names`, `given_names`, `aliases`, `titles` | Name variants and noble titles from Wikidata, `\|`-separated. |
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
| `confidence` | Match score, 0.60 to 1; blank if unmatched. |
| `method` | Evidence for the match, `+`-joined: `surname`, `surname_part` or `fuzzy_surname`, then any of `surname_rest`, `forename`, `known_as`, `later_forename`, `initials`, `initials_partial`, `initials_conflict`, `first_initial`, `forename_conflict`, `title`, `seat`, `seat_conflict`, `only_one_sitting`. For unmatched names, the reason: `no_surname_match`, `below_threshold`, `ambiguous`, `unparsed` or `no_date`. |

### names_unmatched
`names_unmatched.csv` in `data/name_resolution/`, one row per distinct unmatched name, most frequent first: `raw_name`, `is_teller`, `rows` (times it appears), `first_date`, `last_date`, `reason`, and `candidates` (the top three MPs with their scores). `build_final.py` copies it to `manual_checks/unmatched_names.csv`, leaving out names already matched by hand.

## Checks
`check_names.py` reports the match rate (overall, voters and tellers, by decade, by method and reason); MPs sitting on 1 July of each year, where counts outside 600 to 720 point to gaps or overlaps in the roster (1945 shows 0 because Parliament was dissolved on that day); MPs who sat in the period but were never matched; matches to someone not sitting that day; one MP matched to two different printed names in a division, or on both sides; and samples of random matches, the lowest-confidence matches and the most frequent unmatched names.

## Limitations
- **Courtesy titles and changed names:** heirs who sat under a courtesy title are matched only where the title is in `courtesy_titles.csv`; MPs printed under a maiden or earlier name (`Jackson, Miss Margaret` for Margaret Beckett) are unmatched.
- **Gaps in Wikidata:** a few MPs who sat in the period are missing from the roster (e.g. Ernest Meysey-Thompson), so their votes cannot be matched, and an MP with the same surname may be matched instead (`Wilson, Henry J.` is matched to John William Wilson). These show in `manual_checks/twice_one_side.csv`.
- **Genuine ambiguity:** two MPs with the same surname and forename sitting at once (`Williams, Alan`, `Taylor, John`) are left unmatched unless the constituency is printed.
- **Resolution is a lower bound:** ambiguous and badly damaged names are left unmatched rather than guessed.
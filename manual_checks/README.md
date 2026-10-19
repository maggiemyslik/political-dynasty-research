# MANUAL CHECKS

Everything in the final datasets that could not be settled automatically. All files are written by `python code/build_final.py`, except `manual_matches.csv`, which is filled in by hand and read by it.

## Status
Hand matching has not been started: `manual_matches.csv` is empty. Counts from the current build:

| File | Rows |
|---|---|
| `unmatched_names.csv` | 28,120 names (81,438 votes) |
| `unmatched_mps.csv` | 94 |
| `both_sides.csv` | 2,650 |
| `twice_one_side.csv` | 5,196 |
| `low_confidence.csv` | 2,138 |
| `problem_divisions.csv` | 3,141 |
| `missing_volumes.csv` | 219 |

## Files
| File | One row per | What to do with it |
|---|---|---|
| `unmatched_names.csv` | Printed name no MP was matched to, most frequent first | Match by hand in `manual_matches.csv`. `reason` says why it failed (`no_surname_match`, `below_threshold`, `ambiguous`, `unparsed`); `candidates` lists the three best MPs with their scores. |
| `unmatched_mps.csv` | MP who sat during the period but has no votes | Most are genuine (1918 Sinn Féin members who never took their seats, MPs who sat for weeks). Others point to a name the resolver keeps missing: find it in `unmatched_names.csv`. |
| `both_sides.csv` | MP matched in both lists of one division | Both rows were dropped from the votes. Usually the same name printed in both lists in the source; check the division in Hansard if it matters. |
| `twice_one_side.csv` | MP matched to two printed names on one side of one division | The more confident match was kept. A different MP behind the dropped name means a wrong match. |
| `low_confidence.csv` | Printed name and MP matched below 0.70 confidence | Matches that are probably right but rest on weak evidence. Correct a wrong one by adding the right MP to `manual_matches.csv`. |
| `problem_divisions.csv` | Division with no names, no printed totals, more than 10 names off its printed totals, or a name on both sides | `issues` lists which. These are scrape or source problems, not name problems. |
| `missing_volumes.csv` | Hansard volume absent from the archive or with no divisions found | Gaps in coverage. |
| `manual_matches.csv` | Printed name matched by hand | Fill in: `raw_name` and `is_teller` copied exactly from `unmatched_names.csv`, the MP's `politician_id` from `data/name_resolution/mp_spells.csv`, and an optional `note`. Re-run `build_final.py` to apply. |

## Notes
- Matches made by hand appear in `votes_final.csv.gz` with `method` = `manual` and a blank `confidence`.
- A hand match applies to every division where that exact printed name appears. Only add one where the name means the same MP throughout its date range (shown in `unmatched_names.csv`).
- `manual_matches.csv` is never overwritten; the other files are rebuilt on every run.
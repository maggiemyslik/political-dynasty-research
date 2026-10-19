import csv
import gzip
import shutil
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "role_call_votes" / "divisions_raw.csv"
RESOLVED = ROOT / "data" / "name_resolution" / "names_resolved.csv"
UNMATCHED = ROOT / "data" / "name_resolution" / "names_unmatched.csv"
SPELLS = ROOT / "data" / "name_resolution" / "mp_spells.csv"
DYNASTY = ROOT / "data" / "dynasties" / "output"
FINAL = ROOT / "final"
CHECKS = ROOT / "manual_checks"
MANUAL = CHECKS / "manual_matches.csv"

LAST_VOLUME = {5: 1000, 6: 428}
LOW_CONFIDENCE = 0.70  # matches below this are listed for checking
FAR_OFF = 10           # divisions more than this many names off their printed totals are listed

DIVISION_FIELDS = ["division_id", "series", "volume", "date", "division_number", "time",
                   "column_start", "debate_title", "question_text", "ayes_declared",
                   "noes_declared", "ayes_extracted", "noes_extracted", "division_flags"]
FIELDS = DIVISION_FIELDS + ["politician_id", "mp_name", "constituency", "vote", "is_teller",
                            "raw_name", "confidence", "method"]

counts = Counter()
both_sides, one_side = [], []
low: dict[tuple[str, str], dict] = {}
voted: set[str] = set()


def write_csv(path: Path, fields: list[str], rows) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def day(v: str):
    return date.fromisoformat(v) if v else None


# roster, for names and seats of manual matches and for MPs never matched
spells = defaultdict(list)
names = {}
with SPELLS.open(newline="", encoding="utf-8") as fh:
    for r in csv.DictReader(fh):
        spells[r["politician_id"]].append((day(r["start"]), day(r["end"]), r["constituency"]))
        names[r["politician_id"]] = r["name"]


def seat_on(mp: str, d) -> str:
    return next((c for s, e, c in spells[mp] if d and (s is None or s <= d) and (e is None or e >= d)), "")


# hand matches for unmatched names: raw_name + is_teller -> politician_id
CHECKS.mkdir(exist_ok=True)
manual = {}
if MANUAL.exists():
    with MANUAL.open(newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["politician_id"].strip():
                if r["politician_id"].strip() not in names:
                    sys.exit(f"manual_matches.csv: {r['politician_id']} is not in mp_spells.csv")
                manual[(r["raw_name"], r["is_teller"])] = r["politician_id"].strip()
else:
    write_csv(MANUAL, ["raw_name", "is_teller", "politician_id", "note"], [])


def write_division(rows: list[dict], writer: csv.DictWriter) -> None:
    # an MP votes once per division. the same MP twice on one side keeps the more
    # confident match; on both sides the side is unknown, so both are dropped
    by_mp: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_mp[r["politician_id"]].append(r)
    for mp, mp_rows in by_mp.items():
        if len({r["vote"] for r in mp_rows}) > 1:
            counts["dropped: same MP on both sides"] += len(mp_rows)
            both_sides.append({"division_id": mp_rows[0]["division_id"], "date": mp_rows[0]["date"],
                               "politician_id": mp, "mp_name": mp_rows[0]["mp_name"],
                               "aye_names": "; ".join(r["raw_name"] for r in mp_rows if r["vote"] == "aye"),
                               "no_names": "; ".join(r["raw_name"] for r in mp_rows if r["vote"] == "no")})
            continue
        keep = max(mp_rows, key=lambda r: float(r["confidence"] or 1))
        if len(mp_rows) > 1:
            counts["dropped: same MP twice on one side"] += len(mp_rows) - 1
            one_side.append({"division_id": keep["division_id"], "date": keep["date"],
                             "politician_id": mp, "mp_name": keep["mp_name"], "vote": keep["vote"],
                             "kept_name": keep["raw_name"],
                             "dropped_names": "; ".join(r["raw_name"] for r in mp_rows if r is not keep)})
        writer.writerow(keep)
        voted.add(mp)
        counts["votes written"] += 1


FINAL.mkdir(exist_ok=True)
shutil.copyfile(DYNASTY / "mp_relatives_coded.csv", FINAL / "relationships_final.csv")
shutil.copyfile(DYNASTY / "mp_relatives_coded.dta", FINAL / "relationships_final.dta")
with (DYNASTY / "mp_relatives_coded.csv").open(newline="", encoding="utf-8") as fh:
    known = {row["politician_id"] for row in csv.DictReader(fh)}

divisions: dict[str, dict] = {}
volumes: set[tuple[int, int]] = set()

# names_resolved.csv has one row per named row of divisions_raw.csv, in the same order
with RAW.open(newline="", encoding="utf-8") as raw_fh, \
     RESOLVED.open(newline="", encoding="utf-8") as res_fh, \
     gzip.open(FINAL / "votes_final.csv.gz", "wt", newline="", encoding="utf-8") as out:
    resolved = csv.DictReader(res_fh)
    writer = csv.DictWriter(out, fieldnames=FIELDS)
    writer.writeheader()
    current, rows = None, []
    for raw in csv.DictReader(raw_fh):
        if raw["source_url"] is None:
            continue
        divisions.setdefault(raw["division_id"], {f: raw[f] for f in DIVISION_FIELDS})
        volumes.add((int(raw["series"]), int(raw["volume"])))
        if not raw["raw_name"]:
            continue
        res = next(resolved)
        if (res["division_id"], res["raw_name"]) != (raw["division_id"], raw["raw_name"]):
            sys.exit(f"divisions_raw.csv and names_resolved.csv are out of step at {raw['division_id']}: "
                     "re-run resolve_names.py")
        if raw["division_id"] != current:
            write_division(rows, writer)
            current, rows = raw["division_id"], []
        mp, conf, method, mp_name, seat = (res["politician_id"], res["confidence"], res["method"],
                                           res["mp_name"], res["constituency"])
        if not mp and (raw["raw_name"], raw["is_teller"]) in manual:
            mp = manual[(raw["raw_name"], raw["is_teller"])]
            conf, method, mp_name, seat = "", "manual", names[mp], seat_on(mp, day(raw["date"]))
            counts["matched by hand"] += 1
        if not mp:
            counts["skipped: unmatched name"] += 1
            continue
        if mp not in known:
            counts["MP not in relationships file"] += 1
        if conf and float(conf) < LOW_CONFIDENCE:
            pair = low.setdefault((raw["raw_name"], mp), {
                "raw_name": raw["raw_name"], "politician_id": mp, "mp_name": mp_name,
                "constituency": seat, "confidence": conf, "method": method, "rows": 0,
                "first_date": raw["date"], "last_date": raw["date"]})
            pair["rows"] += 1
            pair["last_date"] = raw["date"] or pair["last_date"]
        rows.append({**{f: raw[f] for f in DIVISION_FIELDS}, "politician_id": mp, "mp_name": mp_name,
                     "constituency": seat, "vote": raw["side"], "is_teller": raw["is_teller"],
                     "raw_name": raw["raw_name"], "confidence": conf, "method": method})
    write_division(rows, writer)

# --- manual_checks/
with UNMATCHED.open(newline="", encoding="utf-8") as fh:
    unmatched = [u for u in csv.DictReader(fh) if (u["raw_name"], u["is_teller"]) not in manual]
write_csv(CHECKS / "unmatched_names.csv", list(unmatched[0].keys()) if unmatched else ["raw_name"], unmatched)

dates = sorted(d["date"] for d in divisions.values() if d["date"])
first, last = day(dates[0]), day(dates[-1])
never = [{"politician_id": mp, "mp_name": names[mp],
          "seats": "; ".join(f"{c} ({s} to {e})" for s, e, c in spells[mp])}
         for mp in sorted(spells) if mp not in voted and any(
             (s is None or s <= last) and (e is None or e >= first) for s, e, _ in spells[mp] if s or e)]
write_csv(CHECKS / "unmatched_mps.csv", ["politician_id", "mp_name", "seats"], never)

write_csv(CHECKS / "both_sides.csv",
          ["division_id", "date", "politician_id", "mp_name", "aye_names", "no_names"], both_sides)
write_csv(CHECKS / "twice_one_side.csv",
          ["division_id", "date", "politician_id", "mp_name", "vote", "kept_name", "dropped_names"], one_side)
write_csv(CHECKS / "low_confidence.csv",
          ["raw_name", "politician_id", "mp_name", "constituency", "confidence", "method", "rows",
           "first_date", "last_date"], sorted(low.values(), key=lambda r: -r["rows"]))

problems = []
for d in divisions.values():
    issues = []
    got = int(d["ayes_extracted"]) + int(d["noes_extracted"])
    if got == 0:
        issues.append("no_names")
    if not d["ayes_declared"]:
        issues.append("no_printed_totals")
    elif max(abs(int(d["ayes_extracted"]) - int(d["ayes_declared"])),
             abs(int(d["noes_extracted"]) - int(d["noes_declared"]))) > FAR_OFF:
        issues.append(f"over_{FAR_OFF}_off_printed_totals")
    if "names_on_both_sides" in d["division_flags"]:
        issues.append("names_on_both_sides")
    if issues:
        problems.append({**d, "issues": ";".join(issues)})
write_csv(CHECKS / "problem_divisions.csv", DIVISION_FIELDS + ["issues"], problems)

missing = [{"series": s, "volume": v} for s, last_vol in LAST_VOLUME.items()
           for v in range(1, last_vol + 1) if (s, v) not in volumes]
write_csv(CHECKS / "missing_volumes.csv", ["series", "volume"], missing)

print(f"wrote {FINAL}/: relationships_final.csv, relationships_final.dta, votes_final.csv.gz "
      f"({(FINAL / 'votes_final.csv.gz').stat().st_size / 1e6:,.0f} MB)")
for k, v in counts.most_common():
    print(f"  {k}: {v:,}")
print(f"wrote {CHECKS}/: unmatched_names {len(unmatched):,}, unmatched_mps {len(never):,}, "
      f"both_sides {len(both_sides):,}, twice_one_side {len(one_side):,}, low_confidence {len(low):,}, "
      f"problem_divisions {len(problems):,}, missing_volumes {len(missing):,}")
import csv
import random
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "name_resolution"
SPELLS = DATA / "mp_spells.csv"
RESOLVED = DATA / "names_resolved.csv"
UNMATCHED = DATA / "names_unmatched.csv"
HOUSE = (600, 720)  # plausible number of sitting MPs on any day, 1909-2004


def pct(n: int, d: int) -> str:
    return f"{n / d:.1%}" if d else "n/a"


def section(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


def day(v: str):
    return date.fromisoformat(v) if v else None


spells = defaultdict(list)
names = {}
with SPELLS.open(newline="", encoding="utf-8") as fh:
    for r in csv.DictReader(fh):
        spells[r["politician_id"]].append((day(r["start"]), day(r["end"]), r["constituency"]))
        names[r["politician_id"]] = r["name"]


def sitting(mp: str, d: date) -> bool:
    return any((s is None or s <= d) and (e is None or e >= d) for s, e, _ in spells[mp] if s or e)


rows = matched = not_sitting = 0
by_decade = defaultdict(lambda: [0, 0])
by_teller = defaultdict(lambda: [0, 0])
methods, reasons = Counter(), Counter()
votes = Counter()
twice, both_sides = [], []
dates = set()
rng = random.Random(1)
sample, seen = [], 0
lowest: dict[tuple[str, str], dict] = {}


def close_division(did: str, picks: dict) -> None:
    # an MP votes once per division: two printed names on one MP, or one MP on both
    # sides, means a wrong match (or a scrape fault if the same name was printed twice)
    for mp, entries in picks.items():
        if len({side for side, _ in entries}) > 1:
            both_sides.append((did, mp, entries))
        elif len({raw for _, raw in entries}) > 1:
            twice.append((did, mp, entries))


with RESOLVED.open(newline="", encoding="utf-8") as fh:
    current, picks = None, defaultdict(list)
    for r in csv.DictReader(fh):
        if r["raw_name"] is None:
            continue
        rows += 1
        if r["division_id"] != current:
            if current:
                close_division(current, picks)
            current, picks = r["division_id"], defaultdict(list)
        decade = r["date"][:3] + "0s" if r["date"] else "no date"
        if r["date"]:
            dates.add(r["date"])
        by_decade[decade][0] += 1
        by_teller[r["is_teller"]][0] += 1
        if not r["politician_id"]:
            reasons[r["method"]] += 1
            continue
        matched += 1
        by_decade[decade][1] += 1
        by_teller[r["is_teller"]][1] += 1
        methods[r["method"]] += 1
        votes[r["politician_id"]] += 1
        if r["is_teller"] != "1":
            picks[r["politician_id"]].append((r["side"], r["raw_name"]))
        if r["date"] and not sitting(r["politician_id"], day(r["date"])):
            not_sitting += 1
        seen += 1
        if len(sample) < 15:
            sample.append(r)
        elif (j := rng.randrange(seen)) < 15:
            sample[j] = r
        pair = (r["raw_name"], r["politician_id"])
        if pair not in lowest or float(r["confidence"]) < float(lowest[pair]["confidence"]):
            lowest[pair] = r
    if current:
        close_division(current, picks)


def show(r: dict) -> str:
    return (f"{r['confidence']}  {r['date']}  {r['raw_name']}  ->  {r['mp_name']} ({r['constituency']})"
            f"  [{r['method']}]")


section("COVERAGE")
print(f"names: {rows:,}   matched: {matched:,} ({pct(matched, rows)})")
for t, (n, m) in sorted(by_teller.items()):
    print(f"  {'tellers' if t == '1' else 'voters'}: {pct(m, n)} of {n:,}")
print("by decade:")
for d, (n, m) in sorted(by_decade.items()):
    print(f"  {d}: {pct(m, n):>6} of {n:,}")
print("unmatched by reason:")
for k, v in reasons.most_common():
    print(f"  {k}: {v:,} ({pct(v, rows)})")
print("matched by method (top 15):")
for k, v in methods.most_common(15):
    print(f"  {k}: {v:,} ({pct(v, matched)})")

section("ROSTER")
print("MPs sitting on 1 July of each year (a gap or overlap in the roster shows up here):")
counts = {y: sum(sitting(mp, date(y, 7, 1)) for mp in spells) for y in range(1909, 2005)}
odd = {y: c for y, c in counts.items() if not HOUSE[0] <= c <= HOUSE[1]}
print(f"  min {min(counts.values())}, max {max(counts.values())}")
print(f"  years outside {HOUSE[0]}-{HOUSE[1]}: {odd or 'none'}")
if dates:
    first, last = day(min(dates)), day(max(dates))
    never = [mp for mp in spells if not votes[mp] and any(
        (s is None or s <= last) and (e is None or e >= first) for s, e, _ in spells[mp] if s or e)]
    print(f"MPs who sat between {first} and {last} but were never matched: {len(never):,}")
    for mp in never[:10]:
        print(f"  {mp} {names[mp]}  {[(str(s), str(e), c) for s, e, c in spells[mp]][:3]}")

section("MATCHING ERRORS")
print(f"matched to someone not sitting that day: {not_sitting:,}")
print(f"one MP matched to two different printed names in a division: {len(twice):,}")
for did, mp, entries in twice[:5]:
    print(f"  {did}  {mp} {names.get(mp, '')}: {entries}")
print(f"one MP matched on both sides of a division: {len(both_sides):,}")
for did, mp, entries in both_sides[:5]:
    print(f"  {did}  {mp} {names.get(mp, '')}: {entries}")

section("SAMPLE: RANDOM MATCHES")
for r in sample:
    print(f"  {show(r)}")

section("SAMPLE: LOWEST-CONFIDENCE MATCHES")
for r in sorted(lowest.values(), key=lambda r: float(r["confidence"]))[:25]:
    print(f"  {show(r)}")

section("MOST FREQUENT UNMATCHED NAMES")
if UNMATCHED.exists():
    with UNMATCHED.open(newline="", encoding="utf-8") as fh:
        for i, u in enumerate(csv.DictReader(fh)):
            if i == 25:
                break
            print(f"  {u['rows']:>6}  {u['raw_name']}  [{u['reason']}]  {u['candidates']}")

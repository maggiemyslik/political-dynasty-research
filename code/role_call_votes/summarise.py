# python code/role_call_votes/summarise.py > summary.txt

import csv
import re
from collections import Counter, defaultdict

from hansard_scraping import DATA_DIR, LAST_VOLUME, OUT_NAME

CSV_PATH = DATA_DIR / OUT_NAME
DONE_PATH = DATA_DIR / "divisions_raw_done.txt"
MAX_SEATS = 707
BUCKETS = [(0, "exact"), (2, "1-2"), (10, "3-10"), (50, "11-50"), (10**9, "50+")]


def pct(n: int, d: int) -> str:
    return f"{n / d:.0%}" if d else "n/a"


def section(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


def gap(d: dict, side: str) -> int:
    return int(d[f"{side}_extracted"]) - int(d[f"{side}_declared"])


def within(d: dict, tol: int) -> bool:
    return all(abs(gap(d, s)) <= tol for s in ("ayes", "noes"))


def voters(d: dict) -> int:
    return int(d["ayes_extracted"]) + int(d["noes_extracted"])


def ranges(nums: list[int]) -> str:
    # [1, 2, 3, 7] -> "1-3, 7"
    out, start = [], None
    for i, n in enumerate(nums):
        if start is None:
            start = n
        if i + 1 == len(nums) or nums[i + 1] != n + 1:
            out.append(str(start) if start == n else f"{start}-{n}")
            start = None
    return ", ".join(out) or "none"


def flag_names(d: dict) -> list[str]:
    return [f.split(":")[0] for f in d["division_flags"].split(";") if f]


rows = 0
empty = Counter()
divs: dict[str, dict] = {}
split: set[str] = set()  # a division's rows should be one block; if not, it was written twice
sample = []
with CSV_PATH.open(newline="", encoding="utf-8") as fh:
    reader = csv.DictReader(fh)
    columns = reader.fieldnames or []
    previous = None
    for row in reader:
        if row["source_url"] is None:  # half-written last line while the scraper runs
            continue
        rows += 1
        for c in columns:
            if not row[c]:
                empty[c] += 1
        did = row["division_id"]
        if did != previous and did in divs:
            split.add(did)
        previous = did
        d = divs.setdefault(did, {**row, "tellers": 0})
        d["tellers"] += row["is_teller"] == "1"
        if len(sample) < 6 and row["raw_name"]:
            sample.append(row)

done = set(DONE_PATH.read_text().split()) if DONE_PATH.exists() else set()
dated = sorted(d["date"] for d in divs.values() if d["date"])
checked = [d for d in divs.values() if d["ayes_declared"] and d["noes_declared"]]

section("COMPLETENESS")
vol_divs = Counter((d["series"], int(d["volume"])) for d in divs.values())
for series, last in LAST_VOLUME.items():
    have = {v for s, v in vol_divs if s == str(series)}
    finished = {int(re.search(r"CV(\d{4})", u)[1]) for u in done if f"S{series}CV" in u}
    never = sorted(set(range(1, last + 1)) - finished)
    blank = sorted(finished - have)
    print(f"Series {series} (volumes 1 to {last})")
    print(f"  with divisions:                      {len(have):>5}")
    print(f"  absent from archive or 0 divisions:  {len(blank):>5}  {ranges(blank)}")
    print(f"  not finished (re-run the scraper):   {len(never):>5}  {ranges(never)}")
if dated:
    years = Counter(int(x[:4]) for x in dated)
    print(f"Dates: {dated[0]} to {dated[-1]}")
    print(f"Years with no divisions: {[y for y in range(min(years), max(years) + 1) if not years[y]] or 'none'}")
    print(f"Years with under 50: {[f'{y} ({years[y]})' for y in sorted(years) if years[y] < 50] or 'none'}")

section("SIZE")
print(f"File: {CSV_PATH.stat().st_size / 1e6:,.0f} MB")
print(f"Rows: {rows:,}   Divisions: {len(divs):,}   Volumes: {len(vol_divs):,}")
per = sorted(voters(d) for d in divs.values())
if per:
    print(f"Voters per division: median {per[len(per) // 2]}, 10th pct {per[len(per) // 10]}, "
          f"90th pct {per[9 * len(per) // 10]}, max {per[-1]}")
print("Sample rows:")
for r in sample:
    print(f"  {r['division_id']} | {r['side']} | teller={r['is_teller']} | {r['raw_name']}")

section("DATA QUALITY")
print("Empty values by column:")
for c in columns:
    if empty[c]:
        print(f"  {c}: {empty[c]:,} ({pct(empty[c], rows)})")
flags = Counter(f for d in divs.values() for f in flag_names(d))
print("Division flags:")
for f, c in flags.most_common():
    print(f"  {f}: {c:,} ({pct(c, len(divs))})")
tellers = Counter(min(d["tellers"], 5) for d in divs.values())
print(f"Tellers per division: 4 = {tellers[4]:,} ({pct(tellers[4], len(divs))}), "
      f"fewer = {sum(v for k, v in tellers.items() if k < 4):,}, more = {tellers[5]:,}")
print(f"Divisions whose rows are not in one block (duplicated): {len(split):,}  {sorted(split)[:5]}")

section("IMPOSSIBLE RESULTS")
over = [d["division_id"] for d in divs.values() if voters(d) > MAX_SEATS]
both = [d["division_id"] for d in divs.values() if "names_on_both_sides" in flag_names(d)]
none = [d["division_id"] for d in divs.values() if voters(d) == 0]
print(f"More than {MAX_SEATS} voters:  {len(over):,}  {over[:5]}")
print(f"Name on both sides:    {len(both):,}  {both[:5]}")
print(f"No names at all:       {len(none):,}  {none[:5]}")

section("AGAINST PRINTED TOTALS")
print(f"Divisions with printed totals: {len(checked):,} ({pct(len(checked), len(divs))})")
for series in sorted({d["series"] for d in checked}):
    sub = [d for d in checked if d["series"] == series]
    print(f"Series {series}: " + ", ".join(
        f"{label} {pct(sum(within(d, tol) for d in sub), len(sub))}"
        for tol, label in ((0, "exact"), (2, "within 2"), (10, "within 10"))) + "  (both sides)")
for side in ("ayes", "noes"):
    sizes = Counter(next(label for limit, label in BUCKETS if abs(gap(d, side)) <= limit) for d in checked)
    print(f"{side} off by: " + ", ".join(f"{label} {pct(sizes[label], len(checked))}" for _, label in BUCKETS)
          + f"   too many (>2): {sum(gap(d, side) > 2 for d in checked):,}"
          + f"   too few (>2): {sum(gap(d, side) < -2 for d in checked):,}")

print("\nBy decade (within 2 on both sides / no printed totals):")
decades = defaultdict(lambda: [0, 0, 0])
for d in divs.values():
    n = decades[d["date"][:3] + "0s" if d["date"] else "no date"]
    n[0] += 1
    if d["ayes_declared"] and d["noes_declared"]:
        n[1] += 1
        n[2] += within(d, 2)
for decade in sorted(decades):
    n, t, ok = decades[decade]
    print(f"  {decade}: {n:>6,} divisions   within 2 {pct(ok, t):>4}   no totals {pct(n - t, n):>4}")

print("\n10 worst:")
for d in sorted(checked, key=lambda d: -max(abs(gap(d, "ayes")), abs(gap(d, "noes"))))[:10]:
    print(f"  {d['division_id']}  ayes {d['ayes_extracted']}/{d['ayes_declared']}"
          f"  noes {d['noes_extracted']}/{d['noes_declared']}")
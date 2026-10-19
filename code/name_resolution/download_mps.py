import csv
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "name_resolution" / "mp_spells.csv"
DYNASTY = ROOT / "data" / "dynasties" / "output" / "mp_relatives_coded.csv"

ENDPOINT = "https://query.wikidata.org/sparql"
UA = "academic-research-scraper (LSE; maggie.myslik@lse.ac.uk)"
# Member of Parliament in the United Kingdom; each "member of the nth Parliament" is a subclass
MP_CLASS = "Q16707842"
FIRST, LAST = "1909-01-01", "2004-12-31"
BATCH = 250
FIELDS = ["politician_id", "name", "family_names", "given_names", "aliases",
          "constituency", "start", "end", "in_dynasty_file"]

# a spell's dates come from the statement, or from its parliament when the statement has none
SPELLS = f"""
SELECT ?mp ?start ?end ?termStart ?termEnd ?seatLabel WHERE {{
  ?position wdt:P279* wd:{MP_CLASS} .
  ?mp p:P39 ?st .
  ?st ps:P39 ?position .
  OPTIONAL {{ ?st pq:P580 ?start }}
  OPTIONAL {{ ?st pq:P582 ?end }}
  OPTIONAL {{ ?st pq:P2937 ?term .
             OPTIONAL {{ ?term wdt:P580 ?termStart }}
             OPTIONAL {{ ?term wdt:P582 ?termEnd }} }}
  OPTIONAL {{ ?st pq:P768 ?seat . ?seat rdfs:label ?seatLabel FILTER(LANG(?seatLabel) = "en") }}
  BIND(COALESCE(?start, ?termStart) AS ?s)
  BIND(COALESCE(?end, ?termEnd) AS ?e)
  FILTER(BOUND(?s) || BOUND(?e))
  FILTER(!BOUND(?s) || ?s <= "{LAST}T00:00:00Z"^^xsd:dateTime)
  FILTER(!BOUND(?e) || ?e >= "{FIRST}T00:00:00Z"^^xsd:dateTime)
}}
"""

NAMES = """
SELECT ?mp ?label
  (GROUP_CONCAT(DISTINCT ?alias; separator="|") AS ?aliases)
  (GROUP_CONCAT(DISTINCT ?familyLabel; separator="|") AS ?family)
  (GROUP_CONCAT(DISTINCT ?givenLabel; separator="|") AS ?given)
WHERE {
  VALUES ?mp { %s }
  OPTIONAL { ?mp rdfs:label ?label FILTER(LANG(?label) = "en") }
  OPTIONAL { ?mp skos:altLabel ?alias FILTER(LANG(?alias) = "en") }
  OPTIONAL { ?mp wdt:P734 ?f . ?f rdfs:label ?familyLabel FILTER(LANG(?familyLabel) = "en") }
  OPTIONAL { ?mp wdt:P735 ?g . ?g rdfs:label ?givenLabel FILTER(LANG(?givenLabel) = "en") }
}
GROUP BY ?mp ?label
"""


def query(sparql: str) -> list[dict]:
    for wait in (10, 60, None):
        try:
            r = requests.post(ENDPOINT, data={"query": sparql}, timeout=300,
                              headers={"User-Agent": UA, "Accept": "application/sparql-results+json"})
            r.raise_for_status()
            return r.json()["results"]["bindings"]
        except (requests.RequestException, ValueError) as exc:
            if wait is None:
                sys.exit(f"Wikidata query failed: {exc}")
            print(f"query failed ({exc}), retrying in {wait}s")
            time.sleep(wait)


def val(b: dict, k: str) -> str:
    return b[k]["value"] if k in b else ""


def qid(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]


print("downloading seat spells")
spells = set()
for b in query(SPELLS):
    start = (val(b, "start") or val(b, "termStart"))[:10]
    end = (val(b, "end") or val(b, "termEnd"))[:10]
    spells.add((qid(val(b, "mp")), start, end, val(b, "seatLabel")))

ids = sorted({s[0] for s in spells})
if len(ids) < 1000:
    sys.exit(f"only {len(ids)} MPs returned: check that {MP_CLASS} is still the UK MP class on Wikidata")

print(f"downloading names for {len(ids):,} MPs")
names = {}
for i in range(0, len(ids), BATCH):
    batch = " ".join(f"wd:{x}" for x in ids[i:i + BATCH])
    for b in query(NAMES % batch):
        names[qid(val(b, "mp"))] = b
    time.sleep(1)

dynasty = set()
if DYNASTY.exists():
    with DYNASTY.open(newline="", encoding="utf-8") as fh:
        dynasty = {row["politician_id"] for row in csv.DictReader(fh)}

OUT.parent.mkdir(parents=True, exist_ok=True)
with OUT.open("w", newline="", encoding="utf-8") as fh:
    writer = csv.DictWriter(fh, fieldnames=FIELDS)
    writer.writeheader()
    for mp, start, end, seat in sorted(spells):
        b = names.get(mp, {})
        writer.writerow({
            "politician_id": mp,
            "name": val(b, "label"),
            "family_names": val(b, "family"),
            "given_names": val(b, "given"),
            "aliases": val(b, "aliases"),
            "constituency": seat,
            "start": start,
            "end": end,
            "in_dynasty_file": int(mp in dynasty),
        })

print(f"\nwrote {OUT}")
print(f"MPs: {len(ids):,}   seat spells: {len(spells):,}")
print(f"spells with no start: {sum(1 for s in spells if not s[1]):,}   no end: {sum(1 for s in spells if not s[2]):,}"
      f"   no constituency: {sum(1 for s in spells if not s[3]):,}")
print(f"MPs with no English name: {sum(1 for x in ids if not val(names.get(x, {}), 'label')):,}")
if dynasty:
    print(f"MPs sitting 1909-2004 missing from the dynasty file: {len(set(ids) - dynasty):,}")
    print(f"dynasty file IDs not among these MPs: {len(dynasty - set(ids)):,}")
else:
    print(f"dynasty file not found: {DYNASTY}")

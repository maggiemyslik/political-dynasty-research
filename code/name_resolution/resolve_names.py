import csv
import re
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "role_call_votes" / "divisions_raw.csv"
DATA = ROOT / "data" / "name_resolution"
SPELLS = DATA / "mp_spells.csv"
RESOLVED = DATA / "names_resolved.csv"
UNMATCHED = DATA / "names_unmatched.csv"

THRESHOLD = 0.60  # lowest score accepted as a match
MARGIN = 0.10     # best must beat the runner-up by this much
FUZZY = 0.85      # surname similarity for OCR-damaged names

RESOLVED_FIELDS = ["division_id", "date", "side", "is_teller", "raw_name", "politician_id",
                   "mp_name", "constituency", "confidence", "method"]
UNMATCHED_FIELDS = ["raw_name", "is_teller", "rows", "first_date", "last_date", "reason", "candidates"]

TITLES = {
    "mr", "mrs", "miss", "ms", "dr", "sir", "dame", "lady", "lord", "rt", "right", "hon",
    "honourable", "the", "of", "captain", "capt", "major", "maj", "col", "colonel", "lieut",
    "lieutenant", "lt", "general", "gen", "brigadier", "brig", "admiral", "adm", "rear", "vice",
    "commander", "comdr", "cdr", "wing", "squadron", "flight", "leader", "professor", "prof",
    "alderman", "viscount", "viscountess", "earl", "marquess", "marquis", "countess", "baron",
    "baroness", "duke", "duchess", "bt", "bart",
}
PEERAGE = {"viscount", "viscountess", "earl", "marquess", "marquis", "countess", "baron",
           "baroness", "duke", "duchess", "lord", "lady"}
SEAT_FILLER = {"and", "the", "of", "upon", "on", "under", "in", "division", "borough", "burghs",
               "county", "city", "uk", "parliament", "constituency", "district", "boroughs"}
DIRECTIONS = {"north", "south", "east", "west", "central", "mid", "northern", "southern",
              "eastern", "western", "inner", "outer", "upper", "lower"}
SHORT_DIRECTIONS = {"n": "north", "s": "south", "e": "east", "w": "west", "c": "central"}
PARENS = re.compile(r"\(([^)]*)\)?")


def key(text: str) -> str:
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).lower()
    text = re.sub(r"^m(?:a?c|['\u2019])\s*", "mac", text)  # M'Kenna, Mc Kenna, McKenna -> mackenna
    return re.sub(r"[^a-z]", "", text)


def words(text: str) -> list[str]:
    # tokens that are neither titles nor ordinals ("11th")
    return [t for t in re.split(r"[\s.]+", text)
            if key(t) and key(t) not in TITLES and not re.search(r"\d", t)]


@dataclass
class Spell:
    start: Optional[date]
    end: Optional[date]
    seat: str


@dataclass
class MP:
    id: str
    name: str
    keys: set = field(default_factory=set)
    forenames: set = field(default_factory=set)
    initials: set = field(default_factory=set)
    spells: list = field(default_factory=list)


def load_mps() -> list[MP]:
    def day(v: str) -> Optional[date]:
        return date.fromisoformat(v) if v else None

    mps: dict[str, MP] = {}
    with SPELLS.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            mp = mps.get(row["politician_id"])
            if mp is None:
                mp = mps[row["politician_id"]] = MP(row["politician_id"], row["name"])
                for full in [row["name"]] + row["aliases"].split("|"):
                    # "Arthur Annesley, 11th Viscount Valentia": name before the comma, title after
                    main, *titles = full.split(",")
                    w = words(main)
                    if w:
                        mp.keys.add(key(w[-1]))
                        if len(w) > 1:
                            mp.keys.add(key(w[-2]) + key(w[-1]))
                        mp.initials.add("".join(key(t)[0] for t in w[:-1]))
                        mp.forenames.update(key(t) for t in w[:-1] if len(key(t)) > 1)
                    # only titles count: an alias like "Jones, Thomas" must not add "thomas"
                    for t in titles:
                        if PEERAGE & {key(x) for x in t.split()} and (tw := words(t)):
                            mp.keys.add(key(tw[-1]))
                families = [f for f in row["family_names"].split("|") if key(f)]
                mp.keys.update(key(f) for f in families)
                if len(families) > 1:
                    mp.keys.add("".join(key(f) for f in families))
                mp.forenames.update(key(g) for g in row["given_names"].split("|") if len(key(g)) > 1)
                mp.keys.discard("")
                mp.initials.discard("")
            mp.spells.append(Spell(day(row["start"]), day(row["end"]), row["constituency"]))
    return list(mps.values())


def parse(raw: str, teller: bool) -> Optional[tuple[str, list[str], str]]:
    # list entries are "Surname, Forenames (Seat)", tellers are "Mr. Forenames Surname"
    found = PARENS.findall(raw)
    seat = found[-1].strip() if found else ""
    text = PARENS.sub(" ", raw)
    if teller or "," not in text:
        w = words(text)
        if not w:
            return None
        return w[-1], w[:-1], seat
    head, rest = text.split(",", 1)
    w = words(head)
    if not w:
        return None
    return " ".join(w), words(rest), seat


def seat_tokens(text: str) -> tuple[set[str], set[str]]:
    toks = [key(t) for t in re.split(r"[\s,/&()\-]+", text)]
    toks = [SHORT_DIRECTIONS.get(t, t) for t in toks if t and t not in SEAT_FILLER]
    return {t for t in toks if t not in DIRECTIONS}, {t for t in toks if t in DIRECTIONS}


def same_place(a: str, b: str) -> bool:
    # a may be an abbreviation of b ("bham" for birmingham, "stechfd" for stechford)
    if a == b or SequenceMatcher(None, a, b).ratio() >= 0.8:
        return True
    rest = iter(b)
    return len(a) >= 2 and a[0] == b[0] and all(c in rest for c in a)


def seats_agree(printed: str, roster: str) -> bool:
    p_place, p_dir = seat_tokens(printed)
    r_place, r_dir = seat_tokens(roster)
    if not p_place or not r_place:
        return False
    if p_dir and r_dir and not p_dir & r_dir:
        return False
    # either side may carry an extra county prefix, so one side must be fully matched
    p_ok = all(any(same_place(p, r) for r in r_place) for p in p_place)
    r_ok = all(any(same_place(p, r) for p in p_place) for r in r_place)
    return p_ok or r_ok


def score(mp: MP, spell: Spell, given: list[str], seat: str, fuzzy: bool) -> tuple[float, list[str]]:
    value, why = (0.45, ["fuzzy_surname"]) if fuzzy else (0.60, ["surname"])
    names = {key(t) for t in given if len(key(t)) > 1}
    inits = "".join(key(t)[0] for t in given if key(t))
    if names & mp.forenames:
        value += 0.25
        why.append("forename")
    elif inits and mp.initials:
        if inits in mp.initials:
            value += 0.25
            why.append("initials")
        elif any(i.startswith(inits) or inits.startswith(i) for i in mp.initials):
            value += 0.15
            why.append("initials_partial")
        elif any(i[0] == inits[0] for i in mp.initials):
            value += 0.05
            why.append("first_initial")
        else:
            value -= 0.25
            why.append("initials_conflict")
    if seat and spell.seat:
        if seats_agree(seat, spell.seat):
            value += 0.25
            why.append("seat")
        else:
            value -= 0.15
            why.append("seat_conflict")
    return value, why


def resolve(raw: str, teller: bool, index: dict[str, list]) -> tuple[Optional[tuple], str, list]:
    """Returns (best (score, why, mp, spell) or None, reason if unmatched, ranked candidates)."""
    parsed = parse(raw, teller)
    if parsed is None:
        return None, "unparsed", []
    surname, given, seat = parsed
    k = key(surname)
    pool, fuzzy = index.get(k, []), False
    if not pool:
        near = [o for o in index if o[:1] == k[:1] and abs(len(o) - len(k)) <= 2
                and SequenceMatcher(None, k, o).ratio() >= FUZZY]
        pool, fuzzy = [x for o in near for x in index[o]], True
    if not pool:
        return None, "no_surname_match", []

    best: dict[str, tuple] = {}
    for mp, spell in pool:
        value, why = score(mp, spell, given, seat, fuzzy)
        if mp.id not in best or value > best[mp.id][0]:
            best[mp.id] = (value, why, mp, spell)
    ranked = sorted(best.values(), key=lambda t: -t[0])
    if len(ranked) == 1:
        value, why, mp, spell = ranked[0]
        ranked = [(value + 0.20, why + ["only_one_sitting"], mp, spell)]
    top = ranked[0]
    if top[0] < THRESHOLD:
        return None, "below_threshold", ranked
    if len(ranked) > 1 and top[0] - ranked[1][0] < MARGIN:
        return None, "ambiguous", ranked
    return top, "", ranked


def describe(ranked: list) -> str:
    return "; ".join(f"{mp.id} {mp.name} ({spell.seat}) {min(v, 1):.2f}" for v, _, mp, spell in ranked[:3])


def main() -> None:
    if not SPELLS.exists():
        sys.exit(f"{SPELLS} not found: run download_mps.py first")
    mps = load_mps()
    flat = [(mp, s) for mp in mps for s in mp.spells if s.start or s.end]
    current_day, members, index, cache = None, None, {}, {}
    unmatched: dict[tuple[str, str], dict] = {}
    rows = matched = 0

    with RAW.open(newline="", encoding="utf-8") as src, \
         RESOLVED.open("w", newline="", encoding="utf-8") as dst:
        writer = csv.DictWriter(dst, fieldnames=RESOLVED_FIELDS)
        writer.writeheader()
        for row in csv.DictReader(src):
            if row["source_url"] is None or not row["raw_name"]:
                continue  # half-written last line, or a division with no names
            rows += 1
            if rows % 500_000 == 0:
                print(f"{rows:,} names")
            day = date.fromisoformat(row["date"]) if row["date"] else None
            if day != current_day:
                current_day = day
                sitting = frozenset(i for i, (_, s) in enumerate(flat)
                                    if day and (s.start is None or s.start <= day)
                                    and (s.end is None or s.end >= day))
                # the House only changes at elections and by-elections, so results carry over
                if sitting != members:
                    members, cache = sitting, {}
                    index = defaultdict(list)
                    for i in sitting:
                        mp, s = flat[i]
                        for k in mp.keys:
                            index[k].append((mp, s))

            teller = row["is_teller"] == "1"
            ck = (row["raw_name"], teller)
            if ck not in cache:
                cache[ck] = resolve(row["raw_name"], teller, index) if day else (None, "no_date", [])
            best, reason, ranked = cache[ck]

            out = {k: row[k] for k in ("division_id", "date", "side", "is_teller", "raw_name")}
            if best:
                value, why, mp, spell = best
                matched += 1
                out.update(politician_id=mp.id, mp_name=mp.name, constituency=spell.seat,
                           confidence=f"{min(value, 1):.2f}", method="+".join(why))
            else:
                out.update(method=reason)
                u = unmatched.setdefault((row["raw_name"], row["is_teller"]), {
                    "raw_name": row["raw_name"], "is_teller": row["is_teller"], "rows": 0,
                    "first_date": row["date"], "last_date": row["date"], "reason": reason,
                    "candidates": describe(ranked)})
                u["rows"] += 1
                u["last_date"] = row["date"] or u["last_date"]
            writer.writerow(out)

    with UNMATCHED.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=UNMATCHED_FIELDS)
        writer.writeheader()
        writer.writerows(sorted(unmatched.values(), key=lambda u: -u["rows"]))

    print(f"{rows:,} names, {matched:,} matched ({matched / rows:.1%})" if rows else "no names found")
    print(f"{len(unmatched):,} distinct unmatched names")
    print(f"wrote {RESOLVED}\nwrote {UNMATCHED}")


if __name__ == "__main__":
    main()

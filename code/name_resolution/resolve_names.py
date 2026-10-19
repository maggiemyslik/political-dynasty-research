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
# hand-made: courtesy titles Wikidata doesn't record ("Dalkeith, Earl of"), one row per MP
COURTESY = Path(__file__).resolve().parent / "courtesy_titles.csv"

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
    "baroness", "duke", "duchess", "bt", "bart", "cmdr", "cmd", "com", "sqn", "ldr", "wg", "hn",
}
PEERAGE = {"viscount", "viscountess", "earl", "marquess", "marquis", "countess", "baron",
           "baroness", "duke", "duchess", "lord", "lady"}
SEAT_FILLER = {"and", "the", "of", "upon", "on", "under", "in", "division", "borough", "burghs",
               "county", "city", "uk", "parliament", "constituency", "district", "boroughs"}
DIRECTIONS = {"north", "south", "east", "west", "central", "mid", "northern", "southern",
              "eastern", "western", "inner", "outer", "upper", "lower"}
SHORT_DIRECTIONS = {"n": ["north"], "s": ["south"], "e": ["east"], "w": ["west"], "c": ["central"],
                    "ne": ["north", "east"], "nw": ["north", "west"], "se": ["south", "east"],
                    "sw": ["south", "west"]}
PARTICLES = {"de", "la", "le", "du", "van", "von", "der", "den", "ter", "st", "ap"}
NICKNAMES = {
    "bill": "william", "billy": "william", "will": "william", "bob": "robert", "bobby": "robert",
    "rob": "robert", "jim": "james", "jimmy": "james", "jamie": "james", "ted": "edward",
    "teddy": "edward", "ed": "edward", "eddie": "edward", "tony": "anthony", "dick": "richard",
    "harry": "henry", "jack": "john", "johnny": "john", "tom": "thomas", "tommy": "thomas",
    "mike": "michael", "mick": "michael", "dave": "david", "dai": "david", "andy": "andrew",
    "alf": "alfred", "fred": "frederick", "freddie": "frederick", "ron": "ronald", "ken": "kenneth",
    "joe": "joseph", "geoff": "geoffrey", "nick": "nicholas", "pat": "patrick", "sam": "samuel",
    "sandy": "alexander", "alex": "alexander", "alec": "alexander", "chris": "christopher",
    "doug": "douglas", "don": "donald", "reg": "reginald", "stan": "stanley", "len": "leonard",
    "les": "leslie", "terry": "terence", "gerry": "gerald", "phil": "philip", "steve": "stephen",
    "tim": "timothy", "wally": "walter", "bert": "herbert", "charlie": "charles", "chas": "charles",
    "peggy": "margaret", "maggie": "margaret", "betty": "elizabeth", "liz": "elizabeth",
}
PARENS = re.compile(r"\(([^)]*)\)?")


def key(text: str) -> str:
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).lower()
    text = re.sub(r"^m(?:a?c|['\u2019])\s*", "mac", text)  # M'Kenna, Mc Kenna, McKenna -> mackenna
    return re.sub(r"[^a-z]", "", text)


def ocr(k: str) -> str:
    # letters OCR confuses: Hardle/Hardie, Thumham/Thurnham, Bitten/Biffen, Grossman/Crossman
    return k.replace("rn", "m").replace("l", "i").replace("f", "t").replace("g", "c")


def hyphen_parts(name: str) -> set[str]:
    # every shorter run of a hyphenated name: Fuller-Acland-Hood -> acland, aclandhood, hood ...
    p = [key(x) for x in name.split("-") if key(x)]
    return {"".join(p[i:j]) for i in range(len(p)) for j in range(i + 1, len(p) + 1)
            if j - i < len(p) and len("".join(p[i:j])) >= 3}


def is_title(t: str) -> bool:
    parts = [key(p) for p in t.split("-") if key(p)]  # "Lt.-Col.", "Brigadier-General"
    return bool(parts) and all(p in TITLES for p in parts)


def words(text: str, keep_last: bool = False) -> list[str]:
    # drops titles and ordinals ("11th"); keep_last so "John Major" keeps "Major"
    toks = [t for t in re.split(r"[\s.]+", text) if key(t) and not re.search(r"\d", t)]
    return [t for i, t in enumerate(toks) if not is_title(t) or (keep_last and i == len(toks) - 1)]


def forename_match(printed: list[str], known: set[str]) -> bool:
    # nicknames and short forms count ("Tony" for Antony, "Jim" for James)
    canon = lambda n: NICKNAMES.get(n, n)
    return any(canon(a) == canon(b) or (min(len(a), len(b)) >= 3 and (a in b or b in a))
               for a in printed for b in known)


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
    letters: set = field(default_factory=set)  # first letters of every known forename
    part_keys: set = field(default_factory=set)  # one half of a double-barrelled name
    ranks: set = field(default_factory=set)  # peerage words in any of their names ("viscountess")
    first_name: str = ""
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
                for full in [row["name"]] + row["aliases"].split("|") + row["titles"].split("|"):
                    mp.ranks.update(PEERAGE & {key(x) for x in full.split()})
                for full in [row["name"]] + row["aliases"].split("|"):
                    # "Arthur Annesley, 11th Viscount Valentia": name before the comma, title after
                    main, *titles = full.split(",")
                    w = words(main, keep_last=True)
                    if w:
                        mp.keys.add(key(w[-1]))
                        # each part of a double-barrelled name: "Thomas, Dafydd" is Dafydd Elis-Thomas
                        mp.part_keys.update(hyphen_parts(w[-1]))
                        if len(w) > 1:
                            mp.keys.add(key(w[-2]) + key(w[-1]))
                        # particles belong to the surname: "De la Bere" -> delabere
                        n = len(w) - 1
                        while n > 0 and key(w[n - 1]) in PARTICLES:
                            n -= 1
                        mp.keys.add("".join(key(t) for t in w[n:]))
                        given = [key(t) for t in w[:n] if key(t) not in PARTICLES]
                        mp.forenames.update(g for g in given if len(g) > 1)
                        mp.letters.update(g[0] for g in given)
                        if full == row["name"] and given:
                            mp.first_name = given[0]
                    # only titles count: an alias like "Jones, Thomas" must not add "thomas"
                    for t in titles:
                        if PEERAGE & {key(x) for x in t.split()} and (tw := words(t)):
                            mp.keys.add(key(tw[-1]))
                            mp.keys.add("".join(key(x) for x in tw))  # "Earl Castle Stewart"
                # noble titles: "Earl of Dalkeith" -> dalkeith; single words ("baronet") carry no name
                for t in row["titles"].split("|"):
                    if len(t.split()) > 1 and (tw := words(t, keep_last=True)):
                        mp.keys.add(key(tw[-1]))
                families = [f for f in row["family_names"].split("|") if key(f)]
                mp.keys.update(key(f) for f in families)
                mp.part_keys.update(k for f in families for k in hyphen_parts(f))
                if len(families) > 1:
                    mp.keys.add("".join(key(f) for f in families))
                given = [key(g) for g in row["given_names"].split("|") if key(g)]
                mp.forenames.update(g for g in given if len(g) > 1)
                mp.letters.update(g[0] for g in given)
                # a nickname's full form gives its initial too: "Bill Deedes" is W. F. Deedes
                mp.letters.update(NICKNAMES[f][0] for f in mp.forenames if f in NICKNAMES)
                mp.keys.discard("")
                mp.part_keys -= mp.keys
            mp.spells.append(Spell(day(row["start"]), day(row["end"]), row["constituency"]))
    if COURTESY.exists():
        with COURTESY.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if row["politician_id"] in mps:
                    mps[row["politician_id"]].keys.add(key(row["title"]))
    return list(mps.values())


def parse(raw: str, teller: bool) -> Optional[tuple[str, list[str], str]]:
    # list entries are "Surname, Forenames (Seat)", tellers are "Mr. Forenames Surname"
    found = PARENS.findall(raw)
    seat = found[-1].strip() if found else ""
    text = PARENS.sub(" ", raw)
    toks = text.split()
    if teller or ("," not in text and toks and is_title(toks[0].strip("."))):
        w = words(text, keep_last=True)
        return (w[-1], w[:-1], seat) if w else None
    if "," in text:
        head, rest = text.split(",", 1)
    else:
        # comma lost to OCR ("Robertson. J. M."): the surname still comes first
        head, rest = (toks[0], " ".join(toks[1:])) if toks else ("", "")
    # a surname that is also a title word stays ("Lord, Michael", "Major, John")
    w = words(head) or [t for t in re.split(r"[\s.]+", head) if key(t)]
    return (" ".join(w), words(rest), seat) if w else None


def seat_tokens(text: str) -> tuple[set[str], set[str]]:
    toks = [key(t) for t in re.split(r"[\s,/&()\-]+", text)]
    toks = [d for t in toks if t and t not in SEAT_FILLER for d in SHORT_DIRECTIONS.get(t, [t])]
    return {t for t in toks if t not in DIRECTIONS}, {t for t in toks if t in DIRECTIONS}


def same_place(a: str, b: str) -> bool:
    # a may be an abbreviation of b ("bham" for birmingham, "stechfd" for stechford)
    if len(a) == 1:
        return a == b[0]  # "Middleton & P" for Middleton and Prestwich
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


def score(mp: MP, spell: Spell, k: str, given: list[str], seat: str, fuzzy: bool,
          ranks: set[str]) -> tuple[float, list[str]]:
    if fuzzy:
        value, why = 0.45, ["fuzzy_surname"]
    elif k in mp.part_keys:
        value, why = 0.50, ["surname_part"]  # "Cooper" for Cooper-Key: weaker than a full surname
    else:
        value, why = 0.60, ["surname"]
    spelled = [key(t) for t in given if len(key(t)) > 1 and key(t) not in PARTICLES]
    inits = {key(t) for t in given if len(key(t)) == 1}
    # initials are compared as sets: Wikidata often labels MPs by the name they went by.
    # a spelled forename that doesn't match falls back to the initials when there are any
    # the rest of a double-barrelled surname printed as a forename: "Stewart, Henderson"
    rest = [t for t in spelled if t in mp.keys | mp.part_keys]
    spelled = [t for t in spelled if t not in rest]
    if rest:
        value += 0.25
        why.append("surname_rest")
    if spelled and mp.forenames and forename_match(spelled[:1], mp.forenames):
        value += 0.25
        why.append("forename")
        if spelled[0] == mp.first_name:
            value += 0.10  # the name they went by: Michael vs Patrick Michael McNair-Wilson
            why.append("known_as")
    elif spelled and mp.forenames and forename_match(spelled[1:], mp.forenames):
        value += 0.15  # only a later forename matches: "Megan Lloyd" is not David Lloyd George
        why.append("later_forename")
    elif spelled and not inits and mp.forenames:
        if spelled[0][0] in mp.letters:
            value += 0.05
            why.append("first_initial")
        else:
            value -= 0.25
            why.append("forename_conflict")
    elif inits and mp.letters:
        if inits <= mp.letters:
            value += 0.25
            why.append("initials")
        elif inits & mp.letters:
            value += 0.15
            why.append("initials_partial")
        else:
            value -= 0.25
            why.append("initials_conflict")
    if ranks & mp.ranks:
        value += 0.10  # "Astor, Viscountess" is Nancy, not John Jacob Astor
        why.append("title")
    if seat and spell.seat:
        if seats_agree(seat, spell.seat):
            value += 0.25
            why.append("seat")
        else:
            value -= 0.15
            why.append("seat_conflict")
    return value, why


def resolve(raw: str, teller: bool, index: dict[str, list], canon: dict[str, set]
            ) -> tuple[Optional[tuple], str, list]:
    """Returns (best (score, why, mp, spell) or None, reason if unmatched, ranked candidates)."""
    parsed = parse(raw, teller)
    if parsed is None:
        return None, "unparsed", []
    surname, given, seat = parsed
    k = key(surname)
    ranks = PEERAGE & {key(t) for t in re.split(r"[\s.,()]+", raw)}
    pool, fuzzy = index.get(k, []), False
    if not pool:
        near = canon.get(ocr(k)) or [o for o in index if o[:1] == k[:1] and abs(len(o) - len(k)) <= 2
                                     and SequenceMatcher(None, k, o).ratio() >= FUZZY]
        pool, fuzzy = [x for o in near for x in index[o]], True
    if not pool:
        return None, "no_surname_match", []

    best: dict[str, tuple] = {}
    for mp, spell in pool:
        value, why = score(mp, spell, k, given, seat, fuzzy, ranks)
        if mp.id not in best or value > best[mp.id][0]:
            best[mp.id] = (value, why, mp, spell)
    ranked = sorted(best.values(), key=lambda t: -t[0])
    if len(ranked) == 1:
        value, why, mp, spell = ranked[0]
        ranked = [(value + 0.20, why + ["only_one_sitting"], mp, spell)]
    top = ranked[0]
    if top[0] < THRESHOLD:
        return None, "below_threshold", ranked
    if len(ranked) > 1 and top[0] - ranked[1][0] < MARGIN - 1e-9:  # 0.85 - 0.75 is 0.0999...
        return None, "ambiguous", ranked
    return top, "", ranked


def describe(ranked: list) -> str:
    return "; ".join(f"{mp.id} {mp.name} ({spell.seat}) {min(v, 1):.2f}" for v, _, mp, spell in ranked[:3])


def main() -> None:
    if not SPELLS.exists():
        sys.exit(f"{SPELLS} not found: run download_mps.py first")
    mps = load_mps()
    flat = [(mp, s) for mp in mps for s in mp.spells if s.start or s.end]
    current_day, members, index, canon, cache = None, None, {}, {}, {}
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
                    index, canon = defaultdict(list), defaultdict(set)
                    for i in sitting:
                        mp, s = flat[i]
                        for k in mp.keys | mp.part_keys:
                            index[k].append((mp, s))
                            canon[ocr(k)].add(k)

            teller = row["is_teller"] == "1"
            ck = (row["raw_name"], teller)
            if ck not in cache:
                cache[ck] = resolve(row["raw_name"], teller, index, canon) if day else (None, "no_date", [])
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
#!/usr/bin/env python3
"""
Resolve the verbatim names in divisions_raw.csv to member IDs.

    python hansard_resolve.py --roster data/role_call_votes/member_spells.csv

Reads  data/role_call_votes/divisions_raw.csv
Writes data/role_call_votes/divisions_resolved.csv
       data/role_call_votes/unresolved.csv   (adjudication queue, top 3 candidates)

The roster must be spell-level, one row per member x seat x date range:
    member_id,surname,forenames,constituency,start,end,party
"""

from __future__ import annotations

import argparse
import csv
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable, Optional

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "role_call_votes"
RAW_NAME = "divisions_raw.csv"
RESOLVED_NAME = "divisions_resolved.csv"
REVIEW_NAME = "unresolved.csv"

THRESHOLD = 0.60
AMBIGUITY_GAP = 0.10

# --------------------------------------------------------------------------
# Vocabularies
# --------------------------------------------------------------------------

HONORIFIC = re.compile(
    r"^\s*((?:Rt\.?\s*Hon|Right\s+Hon|The\s+Hon|Hon|Mr|Mrs|Miss|Ms|Dr|Sir|Dame|"
    r"Lady|Lord|Viscountess|Viscount|Earl|Marquess)\.?)(?=\s|$)\s*", re.I
)
RANK = re.compile(
    r"^\s*((?:Lieutenant|Lieut|Lt)\.?[-\s]*(?:Colonel|Col|Commander|Comdr)\.?|"
    r"Brigadier[-\s]*General|Brigadier|Brig\.?[-\s]*Gen\.?|Brig\.?|"
    r"Squadron\s+Leader|Sqn\.?[-\s]*Ldr\.?|Wing\s+Commander|Wing\s+Comdr\.?|"
    r"Flight\s+Lieutenant|Fl(?:igh)?t\.?\s*Lieut\.?|"
    r"Rear[-\s]*Admiral|Vice[-\s]*Admiral|Colonel|Col\.?|Captain|Capt\.?|"
    r"Major[-\s]*General|Major|Maj\.?|Admiral|Adm\.?|Commander|Comdr\.?|"
    r"General|Gen\.?|Professor|Prof\.?|Alderman)(?=\s|$)\s*", re.I
)
PARTICLES = {"de", "du", "van", "von", "le", "la", "st", "ap", "der", "den", "ter"}
# Tokens that distinguish seats sharing a base name (Newcastle E / N / W).
QUALIFIERS = {
    "north", "south", "east", "west", "central", "centre", "mid", "middle",
    "upper", "lower", "ne", "nw", "se", "sw", "north-east", "north-west",
    "south-east", "south-west", "city", "county", "borough", "burghs",
    "university", "universities", "first", "second", "third", "division",
}
OCR_FIXES = [
    (r"\brn\b", "m"),
    (r"(?<=[a-z])1(?=[a-z])", "l"),
    (r"(?<=[a-z])0(?=[a-z])", "o"),
    (r"(?<=[a-z])5(?=[a-z])", "s"),
]
CONNECTIVES = {"upon", "on", "under", "in", "and", "the"}
PUNCT = re.compile(r"[.,;:]+")
PARENS = re.compile(r"\(([^)]*)\)")


def key(text: str) -> str:
    text = "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    ).lower()
    for pattern, repl in OCR_FIXES:
        text = re.sub(pattern, repl, text)
    text = re.sub(r"^m['\u2019c]\s*", "mac", text)
    text = re.sub(r"^mac\s+", "mac", text)
    return re.sub(r"[^a-z]", "", text)


# --------------------------------------------------------------------------
# Name parsing
# --------------------------------------------------------------------------

@dataclass
class ParsedName:
    raw: str
    surname: str
    surname_key: str
    initials: str = ""
    forenames: str = ""
    honorific: str = ""
    rank: str = ""
    constituency: str = ""
    flags: list[str] = field(default_factory=list)


def _strip_titles(text: str) -> tuple[str, str, str]:
    """Peel honorifics and ranks in any order until neither matches."""
    honorifics, ranks = [], []
    while True:
        if m := HONORIFIC.match(text):
            honorifics.append(m[1].strip())
            text = text[m.end():]
            continue
        if m := RANK.match(text):
            ranks.append(m[1].strip())
            text = text[m.end():]
            continue
        break
    return " ".join(honorifics), " ".join(ranks), text


def parse_name(raw: str, teller: bool = False) -> Optional[ParsedName]:
    """List entries are surname-first; tellers are printed forename-first."""
    text = re.sub(r"\s+", " ", raw).strip()
    if not text:
        return None

    constituency = ""
    if found := PARENS.findall(text):
        constituency = found[-1].strip()
        text = PARENS.sub("", text).strip()

    flags: list[str] = []
    if teller:
        honorific, rank, rest = _strip_titles(text)
        tokens = [PUNCT.sub("", t) for t in rest.split()]
        tokens = [t for t in tokens if t]
        if not tokens:
            return None
        surname = tokens[-1]
        head = tokens[:-1]
        flags.append("teller_forename_first")
    else:
        if "," in text:
            surname_part, rest = text.split(",", 1)
        else:
            surname_part, rest = text, ""
            flags.append("no_comma")
        honorific, rank, surname_part = _strip_titles(surname_part)
        surname = surname_part.strip().rstrip(".")
        extra_hon, extra_rank, rest = _strip_titles(rest)
        honorific = " ".join(x for x in (honorific, extra_hon) if x)
        rank = " ".join(x for x in (rank, extra_rank) if x)
        head = [PUNCT.sub("", t) for t in rest.split()]
        head = [t for t in head if t]

    if not surname or key(surname) in {key(w) for w in re.split(r"[\s.-]+", rank) if w}:
        return None

    initials, forenames = [], []
    for token in head:
        if len(token) == 1 and token.isalpha():
            initials.append(token.upper())
        elif token.lower() in PARTICLES:
            flags.append("forename_particle")
        elif token.isalpha():
            forenames.append(token)
    if not initials and not forenames:
        flags.append("no_forename")

    return ParsedName(
        raw=raw, surname=surname, surname_key=key(surname),
        initials="".join(initials), forenames=" ".join(forenames),
        honorific=honorific, rank=rank, constituency=constituency, flags=flags,
    )


# --------------------------------------------------------------------------
# Roster
# --------------------------------------------------------------------------

@dataclass
class Spell:
    member_id: str
    surname: str
    forenames: str
    constituency: str
    start: Optional[date]
    end: Optional[date]
    party: str = ""

    @property
    def surname_key(self) -> str:
        return key(self.surname)

    @property
    def initials(self) -> str:
        return "".join(w[0].upper() for w in self.forenames.split() if w)

    def covers(self, day: Optional[date]) -> bool:
        if day is None:
            return True
        return not ((self.start and day < self.start) or (self.end and day > self.end))


def _seat_tokens(name: str) -> tuple[set[str], set[str]]:
    tokens = [key(t) for t in re.split(r"[,\s/&-]+", name) if len(t) > 1]
    base = {t for t in tokens if t and t not in {key(q) for q in QUALIFIERS} and t != "and"}
    quals = {t for t in tokens if t in {key(q) for q in QUALIFIERS}}
    return base, quals


def seats_agree(printed: str, roster: str) -> bool:
    """
    Names must share a place token, neither side may name a further place the
    other does not (Kingston upon Thames vs Kingston upon Hull), and directional
    qualifiers must not conflict (Newcastle East vs Newcastle North). A county
    prefix on one side only is allowed (Monmouth, Pontypool vs Pontypool).
    """
    p_base, p_qual = _seat_tokens(printed)
    r_base, r_qual = _seat_tokens(roster)
    left, right = p_base - CONNECTIVES, r_base - CONNECTIVES
    if not left or not right:
        return False

    paired_left, paired_right = set(), set()
    for a in left:
        for b in right:
            if a == b or SequenceMatcher(None, a, b).ratio() >= 0.80:
                paired_left.add(a)
                paired_right.add(b)
    if not paired_left:
        return False
    if (left - paired_left) and (right - paired_right):
        return False
    if p_qual and r_qual and not (p_qual & r_qual):
        return False
    return True


def score(name: ParsedName, spell: Spell) -> tuple[float, str]:
    value, why = 0.55, "surname"
    if name.initials and spell.initials:
        if name.initials == spell.initials:
            value, why = 0.97, "initials"
        elif spell.initials.startswith(name.initials):
            value, why = 0.88, "initial_prefix"
        elif name.initials[0] == spell.initials[0]:
            value, why = 0.72, "first_initial"
        else:
            return 0.05, "initials_conflict"
    elif name.forenames:
        first = name.forenames.split()[0].lower()
        theirs = (spell.forenames.split() or [""])[0].lower()
        if first == theirs:
            value, why = 0.95, "forename"
        elif theirs.startswith(first) or first.startswith(theirs):
            value, why = 0.80, "forename_prefix"
        else:
            return 0.05, "forename_conflict"
    if name.constituency and spell.constituency:
        if seats_agree(name.constituency, spell.constituency):
            value, why = min(0.99, value + 0.15), why + "+seat"
        else:
            value, why = max(0.10, value - 0.35), why + "+seat_conflict"
    return value, why


class Roster:
    def __init__(self, spells: Iterable[Spell]):
        self.by_surname: dict[str, list[Spell]] = defaultdict(list)
        self.by_id: dict[str, Spell] = {}
        for spell in spells:
            self.by_surname[spell.surname_key].append(spell)
            self.by_id[spell.member_id] = spell

    @classmethod
    def from_csv(cls, path: Path) -> "Roster":
        def day(value: str) -> Optional[date]:
            value = (value or "").strip()
            return date.fromisoformat(value) if value else None

        with path.open(newline="", encoding="utf-8") as fh:
            return cls(
                Spell(
                    member_id=row["member_id"], surname=row["surname"],
                    forenames=row.get("forenames", ""),
                    constituency=row.get("constituency", ""),
                    start=day(row.get("start", "")), end=day(row.get("end", "")),
                    party=row.get("party", ""),
                )
                for row in csv.DictReader(fh)
            )

    def candidates(self, name: ParsedName, day: Optional[date]
                   ) -> list[tuple[float, str, Spell]]:
        pool = [s for s in self.by_surname.get(name.surname_key, []) if s.covers(day)]
        scored = []
        for spell in pool:
            value, why = score(name, spell)
            if len(pool) == 1 and value >= 0.50:
                value, why = max(value, 0.85), why + "+unique_surname"
            scored.append((value, why, spell))
        return sorted(scored, key=lambda t: -t[0])


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------

def resolve(rows: list[dict], roster: Roster) -> None:
    """Annotate rows in place with member_id, confidence and method."""
    names: dict[tuple[str, str], Optional[ParsedName]] = {}
    for row in rows:
        row_key = (row["raw_name"], row["is_teller"])
        if row_key not in names:
            names[row_key] = parse_name(
                row["raw_name"], teller=row["is_teller"] in {"1", "True", "true"}
            )
        row["_name"] = names[row_key]
        row["_date"] = date.fromisoformat(row["date"]) if row["date"] else None
        row["_cands"] = (
            roster.candidates(row["_name"], row["_date"]) if row["_name"] else []
        )

    # Pass 1: within each division, one spell may be used once. Assign greedily
    # by descending score so the best-evidenced entry claims a contested spell.
    claims: list[tuple[float, str, int, str]] = []
    for i, row in enumerate(rows):
        for value, why, spell in row["_cands"]:
            claims.append((value, why, i, spell.member_id))
    claims.sort(key=lambda c: -c[0])

    used: dict[str, set[str]] = defaultdict(set)
    for value, why, i, member_id in claims:
        row = rows[i]
        if row.get("member_id") or member_id in used[row["division_id"]]:
            continue
        cands = row["_cands"]
        if len(cands) > 1 and cands[0][0] - cands[1][0] < AMBIGUITY_GAP:
            continue
        if value < THRESHOLD:
            continue
        row.update(member_id=member_id, match_confidence=round(value, 3),
                   match_method=why)
        used[row["division_id"]].add(member_id)

    # Pass 2: a name string resolved confidently anywhere resolves everywhere the
    # same member was sitting, which recovers entries printed without initials.
    learned: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        if row.get("member_id"):
            learned[row["raw_name"]].append(row["member_id"])
    for raw, ids in learned.items():
        learned[raw] = [i for i in set(ids)]

    for row in rows:
        if row.get("member_id") or not row["_name"]:
            continue
        options = [
            roster.by_id[i] for i in learned.get(row["raw_name"], [])
            if i in roster.by_id and roster.by_id[i].covers(row["_date"])
            and i not in used[row["division_id"]]
        ]
        if len(options) == 1:
            row.update(member_id=options[0].member_id, match_confidence=0.80,
                       match_method="propagated")
            used[row["division_id"]].add(options[0].member_id)


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

CARRY = [
    "division_id", "series", "volume", "date", "division_number", "time",
    "column_start", "debate_title", "question_text", "ayes_declared",
    "noes_declared", "ayes_extracted", "noes_extracted", "division_flags",
    "side", "is_teller", "raw_name",
]
ADDED = [
    "member_id", "surname", "initials", "forenames", "honorific", "rank",
    "constituency", "match_confidence", "match_method", "candidates", "name_flags",
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path, default=DATA_DIR / RAW_NAME)
    ap.add_argument("--roster", type=Path, default=DATA_DIR / "member_spells.csv")
    ap.add_argument("--out", type=Path, default=DATA_DIR / RESOLVED_NAME)
    ap.add_argument("--review", type=Path, default=DATA_DIR / REVIEW_NAME)
    args = ap.parse_args()

    with args.raw.open(newline="", encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if r["raw_name"]]
    roster = Roster.from_csv(args.roster)
    resolve(rows, roster)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CARRY + ADDED)
        writer.writeheader()
        for row in rows:
            name = row["_name"]
            writer.writerow({
                **{k: row.get(k, "") for k in CARRY},
                "member_id": row.get("member_id", ""),
                "surname": name.surname if name else "",
                "initials": name.initials if name else "",
                "forenames": name.forenames if name else "",
                "honorific": name.honorific if name else "",
                "rank": name.rank if name else "",
                "constituency": name.constituency if name else "",
                "match_confidence": row.get("match_confidence", ""),
                "match_method": row.get("match_method", "unmatched"),
                "candidates": len(row["_cands"]),
                "name_flags": ";".join(name.flags) if name else "unparsed",
            })

    with args.review.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=[
            "division_id", "date", "side", "raw_name", "candidates",
            "option_1", "score_1", "option_2", "score_2", "option_3", "score_3",
        ])
        writer.writeheader()
        review = 0
        for row in rows:
            if row.get("member_id"):
                continue
            review += 1
            entry = {
                "division_id": row["division_id"], "date": row["date"],
                "side": row["side"], "raw_name": row["raw_name"],
                "candidates": len(row["_cands"]),
            }
            for n, (value, _, spell) in enumerate(row["_cands"][:3], start=1):
                entry[f"option_{n}"] = f"{spell.member_id} {spell.surname}, {spell.forenames} ({spell.constituency})"
                entry[f"score_{n}"] = round(value, 3)
            writer.writerow(entry)

    matched = sum(1 for r in rows if r.get("member_id"))
    print(f"{len(rows)} rows, {matched} matched ({matched / len(rows):.1%}), "
          f"{review} to review")
    print(f"{args.out}\n{args.review}")


if __name__ == "__main__":
    main()
"""
====================

Parsing and name-resolution layer for UK House of Commons division lists,
5th series Hansard (1909- ), targeting the 1910-1987 coverage gap.

Two stages, deliberately kept separate:

  Stage 1  parse_division()      raw division text  ->  structured Division
  Stage 2  MemberRoster.match()  parsed name + date ->  member_id

Stage 1 is mechanical and should be near-lossless. Stage 2 is the hard part
and is where you want to keep every scrap of evidence the printed list gives
you (initials, constituency, honorific, rank) rather than collapsing to a
surname early.

Design note on ordering
-----------------------
Historic Hansard prints division lists in two or three newspaper-style
columns. Naive text extraction interleaves them wrongly. This does not
matter: a division list is a *set*, not a sequence. Never rely on order.
The one thing you must not lose is which side (Aye/No) a name sat under,
and the tellers, who are votes but are printed outside the lists.
"""

from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher
from typing import Iterable, Iterator, Optional, Sequence


# ---------------------------------------------------------------------------
# 1. Vocabularies
# ---------------------------------------------------------------------------

# Stripped before surname parsing, but retained on the record: honorifics are
# a weak identity signal (Rt Hon => held/holds Cabinet office; Sir => baronet
# or knight) and a strong OCR-error signal.
HONORIFICS = {
    "mr", "mrs", "miss", "ms", "dr", "sir", "dame", "lord", "lady",
    "viscount", "viscountess", "earl", "marquess", "hon", "rt hon",
    "right hon", "the hon",
}

# Military and professional ranks are extremely common 1918-1955. Note the
# spelling drift: "Lieut.-Colonel", "Lieut.-Col.", "Lt.-Col.", "Lieutenant-
# Colonel" are the same rank across four decades of typesetting.
RANKS = {
    "col", "colonel", "lt col", "lieut col", "lieutenant colonel",
    "lt comdr", "lieut commander", "commander", "capt", "captain",
    "maj", "major", "brig", "brigadier", "brigadier general",
    "gen", "general", "adm", "admiral", "rear admiral", "vice admiral",
    "sqn ldr", "squadron leader", "wing comdr", "wing commander",
    "flt lieut", "flight lieutenant", "sir", "prof", "professor",
    "alderman", "earl",
}

# Surname particles that must stay attached to the surname.
PARTICLES = {
    "de", "de la", "du", "van", "van der", "von", "le", "la",
    "o", "mac", "mc", "st", "ap",
}

# OCR confusions seen in scanned 5th-series division lists. Applied only to
# the normalised matching key, never to the stored surface form.
OCR_SUBSTITUTIONS = [
    (r"\brn\b", "m"),
    (r"(?<=[a-z])1(?=[a-z])", "l"),   # Wi1son -> Wilson
    (r"(?<=[a-z])0(?=[a-z])", "o"),   # J0hnson -> Johnson
    (r"(?<=[a-z])5(?=[a-z])", "s"),
    (r"\bl\.-col", "lt.-col"),
]


# ---------------------------------------------------------------------------
# 2. Records
# ---------------------------------------------------------------------------

@dataclass
class ParsedName:
    """One line of a printed division list, decomposed."""
    raw: str
    surname: str                     # surface form, e.g. "Lloyd-Greame"
    surname_key: str                 # matching key, e.g. "lloydgreame"
    initials: str = ""               # "A.V." -> "AV"
    forenames: str = ""              # spelled-out forenames, if printed
    honorific: str = ""              # "Rt. Hon.", "Sir", "Mr."
    rank: str = ""                   # "Lieut.-Colonel"
    constituency: str = ""           # from trailing parentheses
    is_teller: bool = False
    parse_flags: list[str] = field(default_factory=list)

    @property
    def first_initial(self) -> str:
        if self.initials:
            return self.initials[0]
        if self.forenames:
            return self.forenames[0].upper()
        return ""


@dataclass
class Vote:
    member_id: Optional[str]         # filled by stage 2
    side: str                        # "aye" | "no"
    name: ParsedName
    match_confidence: float = 0.0
    match_method: str = "unmatched"
    match_candidates: int = 0


@dataclass
class Division:
    # --- identification -----------------------------------------------
    division_number: Optional[int] = None     # resets each session
    session: str = ""                         # "1935-36"
    date: Optional[date] = None
    time: str = ""                            # "10.14 p.m." from the header
    # --- source location ----------------------------------------------
    series: str = "5"                         # 5th series from 1909
    volume: Optional[int] = None
    column_start: Optional[int] = None
    source_url: str = ""
    # --- substance ----------------------------------------------------
    debate_title: str = ""                    # enclosing debate heading
    question_text: str = ""                   # the motion actually put
    # --- result -------------------------------------------------------
    ayes_declared: Optional[int] = None       # as printed
    noes_declared: Optional[int] = None
    votes: list[Vote] = field(default_factory=list)
    parse_flags: list[str] = field(default_factory=list)

    @property
    def ayes_counted(self) -> int:
        return sum(1 for v in self.votes if v.side == "aye")

    @property
    def noes_counted(self) -> int:
        return sum(1 for v in self.votes if v.side == "no")

    def reconcile(self) -> list[str]:
        """
        The single most valuable validation you have: Hansard prints the
        declared totals separately from the name lists, so counted-vs-declared
        is a free integrity check on every division. Discrepancies are almost
        always dropped OCR lines or missing tellers, not clerical error in
        the original.
        """
        problems = []
        if self.ayes_declared is not None and self.ayes_counted != self.ayes_declared:
            problems.append(
                f"aye mismatch: counted {self.ayes_counted} vs declared {self.ayes_declared}"
            )
        if self.noes_declared is not None and self.noes_counted != self.noes_declared:
            problems.append(
                f"no mismatch: counted {self.noes_counted} vs declared {self.noes_declared}"
            )
        return problems


# ---------------------------------------------------------------------------
# 3. Name parsing
# ---------------------------------------------------------------------------

_PUNCT_RE = re.compile(r"[.,;:]+")
_WS_RE = re.compile(r"\s+")
_PAREN_RE = re.compile(r"\(([^)]*)\)")
_INITIAL_TOKEN_RE = re.compile(r"^[A-Z]\.?$")


def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    )


def normalise_key(s: str) -> str:
    """Aggressive normalisation for the blocking/matching key only."""
    s = _strip_accents(s).lower()
    for pat, rep in OCR_SUBSTITUTIONS:
        s = re.sub(pat, rep, s)
    # Mac/Mc/M' collapse: MacDonald, McDonald, M'Donald all key to macdonald
    s = re.sub(r"^m['’c]\s*", "mac", s)
    s = re.sub(r"^mac\s+", "mac", s)
    s = re.sub(r"[^a-z]", "", s)
    return s


def _extract_honorific(text: str) -> tuple[str, str]:
    """Pull a leading honorific off a forename blob."""
    m = re.match(
        r"^\s*((?:Rt\.?\s*Hon\.?|Right\s+Hon\.?|The\s+Hon\.?|Hon\.?|Mr\.?|Mrs\.?|"
        r"Miss|Ms\.?|Dr\.?|Sir|Dame|Lady|Lord|Viscountess|Viscount)\s*)",
        text,
        flags=re.I,
    )
    if not m:
        return "", text
    return m.group(1).strip(), text[m.end():]


def _extract_rank(text: str) -> tuple[str, str]:
    # NOTE: longest alternative first in every family. Otherwise "Capt\.?"
    # matches the first four letters of "Captain" and leaves "ain" behind,
    # which then gets parsed as a forename and blocks the match.
    m = re.match(
        r"^\s*((?:Lieutenant|Lieut\.?|Lt\.?)[-\s]*(?:Colonel|Col\.?|Commander|Comdr\.?)|"
        r"Brigadier[-\s]*(?:General)?|Brig\.?[-\s]*(?:General|Gen\.?)?|"
        r"Squadron\s+Leader|Sqn\.?[-\s]*Ldr\.?|"
        r"Wing\s+Commander|Wing\s+Comdr\.?|"
        r"Flight\s+Lieutenant|Flight\s+Lieut\.?|Flt\.?\s*Lieut\.?|"
        r"Colonel|Col\.?|Captain|Capt\.?|Major|Maj\.?|"
        r"Admiral|Adm\.?|Commander|Comdr\.?|"
        r"General|Gen\.?|Professor|Prof\.?|Alderman)"
        r"\.?(?=\s|$)\s*",
        text,
        flags=re.I,
    )
    if not m:
        return "", text
    return m.group(1).strip(), text[m.end():]


def parse_member_name(raw: str, is_teller: bool = False) -> Optional[ParsedName]:
    """
    Parse one printed entry.

    The dominant 5th-series format is:

        Surname, Initials
        Surname, Rt. Hon. Initials
        Surname, Rank Initials (Constituency)
        Surname, Forename (Constituency)

    i.e. surname first, comma, then everything else. This holds for the
    overwhelming majority of lines 1910-1987, but see parse_flags for the
    cases where it does not:

      * no comma at all              "Bevan"            (bare surname)
      * title-as-surname             "Cecil, Lord H."   (honorific after comma)
      * double-barrelled surnames    "Acland-Troyte, Lt.-Col. G. J."
      * particles                    "de Chair, S. S." / "Mander, G. le M."
      * constituency disambiguators  "Davies, R. J. (Westhoughton)"
    """
    raw = raw.strip()
    if not raw:
        return None

    text = _WS_RE.sub(" ", raw)
    flags: list[str] = []

    # Constituency in trailing parentheses. Sometimes doubled:
    # "Griffiths, T. (Monmouth, Pontypool)"
    constituency = ""
    parens = _PAREN_RE.findall(text)
    if parens:
        constituency = parens[-1].strip()
        text = _PAREN_RE.sub("", text).strip()

    # Split on the FIRST comma only. Later commas belong to the constituency
    # (already removed) or are OCR noise.
    if "," in text:
        surname_part, rest = text.split(",", 1)
    else:
        surname_part, rest = text, ""
        flags.append("no_comma")

    surname = surname_part.strip().rstrip(".")
    if not surname:
        return None

    honorific, rest = _extract_honorific(rest)
    rank, rest = _extract_rank(rest)
    # Honorific can follow the rank: "Lt.-Col. Rt. Hon. J. T. C."
    if not honorific:
        honorific, rest = _extract_honorific(rest)

    # Remaining tokens are initials and/or spelled-out forenames.
    initials_chars: list[str] = []
    forenames: list[str] = []
    for tok in rest.replace(".", ". ").split():
        clean = _PUNCT_RE.sub("", tok)
        if not clean:
            continue
        if len(clean) == 1 and clean.isalpha():
            initials_chars.append(clean.upper())
        elif clean.lower() in PARTICLES or clean in {"La", "Le", "De", "Du", "Van"}:
            # "McEntee, V. La T." - particle inside the forename blob
            flags.append("forename_particle")
        elif clean.isalpha():
            forenames.append(clean)
        else:
            flags.append(f"odd_token:{clean}")

    if not initials_chars and not forenames:
        flags.append("no_forename")

    return ParsedName(
        raw=raw,
        surname=surname,
        surname_key=normalise_key(surname),
        initials="".join(initials_chars),
        forenames=" ".join(forenames),
        honorific=honorific,
        rank=rank,
        constituency=constituency,
        is_teller=is_teller,
        parse_flags=flags,
    )


# ---------------------------------------------------------------------------
# 4. Division parsing
# ---------------------------------------------------------------------------

_DIV_HEADER_RE = re.compile(
    r"Division\s+No\.?\s*(\d+)", re.I
)
_TIME_RE = re.compile(r"\[?\s*(\d{1,2}[.:]\d{2}\s*[ap]\.?\s*m\.?)", re.I)
_TOTALS_RE = re.compile(
    r"The\s+House\s+divided:?\s*Ayes,?\s*(\d+)\s*;?\s*Noes,?\s*(\d+)", re.I
)
_AYES_HEAD_RE = re.compile(r"^\s*AYES\.?\s*$", re.I | re.M)
_NOES_HEAD_RE = re.compile(r"^\s*NOES\.?\s*$", re.I | re.M)
_TELLERS_RE = re.compile(
    r"TELLERS\s+FOR\s+THE\s+(AYES|NOES)[.:\s—-]*(.*?)(?=TELLERS|^\s*(?:AYES|NOES)\.?\s*$|\Z)",
    re.I | re.S | re.M,
)
_QUESTION_RE = re.compile(
    r"(Question\s+(?:again\s+)?put,?\s*[\"“][^\"”]*[\"”]\.?|Question\s+put\.)", re.I | re.S
)


def _split_cells(block: str) -> Iterator[str]:
    """
    Yield candidate name entries from a list block.

    Column-aware text extraction from Historic Hansard HTML gives one name per
    <td>; plain-text extraction gives 2-3 names per physical line separated by
    runs of whitespace. Handle both by splitting on newlines AND on runs of
    3+ spaces.
    """
    for line in block.splitlines():
        line = line.strip()
        if not line:
            continue
        for cell in re.split(r"\s{3,}|\t+", line):
            cell = cell.strip()
            if cell:
                yield cell


def _looks_like_name(cell: str) -> bool:
    if len(cell) < 3:
        return False
    if re.match(r"^(tellers|ayes|noes|division|the house)", cell, re.I):
        return False
    if not re.search(r"[A-Za-z]{2}", cell):
        return False
    return True


def parse_tellers(text: str) -> dict[str, list[ParsedName]]:
    """
    Tellers are votes. They are printed as 'Mr. X and Mr. Y', forename-first,
    which is the reverse of the list format, so they need their own parser.
    Omitting them is the most common single cause of a counted-vs-declared
    off-by-two.
    """
    out: dict[str, list[ParsedName]] = {"aye": [], "no": []}
    for side_word, blob in _TELLERS_RE.findall(text):
        side = "aye" if side_word.lower().startswith("aye") else "no"
        blob = _PAREN_RE.sub("", blob)
        for chunk in re.split(r"\band\b|,", blob, flags=re.I):
            chunk = chunk.strip(" .;—-\n")
            if not _looks_like_name(chunk):
                continue
            hon, rest = _extract_honorific(chunk)
            rank, rest = _extract_rank(rest)
            toks = [_PUNCT_RE.sub("", t) for t in rest.split() if _PUNCT_RE.sub("", t)]
            if not toks:
                continue
            surname = toks[-1]
            inits = "".join(t.upper() for t in toks[:-1] if len(t) == 1)
            fores = " ".join(t for t in toks[:-1] if len(t) > 1)
            out[side].append(
                ParsedName(
                    raw=chunk,
                    surname=surname,
                    surname_key=normalise_key(surname),
                    initials=inits,
                    forenames=fores,
                    honorific=hon,
                    rank=rank,
                    is_teller=True,
                    parse_flags=["teller_forename_first"],
                )
            )
    return out


def parse_division(text: str, **meta) -> Division:
    """
    Parse one division from Hansard text. `meta` overrides or supplies fields
    the text does not carry (session, volume, column_start, source_url,
    debate_title, date).
    """
    div = Division(**{k: v for k, v in meta.items() if k in Division.__annotations__})

    if m := _DIV_HEADER_RE.search(text):
        div.division_number = int(m.group(1))
    if not div.time:
        if m := _TIME_RE.search(text[:400]):
            div.time = m.group(1).strip()
    if m := _TOTALS_RE.search(text):
        div.ayes_declared, div.noes_declared = int(m.group(1)), int(m.group(2))
    if not div.question_text:
        if m := _QUESTION_RE.search(text):
            div.question_text = _WS_RE.sub(" ", m.group(1)).strip()

    # Locate the AYES / NOES blocks.
    aye_m = _AYES_HEAD_RE.search(text) or _DIV_HEADER_RE.search(text)
    no_m = _NOES_HEAD_RE.search(text)
    if aye_m is None or no_m is None:
        div.parse_flags.append("missing_side_heading")
        return div

    aye_block = text[aye_m.end(): no_m.start()]
    no_block = text[no_m.end():]

    tellers = parse_tellers(text)

    for side, block in (("aye", aye_block), ("no", no_block)):
        # Drop the teller lines from the name block so they are not parsed twice.
        block = _TELLERS_RE.sub("", block)
        for cell in _split_cells(block):
            if not _looks_like_name(cell):
                continue
            pn = parse_member_name(cell)
            if pn:
                div.votes.append(Vote(member_id=None, side=side, name=pn))
        for pn in tellers[side]:
            div.votes.append(Vote(member_id=None, side=side, name=pn))

    div.parse_flags.extend(div.reconcile())
    return div


# ---------------------------------------------------------------------------
# 5. Roster matching
# ---------------------------------------------------------------------------

def _constituency_agrees(printed: str, roster: str, ratio: float = 0.85) -> bool:
    """
    Constituency strings need fuzzy comparison for three separate reasons:

      * spelling drift over the period   "Merthyr Tydvil" / "Merthyr Tydfil"
      * county-then-seat qualifiers      "(Monmouth, Pontypool)" -> Pontypool
      * ordering and connectives         "Ross and Cromarty" / "Ross & Cromarty"

    Token overlap catches the second, sequence ratio catches the first.
    """
    p_tokens = {normalise_key(t) for t in re.split(r"[,\s/&-]+", printed) if len(t) > 2}
    r_tokens = {normalise_key(t) for t in re.split(r"[,\s/&-]+", roster) if len(t) > 2}
    p_tokens.discard("and")
    r_tokens.discard("and")
    if p_tokens & r_tokens:
        return True
    pk, rk = normalise_key(printed), normalise_key(roster)
    if pk and rk and (pk in rk or rk in pk):
        return True
    for a in p_tokens:
        for b in r_tokens:
            if SequenceMatcher(None, a, b).ratio() >= ratio:
                return True
    return False


@dataclass
class RosterMember:
    member_id: str
    surname: str
    forenames: str
    constituency: str
    start: Optional[date]
    end: Optional[date]
    party: str = ""

    @property
    def surname_key(self) -> str:
        return normalise_key(self.surname)

    @property
    def initials(self) -> str:
        return "".join(w[0].upper() for w in self.forenames.split() if w)

    def serving_on(self, d: Optional[date]) -> bool:
        if d is None:
            return True
        if self.start and d < self.start:
            return False
        if self.end and d > self.end:
            return False
        return True


class MemberRoster:
    """
    Blocking index over a roster of MP service spells.

    Build this from Rush + Wikidata rather than from the division lists
    themselves. The unit is the *spell* (member x seat x date range), not the
    person, because constituency changes are the main disambiguator available.
    """

    def __init__(self, members: Iterable[RosterMember]):
        self.members = list(members)
        self._by_surname: dict[str, list[RosterMember]] = {}
        for m in self.members:
            self._by_surname.setdefault(m.surname_key, []).append(m)

    @classmethod
    def from_csv(cls, path: str) -> "MemberRoster":
        """Expects: member_id,surname,forenames,constituency,start,end,party"""
        def _d(s: str) -> Optional[date]:
            s = (s or "").strip()
            return date.fromisoformat(s) if s else None

        with open(path, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        return cls(
            RosterMember(
                member_id=r["member_id"],
                surname=r["surname"],
                forenames=r.get("forenames", ""),
                constituency=r.get("constituency", ""),
                start=_d(r.get("start", "")),
                end=_d(r.get("end", "")),
                party=r.get("party", ""),
            )
            for r in rows
        )

    # -- scoring ---------------------------------------------------------

    @staticmethod
    def _score(name: ParsedName, m: RosterMember) -> tuple[float, str]:
        score, why = 0.55, "surname+date"

        if name.initials and m.initials:
            if name.initials == m.initials:
                score, why = 0.97, "surname+full_initials"
            elif m.initials.startswith(name.initials):
                score, why = 0.88, "surname+initial_prefix"
            elif name.initials[0] == m.initials[0]:
                score, why = 0.72, "surname+first_initial"
            else:
                return 0.05, "initials_conflict"
        elif name.forenames:
            first = name.forenames.split()[0].lower()
            mfirst = (m.forenames.split() or [""])[0].lower()
            if first == mfirst:
                score, why = 0.95, "surname+forename"
            elif mfirst.startswith(first) or first.startswith(mfirst):
                score, why = 0.80, "surname+forename_prefix"
            else:
                return 0.05, "forename_conflict"

        if name.constituency and m.constituency:
            if _constituency_agrees(name.constituency, m.constituency):
                score, why = min(0.99, score + 0.15), why + "+constituency"
            else:
                score, why = max(0.10, score - 0.35), why + "+constituency_conflict"

        return score, why

    def match(
        self, name: ParsedName, on: Optional[date] = None, threshold: float = 0.60
    ) -> tuple[Optional[RosterMember], float, str, int]:
        """Return (member, confidence, method, n_candidates_considered)."""
        pool = [m for m in self._by_surname.get(name.surname_key, []) if m.serving_on(on)]
        if not pool:
            return None, 0.0, "no_surname_block", 0

        scored = sorted(
            ((self._score(name, m), m) for m in pool), key=lambda t: -t[0][0]
        )
        (best_score, best_why), best = scored[0]

        # Tellers and OCR-truncated lines often carry no initials at all. If
        # exactly one member with that surname was sitting on the date, that is
        # strong evidence on its own - but only when nothing actively conflicts.
        if len(pool) == 1 and best_score >= 0.50:
            best_score, best_why = max(best_score, 0.85), best_why + "+unique_surname"

        # Ambiguity penalty: if the runner-up is close, do not silently pick.
        if len(scored) > 1:
            runner = scored[1][0][0]
            if best_score - runner < 0.10:
                return None, best_score, f"ambiguous({best_why})", len(pool)

        if best_score < threshold:
            return None, best_score, f"below_threshold({best_why})", len(pool)
        return best, best_score, best_why, len(pool)

    def resolve_division(self, div: Division, threshold: float = 0.60) -> Division:
        for v in div.votes:
            m, conf, why, n = self.match(v.name, on=div.date, threshold=threshold)
            v.member_id = m.member_id if m else None
            v.match_confidence = conf
            v.match_method = why
            v.match_candidates = n
        return div


# ---------------------------------------------------------------------------
# 6. Output
# ---------------------------------------------------------------------------

LONG_FIELDS = [
    "division_number", "session", "date", "time", "series", "volume",
    "column_start", "debate_title", "question_text", "ayes_declared",
    "noes_declared", "ayes_counted", "noes_counted", "source_url",
    "side", "member_id", "raw_name", "surname", "initials", "forenames",
    "honorific", "rank", "constituency", "is_teller",
    "match_confidence", "match_method", "match_candidates", "parse_flags",
]


def to_long_rows(div: Division) -> list[dict]:
    """One row per member-division. This is the shape you want on disk."""
    base = {
        "division_number": div.division_number,
        "session": div.session,
        "date": div.date.isoformat() if div.date else "",
        "time": div.time,
        "series": div.series,
        "volume": div.volume,
        "column_start": div.column_start,
        "debate_title": div.debate_title,
        "question_text": div.question_text,
        "ayes_declared": div.ayes_declared,
        "noes_declared": div.noes_declared,
        "ayes_counted": div.ayes_counted,
        "noes_counted": div.noes_counted,
        "source_url": div.source_url,
    }
    rows = []
    for v in div.votes:
        n = v.name
        rows.append({
            **base,
            "side": v.side,
            "member_id": v.member_id or "",
            "raw_name": n.raw,
            "surname": n.surname,
            "initials": n.initials,
            "forenames": n.forenames,
            "honorific": n.honorific,
            "rank": n.rank,
            "constituency": n.constituency,
            "is_teller": int(n.is_teller),
            "match_confidence": round(v.match_confidence, 3),
            "match_method": v.match_method,
            "match_candidates": v.match_candidates,
            "parse_flags": ";".join(n.parse_flags),
        })
    return rows


def write_long_csv(divisions: Sequence[Division], path: str) -> int:
    rows = [r for d in divisions for r in to_long_rows(d)]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=LONG_FIELDS)
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def coverage_report(divisions: Sequence[Division]) -> dict:
    """Run this after every batch. Match rate is your project's headline metric."""
    votes = [v for d in divisions for v in d.votes]
    matched = [v for v in votes if v.member_id]
    ambiguous = [v for v in votes if v.match_method.startswith("ambiguous")]
    unblocked = [v for v in votes if v.match_method == "no_surname_block"]
    return {
        "divisions": len(divisions),
        "vote_rows": len(votes),
        "matched": len(matched),
        "match_rate": round(len(matched) / len(votes), 4) if votes else 0.0,
        "ambiguous": len(ambiguous),
        "surname_not_in_roster": len(unblocked),
        "divisions_failing_reconciliation": sum(
            1 for d in divisions if d.reconcile()
        ),
    }


# ---------------------------------------------------------------------------
# 7. Demo
# ---------------------------------------------------------------------------

_SAMPLE = """
Question put, "That the Bill be now read a Second time."

The House divided: Ayes, 10; Noes, 8.

Division No. 43.]      AYES      [10.14 p.m.

Adamson, W. M.                     Griffiths, T. (Monmouth, Pontypool)
Alexander, Rt. Hon. A. V.          Hall, G. H. (Merthyr Tydvil)
Attlee, Rt. Hon. C. R.             Jones, Morgan (Caerphilly)
Cripps, Sir Stafford               McEntee, V. La T.

TELLERS FOR THE AYES.—
Mr. Charleton and Mr. Whiteley.

NOES.

Acland-Troyte, Lieut.-Col. G. J.   Cecil, Rt. Hon. Lord H.
Baldwin, Rt. Hon. Stanley          de Chair, S. S.
Bossom, A. C.                      Davies, R. J. (Westhoughton)

TELLERS FOR THE NOES.—
Captain Margesson and Sir George Penny.
"""

_ROSTER = [
    RosterMember("R001", "Adamson", "William Murdoch", "Cannock", date(1935, 11, 14), date(1945, 6, 15)),
    RosterMember("R002", "Alexander", "Albert Victor", "Hillsborough", date(1935, 11, 14), date(1950, 2, 23)),
    RosterMember("R003", "Attlee", "Clement Richard", "Limehouse", date(1922, 11, 15), date(1950, 2, 23)),
    RosterMember("R004", "Cripps", "Stafford", "Bristol East", date(1931, 1, 16), date(1950, 2, 23)),
    RosterMember("R005", "Griffiths", "Thomas", "Pontypool", date(1918, 12, 14), date(1935, 10, 25)),
    RosterMember("R006", "Griffiths", "Thomas", "Pontypool", date(1935, 11, 14), date(1945, 6, 15)),
    RosterMember("R007", "Hall", "George Henry", "Merthyr Tydfil", date(1922, 11, 15), date(1946, 10, 1)),
    RosterMember("R008", "Jones", "Morgan", "Caerphilly", date(1921, 8, 1), date(1939, 4, 23)),
    RosterMember("R009", "Jones", "Morgan", "Denbigh", date(1930, 1, 1), date(1945, 6, 15)),
    RosterMember("R010", "McEntee", "Valentine La Touche", "Walthamstow West", date(1929, 5, 30), date(1950, 2, 23)),
    RosterMember("R011", "Acland-Troyte", "Gilbert John", "Tiverton", date(1924, 10, 29), date(1945, 6, 15)),
    RosterMember("R012", "Baldwin", "Stanley", "Bewdley", date(1908, 1, 1), date(1937, 5, 28)),
    RosterMember("R013", "Bossom", "Alfred Charles", "Maidstone", date(1931, 10, 27), date(1959, 9, 18)),
    RosterMember("R014", "Cecil", "Hugh Richard Heathcote", "Oxford University", date(1910, 1, 1), date(1937, 2, 1)),
    RosterMember("R015", "de Chair", "Somerset Struben", "South West Norfolk", date(1935, 11, 14), date(1945, 6, 15)),
    RosterMember("R016", "Davies", "Rhys John", "Westhoughton", date(1921, 1, 1), date(1951, 10, 5)),
    RosterMember("R017", "Charleton", "Henry Cecil", "Leeds South", date(1922, 11, 15), date(1945, 6, 15)),
    RosterMember("R018", "Whiteley", "William", "Blaydon", date(1922, 11, 15), date(1955, 5, 6)),
    RosterMember("R019", "Margesson", "Henry David Reginald", "Rugby", date(1924, 10, 29), date(1942, 1, 1)),
    RosterMember("R020", "Penny", "George", "Kingston upon Thames", date(1922, 11, 15), date(1937, 1, 1)),
]


def _demo() -> None:
    div = parse_division(
        _SAMPLE,
        session="1935-36",
        date=date(1936, 3, 11),
        volume=310,
        column_start=1024,
        debate_title="Tithe Bill",
        source_url="https://api.parliament.uk/historic-hansard/commons/1936/mar/11",
    )

    print(f"Division {div.division_number}  {div.date}  {div.time}")
    print(f"declared {div.ayes_declared}-{div.noes_declared} | "
          f"counted {div.ayes_counted}-{div.noes_counted}")
    print(f"question: {div.question_text}")
    print(f"flags: {div.parse_flags or 'none'}\n")

    roster = MemberRoster(_ROSTER)
    roster.resolve_division(div)

    print(f"{'side':5} {'id':6} {'conf':>5}  {'method':34} raw")
    print("-" * 100)
    for v in sorted(div.votes, key=lambda v: (v.side, v.name.surname_key)):
        print(f"{v.side:5} {(v.member_id or '--'):6} {v.match_confidence:5.2f}  "
              f"{v.match_method:34} {v.name.raw}")

    print()
    for k, val in coverage_report([div]).items():
        print(f"  {k:34} {val}")

    n = write_long_csv([div], "/mnt/user-data/outputs/divisions_long_sample.csv")
    print(f"\nwrote {n} rows")


if __name__ == "__main__":
    _demo()
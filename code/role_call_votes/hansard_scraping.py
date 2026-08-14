#!/usr/bin/env python3
"""
Download 5th/6th series Hansard volumes and extract Commons division lists.

Writes one row per name-in-division to data/role_call_votes/divisions_raw.csv.
Volume zips are streamed and discarded, never cached. Names are stored verbatim;
name parsing and member matching happen in hansard_resolve.py.

    python hansard_scrape.py --series 5 --from 1 --to 1000
    python hansard_scrape.py --series 6 --from 1 --to 100 --resume

--resume reads the volumes already present in the output file and skips them.
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import sys
import time
import zipfile
from datetime import date
from pathlib import Path
from typing import Iterator, Optional
from xml.etree import ElementTree as ET

import requests

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

# code/role_call_votes/hansard_scrape.py -> project root -> data/role_call_votes
DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "role_call_votes"
OUT_NAME = "divisions_raw.csv"

BASE = "https://www.hansard-archive.parliament.uk"
SERIES_DIR = {
    5: "Official_Report,_House_of_Commons_(5th_Series)_Vol_1_(Jan_1909)_to_Vol_1000_(March_1981)",
    6: "The_Official_Report,_House_of_Commons_(6th_Series)_Vol_1_(March_1981)_to_2004",
}
DELAY = 2.0
UA = "academic-research-scraper (LSE; maggie.myslik@lse.ac.uk)"

FIELDS = [
    "division_id", "series", "volume", "sitting_file", "date", "division_number",
    "time", "column_start", "debate_title", "question_text",
    "ayes_declared", "noes_declared", "ayes_extracted", "noes_extracted",
    "side", "is_teller", "raw_name", "division_flags", "source_url",
]

# --------------------------------------------------------------------------
# Patterns
# --------------------------------------------------------------------------

DIV_HEADER = re.compile(r"Division\s+No\.?\s*(\d+)", re.I)
TIME = re.compile(r"(\d{1,2}[.:]\d{2}\s*[ap]\.?\s*m\.?)", re.I)
# Accepts both printed forms: "Ayes, 10; Noes, 8" and "Ayes 306, Noes 296".
TOTALS = re.compile(
    r"(?:The\s+House\s+divided|Division\s+No)[^\n]{0,80}?"
    r"Ayes[,\s]+(\d+)\s*[;,]\s*Noes[,\s]+(\d+)", re.I | re.S
)
TOTALS_LOOSE = re.compile(r"\bAyes[,\s]+(\d+)\s*[;,]\s*Noes[,\s]+(\d+)", re.I)
AYES_HEAD = re.compile(r"^\s*AYES\.?\s*$", re.I | re.M)
NOES_HEAD = re.compile(r"^\s*NOES\.?\s*$", re.I | re.M)
TELLERS = re.compile(
    r"TELLERS\s+FOR\s+THE\s+(AYES|NOES)[.:\s\u2014-]*(.*?)"
    r"(?=TELLERS|^\s*(?:AYES|NOES)\.?\s*$|Question\s+(?:accordingly\s+)?"
    r"(?:agreed|negatived|put)|\Z)", re.I | re.S | re.M
)
QUESTION = re.compile(
    r"Question\s+(?:again\s+)?put[^\n]{0,400}?[\"\u201c][^\"\u201d]{0,400}[\"\u201d]|"
    r"Question\s+put\.", re.I | re.S
)
DIV_END = re.compile(r"Question\s+(?:accordingly\s+)?(?:agreed\s+to|negatived)", re.I)
ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
COMPACT_DATE = re.compile(r"(19\d{2})(\d{2})(\d{2})")

# Fragments produced when a single printed entry is split by wide OCR spacing.
FRAGMENT_START = re.compile(
    r"^(?:Rt\.?\s*Hon|Right\s+Hon|Hon|Mr|Mrs|Miss|Ms|Dr|Sir|Dame|Lady|Lord|"
    r"Viscount(?:ess)?|Lieut|Lt|Col|Capt|Major|Maj|Brig|Gen|Adm|Comdr|Commander|"
    r"Professor|Prof|Alderman|[A-Z]\.)", re.I
)
NOT_A_NAME = re.compile(r"^(?:tellers|ayes|noes|division|the\s+house|question|col\b)", re.I)

TITLE_MARK = "\x01T\x01"
COL_MARK = "\x01C\x01"


# --------------------------------------------------------------------------
# XML handling
# --------------------------------------------------------------------------

def _tag(el) -> str:
    return el.tag.rsplit("}", 1)[-1].lower() if isinstance(el.tag, str) else ""


def _line(value: Optional[str]) -> str:
    """Fold newlines but keep wide runs of spaces: they are the column gutter."""
    if not value:
        return ""
    return re.sub(r"[\n\r\f\v]+", " ", value).strip()


def flatten(root) -> str:
    """Document-order text, with section titles and column numbers marked."""
    parts: list[str] = []
    for el in root.iter():
        tag = _tag(el)
        text = _line(el.text)
        if text:
            if "title" in tag or "heading" in tag:
                parts.append(TITLE_MARK + text)
            elif tag in {"col", "colnum", "column"}:
                parts.append(COL_MARK + text)
            else:
                parts.append(text)
        tail = _line(el.tail)
        if tail:
            parts.append(tail)
    return "\n".join(parts)


def sitting_date(root, filename: str) -> Optional[date]:
    for el in root.iter():
        for value in el.attrib.values():
            if m := ISO_DATE.search(str(value)):
                try:
                    return date(int(m[1]), int(m[2]), int(m[3]))
                except ValueError:
                    pass
    if m := COMPACT_DATE.search(filename):
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            pass
    return None


# --------------------------------------------------------------------------
# Division extraction
# --------------------------------------------------------------------------

def _cells(block: str) -> list[str]:
    """Split a name block into entries, rejoining entries broken by wide spacing."""
    out: list[str] = []
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith(("\x01",)):
            continue
        for cell in re.split(r"\s{3,}|\t+", line):
            cell = cell.strip()
            if not cell or NOT_A_NAME.match(cell):
                continue
            if "," not in cell and out and FRAGMENT_START.match(cell) and (
                out[-1].endswith(",") or "," not in out[-1]
            ):
                out[-1] = f"{out[-1]} {cell}".replace(", ,", ",")
            else:
                out.append(cell)
    return [c for c in (c.strip() for c in out) if len(c) >= 3 and re.search(r"[A-Za-z]{2}", c)]


def _tellers(text: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {"aye": [], "no": []}
    for side_word, blob in TELLERS.findall(text):
        side = "aye" if side_word.lower().startswith("aye") else "no"
        for chunk in re.split(r"\band\b|,", blob, flags=re.I):
            chunk = chunk.strip(" .;\u2014-\n")
            if chunk and not NOT_A_NAME.match(chunk) and re.search(r"[A-Za-z]{2}", chunk):
                found[side].append(chunk)
    return found


def _last_before(text: str, index: int, mark: str) -> str:
    cut = text.rfind(mark, 0, index)
    if cut == -1 or (cut and text[cut - 1] != "\n"):
        return ""
    return text[cut + len(mark):].split("\n", 1)[0].strip()


def divisions_in_file(xml_bytes: bytes, filename: str, series: int, volume: int
                      ) -> Iterator[dict]:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return
    text = flatten(root)
    day = sitting_date(root, filename)
    marks = list(DIV_HEADER.finditer(text))

    for i, mark in enumerate(marks):
        stop = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        chunk = text[mark.start():stop]
        if end := DIV_END.search(chunk):
            chunk = chunk[:end.end()]
        floor = marks[i - 1].end() if i else 0
        window = text[max(floor, mark.start() - 1500):mark.start()] + chunk

        flags: list[str] = []
        if day is None:
            flags.append("no_date")

        aye_head, no_head = AYES_HEAD.search(chunk), NOES_HEAD.search(chunk)
        hits = list(TOTALS.finditer(window)) or list(TOTALS_LOOSE.finditer(window))
        declared = (int(hits[-1][1]), int(hits[-1][2])) if hits else (None, None)

        entries: list[tuple[str, str, int]] = []
        if aye_head and no_head and no_head.start() > aye_head.start():
            blocks = {
                "aye": TELLERS.sub("", chunk[aye_head.end():no_head.start()]),
                "no": TELLERS.sub("", chunk[no_head.end():]),
            }
            for side, block in blocks.items():
                entries += [(side, name, 0) for name in _cells(block)]
            for side, names in _tellers(chunk).items():
                entries += [(side, name, 1) for name in names]
        else:
            flags.append("no_name_lists")

        counted = {
            "aye": sum(1 for s, _, _ in entries if s == "aye"),
            "no": sum(1 for s, _, _ in entries if s == "no"),
        }
        if declared[0] is None:
            flags.append("no_declared_totals")
        else:
            if counted["aye"] != declared[0]:
                flags.append(f"aye_mismatch:{counted['aye']}v{declared[0]}")
            if counted["no"] != declared[1]:
                flags.append(f"no_mismatch:{counted['no']}v{declared[1]}")

        asked = list(QUESTION.finditer(window))
        question = re.sub(r"\s+", " ", asked[-1][0]).strip() if asked else ""
        column = _last_before(text, mark.start(), COL_MARK)
        time_m = TIME.search(chunk[:400])
        base = {
            "division_id": f"S{series}CV{volume:04d}-{day or 'nodate'}-{mark[1]}",
            "series": series,
            "volume": volume,
            "sitting_file": filename,
            "date": day.isoformat() if day else "",
            "division_number": mark[1],
            "time": time_m[1].strip() if time_m else "",
            "column_start": column,
            "debate_title": _last_before(text, mark.start(), TITLE_MARK),
            "question_text": question,
            "ayes_declared": declared[0] if declared[0] is not None else "",
            "noes_declared": declared[1] if declared[1] is not None else "",
            "ayes_extracted": counted["aye"],
            "noes_extracted": counted["no"],
            "division_flags": ";".join(flags),
            "source_url": f"{BASE}/{SERIES_DIR[series]}/S{series}CV{volume:04d}P0.zip",
        }
        for side, name, teller in entries:
            yield {**base, "side": side, "is_teller": teller, "raw_name": name}
        if not entries:
            yield {**base, "side": "", "is_teller": "", "raw_name": ""}


# --------------------------------------------------------------------------
# Fetch and drive
# --------------------------------------------------------------------------

def volume_zip(series: int, volume: int) -> Optional[zipfile.ZipFile]:
    url = f"{BASE}/{SERIES_DIR[series]}/S{series}CV{volume:04d}P0.zip"
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=180)
    except requests.RequestException as exc:
        print(f"vol {volume}: {exc}", file=sys.stderr)
        return None
    finally:
        time.sleep(DELAY)
    if r.status_code == 404 or not r.content.startswith(b"PK"):
        print(f"vol {volume}: unavailable ({r.status_code})", file=sys.stderr)
        return None
    return zipfile.ZipFile(io.BytesIO(r.content))


def done_volumes(path: Path, series: int) -> set[int]:
    if not path.exists():
        return set()
    with path.open(newline="", encoding="utf-8") as fh:
        return {
            int(row["volume"]) for row in csv.DictReader(fh)
            if row.get("series") == str(series) and row.get("volume")
        }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", type=int, default=5, choices=[5, 6])
    ap.add_argument("--from", dest="lo", type=int, required=True)
    ap.add_argument("--to", dest="hi", type=int, required=True)
    ap.add_argument("--out", type=Path, default=DATA_DIR / OUT_NAME)
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    skip = done_volumes(args.out, args.series) if args.resume else set()
    append = args.resume and args.out.exists()

    rows = divisions = 0
    with args.out.open("a" if append else "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        if not append:
            writer.writeheader()
        for volume in range(args.lo, args.hi + 1):
            if volume in skip:
                continue
            archive = volume_zip(args.series, volume)
            if archive is None:
                continue
            seen: set[str] = set()
            with archive:
                for name in sorted(n for n in archive.namelist() if n.lower().endswith(".xml")):
                    for row in divisions_in_file(
                        archive.read(name), name, args.series, volume
                    ):
                        writer.writerow(row)
                        rows += 1
                        seen.add(row["division_id"])
            divisions += len(seen)
            fh.flush()
            print(f"vol {volume}: {len(seen)} divisions")

    print(f"{divisions} divisions, {rows} rows -> {args.out}")


if __name__ == "__main__":
    main()
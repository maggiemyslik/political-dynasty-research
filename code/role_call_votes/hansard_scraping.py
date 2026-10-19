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

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "role_call_votes"
OUT_NAME = "divisions_raw.csv"

BASE = "https://www.hansard-archive.parliament.uk"
SERIES_DIR = {
    5: "Official_Report,_House_of_Commons_(5th_Series)_Vol_1_(Jan_1909)_to_Vol_1000_(March_1981)",
    6: "The_Official_Report,_House_of_Commons_(6th_Series)_Vol_1_(March_1981)_to_2004",
}
DELAY = 2.0
# missing volumes and volumes past the end return the same page, so the end is fixed:
# 5th series 1000 (folder name), 6th series 428 (Nov-Dec 2004)
LAST_VOLUME = {5: 1000, 6: 428}
MAX_TOTAL = 700
RETRY_WAITS = [30, 120]
FINAL_PASS_WAIT = 300
UA = "academic-research-scraper (LSE; maggie.myslik@lse.ac.uk)"

FIELDS = [
    "division_id", "series", "volume", "sitting_file", "date", "division_number",
    "time", "column_start", "debate_title", "question_text",
    "ayes_declared", "noes_declared", "ayes_extracted", "noes_extracted",
    "side", "is_teller", "raw_name", "division_flags", "source_url",
]

# must start a line, so "Division No. 45" mid-sentence doesn't split a division
DIV_HEADER = re.compile(r"^\s*Divi?si?[o0]n\s+N[o0]\.?\s*(\d+)", re.I | re.M)
TIME = re.compile(r"(\d{1,2}[.:]\d{2}\s*[ap]\.?\s*m\.?)", re.I)
TOTALS = re.compile(
    r"(?:The\s+(?:House|Committee)\s+divided|Division\s+No).{0,80}?"
    r"A[yv][a-z]{1,3}[,\s]+(\d+)\s*[;,]\s*N[o0][a-z]{1,3}[,\s]+(\d+)", re.I | re.S
)
TOTALS_LOOSE = re.compile(r"\bA[yv][a-z]{1,3}[,\s]+(\d+)\s*[;,]\s*N[o0][a-z]{1,3}[,\s]+(\d+)", re.I)
AYES_HEAD = re.compile(r"^\s*AYE[S5]\.?\s*(?:\[[^\n]*)?$", re.I | re.M)
NOES_HEAD = re.compile(r"^\s*N[O0]ES\.?\s*(?:\[[^\n]*)?$", re.I | re.M)
TELLER_HEAD = re.compile(r"TELLERS\.?\s+F[O0][RB]\s+THE\s+(?:AYE[S5]|N[O0]ES)[.:\s\u2014-]*", re.I)
QUESTION = re.compile(
    r"Question\s+(?:again\s+)?put[^\n]{0,400}?[\"\u201c][^\"\u201d]{0,400}[\"\u201d]|"
    r"Question\s+put\.", re.I | re.S
)
DIV_END = re.compile(r"Question\s+(?:accordingly\s+)?(?:agreed\s+to|negatived)", re.I)
ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
COMPACT_DATE = re.compile(r"(19\d{2})(\d{2})(\d{2})")

FRAGMENT_START = re.compile(
    r"^(?:Rt\.?\s*Hon|Right\s+Hon|Hon|Mr|Mrs|Miss|Ms|Dr|Sir|Dame|Lady|Lord|"
    r"Viscount(?:ess)?|Lieut|Lt|Col|Capt|Major|Maj|Brig|Gen|Adm|Comdr|Commander|"
    r"Professor|Prof|Alderman|[A-Z]\.)", re.I
)
NOT_A_NAME = re.compile(r"^(?:tellers|ayes|noes|division|the\s+(?:house|committee)|question|col\b)", re.I)
LIST_ENTRY = re.compile(r"^[A-Z][A-Za-z'\-]+(?:[ -][A-Za-z'\-]+)*,\s")

TITLE_MARK = "\x01T\x01"
COL_MARK = "\x01C\x01"


def _tag(el) -> str:
    return el.tag.rsplit("}", 1)[-1].lower() if isinstance(el.tag, str) else ""


def _line(value: Optional[str]) -> str:
    # keep runs of spaces, they're the column gutter
    if not value:
        return ""
    return re.sub(r"[\n\r\f\v]+", " ", value).strip()


def flatten(root) -> str:
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


def _cells(block: str) -> list[str]:
    out: list[str] = []
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith(("\x01",)):
            continue
        for cell in re.split(r"\s{3,}|\t+", line):
            cell = cell.strip()
            if not cell or NOT_A_NAME.match(cell):
                continue
            # rejoin an entry broken up by wide OCR spacing
            if "," not in cell and out and FRAGMENT_START.match(cell) and (
                out[-1].endswith(",") or "," not in out[-1]
            ):
                out[-1] = f"{out[-1]} {cell}".replace(", ,", ",")
            else:
                out.append(cell)
    return [c for c in (c.strip() for c in out) if len(c) >= 3 and re.search(r"[A-Za-z]{2}", c)]


def _is_prose(line: str) -> bool:
    return (
        line.startswith(TITLE_MARK)
        or line.startswith((":", "\u00a3", '"'))
        # a single stray digit is OCR damage in a name ("Sears, 1. E."), so it's kept
        or re.search(r"\d{2,}|^\d|\b(?:Sec|Vote|Class)\.\s*\d|\d\s*[ap]\.?\s*m\b", line, re.I) is not None
        or len(line) > 70
        # speaker labels ("Mr. HAY") vs OCR capitals in a "Surname, Forename" entry
        or (re.search(r"\b[A-Z]{3,}\b", line) is not None and not LIST_ENTRY.match(line))
        or NOT_A_NAME.match(line) is not None
    )


def _list_lines(block: str, is_noes: bool) -> list[str]:
    # lists can break off for debate at a column break and resume, so prose is skipped,
    # not treated as the end. Noes end at the first prose after their tellers heading
    lines: list[str] = []
    first_break: int | None = None
    tellers_seen = False
    raw = [l.strip() for l in block.splitlines()]
    joined: list[str] = []
    for line in raw:
        # constituency on its own line: "Benyon, Thomas" / "(A'don)"
        if line.startswith("(") and joined and joined[-1] and not joined[-1].startswith(("\x01", "[")):
            joined[-1] = f"{joined[-1]} {line}"
            continue
        # unclosed bracket carries on to the next line, but never swallows a name unless
        # the line ends in "(" (stray brackets: "Barker, G. (Monmouth, (Abertillery)")
        if joined and joined[-1].count("(") > joined[-1].count(")") \
                and len(joined[-1]) < 70 and not line.startswith(COL_MARK) \
                and (joined[-1].endswith("(") or not LIST_ENTRY.match(line)):
            joined[-1] = f"{joined[-1]}{line}" if joined[-1].endswith("(") or line == ")" \
                else f"{joined[-1]} {line}"
        else:
            joined.append(line)
    for line in joined:
        if not line or line.startswith(COL_MARK) or line.startswith("["):
            continue
        if TELLER_HEAD.match(line):
            tellers_seen = True
            lines.append(line)
            continue
        if _is_prose(line):
            if line.startswith(":"):
                # drop the speaker label in the line or two before a speech
                for j in range(max(0, len(lines) - 2), len(lines)):
                    cand = lines[j]
                    if FRAGMENT_START.match(cand) and not cand.endswith(".") \
                            and not TELLER_HEAD.match(cand):
                        del lines[j:]
                        break
            if is_noes and tellers_seen:
                break
            if first_break is None:
                first_break = len(lines)
            continue
        lines.append(line)
    # no tellers heading on the Noes: nothing marks the end, keep up to the first break
    if is_noes and not tellers_seen and first_break is not None:
        lines = lines[:first_break]
    return lines


def _columns(lines: list[str]) -> int:
    # read across the page, so the lag with the smallest alphabetical jumps = no. of columns
    keys = [l[0].lower() for l in lines if l[:1].isalpha() and not TELLER_HEAD.match(l)][:60]
    if len(keys) < 12:
        return 3

    def jump(k: int) -> float:
        return sum(abs(ord(keys[i + k]) - ord(keys[i])) for i in range(len(keys) - k)) / (len(keys) - k)

    return min((2, 3), key=jump)


def _split_tellers(lines: list[str]) -> tuple[list[str], list[str], bool]:
    """Returns (names, tellers, tellers heading found).
    Teller text sits in one column, so it shows up every 2nd/3rd line from the heading."""
    start = next((i for i, l in enumerate(lines) if TELLER_HEAD.match(l)), None)
    if start is None:
        return lines, [], False
    stride = _columns(lines[:start])
    used, text = {start}, ""
    first = start
    if not TELLER_HEAD.sub("", lines[start], count=1).strip() and start + 1 < len(lines) \
            and lines[start + 1].lstrip().startswith(("\u2014", "-", "\u2013")):
        first = start + 1  # bare heading, names start on the next line
    i, k = first, 0
    while i < len(lines):
        frag = TELLER_HEAD.sub("", lines[i], count=1) if i == start else lines[i]
        used.add(i)
        text = f"{text} {frag}".strip()
        # done once it reads "A and B." ending in a surname, not an initial ("George D.")
        if re.search(r"\band\b.*\b[A-Za-z'-]*[a-z]{2,}[A-Za-z'-]*\.$", text, re.I) or k >= 4:
            break
        # short last columns: the stride can overrun the list
        i, k = (i + stride if i + stride < len(lines) else i + 1), k + 1
    names = [l for i, l in enumerate(lines) if i not in used]
    tellers = [t.strip(" .;:\u2014-") for t in re.split(r"\s+and\s+", text, maxsplit=1, flags=re.I)]
    return names, [t for t in tellers if re.search(r"[A-Za-z]{2}", t)], True


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
        # totals are printed before or after the header, and the other spot holds a
        # neighbour's totals. keep both, pick the one closer to the names counted
        before = text[max(floor, mark.start() - 1500):mark.start()]
        before_hits = list(TOTALS.finditer(before)) or list(TOTALS_LOOSE.finditer(before))
        after_hit = next(TOTALS.finditer(chunk), None)
        candidates = [(int(m[1]), int(m[2])) for m in (before_hits[-1:] + [after_hit]) if m]
        # never more than 707 seats, so anything over is misread OCR ("3271")
        implausible = [c for c in candidates if max(c) > MAX_TOTAL]
        if implausible:
            flags.append(f"implausible_totals:{implausible[0][0]}v{implausible[0][1]}")
            candidates = []

        entries: list[tuple[str, str, int]] = []
        if no_head and not aye_head:
            flags.append("no_ayes_heading")
            aye_start = chunk.find("\n") + 1
        else:
            aye_start = aye_head.end() if aye_head else 0
        if no_head and no_head.start() > aye_start:
            sides = {
                "aye": _list_lines(chunk[aye_start:no_head.start()], is_noes=False),
                "no": _list_lines(chunk[no_head.end():], is_noes=True),
            }
            for side, lines in sides.items():
                names, tellers, found = _split_tellers(lines)
                if not found:
                    flags.append(f"missing_{side}_tellers")
                entries += [(side, name, 0) for name in _cells("\n".join(names))]
                entries += [(side, name, 1) for name in tellers]
        else:
            flags.append("no_name_lists")

        voters = {side: [n.lower() for s, n, t in entries if s == side and not t] for side in ("aye", "no")}
        both = set(voters["aye"]) & set(voters["no"])
        if both:
            flags.append(f"names_on_both_sides:{len(both)}")
        repeats = sum(len(v) - len(set(v)) for v in voters.values())
        if repeats:
            flags.append(f"repeated_names:{repeats}")

        # printed totals exclude tellers
        counted = {
            "aye": sum(1 for s, _, t in entries if s == "aye" and not t),
            "no": sum(1 for s, _, t in entries if s == "no" and not t),
        }
        declared = min(candidates, key=lambda c: abs(c[0] - counted["aye"]) + abs(c[1] - counted["no"])) \
            if candidates else (None, None)
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


def volume_zip(series: int, volume: int) -> tuple[Optional[zipfile.ZipFile], bool]:
    """Returns (archive, absent). absent = archive has no such volume."""
    url = f"{BASE}/{SERIES_DIR[series]}/S{series}CV{volume:04d}P0.zip"
    for attempt, wait in enumerate(RETRY_WAITS + [None]):
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=180)
            break
        except requests.RequestException as exc:
            if wait is None:
                print(f"S{series} vol {volume}: failed after {attempt + 1} attempts ({exc})",
                      file=sys.stderr)
                return None, False
            print(f"S{series} vol {volume}: connection problem, retrying in {wait}s", file=sys.stderr)
            time.sleep(wait)
        finally:
            time.sleep(DELAY)
    # the archive answers a missing volume with a 200 HTML page, never a 404
    if not r.content.startswith(b"PK"):
        print(f"S{series} vol {volume}: not in archive", file=sys.stderr)
        return None, r.ok
    return zipfile.ZipFile(io.BytesIO(r.content)), False


def keep_only(path: Path, keep: set[str]) -> int:
    # drops rows from volumes not in the done file (interrupted part-way)
    tmp = path.with_suffix(".tmp")
    dropped = 0
    with path.open(newline="", encoding="utf-8") as src, \
         tmp.open("w", newline="", encoding="utf-8") as dst:
        writer = csv.DictWriter(dst, fieldnames=FIELDS)
        writer.writeheader()
        for row in csv.DictReader(src):
            if row["source_url"] in keep:
                writer.writerow(row)
            else:
                dropped += 1
    tmp.replace(path)
    return dropped


def main() -> None:
    out = DATA_DIR / OUT_NAME
    done_path = DATA_DIR / "divisions_raw_done.txt"
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    done = set(done_path.read_text().split()) if done_path.exists() else set()
    if out.exists():
        dropped = keep_only(out, done)
        if dropped:
            print(f"removed {dropped} rows from unfinished volumes")
    else:
        with out.open("w", newline="", encoding="utf-8") as fh:
            csv.DictWriter(fh, fieldnames=FIELDS).writeheader()

    jobs = [(series, volume) for series in SERIES_DIR
            for volume in range(1, (LAST_VOLUME[series] or 0) + 1)]
    rows = divisions = 0
    with out.open("a", newline="", encoding="utf-8") as fh, \
         done_path.open("a", encoding="utf-8") as done_fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)

        def scrape(series: int, volume: int) -> bool:
            # False only if the download failed
            nonlocal rows, divisions
            url = f"{BASE}/{SERIES_DIR[series]}/S{series}CV{volume:04d}P0.zip"
            if url in done:
                return True
            archive, absent = volume_zip(series, volume)
            if archive is None:
                if absent:
                    done_fh.write(url + "\n")
                    done_fh.flush()
                    return True
                return False
            seen: set[str] = set()
            with archive:
                for name in sorted(n for n in archive.namelist() if n.lower().endswith(".xml")):
                    for row in divisions_in_file(archive.read(name), name, series, volume):
                        writer.writerow(row)
                        rows += 1
                        seen.add(row["division_id"])
            divisions += len(seen)
            fh.flush()
            done_fh.write(url + "\n")
            done_fh.flush()
            print(f"S{series} vol {volume}: {len(seen)} divisions")
            return True

        failed = [job for job in jobs if not scrape(*job)]
        if failed:
            print(f"{len(failed)} volumes failed to download; retrying them in {FINAL_PASS_WAIT}s")
            time.sleep(FINAL_PASS_WAIT)
            failed = [job for job in failed if not scrape(*job)]

    print(f"{divisions} divisions, {rows} rows added -> {out}")
    if failed:
        print(f"STILL FAILED ({len(failed)}): " + ", ".join(f"S{s} vol {v}" for s, v in failed))
        print("Run the scraper again to fetch just these.")
    else:
        print("All volumes done.")


if __name__ == "__main__":
    main()
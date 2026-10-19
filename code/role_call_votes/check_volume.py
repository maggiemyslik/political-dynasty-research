# python code/role_call_votes/check_volume.py 5 864 > vol864.txt   every division in a volume
# python code/role_call_votes/check_volume.py 5 268 289            raw text of one division to a file

import re
import sys
from collections import Counter
from itertools import groupby
from pathlib import Path
from xml.etree import ElementTree as ET

import hansard_scraping as h

series = int(sys.argv[1]) if len(sys.argv) > 1 else 5
volume = int(sys.argv[2]) if len(sys.argv) > 2 else 1
number = sys.argv[3] if len(sys.argv) > 3 else None


def readable(text: str) -> str:
    return text.replace(h.TITLE_MARK, "[TITLE] ").replace(h.COL_MARK, "[COL] ")


def rejected(chunk: str) -> list[str]:
    # short lines inside the lists that the parser treated as prose
    aye_head, no_head = h.AYES_HEAD.search(chunk), h.NOES_HEAD.search(chunk)
    if not no_head:
        return []
    start = aye_head.end() if aye_head else chunk.find("\n") + 1
    out = []
    for line in (l.strip() for l in chunk[start:].splitlines()):
        if line.startswith(h.TITLE_MARK):
            break
        if h.TELLER_HEAD.match(line) or h.NOES_HEAD.match(line):
            continue
        if line and len(line) <= 70 and not line.startswith(("\x01", "[", ":")) and h._is_prose(line):
            out.append(line)
    return out


def reason(line: str) -> str:
    if re.search(r"\d", line):
        return "digit"
    if re.search(r"\b[A-Z]{3,}\b", line):
        return "capitals"
    if h.NOT_A_NAME.match(line):
        return "not-a-name word"
    if line.startswith(("\u00a3", '"')):
        return "punctuation start"
    return "other"


archive, _ = h.volume_zip(series, volume)
if archive is None:
    sys.exit("could not download that volume")
with archive:
    files = {n: archive.read(n) for n in sorted(archive.namelist()) if n.lower().endswith(".xml")}

if number:
    text = "\n".join(h.flatten(ET.fromstring(xml)) for xml in files.values())
    marks = list(h.DIV_HEADER.finditer(text))
    i = next((i for i, m in enumerate(marks) if m[1] == number), None)
    if i is None:
        sys.exit(f"division {number} not found in volume {volume}")
    start = max(0, marks[i].start() - 1500)
    stop = marks[i + 1].start() if i + 1 < len(marks) else len(text)
    out = Path(f"dump_S{series}_vol{volume}_div{number}.txt")
    out.write_text(readable(text[start:min(stop, start + 60_000)]), encoding="utf-8")
    sys.exit(f"wrote {out}")

total = exact = short = 0
reasons = Counter()
for name, xml in files.items():
    try:
        text = h.flatten(ET.fromstring(xml))
    except ET.ParseError:
        print(f"{name}: XML does not parse")
        continue
    marks = list(h.DIV_HEADER.finditer(text))
    chunks: dict[str, str] = {}
    for i, m in enumerate(marks):
        stop = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        chunks.setdefault(m[1], text[m.start():stop])

    rows = h.divisions_in_file(xml, name, series, volume)
    for did, group in groupby(rows, key=lambda r: r["division_id"]):
        group = list(group)
        r = group[0]
        total += 1
        match = str(r["ayes_extracted"]) == str(r["ayes_declared"]) \
            and str(r["noes_extracted"]) == str(r["noes_declared"])
        exact += match
        tellers = [f"{x['side']}: {x['raw_name']}" for x in group if x["is_teller"] == 1]
        print(f"{did}  ayes {r['ayes_extracted']}/{r['ayes_declared'] or '?'}"
              f"  noes {r['noes_extracted']}/{r['noes_declared'] or '?'}  {'OK' if match else 'MISMATCH'}")
        print(f"    tellers: {'; '.join(tellers) or 'none found'}")
        if r["division_flags"]:
            print(f"    flags: {r['division_flags']}")
        if r["ayes_declared"] == "":
            continue
        if max(r["ayes_declared"] - r["ayes_extracted"], r["noes_declared"] - r["noes_extracted"]) > 2:
            short += 1
            lines = rejected(chunks.get(r["division_number"], ""))
            reasons.update(reason(l) for l in lines)
            for line in lines[:10]:
                print(f"    rejected [{reason(line)}] {line}")

print(f"\n{total} divisions, {exact} matching the printed totals exactly, {short} more than 2 names short")
if reasons:
    print("rejected lines in those: " + ", ".join(f"{k} {v}" for k, v in reasons.most_common()))

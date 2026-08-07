#!/usr/bin/env python3
"""
fetch_rush_relationships.py
===========================
Pull the FULL typed kinship graph from the Rush "Members of Parliament after
1832" database and merge it onto the Wikidata panel (MPdata1_2_basic_bio.dta),
keyed by rushid. 

The Rush database (History of Parliament Trust, CC-BY 4.0) records member-to-
member relationships with 18 typed labels. 

member IDs ARE our `rushid`
(verified: /members/2102 = Joseph Chamberlain).

USAGE
-----
pip install requests beautifulsoup4 pandas pyreadstat
python fetch_rush_relationships.py --dta MPdata1_2_basic_bio.dta \
        --out rush_kinship.csv [--compare mp_relatives_coded.xlsx] [--selftest]

dataset at https://rush-datasette.shedcode.co.uk exposes the raw tables
with built-in CSV/JSON export

we can use that for the `members` table rather than scraping 10k member pages.
"""
from __future__ import annotations
import argparse, re, sys, time
from collections import defaultdict

BASE = "https://rushdatabase.shedcode.co.uk"

# 18 relationship types (id -> site label), taken from /relationship_types.
RELATIONSHIP_TYPE_IDS = {
    1: "Indirect relative of", 2: "Cousin of", 3: "Sibling of", 4: "Child of",
    5: "Parent of", 6: "Nibling of", 7: "Grandchild of", 8: "Grandparent of",
    9: "Spouse of", 10: "Pibling of", 11: "Great great niece / nephew of",
    12: "Great grandchild of", 13: "Great grandparent of",
    14: "Great great aunt / uncle of", 15: "Partner of", 16: "Civil partner of",
    17: "Great aunt / uncle of", 18: "Great niece / nephew of",
}

# Translate a directed edge label into the relation as seen by the FROM member
# and by the TO member. ("A <label> B": what is B to A, and what is A to B.)
# 'from' = relation of the *other* person (B) to A; 'to' = relation of A to B.
EDGE_PERSPECTIVE = {
    "Parent of":            ("child", "parent"),
    "Child of":             ("parent", "child"),
    "Grandparent of":       ("grandchild", "grandparent"),
    "Grandchild of":        ("grandparent", "grandchild"),
    "Pibling of":           ("nibling", "pibling"),        # pibling = aunt/uncle
    "Nibling of":           ("pibling", "nibling"),        # nibling = niece/nephew
    "Great grandparent of": ("great-grandchild", "great-grandparent"),
    "Great grandchild of":  ("great-grandparent", "great-grandchild"),
    "Great aunt / uncle of":        ("great-nibling", "great-pibling"),
    "Great niece / nephew of":      ("great-pibling", "great-nibling"),
    "Great great aunt / uncle of":  ("great-great-nibling", "great-great-pibling"),
    "Great great niece / nephew of":("great-great-pibling", "great-great-nibling"),
    # symmetric
    "Sibling of":      ("sibling", "sibling"),
    "Cousin of":       ("cousin", "cousin"),
    "Spouse of":       ("spouse", "spouse"),
    "Civil partner of":("civil partner", "civil partner"),
    "Partner of":      ("partner", "partner"),
    "Indirect relative of": ("indirect", "indirect"),
}

# Nearest-relation priority (lower = closer). Blood relations rank above marital.
CLOSENESS_RANK = {
    "parent": 1, "child": 1, "sibling": 2,
    "grandparent": 3, "grandchild": 3,
    "pibling": 4, "nibling": 4,
    "cousin": 5,
    "great-grandparent": 6, "great-grandchild": 6,
    "great-pibling": 6, "great-nibling": 6,
    "great-great-pibling": 7, "great-great-nibling": 7,
    "indirect": 8,
    "spouse": 9, "civil partner": 9, "partner": 9,
}

# Gender refinement for the labels Valentino wants spelled out.
GENDERED = {
    ("parent", "Male"): "father", ("parent", "Female"): "mother",
    ("child", "Male"): "son", ("child", "Female"): "daughter",
    ("sibling", "Male"): "brother", ("sibling", "Female"): "sister",
    ("pibling", "Male"): "uncle", ("pibling", "Female"): "aunt",
    ("nibling", "Male"): "nephew", ("nibling", "Female"): "niece",
    ("grandparent", "Male"): "grandfather", ("grandparent", "Female"): "grandmother",
    ("grandchild", "Male"): "grandson", ("grandchild", "Female"): "granddaughter",
}

MEMBER_RE = re.compile(r"/members/(\d+)")


# --------------------------------------------------------------------------
# Scraping
# --------------------------------------------------------------------------
def _session():
    import requests
    s = requests.Session()
    s.headers.update({
        "User-Agent": "academic-research/1.0 (LSE peerage-effects project; CC-BY data)",
        "Accept": "text/html",
    })
    return s


def fetch_relationship_edges(sess, delay=1.0):
    """Return list of (from_rushid, to_rushid, label) across all 18 types."""
    from bs4 import BeautifulSoup
    edges = []
    for tid, label in RELATIONSHIP_TYPE_IDS.items():
        url = f"{BASE}/relationship_types/{tid}"
        for attempt in range(3):
            try:
                r = sess.get(url, timeout=30); r.raise_for_status(); break
            except Exception as e:
                if attempt == 2:
                    print(f"  ! giving up on {url}: {e}", file=sys.stderr); r = None
                time.sleep(2 * (attempt + 1))
        if r is None:
            continue
        pairs = parse_relationship_table(r.text, BeautifulSoup)
        for a, b in pairs:
            edges.append((a, b, label))
        print(f"  type {tid:>2} {label:<32} {len(pairs):>4} edges")
        time.sleep(delay)
    return edges


def parse_relationship_table(html, BeautifulSoup=None):
    """Extract (from_id, to_id) integer pairs from a relationship_type page.

    The page renders one <table> with two columns (From, To), each cell an
    <a href='/members/ID'>. We read the table row-wise so From/To stay paired.
    Falls back to a pairwise scan of member links if no table is found.
    """
    if BeautifulSoup is not None:
        soup = BeautifulSoup(html, "html.parser")
        table = None
        for t in soup.find_all("table"):
            head = " ".join(th.get_text(strip=True).lower() for th in t.find_all("th"))
            if "from" in head and "to" in head:
                table = t; break
        if table is None:
            table = soup.find("table")
        pairs = []
        if table is not None:
            for tr in table.find_all("tr"):
                links = tr.find_all("a", href=MEMBER_RE)
                ids = [int(MEMBER_RE.search(a["href"]).group(1)) for a in links]
                if len(ids) >= 2:
                    pairs.append((ids[0], ids[1]))
            if pairs:
                return pairs
    # regex fallback: consecutive /members/ID links, paired (from, to)
    ids = [int(m) for m in MEMBER_RE.findall(html)]
    return list(zip(ids[0::2], ids[1::2]))


# --------------------------------------------------------------------------
# Coding
# --------------------------------------------------------------------------
def build_member_kinship(edges, gender_map=None, name_map=None):
    """edges: list of (from_id, to_id, label). Returns dict rushid -> summary."""
    gender_map = gender_map or {}
    name_map = name_map or {}
    # subject -> set of (other_id, relation)
    rel = defaultdict(set)
    for a, b, label in edges:
        persp = EDGE_PERSPECTIVE.get(label)
        if persp is None:
            continue
        rel_from, rel_to = persp
        rel[a].add((b, rel_from))   # from A's view, B is rel_from
        rel[b].add((a, rel_to))     # from B's view, A is rel_to

    out = {}
    for rid, links in rel.items():
        blood = [(o, r) for (o, r) in links if r not in ("spouse", "civil partner", "partner")]
        relations = {r for (_, r) in links}
        # nearest relation
        best = min(links, key=lambda x: CLOSENESS_RANK.get(x[1], 99))
        best_other, best_rel = best
        g = gender_map.get(best_other)
        closeness = GENDERED.get((best_rel, g), best_rel)
        others = {o for (o, _) in links}
        names = sorted({name_map.get(o, str(o)) for o in others})
        out[rid] = dict(
            has_relative_rush=1,
            closeness_rush=closeness,
            relation_types_rush="; ".join(sorted(relations)),
            n_relatives_rush=len(others),
            multiple_relatives_rush=int(len(others) > 1 or len(relations) > 1),
            has_blood_relative_rush=int(len(blood) > 0),
            relative_names_rush=" | ".join(names),
        )
    return out


# --------------------------------------------------------------------------
# Merge
# --------------------------------------------------------------------------
def load_panel(dta_path):
    import pyreadstat, pandas as pd
    df, _ = pyreadstat.read_dta(dta_path)
    # collapse to unique MPs by politician_id (mirror of the first-pass pipeline)
    df["rushid_int"] = pd.to_numeric(df.get("rushid"), errors="coerce").astype("Int64")
    keep = ["politician_id", "politician_name", "rushid", "rushid_int", "gender"]
    keep = [c for c in keep if c in df.columns]
    uniq = df[keep].dropna(subset=["politician_id"]).drop_duplicates("politician_id")
    return uniq


def main():
    import pandas as pd
    ap = argparse.ArgumentParser()
    ap.add_argument("--dta", help="path to MPdata1_2_basic_bio.dta")
    ap.add_argument("--out", default="rush_kinship.csv")
    ap.add_argument("--edges-out", default="rush_relationship_edges.csv")
    ap.add_argument("--compare", help="existing coded CSV to check agreement (col has_relative)")
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--selftest", action="store_true", help="run offline logic test and exit")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    gender_map, name_map = {}, {}
    panel = None
    if args.dta:
        panel = load_panel(args.dta)
        gender_map = dict(zip(panel["rushid_int"].dropna().astype(int),
                              panel.loc[panel["rushid_int"].notna(), "gender"]))
        name_map = dict(zip(panel["rushid_int"].dropna().astype(int),
                            panel.loc[panel["rushid_int"].notna(), "politician_name"]))

    print("Fetching Rush relationship graph (18 types)…")
    sess = _session()
    edges = fetch_relationship_edges(sess, delay=args.delay)
    pd.DataFrame(edges, columns=["from_rushid", "to_rushid", "label"]).to_csv(args.edges_out, index=False)
    print(f"→ {len(edges)} directed edges saved to {args.edges_out}")

    kin = build_member_kinship(edges, gender_map, name_map)
    kin_df = pd.DataFrame.from_dict(kin, orient="index").reset_index().rename(columns={"index": "rushid_int"})
    print(f"→ {len(kin_df)} MPs have >=1 Rush relationship")

    if panel is not None:
        merged = panel.merge(kin_df, on="rushid_int", how="left")
        for c in ["has_relative_rush", "multiple_relatives_rush", "has_blood_relative_rush", "n_relatives_rush"]:
            merged[c] = merged[c].fillna(0).astype(int)
        merged.to_csv(args.out, index=False)
        print(f"→ merged onto {len(merged)} panel MPs → {args.out}")
        print(f"   has_relative_rush = 1 : {int(merged['has_relative_rush'].sum())}")

        if args.compare:
            comp = pd.read_csv(args.compare)[["politician_id", "has_relative"]]
            j = merged.merge(comp, on="politician_id", how="inner")
            both = int(((j.has_relative == 1) & (j.has_relative_rush == 1)).sum())
            only_wd = int(((j.has_relative == 1) & (j.has_relative_rush == 0)).sum())
            only_rush = int(((j.has_relative == 0) & (j.has_relative_rush == 1)).sum())
            neither = int(((j.has_relative == 0) & (j.has_relative_rush == 0)).sum())
            print("\n=== Agreement: Wikidata pass vs Rush graph ===")
            print(f"  both flag a relative       : {both}")
            print(f"  Wikidata only (Rush blank) : {only_wd}")
            print(f"  Rush only (Wikidata missed): {only_rush}")
            print(f"  neither                    : {neither}")
    else:
        kin_df.to_csv(args.out, index=False)
        print(f"→ (no --dta) kinship-by-rushid saved to {args.out}")


def selftest():
    """Validate the coding logic offline against known cases."""
    edges = [
        (2102, 2200, "Parent of"),   # Joseph -> Austen
        (2102, 7764, "Parent of"),   # Joseph -> Neville
        (2200, 7764, "Sibling of"),  # Austen ~ Neville
        (9690, 298,  "Cousin of"),   # Mark Garnier ~ Edward Garnier
        (298,  9690, "Cousin of"),
    ]
    genders = {2102: "Male", 2200: "Male", 7764: "Male", 9690: "Male", 298: "Male"}
    names = {2102: "Joseph Chamberlain", 2200: "Austen Chamberlain",
             7764: "Neville Chamberlain", 9690: "Mark Garnier", 298: "Edward Garnier"}
    kin = build_member_kinship(edges, genders, names)
    ok = True
    def check(rid, field, expect):
        nonlocal ok
        got = kin[rid][field]
        flag = "OK " if got == expect else "FAIL"
        if got != expect: ok = False
        print(f"  [{flag}] member {rid} {field} = {got!r} (expect {expect!r})")
    # Austen: parent Joseph (Male -> father) is closest; also sibling Neville
    check(2200, "closeness_rush", "father")
    check(2200, "has_relative_rush", 1)
    check(2200, "multiple_relatives_rush", 1)
    # Joseph: children Austen+Neville -> closest 'child' -> Male -> 'son'
    check(2102, "closeness_rush", "son")
    check(2102, "n_relatives_rush", 2)
    # Garnier: cousin
    check(9690, "closeness_rush", "cousin")
    check(9690, "has_blood_relative_rush", 1)
    print("SELFTEST:", "PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main() or 0)

#!/usr/bin/env python3
"""
mp_relatives_pipeline.py  (script 1 of 2)
=========================================
Offline pipeline that reproduces the first-pass coding of whether each
post-1832 MP had a relative in Parliament, and the cousin enrichment drawn
from the Rush database. 

Creates the two files:

    mp_relatives_coded.csv      one row per MP, with the coded relative fields
    mp_relationship_edges.csv   one row per directed relationship (edge list)

Steps:
    1. Load the Stata panel.
    2. Normalise text columns (resolve the mixed missing-value encoding).
    3. Collapse the panel to one row per MP.
    4. Build the Wikidata kinship graph.
    5. Assemble the coded relative fields (has_relative, closeness, and so on).
    6. Merge the bundled Rush cousin relationships.
    7. Write the coded CSV, the edge list, and a summary to standard output.

Usage:
    pip install pandas numpy pyreadstat
    python mp_relatives_pipeline.py --dta MPdata1_2_basic_bio.dta --outdir .
"""
from __future__ import annotations
import argparse
import os
import pandas as pd
import numpy as np

# ---------------------------------------------------------------------------
# Bundled Rush 'Cousin of' relationships (directed member-id pairs).
# Cached extract of https://rushdatabase.shedcode.co.uk/relationship_types/2
# (retrieved 30 July 2026). Member ids equal the panel's rushid. This is 156
# of the 157 records the page reports; the missing record is a duplicate
# direction and changes no MP's coding. fetch_rush_relationships.py pulls the
# authoritative set, and all other relationship types, from the live source.
# ---------------------------------------------------------------------------
RUSH_COUSIN_EDGES = [
 (1073,8996),(8996,1073),(6665,3551),(7218,6467),(6467,7218),(1405,1404),
 (6618,2073),(1311,2073),(6691,6692),(6692,6691),(1310,2073),(2073,1311),
 (2073,6618),(2073,1310),(3350,6439),(3350,6739),(3350,1524),(3886,3887),
 (3887,3886),(4243,4245),(4243,4244),(4244,4242),(4244,4243),(4244,4245),
 (4245,4244),(4245,4242),(4809,4808),(4897,4896),(6821,5416),(5246,5244),
 (5246,5242),(5246,5247),(5242,5246),(5244,5246),(5245,5246),(4242,4245),
 (4245,4243),(739,6159),(6159,739),(7070,2666),(9436,7096),(8678,8863),
 (8863,8678),(5566,5565),(5563,5565),(5565,5566),(5565,5563),(4280,4281),
 (4281,4280),(9437,7160),(7160,9437),(6694,1442),(2983,2984),(2984,2983),
 (3486,2102),(6278,6934),(6403,4097),(1098,1097),(7027,1097),(1097,1098),
 (1097,7027),(4517,4518),(2481,2480),(9026,6994),(3903,3989),(3986,3989),
 (3989,3903),(3989,3986),(3989,3985),(3985,3989),(1633,6800),(1651,1652),
 (1652,1651),(6787,1615),(7393,7395),(7395,7393),(5152,5153),(5153,5152),
 (2042,6694),(3056,3055),(3056,3057),(3055,3056),(1739,6846),(1739,1030),
 (2853,7311),(7311,2853),(7311,2854),(2854,7311),(927,6261),(798,7181),
 (4955,9114),(4957,9114),(4808,4809),(6739,3350),(2712,6636),(6636,2712),
 (6800,1633),(4242,4243),(4242,4244),(4243,4242),(5040,1554),(1554,5040),
 (6439,3350),(4407,4408),(4408,4407),(4518,4517),(5246,5245),(5247,5246),
 (9436,7095),(9436,7094),(9436,7091),(6905,15),(15,6905),(9442,5248),
 (794,806),(798,806),(794,7181),(7181,806),(806,7181),(806,798),(806,794),
 (728,5118),(2186,1873),(1988,1873),(5695,5696),(5696,5695),(2259,1073),
 (6523,5248),(7211,7218),(7218,7211),(7212,764),(7215,7218),(7218,7215),
 (3599,5578),(5578,3599),(7231,7232),(7232,7231),(7096,9050),(9690,298),
 (298,9690),(5662,8783),(8783,5662),(5662,8743),(1048,1612),(3056,3060),
 (3551,6665),(2854,2855),(2855,2853),(2855,2854),(2853,2855),(927,2123),
 (7181,798),(7181,794),(821,809),(809,821),(4761,6262),
]

# nearest-relation priority for the closeness field (lower rank is closer)
PRIORITY = ["father", "mother", "child", "sibling",
            "grandparent", "grandchild", "uncle/aunt", "nephew/niece"]


# ---------------------------------------------------------------------------
# Step 1-2: load and normalise
# ---------------------------------------------------------------------------
def load_and_clean(dta_path):
    import pyreadstat
    df, _ = pyreadstat.read_dta(dta_path)

    def norm(series):
        # cast to the nullable string dtype, strip, and treat the several
        # literal spellings of 'missing' as true missing values. Using the
        # plain str dtype here would turn real NaNs into the text 'nan' and
        # inflate every fill rate to 100 per cent.
        x = series.astype("string").str.strip()
        return x.mask(x.str.lower().isin(["", "nan", "none", ".", "na"]))

    text_cols = [c for c in df.columns
                 if df[c].dtype == object or str(df[c].dtype) == "str"]
    for c in text_cols:
        df[c] = norm(df[c])
    for c in ["start_year", "end_year", "parliament_number",
              "cabinet_position_dummy", "birth", "death"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df[df["politician_id"].notna()].copy()


# ---------------------------------------------------------------------------
# Step 3: collapse to one row per MP
# ---------------------------------------------------------------------------
def collapse(df):
    def first_valid(s):
        s = s.dropna()
        return s.iloc[0] if len(s) else pd.NA

    static = ["politician_name", "first_name", "last_name", "gender",
              "birth", "death", "rushid", "peerageid", "whoswhoid", "oxnatbibid",
              "father_id", "father_name", "rushid_f", "peerageid_f",
              "whoswhoid_f", "oxnatbibid_f", "mother_id", "mother_name",
              "rushid_m", "peerageid_m", "whoswhoid_m", "oxnatbibid_m"]
    static = [c for c in static if c in df.columns]

    g = df.groupby("politician_id", sort=False)
    mp = g.agg({c: first_valid for c in static})
    mp["n_parliaments"] = g["parliament_number"].nunique()
    mp["first_parliament"] = g["parliament_number"].min()
    mp["last_parliament"] = g["parliament_number"].max()
    mp["first_year"] = g["start_year"].min()
    mp["last_year"] = g["end_year"].max()
    mp["ever_cabinet"] = g["cabinet_position_dummy"].max().fillna(0).astype(int)
    mp["n_constituencies"] = g["constituency"].nunique()
    mp["parties"] = g["party_wikidata"].apply(
        lambda s: "; ".join(sorted(set(s.dropna()))))
    return mp.reset_index()


# ---------------------------------------------------------------------------
# Step 4: Wikidata kinship graph
# ---------------------------------------------------------------------------
def build_kinship(mp):
    MPQ = set(mp["politician_id"])
    father = dict(zip(mp["politician_id"], mp["father_id"]))
    mother = dict(zip(mp["politician_id"], mp["mother_id"]))

    def parent_mp(pid_series):
        return pid_series.where(pid_series.isin(MPQ))

    mp["father_mp_q"] = parent_mp(mp["father_id"])
    mp["mother_mp_q"] = parent_mp(mp["mother_id"])
    mp["father_mp"] = mp["father_mp_q"].notna()
    mp["mother_mp"] = mp["mother_mp_q"].notna()
    # a Rush id on the parent means the parent sits in Rush's MP database,
    # i.e. the parent was itself an MP even where the panel lacks the QID link
    mp["father_in_rush"] = mp["rushid_f"].notna()
    mp["mother_in_rush"] = mp["rushid_m"].notna()

    # siblings: MPs sharing a non-null parent id
    sib = {q: set() for q in MPQ}
    for col in ["father_id", "mother_id"]:
        for _, grp in mp.dropna(subset=[col]).groupby(col):
            members = list(grp["politician_id"])
            if len(members) > 1:
                for m in members:
                    sib[m] |= (set(members) - {m})
    mp["sibling_mps"] = mp["politician_id"].map(lambda q: sib[q])

    # children: invert the upward parent pointers
    children_of = {q: set() for q in MPQ}
    for q in MPQ:
        for p in (father.get(q), mother.get(q)):
            if pd.notna(p) and p in MPQ:
                children_of[p].add(q)
    mp["child_mps"] = mp["politician_id"].map(lambda q: children_of[q])

    def grandparents(q):
        out = set()
        for p in (father.get(q), mother.get(q)):
            if pd.notna(p) and p in MPQ:
                for gp in (father.get(p), mother.get(p)):
                    if pd.notna(gp) and gp in MPQ:
                        out.add(gp)
        return out

    def grandchildren(q):
        out = set()
        for c in children_of[q]:
            out |= children_of[c]
        return out

    def uncles(q):  # a grandparent's other children
        out = set()
        for p in (father.get(q), mother.get(q)):
            if pd.notna(p) and p in MPQ:
                for gp in (father.get(p), mother.get(p)):
                    if pd.notna(gp) and gp in MPQ:
                        out |= (children_of[gp] - {p})
        return out

    def nephews(q):  # children of one's siblings
        out = set()
        for s in sib[q]:
            out |= children_of[s]
        return out

    mp["grandparent_mps"] = mp["politician_id"].map(grandparents)
    mp["grandchild_mps"] = mp["politician_id"].map(grandchildren)
    mp["uncle_mps"] = mp["politician_id"].map(uncles)
    mp["nephew_mps"] = mp["politician_id"].map(nephews)
    return mp


# ---------------------------------------------------------------------------
# Step 5: assemble coded fields
# ---------------------------------------------------------------------------
def assemble_fields(mp):
    name_by_q = dict(zip(mp["politician_id"], mp["politician_name"]))
    first_year_by_q = dict(zip(mp["politician_id"], mp["first_year"]))

    def relation_set(r):
        rels = []
        if r["father_mp"] or r["father_in_rush"]:
            rels.append("father")
        if r["mother_mp"] or r["mother_in_rush"]:
            rels.append("mother")
        if len(r["child_mps"]):
            rels.append("child")
        if len(r["sibling_mps"]):
            rels.append("sibling")
        if len(r["grandparent_mps"]):
            rels.append("grandparent")
        if len(r["grandchild_mps"]):
            rels.append("grandchild")
        if len(r["uncle_mps"]):
            rels.append("uncle/aunt")
        if len(r["nephew_mps"]):
            rels.append("nephew/niece")
        return rels

    mp["relation_types"] = mp.apply(relation_set, axis=1)
    mp["has_relative"] = (mp["relation_types"].map(len) > 0).astype(int)

    def nearest(rels):
        for p in PRIORITY:
            if p in rels:
                return p
        return pd.NA
    mp["closeness"] = mp["relation_types"].map(nearest)

    def relative_qids(r):
        ids = set()
        for c in ["father_mp_q", "mother_mp_q"]:
            if pd.notna(r[c]):
                ids.add(r[c])
        ids |= (r["sibling_mps"] | r["grandparent_mps"] | r["uncle_mps"]
                | r["child_mps"] | r["grandchild_mps"] | r["nephew_mps"])
        return ids
    mp["relative_qids"] = mp.apply(relative_qids, axis=1)
    mp["n_relatives_in_panel"] = mp["relative_qids"].map(len)
    mp["multiple_relatives"] = ((mp["n_relatives_in_panel"] > 1) |
                                (mp["relation_types"].map(len) > 1)).astype(int)

    def preceded(r):
        my = r["first_year"]
        if pd.isna(my) or not r["relative_qids"]:
            return 1 if (r["father_in_rush"] or r["mother_in_rush"]) else pd.NA
        yrs = [first_year_by_q.get(q) for q in r["relative_qids"]]
        yrs = [y for y in yrs if pd.notna(y)]
        earlier = any(y < my for y in yrs) or r["father_in_rush"] or r["mother_in_rush"]
        return int(earlier)
    mp["relative_preceded"] = mp.apply(preceded, axis=1)

    mp["relative_chamber"] = np.where(mp["has_relative"] == 1, "HoC", pd.NA)
    mp["peerage_linked"] = (mp["peerageid"].notna() | mp["peerageid_f"].notna()
                            | mp["peerageid_m"].notna()).astype(int)

    def examples(r):
        parts = []
        if r["father_mp"] or r["father_in_rush"]:
            parts.append(f"father: {r['father_name']}" if pd.notna(r["father_name"]) else "father")
        if r["mother_mp"] or r["mother_in_rush"]:
            parts.append(f"mother: {r['mother_name']}" if pd.notna(r["mother_name"]) else "mother")
        for q in list(r["child_mps"])[:2]:
            parts.append(f"child: {name_by_q.get(q, q)}")
        for q in list(r["sibling_mps"])[:2]:
            parts.append(f"sibling: {name_by_q.get(q, q)}")
        for q in list(r["grandparent_mps"])[:1]:
            parts.append(f"grandparent: {name_by_q.get(q, q)}")
        for q in list(r["grandchild_mps"])[:1]:
            parts.append(f"grandchild: {name_by_q.get(q, q)}")
        for q in list(r["uncle_mps"])[:1]:
            parts.append(f"uncle/aunt: {name_by_q.get(q, q)}")
        for q in list(r["nephew_mps"])[:1]:
            parts.append(f"nephew/niece: {name_by_q.get(q, q)}")
        return " | ".join(parts)
    mp["relative_examples"] = mp.apply(examples, axis=1)
    return mp


# ---------------------------------------------------------------------------
# Step 6: merge bundled Rush cousins
# ---------------------------------------------------------------------------
def merge_cousins(mp):
    from collections import defaultdict
    cousins = defaultdict(set)
    for a, b in RUSH_COUSIN_EDGES:
        cousins[a].add(b)

    mp["_rid"] = pd.to_numeric(mp["rushid"], errors="coerce").astype("Int64")
    rid_to_name = dict(zip(mp["_rid"].dropna().astype(int),
                           mp.loc[mp["_rid"].notna(), "politician_name"]))

    def cousin_info(r):
        rid = r["_rid"]
        if pd.isna(rid):
            return pd.Series([0, ""])
        cs = cousins.get(int(rid), set())
        if not cs:
            return pd.Series([0, ""])
        names = [rid_to_name.get(c, str(c)) for c in sorted(cs)]
        return pd.Series([1, " | ".join(names)])

    mp[["rush_cousin_mp", "rush_cousin_names"]] = mp.apply(cousin_info, axis=1)
    mp["has_relative_incl_cousins"] = (
        (mp["has_relative"] == 1) | (mp["rush_cousin_mp"] == 1)).astype(int)
    return mp


# ---------------------------------------------------------------------------
# Step 7a: build the machine-readable edge list
# ---------------------------------------------------------------------------
def build_edge_list(mp):
    name_by_q = dict(zip(mp["politician_id"], mp["politician_name"]))
    rid_by_q = dict(zip(mp["politician_id"], mp["_rid"]))
    q_by_rid = {int(r): q for q, r in rid_by_q.items() if pd.notna(r)}

    set_cols = {
        "child": "child_mps", "sibling": "sibling_mps",
        "grandparent": "grandparent_mps", "grandchild": "grandchild_mps",
        "uncle/aunt": "uncle_mps", "nephew/niece": "nephew_mps",
    }
    rows = []
    for _, r in mp.iterrows():
        frm = r["politician_id"]
        # gendered parent edges come straight from the parent id fields
        if pd.notna(r["father_mp_q"]):
            rows.append((frm, r["father_mp_q"], "father", "wikidata"))
        if pd.notna(r["mother_mp_q"]):
            rows.append((frm, r["mother_mp_q"], "mother", "wikidata"))
        for rel, col in set_cols.items():
            for to_q in r[col]:
                rows.append((frm, to_q, rel, "wikidata"))
        # Rush cousins, resolving the cousin's rushid to a QID where possible
        rid = r["_rid"]
        if pd.notna(rid):
            for a, b in RUSH_COUSIN_EDGES:
                if a == int(rid):
                    rows.append((frm, q_by_rid.get(b, pd.NA), "cousin", "rush", b))

    edges = pd.DataFrame(
        [(fr, to, rel, src, (extra[0] if extra else pd.NA))
         for (fr, to, rel, src, *extra) in rows],
        columns=["from_politician_id", "to_politician_id", "relation", "source", "to_rushid"])
    edges["from_name"] = edges["from_politician_id"].map(name_by_q)
    edges["to_name"] = edges.apply(
        lambda e: name_by_q.get(e["to_politician_id"], pd.NA), axis=1)
    edges["from_rushid"] = edges["from_politician_id"].map(
        lambda q: rid_by_q.get(q, pd.NA))
    edges = edges[["from_politician_id", "from_name", "from_rushid",
                   "to_politician_id", "to_name", "to_rushid",
                   "relation", "source"]]
    return edges.drop_duplicates().sort_values(
        ["from_name", "relation"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Step 7b: assemble the coded output frame in a fixed column order
# ---------------------------------------------------------------------------
def coded_frame(mp):
    def i(s):
        return pd.to_numeric(s, errors="coerce").astype("Int64")
    out = pd.DataFrame({
        "politician_id": mp["politician_id"],
        "politician_name": mp["politician_name"],
        "first_name": mp["first_name"],
        "last_name": mp["last_name"],
        "gender": mp["gender"],
        "birth_year": i(mp["birth"]),
        "death_year": i(mp["death"]),
        "first_year_in_HoC": i(mp["first_year"]),
        "last_year_in_HoC": i(mp["last_year"]),
        "n_parliaments": i(mp["n_parliaments"]),
        "ever_cabinet": i(mp["ever_cabinet"]),
        "parties": mp["parties"],
        "has_relative": i(mp["has_relative"]),
        "closeness": mp["closeness"],
        "relative_chamber": mp["relative_chamber"],
        "multiple_relatives": i(mp["multiple_relatives"]),
        "relation_types": mp["relation_types"].map(lambda x: "; ".join(x) if x else pd.NA),
        "n_relatives_detected": i(mp["n_relatives_in_panel"]),
        "relative_preceded": i(mp["relative_preceded"]),
        "peerage_linked_flag": i(mp["peerage_linked"]),
        "relative_examples": mp["relative_examples"].replace("", pd.NA),
        "rush_cousin_mp": i(mp["rush_cousin_mp"]),
        "rush_cousin_names": mp["rush_cousin_names"].replace("", pd.NA),
        "has_relative_incl_cousins": i(mp["has_relative_incl_cousins"]),
        "rushid": mp["rushid"],
        "peerageid": mp["peerageid"],
        "whoswhoid": mp["whoswhoid"],
        "oxnatbibid": mp["oxnatbibid"],
        "father_name": mp["father_name"],
        "father_id": mp["father_id"],
        "mother_name": mp["mother_name"],
        "mother_id": mp["mother_id"],
    })
    return out.sort_values(["has_relative", "first_year_in_HoC"],
                           ascending=[False, True]).reset_index(drop=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dta", required=True, help="path to MPdata1_2_basic_bio.dta")
    ap.add_argument("--outdir", default=".")
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)

    df = load_and_clean(args.dta)
    mp = collapse(df)
    print(f"Step 3: collapsed to {len(mp):,} unique MPs "
          f"(panel had {len(df):,} MP-parliament rows)")
    mp = build_kinship(mp)
    mp = assemble_fields(mp)
    mp = merge_cousins(mp)

    coded = coded_frame(mp)
    edges = build_edge_list(mp)

    coded_path = os.path.join(args.outdir, "mp_relatives_coded.csv")
    edges_path = os.path.join(args.outdir, "mp_relationship_edges.csv")
    coded.to_csv(coded_path, index=False)
    edges.to_csv(edges_path, index=False)

    print("\n=== SUMMARY ===")
    print(f"MPs                                  : {len(coded):,}")
    print(f"has_relative (Wikidata graph)        : {int((coded.has_relative==1).sum()):,} "
          f"({100*(coded.has_relative==1).mean():.1f}%)")
    print(f"  relative preceded them (dynastic)  : {int((coded.relative_preceded==1).sum()):,}")
    print(f"  multiple relatives                 : {int((coded.multiple_relatives==1).sum()):,}")
    print(f"MPs with a Rush cousin MP            : {int((coded.rush_cousin_mp==1).sum()):,}")
    print(f"  of which new (has_relative was 0)  : {int(((coded.rush_cousin_mp==1)&(coded.has_relative==0)).sum()):,}")
    print(f"has_relative including cousins       : {int((coded.has_relative_incl_cousins==1).sum()):,}")
    print(f"peerage-linked MPs                   : {int((coded.peerage_linked_flag==1).sum()):,}")
    print(f"\nEdge list rows                       : {len(edges):,}")
    print(edges["relation"].value_counts().to_string())
    print(f"\nwrote {coded_path}")
    print(f"wrote {edges_path}")


if __name__ == "__main__":
    main()

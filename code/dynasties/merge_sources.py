#!/usr/bin/env python3
"""
merge_sources.py

Combines the two relationship edge lists already produced by the panel and Rush
pipelines into ONE per-MP dynasty coding for the UK House of Commons (1832-present).

Reads (from data/dynasties/processing/):
  - mp_relationship_edges.csv    panel edges  (from_politician_id, to_politician_id, relation, source, ...)
  - rush_relationship_edges.csv  Rush edges   (from_rushid, to_rushid, label)
  - mp_relatives_coded.csv        MP universe + support columns (politician_id, politician_name, rushid, peerage_linked_flag, ...)

Writes (to data/dynasties/output/):
  - mp_relatives_coded.csv     the single deliverable, one row per MP
  - mp_relationship_edges.csv  optional combined audit (one row per link, with source + names)

Does NOT read the original Stata file. Edges from both sources are deduped at the
(MP, relative) pair level, so a relative found by both sources is counted once.

Run:  python merge_sources.py   (paths are anchored to this file)
"""

from __future__ import annotations
import sys
from pathlib import Path
import pandas as pd

# ---------------------------------------------------------------------------
# Paths -- anchored to this file: code/dynasties/merge_sources.py
#   parents[2] == repo root (POLITICAL-DYNASTIES)
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[2]
PROCESSING = ROOT / "data" / "dynasties" / "processing"
OUTPUT = ROOT / "data" / "dynasties" / "output"

# ---------------------------------------------------------------------------
# CONFIG -- column names match your actual files
# ---------------------------------------------------------------------------
CONFIG = {
    "panel_edges_file": "mp_relationship_edges.csv",
    "rush_edges_file":  "rush_relationship_edges.csv",
    "coded_file":       "mp_relatives_coded.csv",   # universe + support only

    "out_coded": "mp_relatives_coded.csv",
    "out_edges": "mp_relationship_edges.csv",
    "write_audit": True,

    # Edge layout. Convention: a row reads "<id1> is <rel> of <id2>".
    # If a file is the other way round, swap id1/id2 (only flips parent<->child
    # labels for asymmetric links; counts are unaffected).
    "panel_edge": {"id1": "from_politician_id", "id2": "to_politician_id",
                   "rel": "relation", "key": "politician_id", "source": "panel"},
    "rush_edge":  {"id1": "from_rushid", "id2": "to_rushid",
                   "rel": "label", "key": "rushid", "source": "rush"},

    # Coded-file columns (universe, crosswalk, support)
    "coded_id": "politician_id",
    "coded_name": "politician_name",
    "coded_rushid": "rushid",
    "coded_peerage": "peerage_linked_flag",
}

# ---------------------------------------------------------------------------
# Relationship vocabulary
# ---------------------------------------------------------------------------
RANK = {
    "parent": 1, "child": 1, "sibling": 1,
    "spouse": 2, "civil partner": 2, "partner": 2,
    "grandparent": 3, "grandchild": 3, "uncle/aunt": 3, "niece/nephew": 3,
    "cousin": 4, "great-grandparent": 4, "great-grandchild": 4,
    "great-uncle/aunt": 4, "great-niece/nephew": 4,
    "great-great-uncle/aunt": 5, "great-great-niece/nephew": 5,
    "indirect": 9,
}
RELATION_ORDER = list(RANK.keys())
_ORDER_IX = {r: i for i, r in enumerate(RELATION_ORDER)}

CANON = {
    # raw Rush labels ("a is <label> of b")
    "parent of": "parent", "child of": "child", "sibling of": "sibling",
    "spouse of": "spouse", "civil partner of": "civil partner", "partner of": "partner",
    "grandparent of": "grandparent", "grandchild of": "grandchild",
    "pibling of": "uncle/aunt", "nibling of": "niece/nephew", "cousin of": "cousin",
    "great grandparent of": "great-grandparent", "great grandchild of": "great-grandchild",
    "great aunt/uncle of": "great-uncle/aunt", "great niece/nephew of": "great-niece/nephew",
    "great great aunt/uncle of": "great-great-uncle/aunt",
    "great great niece/nephew of": "great-great-niece/nephew",
    "indirect relative of": "indirect",
    # already-normalised terms
    "parent": "parent", "child": "child", "sibling": "sibling", "spouse": "spouse",
    "civil partner": "civil partner", "partner": "partner",
    "grandparent": "grandparent", "grandchild": "grandchild",
    "uncle/aunt": "uncle/aunt", "aunt/uncle": "uncle/aunt",
    "niece/nephew": "niece/nephew", "nephew/niece": "niece/nephew",
    "cousin": "cousin", "indirect": "indirect",
    "great-grandparent": "great-grandparent", "great-grandchild": "great-grandchild",
    "great-uncle/aunt": "great-uncle/aunt", "great-niece/nephew": "great-niece/nephew",
    "great-great-uncle/aunt": "great-great-uncle/aunt",
    "great-great-niece/nephew": "great-great-niece/nephew",
    # gendered / common variants (in case the panel uses them)
    "father": "parent", "mother": "parent", "son": "child", "daughter": "child",
    "brother": "sibling", "sister": "sibling", "husband": "spouse", "wife": "spouse",
    "grandfather": "grandparent", "grandmother": "grandparent",
    "grandson": "grandchild", "granddaughter": "grandchild",
    "uncle": "uncle/aunt", "aunt": "uncle/aunt",
    "nephew": "niece/nephew", "niece": "niece/nephew",
}
INVERSE = {
    "parent": "child", "child": "parent",
    "grandparent": "grandchild", "grandchild": "grandparent",
    "uncle/aunt": "niece/nephew", "niece/nephew": "uncle/aunt",
    "great-grandparent": "great-grandchild", "great-grandchild": "great-grandparent",
    "great-uncle/aunt": "great-niece/nephew", "great-niece/nephew": "great-uncle/aunt",
    "great-great-uncle/aunt": "great-great-niece/nephew",
    "great-great-niece/nephew": "great-great-uncle/aunt",
    "sibling": "sibling", "cousin": "cousin", "spouse": "spouse",
    "civil partner": "civil partner", "partner": "partner", "indirect": "indirect",
}


def _norm(s) -> str:
    return str(s).strip() if pd.notna(s) else ""


def _normid(s) -> str:
    """Normalise an id; strips a trailing '.0' left by float reads (e.g. rush ids)."""
    s = _norm(s)
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    return s


def _order_key(x: str) -> int:
    return _ORDER_IX.get(x, 999)


def _relkey(label: str) -> str:
    x = " ".join(_norm(label).lower().split())
    return x.replace(" / ", "/").replace("/ ", "/").replace(" /", "/")


def canon(label: str):
    return CANON.get(_relkey(label))


# ---------------------------------------------------------------------------
# Load one edge file -> ego, alter, relation, source  (ids in politician_id space)
# ---------------------------------------------------------------------------
def load_edges(path: Path, spec: dict, crosswalk: dict) -> pd.DataFrame:
    if not path.exists():
        print(f"  (skipping {path.name}: not found)")
        return pd.DataFrame(columns=["ego", "alter", "relation", "source"])
    df = pd.read_csv(path, dtype=str).fillna("")
    id1, id2, relc = spec["id1"], spec["id2"], spec["rel"]
    missing = [c for c in (id1, id2, relc) if c not in df.columns]
    if missing:
        raise SystemExit(f"{path.name}: missing columns {missing}. Found: {list(df.columns)}")

    rows, dropped, unmapped = [], 0, set()
    for _, r in df.iterrows():
        a, b, label = _normid(r[id1]), _normid(r[id2]), _norm(r[relc])
        if spec["key"] == "rushid":
            a, b = crosswalk.get(a), crosswalk.get(b)   # map Rush ids -> politician_id
        if not a or not b or a == b:
            dropped += 1
            continue
        t = canon(label)
        if t is None:
            unmapped.add(label)
            continue
        rows.append((b, a, t, spec["source"]))                 # a is t of b -> b's relative a
        rows.append((a, b, INVERSE.get(t, t), spec["source"]))  # reciprocal
    if dropped:
        print(f"  {path.name}: {dropped:,} rows dropped (endpoint not a panel MP, or self-link)")
    if unmapped:
        print(f"  {path.name}: unmapped relation labels ignored -> {sorted(unmapped)}")
    return pd.DataFrame(rows, columns=["ego", "alter", "relation", "source"])


# ---------------------------------------------------------------------------
# Collapse edges -> one row per MP, over the full universe
# ---------------------------------------------------------------------------
def collapse(edges: pd.DataFrame, universe: pd.DataFrame, name_map: dict) -> pd.DataFrame:
    base = universe.copy()
    base["politician_id"] = base["politician_id"].map(_normid)

    empty_cols = ["ego", "n_relatives_detected", "closeness",
                  "relation_types", "relative_sources", "relative_examples"]
    if edges.empty:
        agg = pd.DataFrame(columns=empty_cols)
    else:
        e = edges.drop_duplicates(subset=["ego", "alter", "relation", "source"]).copy()
        e["rank"] = e["relation"].map(RANK).fillna(9).astype(int)
        e["order"] = e["relation"].map(_ORDER_IX).fillna(999).astype(int)

        closest = (e.sort_values(["rank", "order"])
                     .groupby("ego", as_index=False).first()[["ego", "relation"]]
                     .rename(columns={"relation": "closeness"}))

        pair = e.groupby(["ego", "alter"], as_index=False).agg(
            src=("source", lambda s: sorted(set(s))),
            rel=("relation", lambda s: sorted(set(s), key=_order_key)),
        )
        agg = pair.groupby("ego").agg(
            n_relatives_detected=("alter", "nunique"),
            _ids=("alter", lambda s: list(dict.fromkeys(s))),
            relation_types=("rel", lambda s: ";".join(
                sorted({r for lst in s for r in lst}, key=_order_key))),
            relative_sources=("src", lambda s: ";".join(
                sorted({v for lst in s for v in lst}))),
        ).reset_index().merge(closest, on="ego", how="left")
        agg["relative_examples"] = agg["_ids"].map(
            lambda ids: ";".join(name_map.get(i, i) for i in ids))
        agg = agg.drop(columns=["_ids"])

    coded = base.merge(agg, left_on="politician_id", right_on="ego", how="left") \
                .drop(columns=["ego"], errors="ignore")

    if "n_relatives_detected" not in coded.columns:
        coded["n_relatives_detected"] = 0
    coded["n_relatives_detected"] = coded["n_relatives_detected"].fillna(0).astype(int)
    coded["has_relative"] = (coded["n_relatives_detected"] > 0).astype(int)
    coded["multiple_relatives"] = (coded["n_relatives_detected"] > 1).astype(int)
    for c in ["closeness", "relation_types", "relative_sources", "relative_examples"]:
        if c not in coded.columns:
            coded[c] = ""
        coded[c] = coded[c].fillna("")
    coded["relative_chamber"] = coded["has_relative"].map({1: "HoC", 0: ""})

    if "peerage_linked_flag" not in coded.columns:
        coded["peerage_linked_flag"] = pd.NA
    for c in ["politician_name", "rushid"]:
        if c not in coded.columns:
            coded[c] = ""

    out_cols = ["politician_id", "politician_name", "rushid",
                "has_relative", "n_relatives_detected", "multiple_relatives",
                "closeness", "relation_types",
                "relative_chamber", "peerage_linked_flag",
                "relative_sources", "relative_examples"]
    return coded[out_cols].sort_values("politician_id").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    coded_path = PROCESSING / CONFIG["coded_file"]
    if not coded_path.exists():
        raise SystemExit(f"Universe file not found: {coded_path}")

    print(f"Loading MP universe: {coded_path.name}")
    src = pd.read_csv(coded_path, dtype=str).fillna("")
    for key in ("coded_id",):
        if CONFIG[key] not in src.columns:
            raise SystemExit(f"{coded_path.name}: expected id column '{CONFIG[key]}'. "
                             f"Found: {list(src.columns)}")
    uni = pd.DataFrame({"politician_id": src[CONFIG["coded_id"]].map(_normid)})
    uni["politician_name"] = src[CONFIG["coded_name"]] if CONFIG["coded_name"] in src.columns else ""
    uni["rushid"] = src[CONFIG["coded_rushid"]] if CONFIG["coded_rushid"] in src.columns else ""
    uni["peerage_linked_flag"] = (src[CONFIG["coded_peerage"]]
                                  if CONFIG["coded_peerage"] in src.columns else pd.NA)
    uni = uni.drop_duplicates("politician_id")
    print(f"  {uni['politician_id'].nunique():,} MPs")

    name_map = dict(zip(uni["politician_id"], uni["politician_name"].map(_norm)))
    cw = uni.loc[uni["rushid"].map(_normid) != "", ["rushid", "politician_id"]]
    crosswalk = dict(zip(cw["rushid"].map(_normid), cw["politician_id"]))

    print("Loading edges ...")
    panel_edges = load_edges(PROCESSING / CONFIG["panel_edges_file"], CONFIG["panel_edge"], crosswalk)
    rush_edges = load_edges(PROCESSING / CONFIG["rush_edges_file"], CONFIG["rush_edge"], crosswalk)
    print(f"  panel: {len(panel_edges):,} directed edges | rush: {len(rush_edges):,} directed edges")

    edges = pd.concat([panel_edges, rush_edges], ignore_index=True) \
              .drop_duplicates(subset=["ego", "alter", "relation", "source"])

    coded = collapse(edges, uni, name_map)

    OUTPUT.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT / CONFIG["out_coded"]
    coded.to_csv(out_path, index=False)
    print(f"\nWrote {out_path}  ({len(coded):,} MPs)")
    print(f"  with a relative:  {int(coded['has_relative'].sum()):,}")
    print(f"  with >1 relative: {int(coded['multiple_relatives'].sum()):,}")

    if CONFIG["write_audit"] and not edges.empty:
        audit = (edges.groupby(["ego", "alter"], as_index=False)
                      .agg(relation=("relation", lambda s: ";".join(sorted(set(s)))),
                           source=("source", lambda s: ";".join(sorted(set(s))))))
        audit.insert(1, "ego_name", audit["ego"].map(lambda i: name_map.get(i, "")))
        audit["alter_name"] = audit["alter"].map(lambda i: name_map.get(i, ""))
        audit = audit[["ego", "ego_name", "alter", "alter_name", "relation", "source"]]
        audit_path = OUTPUT / CONFIG["out_edges"]
        audit.to_csv(audit_path, index=False)
        print(f"Wrote {audit_path}  ({len(audit):,} links)  [optional audit]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
# MP DYNASTIES RESEARCH

## Available Sources
| Source | Accuracy | Coverage | limitations | Access |
|---|---|---|---|---|
| **Van Coppenolle dataset** (2017 LSQ) | Accurate. Real dataset; MP-to-MP dynastic ties hand-coded from Stenton & Lees's *Who's Who of British MPs*. | Elections 1832–2005. Relative yes/no, relationship type, a "≥2 previous relatives" flag, plus a narrow aristocrat flag (son/grandson/nephew of a hereditary peer or baronet). | No explicit MP-to-peer links, so HoL barely covered; close links (father/son/brother) solid but distant ones less complete; stops at 2005. | Paper open access; the dataset itself isn't deposited anywhere I can find, so email-her only. |
| **Commons Library SN04809** ("related Members since 2010") | Accurate. A downloadable XLSX listing MPs with family members who are also current or former Members of the Commons, by Parliament, since 2010. | 2010–present. Relative yes/no plus who. | Commons-only; excludes in-laws; ignores links more than four generations back; not guaranteed definitive. No HoL. | Free XLSX (74KB). |
| **Butler & Butler, *British Political Facts*** (2011) | Accurate. Claim traces to the Commons Library page, which cites Butler and Butler 2011 as providing family connections of MPs from 1900 to 2010. | 1900–2010. Narrative family-connection info; good for spot-validation. | Printed reference, not machine-readable; ends 2010. | Print / library. |
| **Wikidata / SPARQL** (WikiProject British Politicians) | Accurate. Sample queries include chains of MPs known to be parent and child; the project indexes 28,700+ MPs, validated complete from 1880 and comprehensive from 1970. | MPs 1832+. Parent/child/sibling/relative links, programmatically queryable. | Family ties only where known, so incomplete, especially distant relatives; Lords coverage less comprehensive. | Free, programmatic. |
| **Rush database**  | The maintained online version of Rush (2001), the exact source Van Coppenolle built from, covering MPs since 1832 and maintained with the Commons Library and History of Parliament. Codes explicit, typed MP-to-MP relationships. | 1832–present, kept current, ~10,600 members. Typed relationships: Child/Parent (841/790), Sibling (927), Cousin (157), aunt-uncle "Pibling" (287), niece-nephew "Nibling" (361), grandparent/grandchild, Spouse (67), plus a large "Indirect relative" bucket (745). Maps onto relative yes/no, closeness, and multiple-relatives. Also a peerage-type field. | Relationships are MP-to-MP (a peer-only relative who was never an MP isn't a node); "Indirect relative" bucket needs interpretation; browsable web app, no obvious bulk download. | Free, CC BY 4.0. |

## Input file
- `MPdata1_2_basic_bio.dta`
- `politician_id` is a Wikidata item id (e.g. `Q983174`), the parent ids are Wikidata ids, and one column is named `party_wikidata`.
- 42 columns, 34,920 member-parliament rows. Cross-reference ids to Rush (98%)
- Missing values appear both as blanks and as NaN --> missing

## First pass (Wikidata graph only)
- Meaning: coding from the panel's parent ids alone, no external data
- Collapses to 10,508 unique MPs.
- Relations from parent ids: father, mother, sibling (shared parent), grandparent, child (inverted links), grandchild, aunt/uncle, niece/nephew. Id-to-id matching, so high precision and a lower bound on recall (no cousins, no relatives by marriage, no Lords-only relatives, about two generations deep).
- Results:
  - 1,994 MPs (19.0%) with a relative
  - 1,177 preceded by one, 929 with more than one
  - Father most common, then child, then sibling.

## Rush database
- Held by the History of Parliament Trust; browsable and as a Dataset.
- A kinship graph, 18 typed relations, about 5,400 directed edges.
- Member ids equal `rushid` 
- pibling = aunt or uncle; nibling = niece or nephew (gender-neutral labels the database uses).
- cousins merged by `rushid`: Fields `rush_cousin_mp`, `rush_cousin_names`, `has_relative_incl_cousins`.
- "Aristocratic connections" field means aristocratic lineage specifically, not "had a Lords relative" --> A partial measure of the aristocratic dimension only.

### Limitations
- Lords relatives: not identifiable from a Commons-only panel. Shortlist for external work is the 4,402 peerage-linked MPs.
- Missing parents: over half of MPs have no recorded father in the panel.

## Existing academic datasets 
- van Coppenolle (2017), LSQ 42(3): UK Commons dynasties since 1832, the closest match. No public deposit located; obtainable from the author; coding rules in the paper and the 2014 LSE thesis.
- Smith and Martin (2017), LSQ 42(1): cabinet-minister selection and dynasties. Open replication data on Harvard Dataverse (10.7910/DVN/5Y5148).

# Running the Scripts 
- `mp_relatives_pipeline.py: load, clean, collapse, build the Wikidata graph, code the first-pass fields, merge the bundled Rush cousins; writes `mp_relatives_coded.csv` (one row per MP) and `mp_relationship_edges.csv` (one row per directed relationship, labelled by relation and source)
- `fetch_rush_relationships.py`: pulls all 18 Rush relation types, reads each edge from both members' perspectives, restores gendered relations, codes the Rush fields, merges by `rushid`.
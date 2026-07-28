# hack_ras To-Do List

Discuss scope with the user before implementing any item (see `dev_rules.md`;
the user has asked to be consulted before hack_ras changes).

---

## OPEN ITEMS

Two items are open; neither has an active need right now. In rough priority:

1. **Blocked Obstruction / Levee writer + `merge.py` support** — §A below.
2. **Dry-run / preview on the mutating ops** (LOW PRIORITY) — §B below.

Everything else once listed here is DONE — see the "DONE" section below and the
`ai_context.md` session notes.

### A. Blocked Obstructions (`#Block Obstruct=`) + `Levee=` — writer / merge support

Domain: **geometry-merge / xsedit** (NOT the plan/geometry file-ops). As of
2026-07-21 both blocks are PARSED read-only (`blocks/xs_block_obstruct.py` →
`CrossSection.blocked_obstructions`; `blocks/xs_levee.py` → `CrossSection.levee`),
but there is **no writer / `merge.py` support** — a merged/edited cross-section
currently drops them. Build when a merge use case actually needs to carry
obstructions/levees through.

Plan (still applies): a `MergeConfig.obstruct_source` field, a `_KEY_PREFIXES`
entry, a `merge_obstruct()` reusing `_write_triplet_lines()`, and GUI wiring.
Format facts: blocked obstructions use the same 8-char `[start, end, elevation]`
triplet layout as IFAs (`normal` flag 0 with left/right + 0.0-edge sentinels /
`multiple_block` flag -1 with literal stations), but with **no** `Permanent`
follower line (obstructions are always solid). Fuller detail lives in the
`ai_context.md` "Future Features — Not Yet Implemented" section.

### B. Dry-run / preview on the mutating ops  (LOW PRIORITY)

A `dry_run=True` (or a preview function) so a mutating op computes its full
change-set — files renamed/deleted, `.prj` / `.rasmap` / plan-file edits,
warnings, current-plan repoint — and returns it WITHOUT writing, so the change
can be shown for confirmation before touching a real model.

**Low priority** (confirmed by the user 2026-07-28): the user always backs up
models before asking for these ops (covers rollback), and `project_health`
verifies after — so a before-commit preview is optional. If built, it is the
highest-effort item: it touches every mutating op (`renumber_plans`/`geoms`,
`delete_plan(s)`/`geom(s)`, `clone_*`, `insert_*_gap`, `compact_*`), each of which
must build its plan-of-changes then apply-or-return — best done as a deliberate
refactor. Pairs with the health inspector (item A, done): they share the
"describe the delta / describe the state" rendering.

**Not planned:** a thin CLI (`python -m hack_ras ...`) — deferred indefinitely;
the agent scripts these fine and the user didn't want the maintenance surface.

---

## DONE (kept for design rationale)

### 2026-07-28 (session 17) — file-ops ergonomics, geometry subsystem, rasmap cleanup, inspector

All shipped; see `ai_context.md` session 17 for full details:
- **`project/geoms.py`** — geometry-file subsystem: `renumber_geom(s)`,
  `insert_geom_gap`, `compact_geoms`, `clone_geom`, `delete_geom` + bulk
  `delete_geoms`; plus `renumber_geoms_in_rasmap`.
- **`delete_plan` rasmap cleanup** (default on): removes RASPlan/RASResults
  layers, and — on `delete_unused_flow`/`delete_unused_geom` — the
  RASEventConditions / `<Geometries>` layers too. New rasmap ops
  `remove_plans_from_rasmap` / `remove_flows_from_rasmap` /
  `remove_geoms_from_rasmap` and `sort_rasmap_layers`. Fixes the number-reuse
  "zombie layer" problem.
- **`plans_with_unlisted_results`** + `result_plan_ids` — flag computed results
  RAS Mapper would append out of order.
- **`compact_plans`** + bulk **`delete_plans`** / **`delete_geoms`** (flexible
  id-spec, fail-fast).
- **Item A — read-only status/health inspector** (`project/health.py`:
  `project_health` → `ProjectHealth` + `format_health`; read-only rasmap queries
  `rasmap_layer_refs` / `result_plan_ids`).

### 2026-07-17 (session 13) — original six plan-file-op items

**ALL SIX ITEMS IMPLEMENTED** — see `ai_context.md` session 13 and the "Plan File
Operations" section for the shipped API. Kept below for design rationale. Both
validation follow-ups are resolved:
(a) `sort_prj_entries(project, kinds=...)` (project/sync.py) provides optional
ascending re-sorting of the .prj plan/geom/unsteady/steady lists (RAS accepts
non-sequential order, but its file-open dialogs present .prj order);
(b) stale rasmap layers after delete-then-reuse of a plan number were originally
declared out of scope (RAS Mapper self-heals), but the number-reuse zombie case
was later fixed properly in session 17 (delete_plan rasmap cleanup, above).

Approved-for-implementation decisions (2026-07-17): item 1 rewrites references and
renames artifacts by default (no warn-only mode) — "give the user what they asked
for"; arbitrary restart names like `banana.rst` carry no plan number and are left
alone. Item 2 is removal-only (clone_plan etc. already add entries when creating
files). Item 3 may use temp filenames like `Stream.p02.tmp` to break rename
cycles. Item 4 stays minimal. Item 5 warns, never blocks. Item 6 added same day at
the user's request. Items 1-5 all came out of the Model_PCA GMF_DFA plan
renumbering job, where each gap had to be worked around with one-off scripts.

#### 1. Restart-file awareness in `renumber_plan`
`.u` files embed plan numbers in restart references, e.g.
`Restart Filename=GMF_DFA.p06.02JAN2026 1200.rst`. Renaming a plan silently orphans
every such reference (and any `Base.pNN.<date>.rst` files on disk written by that
plan). Smallest useful version: scan the project's `.u` files and warn. Full
version: rewrite the references (and optionally rename matching `.rst` files).
This is the gap most likely to bite again.

#### 2. `.prj` sync/cleanup function
Remove `Plan File=` / `Geom File=` / `Unsteady File=` entries whose files do not
exist on disk. In the GMF_DFA job this was a hard prerequisite: `renumber_plan`
treats prj-listed-but-missing IDs as "in use", so no renumbering into those slots
was possible until the stale entries were removed.

#### 3. Bulk renumber from a mapping
`renumber_plans(project, {old: new, ...})` that validates the whole mapping up
front and computes a collision-free rename order (using temp names when the mapping
contains cycles). The GMF_DFA job needed ten renames hand-ordered so every target
was free when its turn came; a cycle (like the u-file rotation done later the same
day) additionally needs a temp name.

#### 4. `.rasmap` plan renumbering
Keep `<Plans>`/`<Results>` layer references in step when plans are renumbered:
remap `Filename` / `GeometryHDF` attribute tokens per the mapping. Verified
empirically (2026-07-17): display names self-heal on load (RAS Mapper reads titles
from the files), stale entries are flagged in the GUI and purgeable via
Tools > "remove missing layers", and hand-edited sections survive a GUI
save round-trip verbatim. Preserving a result layer's block through a rename only
matters when the renamed `pNN.hdf` actually exists.

#### 5. Breach-trigger validity check in `clone_plan`
Plan files store ALL breach trigger fields simultaneously; flags select which are
active (`Breach Start=F1,F2,F3,F4,F5,F6,F7,F8`: F1=True -> "WS Elev" mode,
F5=True -> "WS Elev + Duration" mode, both False -> "Set Time" mode using F3/F4).
`clone_plan` can swap the `Breach Start=` line via `line_edits`, but nothing warns
when a cloned plan's ACTIVE trigger is inconsistent with its new simulation window
— e.g. a Set Time trigger dated before the window start, which is exactly what
happened when Sunny-day breach plans were cloned into event windows in the GMF_DFA
job. Cheap check: parse the active trigger mode; if Set Time, verify the date/time
falls inside the plan's `Simulation Date=` window; warn otherwise.

#### 6. `delete_plan` — remove a plan and its outputs
Requested 2026-07-17. Delete a plan file plus everything keyed to its number:
`pNN.hdf`, `bNN`, `bcoNN`, `ic.oNN`, and `Base.pNN.*.rst` restart files, and the
`Plan File=` entry in the `.prj` (with `Current Plan=` fixup). Refuse when
`pNN.tmp.hdf` exists (a run is active). Optional flags delete the plan's `gNN` /
`uNN` (with their `.hdf` sidecars, and the geometry's `xNN`) when no other listed
plan references them. Warn when a surviving `.u` file's `Restart Filename=`
references the deleted plan's restart output. (The `.rasmap` was originally left
alone; session 17 later added the rasmap cleanup — see the DONE 2026-07-28 note.)

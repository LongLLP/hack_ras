# hack_ras To-Do List

Work items, in rough priority order. Discuss scope with the user before
implementing any of them (see `dev_rules.md`; the user has asked to be consulted
before hack_ras changes).

**2026-07-17 (later): ALL SIX ITEMS IMPLEMENTED** — see ai_context.md session 13
and the "Plan File Operations" section for the shipped API. Kept here for the
design rationale. Both validation follow-ups are resolved:
(a) `sort_prj_entries(project, kinds=...)` (project/sync.py) now provides
optional ascending re-sorting of the .prj plan/geom/unsteady/steady lists (RAS
accepts non-sequential order, but its file-open dialogs present .prj order);
(b) stale rasmap layers after delete-then-reuse of a plan
number are OUT OF SCOPE per the user — RAS Mapper was observed sorting this out
itself on the 03_hack_ras_test model (stale layers flagged, renumbered files
adopted), so hack_ras keeps its hands off the rasmap beyond token remapping.

**2026-07-17: items 1-5 approved for implementation** with these decisions:
item 1 rewrites references and renames artifacts by default (no warn-only mode) —
"give the user what they asked for"; arbitrary restart names like `banana.rst`
carry no plan number and are left alone. Item 2 is removal-only (clone_plan etc.
already add entries when creating files). Item 3 may use temp filenames like
`Stream.p02.tmp` to break rename cycles. Item 4 stays minimal. Item 5 warns,
never blocks. Item 6 added same day at the user's request.

Items 1-5 all came out of the Model_PCA GMF_DFA plan renumbering job (2026-07-17),
where each gap had to be worked around with one-off scripts.

## 1. Restart-file awareness in `renumber_plan`

`.u` files embed plan numbers in restart references, e.g.
`Restart Filename=GMF_DFA.p06.02JAN2026 1200.rst`. Renaming a plan silently orphans
every such reference (and any `Base.pNN.<date>.rst` files on disk written by that
plan). Smallest useful version: scan the project's `.u` files and warn. Full
version: rewrite the references (and optionally rename matching `.rst` files).
This is the gap most likely to bite again.

## 2. `.prj` sync/cleanup function

Remove `Plan File=` / `Geom File=` / `Unsteady File=` entries whose files do not
exist on disk. In the GMF_DFA job this was a hard prerequisite: `renumber_plan`
treats prj-listed-but-missing IDs as "in use", so no renumbering into those slots
was possible until the stale entries were removed.

## 3. Bulk renumber from a mapping

`renumber_plans(project, {old: new, ...})` that validates the whole mapping up
front and computes a collision-free rename order (using temp names when the mapping
contains cycles). The GMF_DFA job needed ten renames hand-ordered so every target
was free when its turn came; a cycle (like the u-file rotation done later the same
day) additionally needs a temp name.

## 4. `.rasmap` plan renumbering

Keep `<Plans>`/`<Results>` layer references in step when plans are renumbered:
remap `Filename` / `GeometryHDF` attribute tokens per the mapping. Verified
empirically (2026-07-17): display names self-heal on load (RAS Mapper reads titles
from the files), stale entries are flagged in the GUI and purgeable via
Tools > "remove missing layers", and hand-edited sections survive a GUI
save round-trip verbatim. Preserving a result layer's block through a rename only
matters when the renamed `pNN.hdf` actually exists.

## 5. Breach-trigger validity check in `clone_plan`

Plan files store ALL breach trigger fields simultaneously; flags select which are
active (`Breach Start=F1,F2,F3,F4,F5,F6,F7,F8`: F1=True -> "WS Elev" mode,
F5=True -> "WS Elev + Duration" mode, both False -> "Set Time" mode using F3/F4).
`clone_plan` can swap the `Breach Start=` line via `line_edits`, but nothing warns
when a cloned plan's ACTIVE trigger is inconsistent with its new simulation window
— e.g. a Set Time trigger dated before the window start, which is exactly what
happened when Sunny-day breach plans were cloned into event windows in the GMF_DFA
job. Cheap check: parse the active trigger mode; if Set Time, verify the date/time
falls inside the plan's `Simulation Date=` window; warn otherwise.

## 6. `delete_plan` — remove a plan and its outputs

Requested 2026-07-17. Delete a plan file plus everything keyed to its number:
`pNN.hdf`, `bNN`, `bcoNN`, `ic.oNN`, and `Base.pNN.*.rst` restart files, and the
`Plan File=` entry in the `.prj` (with `Current Plan=` fixup). Refuse when
`pNN.tmp.hdf` exists (a run is active). Optional flags delete the plan's `gNN` /
`uNN` (with their `.hdf` sidecars, and the geometry's `xNN`) when no other listed
plan references them. Warn when a surviving `.u` file's `Restart Filename=`
references the deleted plan's restart output. The `.rasmap` is left alone —
RAS Mapper flags missing layers and purges them via "remove missing layers".

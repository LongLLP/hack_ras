# tests/test_flow_ops.py
"""Tests for hack_ras.project.flows — flow renumber/gap/compact/reorder/clone/
delete across BOTH flow kinds.

Synthetic mini project (the operations mutate files, so a checked-in fixture
doesn't fit), shaped after the real mixed steady/unsteady model that motivated
the module: f01 is shared by two steady plans, u01 by two unsteady plans, u02
by one, and f03 / u04 are listed but used by NO plan (realistic leftovers, and
the targets of the unreferenced-delete tests). Both namespaces carry a GAP
(no f02, no u03) so compaction has something to close in each.

u02 holds a `Restart Filename=` line — plan-keyed content that a flow renumber
must rename the file around without touching. The .rasmap carries the flow in
both forms it appears in real files: an <EventConditions> RASEventConditions
layer keyed to Base.u##.hdf, and a results-nested RASEventConditions layer
keyed to Base.p##.hdf that must never be matched.
"""
import os
import tempfile
import unittest

from hack_ras import RasProject
from hack_ras.project.flows import (
    DuplicateFlowTitle,
    FlowFileNotFound,
    FlowIdInUse,
    FlowInUse,
    FlowRunActive,
    clone_flow,
    compact_flows,
    delete_flow,
    delete_flows,
    insert_flow_gap,
    renumber_flow,
    renumber_flows,
    reorder_flows,
)

CRLF = "\r\n"
RST = "Mini.p05.02JAN2025 2400.rst"


def _write(path, lines):
    with open(path, "w", encoding="latin-1", newline="") as f:
        f.write(CRLF.join(lines) + CRLF)


def _read(path):
    with open(path, encoding="latin-1", newline="") as f:
        return f.read()


def _entry_ids(path, key):
    """The file IDs on the .prj's <key> lines, in document order."""
    return [l[len(key):].strip() for l in _read(path).splitlines()
            if l.startswith(key)]


def _set_entry_order(path, key, ids):
    """Rewrite the .prj's <key> lines to name <ids>, keeping line positions —
    i.e. scramble the entry order without touching anything else."""
    lines = _read(path).splitlines()
    slots = [i for i, l in enumerate(lines) if l.startswith(key)]
    assert len(slots) == len(ids), (slots, ids)
    for i, fid in zip(slots, ids):
        lines[i] = f"{key}{fid}"
    _write(path, lines)


_RASMAP = [
    "<RASMapper>",
    "  <EventConditions>",
    '    <Layer Name="Unsteady One" Type="RASEventConditions" Filename=".\\Mini.u01.hdf" />',
    '    <Layer Name="Unsteady Two" Type="RASEventConditions" Filename=".\\Mini.u02.hdf" />',
    '    <Layer Name="Unsteady Four" Type="RASEventConditions" Filename=".\\Mini.u04.hdf" />',
    "  </EventConditions>",
    "  <Results>",
    '    <Layer Name="Charlie" Type="RASResults" Filename=".\\Mini.p03.hdf">',
    '      <Layer Name="Event Conditions" Type="RASEventConditions" Filename=".\\Mini.p03.hdf" />',
    "    </Layer>",
    "  </Results>",
    "</RASMapper>",
]

_FLOW_TITLES = {"f01": "Steady One", "f03": "Steady Three",
                "u01": "Unsteady One", "u02": "Unsteady Two",
                "u04": "Unsteady Four"}
_PLAN_FLOW = {"p01": "f01", "p02": "f01", "p03": "u01", "p04": "u01",
              "p05": "u02"}


class FlowProjectBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

        self.prj_path = self.path("Mini.prj")
        _write(self.prj_path, [
            "Proj Title=Mini",
            "Current Plan=p01",
            "Geom File=g01",
            "Flow File=f01",
            "Flow File=f03",
            "Unsteady File=u01",
            "Unsteady File=u02",
            "Unsteady File=u04",
            "Plan File=p01",
            "Plan File=p02",
            "Plan File=p03",
            "Plan File=p04",
            "Plan File=p05",
        ])
        for fid, title in _FLOW_TITLES.items():
            lines = [f"Flow Title={title}", "Program Version=7.00"]
            if fid == "u02":
                lines += ["Use Restart=-1 ", f"Restart Filename={RST}"]
            _write(self.path(f"Mini.{fid}"), lines)
        for pid, fid in _PLAN_FLOW.items():
            _write(self.path(f"Mini.{pid}"),
                   [f"Plan Title={pid}", "Geom File=g01", f"Flow File={fid}",
                    "Run HTab=-1 "])
        for name in ("Mini.g01", "Mini.u01.hdf", "Mini.u02.hdf",
                     "Mini.u04.hdf"):
            with open(self.path(name), "wb") as f:
                f.write(b"x")
        _write(self.path("Mini.rasmap"), _RASMAP)
        self.project = RasProject(self.prj_path)

    def path(self, name):
        return os.path.join(self.folder, name)

    def flow_ids(self):
        return (self.project.model.unsteady_file_ids,
                self.project.model.steady_file_ids)

    def plan_flow(self, pid):
        for line in _read(self.path(f"Mini.{pid}")).splitlines():
            if line.startswith("Flow File="):
                return line[len("Flow File="):].strip()
        return None


class TestRenumberFlows(FlowProjectBase):
    def test_swap_renames_family_and_repoints_plans(self):
        report = renumber_flows(self.project, {"u01": "u02", "u02": "u01"})

        self.assertEqual(sorted(self.project.model.unsteady_file_ids),
                         ["u01", "u02", "u04"])
        # the two plans on u01 now point at u02, and p05 the other way
        self.assertEqual(self.plan_flow("p03"), "u02")
        self.assertEqual(self.plan_flow("p04"), "u02")
        self.assertEqual(self.plan_flow("p05"), "u01")
        self.assertEqual(len(report["plan_refs"]), 3)
        # titles prove the files themselves traded places
        self.assertIn("Flow Title=Unsteady One", _read(self.path("Mini.u02")))
        self.assertIn("Flow Title=Unsteady Two", _read(self.path("Mini.u01")))
        self.assertTrue(os.path.isfile(self.path("Mini.u01.hdf")))
        self.assertTrue(os.path.isfile(self.path("Mini.u02.hdf")))
        # no temp files left behind by the cycle hop
        self.assertEqual([n for n in os.listdir(self.folder)
                          if n.endswith(".renumtmp")], [])
        self.assertEqual(report["rasmap_tokens"], 2)

    def test_restart_line_is_plan_keyed_and_left_alone(self):
        # u02 -> u01 renames the file; the Restart Filename= inside it names a
        # PLAN's .rst and is none of this module's business
        renumber_flows(self.project, {"u01": "u02", "u02": "u01"})
        self.assertIn(f"Restart Filename={RST}", _read(self.path("Mini.u01")))

    def test_chain_is_ordered_automatically(self):
        renumber_flows(self.project, {"u02": "u05", "u04": "u02"})
        self.assertIn("Flow Title=Unsteady Two", _read(self.path("Mini.u05")))
        self.assertIn("Flow Title=Unsteady Four", _read(self.path("Mini.u02")))
        self.assertEqual(self.plan_flow("p05"), "u05")
        self.assertIn("Unsteady File=u05", _read(self.prj_path))

    def test_single_entry_case(self):
        renumber_flow(self.project, "u04", "u09")
        self.assertTrue(os.path.isfile(self.path("Mini.u09")))
        self.assertTrue(os.path.isfile(self.path("Mini.u09.hdf")))
        self.assertIn("Unsteady File=u09", _read(self.prj_path))

    def test_steady_renumber_uses_the_prj_flow_file_key(self):
        report = renumber_flows(self.project, {"f03": "f02"})
        prj = _read(self.prj_path)
        self.assertIn("Flow File=f02", prj)
        self.assertNotIn("Flow File=f03", prj)
        self.assertIn("Flow File=f01", prj)            # untouched
        self.assertNotIn("Unsteady File=f02", prj)     # never the wrong key
        # steady flow has no .hdf sidecar and no rasmap presence
        self.assertEqual(report["files"], [("Mini.f03", "Mini.f02")])
        self.assertEqual(report["rasmap_tokens"], 0)
        self.assertEqual(self.plan_flow("p01"), "f01")

    def test_steady_plan_reference_is_the_same_flow_file_key(self):
        renumber_flows(self.project, {"f01": "f05"})
        self.assertEqual(self.plan_flow("p01"), "f05")
        self.assertEqual(self.plan_flow("p02"), "f05")
        self.assertEqual(self.plan_flow("p03"), "u01")   # unsteady untouched

    def test_cross_kind_move_refused(self):
        with self.assertRaises(ValueError) as ctx:
            renumber_flows(self.project, {"u04": "f05"})
        self.assertIn("across flow kinds", str(ctx.exception))
        self.assertTrue(os.path.isfile(self.path("Mini.u04")))

    def test_bare_number_refused(self):
        with self.assertRaises(ValueError) as ctx:
            renumber_flows(self.project, {"01": "05"})
        self.assertIn("kind prefix", str(ctx.exception))
        self.assertTrue(os.path.isfile(self.path("Mini.u01")))

    def test_occupied_target_refused(self):
        with self.assertRaises(FlowIdInUse):
            renumber_flows(self.project, {"u01": "u02"})
        self.assertIn("Flow Title=Unsteady One", _read(self.path("Mini.u01")))

    def test_orphan_refused(self):
        _write(self.path("Mini.u09"), ["Flow Title=Orphan"])
        with self.assertRaises(ValueError):
            renumber_flows(self.project, {"u09": "u03"})
        self.assertTrue(os.path.isfile(self.path("Mini.u09")))

    def test_missing_file_refused(self):
        os.remove(self.path("Mini.u04"))
        with self.assertRaises(FlowFileNotFound):
            renumber_flows(self.project, {"u04": "u03"})

    def test_active_run_on_a_referencing_plan_refused(self):
        with open(self.path("Mini.p03.tmp.hdf"), "wb") as f:
            f.write(b"x")
        with self.assertRaises(FlowRunActive):
            renumber_flows(self.project, {"u01": "u09"})
        self.assertTrue(os.path.isfile(self.path("Mini.u01")))

    def test_results_nested_event_conditions_layer_is_never_matched(self):
        renumber_flows(self.project, {"u01": "u09"})
        rasmap = _read(self.path("Mini.rasmap"))
        self.assertIn("Mini.u09.hdf", rasmap)
        self.assertNotIn("Mini.u01.hdf", rasmap)
        # the RASEventConditions inside <Results> names a PLAN hdf — untouched
        self.assertEqual(rasmap.count('Filename=".\\Mini.p03.hdf"'), 2)


class TestInsertFlowGap(FlowProjectBase):
    def test_gap_shifts_only_its_own_kind(self):
        mapping = insert_flow_gap(self.project, "u02", 2)
        self.assertEqual(mapping, {"u02": "u04", "u04": "u06"})
        self.assertEqual(sorted(self.project.model.unsteady_file_ids),
                         ["u01", "u04", "u06"])
        self.assertEqual(self.project.model.steady_file_ids, ["f01", "f03"])
        self.assertEqual(self.plan_flow("p05"), "u04")

    def test_gap_on_steady_side(self):
        self.assertEqual(insert_flow_gap(self.project, "f01", 1),
                         {"f01": "f02", "f03": "f04"})
        self.assertEqual(self.plan_flow("p01"), "f02")

    def test_bad_count_refused(self):
        with self.assertRaises(ValueError):
            insert_flow_gap(self.project, "u01", 0)


class TestCompactFlows(FlowProjectBase):
    def test_both_kinds_by_default(self):
        self.assertEqual(compact_flows(self.project),
                         {"u04": "u03", "f03": "f02"})
        self.assertEqual(sorted(self.project.model.unsteady_file_ids),
                         ["u01", "u02", "u03"])
        self.assertEqual(sorted(self.project.model.steady_file_ids),
                         ["f01", "f02"])

    def test_one_kind_only_leaves_the_other_alone(self):
        self.assertEqual(compact_flows(self.project, kinds=("unsteady",)),
                         {"u04": "u03"})
        self.assertEqual(self.project.model.steady_file_ids, ["f01", "f03"])
        self.assertTrue(os.path.isfile(self.path("Mini.f03")))

    def test_steady_only(self):
        self.assertEqual(compact_flows(self.project, kinds=("steady",)),
                         {"f03": "f02"})
        self.assertEqual(sorted(self.project.model.unsteady_file_ids),
                         ["u01", "u02", "u04"])

    def test_noop_when_contiguous(self):
        compact_flows(self.project)
        self.assertEqual(compact_flows(self.project), {})

    def test_unknown_kind_refused(self):
        with self.assertRaises(ValueError):
            compact_flows(self.project, kinds=("unsteady", "transient"))


class TestReorderFlows(FlowProjectBase):
    def test_reorder_swaps_and_compacts(self):
        mapping = reorder_flows(self.project, ["u02", "u01", "u04"])
        self.assertEqual(mapping,
                         {"u02": "u01", "u01": "u02", "u04": "u03"})
        self.assertIn("Flow Title=Unsteady Two", _read(self.path("Mini.u01")))
        self.assertIn("Flow Title=Unsteady One", _read(self.path("Mini.u02")))
        self.assertIn("Flow Title=Unsteady Four", _read(self.path("Mini.u03")))
        self.assertEqual(self.plan_flow("p05"), "u01")

    def test_reorder_sorts_only_the_kind_it_was_given(self):
        # the two namespaces are independent, so reordering the unsteady flows
        # sorts the Unsteady File= lines and must leave a deliberately
        # scrambled Flow File= (steady) order untouched
        _set_entry_order(self.prj_path, "Unsteady File=",
                         ["u04", "u01", "u02"])
        _set_entry_order(self.prj_path, "Flow File=", ["f03", "f01"])
        self.project = RasProject(self.prj_path)
        reorder_flows(self.project, ["u02", "u01", "u04"])
        self.assertEqual(_entry_ids(self.prj_path, "Unsteady File="),
                         ["u01", "u02", "u03"])
        self.assertEqual(_entry_ids(self.prj_path, "Flow File="),
                         ["f03", "f01"])

    def test_reorder_steady(self):
        self.assertEqual(reorder_flows(self.project, ["f03", "f01"]),
                         {"f03": "f01", "f01": "f02"})
        self.assertEqual(self.plan_flow("p01"), "f02")

    def test_reorder_in_current_order_still_closes_the_gap(self):
        # positions come from the list, so u04 at position 3 becomes u03 even
        # though the ORDER is unchanged — reordering compacts by construction
        self.assertEqual(reorder_flows(self.project, ["u01", "u02", "u04"]),
                         {"u04": "u03"})

    def test_reorder_noop_once_contiguous(self):
        compact_flows(self.project, kinds=("unsteady",))
        self.assertEqual(reorder_flows(self.project, ["u01", "u02", "u03"]), {})

    def test_incomplete_order_refuses_and_touches_nothing(self):
        with self.assertRaises(ValueError) as ctx:
            reorder_flows(self.project, ["u02", "u01"])
        self.assertIn("u04", str(ctx.exception))
        self.assertIn("Flow Title=Unsteady One", _read(self.path("Mini.u01")))

    def test_mixed_kind_order_refused(self):
        with self.assertRaises(ValueError) as ctx:
            reorder_flows(self.project, ["u01", "f01"])
        self.assertIn("mixes flow kinds", str(ctx.exception))

    def test_unknown_and_duplicate_refused(self):
        with self.assertRaises(ValueError):
            reorder_flows(self.project, ["u01", "u02", "u09"])
        with self.assertRaises(ValueError):
            reorder_flows(self.project, ["u01", "u01", "u02"])

    def test_empty_order_refused(self):
        with self.assertRaises(ValueError):
            reorder_flows(self.project, [])


class TestCloneFlow(FlowProjectBase):
    def test_clone_unsteady_inserts_prj_entry_ascending(self):
        new = clone_flow(self.project, "u01", "Unsteady Five", new_id="u03")
        self.assertEqual(new, "u03")
        self.assertIn("Flow Title=Unsteady Five", _read(self.path("Mini.u03")))
        prj = _read(self.prj_path).splitlines()
        self.assertEqual([l for l in prj if l.startswith("Unsteady File=")],
                         ["Unsteady File=u01", "Unsteady File=u02",
                          "Unsteady File=u03", "Unsteady File=u04"])
        # RAS regenerates the preprocessor sidecar; the clone must not fake one
        self.assertFalse(os.path.exists(self.path("Mini.u03.hdf")))

    def test_clone_defaults_to_next_free_number(self):
        self.assertEqual(clone_flow(self.project, "u01", "Next"), "u05")

    def test_clone_steady_uses_the_flow_file_key(self):
        clone_flow(self.project, "f01", "Steady Two", new_id="f02")
        prj = _read(self.prj_path).splitlines()
        self.assertEqual([l for l in prj if l.startswith("Flow File=")],
                         ["Flow File=f01", "Flow File=f02", "Flow File=f03"])
        self.assertNotIn("Unsteady File=f02", _read(self.prj_path))

    def test_duplicate_title_refused(self):
        with self.assertRaises(DuplicateFlowTitle):
            clone_flow(self.project, "u01", "Unsteady Two", new_id="u03")
        self.assertFalse(os.path.exists(self.path("Mini.u03")))

    def test_cross_kind_new_id_refused(self):
        with self.assertRaises(ValueError):
            clone_flow(self.project, "u01", "Whatever", new_id="f05")

    def test_taken_new_id_refused(self):
        with self.assertRaises(FlowIdInUse):
            clone_flow(self.project, "u01", "Whatever", new_id="u02")


class TestDeleteFlow(FlowProjectBase):
    def test_delete_unreferenced_cleans_prj_and_rasmap(self):
        report = delete_flow(self.project, "u04")
        self.assertEqual(sorted(report["deleted"]),
                         ["Mini.u04", "Mini.u04.hdf"])
        self.assertEqual(report["prj_removed"], ["Unsteady File=u04"])
        self.assertEqual(report["rasmap_removed"], ["u04"])
        self.assertEqual(self.project.model.unsteady_file_ids, ["u01", "u02"])
        self.assertNotIn("Mini.u04.hdf", _read(self.path("Mini.rasmap")))

    def test_referenced_delete_refused_then_forced(self):
        with self.assertRaises(FlowInUse):
            delete_flow(self.project, "u01")
        self.assertTrue(os.path.isfile(self.path("Mini.u01")))

        report = delete_flow(self.project, "u01", force=True)
        self.assertEqual(report["referencing_plans"], ["p03", "p04"])
        self.assertEqual(len(report["warnings"]), 1)
        self.assertFalse(os.path.exists(self.path("Mini.u01")))

    def test_delete_steady_leaves_rasmap_alone(self):
        report = delete_flow(self.project, "f03")
        self.assertEqual(report["deleted"], ["Mini.f03"])
        self.assertEqual(report["prj_removed"], ["Flow File=f03"])
        self.assertEqual(report["rasmap_removed"], [])
        self.assertEqual(self.project.model.steady_file_ids, ["f01"])
        self.assertIn("Flow File=f01", _read(self.prj_path))

    def test_active_run_refused(self):
        with open(self.path("Mini.p05.tmp.hdf"), "wb") as f:
            f.write(b"x")
        with self.assertRaises(FlowRunActive):
            delete_flow(self.project, "u02", force=True)

    def test_orphan_refused(self):
        _write(self.path("Mini.u09"), ["Flow Title=Orphan"])
        with self.assertRaises(ValueError):
            delete_flow(self.project, "u09")


class TestDeleteFlowsBulk(FlowProjectBase):
    def test_bulk_spec_may_mix_kinds(self):
        report = delete_flows(self.project, "u04,f03")
        self.assertEqual(report["deleted_flows"], ["f03", "u04"])
        self.assertEqual(self.project.model.unsteady_file_ids, ["u01", "u02"])
        self.assertEqual(self.project.model.steady_file_ids, ["f01"])
        self.assertEqual(report["rasmap_removed"], ["u04"])

    def test_bulk_range_with_prefixed_endpoints(self):
        report = delete_flows(self.project, "u01-u02", force=True)
        self.assertEqual(report["deleted_flows"], ["u01", "u02"])
        self.assertEqual(self.project.model.unsteady_file_ids, ["u04"])

    def test_bulk_range_spanning_a_gap_is_fail_fast(self):
        # u02-u04 expands to include u03, which does not exist (the gap)
        with self.assertRaises(FlowFileNotFound):
            delete_flows(self.project, "u02-u04", force=True)
        self.assertTrue(os.path.isfile(self.path("Mini.u02")))
        self.assertTrue(os.path.isfile(self.path("Mini.u04")))

    def test_bare_number_spec_refused(self):
        with self.assertRaises(ValueError) as ctx:
            delete_flows(self.project, "04")
        self.assertIn("kind prefix", str(ctx.exception))
        self.assertTrue(os.path.isfile(self.path("Mini.u04")))

    def test_cross_kind_range_refused(self):
        with self.assertRaises(ValueError) as ctx:
            delete_flows(self.project, "f01-u04")
        self.assertIn("mixes kinds", str(ctx.exception))

    def test_bulk_is_fail_fast_on_a_missing_file(self):
        with self.assertRaises(FlowFileNotFound):
            delete_flows(self.project, "u04,u09")
        self.assertTrue(os.path.isfile(self.path("Mini.u04")))

    def test_bulk_refuses_whole_call_if_any_is_referenced(self):
        with self.assertRaises(FlowInUse):
            delete_flows(self.project, "u01,u04")
        self.assertTrue(os.path.isfile(self.path("Mini.u04")))


if __name__ == "__main__":
    unittest.main()

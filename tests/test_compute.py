# tests/test_compute.py
"""project/compute.py — running plans with ``Ras.exe -c``.

Two halves:
- Pre-flight and bookkeeping, no HEC-RAS needed: run order, restart
  dependencies, the version guard, status reading, skip-on-failure.
- Real runs (marker ``ras_compute``; ``pytest --skip-ras`` leaves them out),
  each on a temp copy: the 2D fixture's p04 -> p05 restart chain in 7.0, the
  steady Wisconsin p02 in 7.0, and — when 7.0.1 is installed — a forced
  7.0.1 run of p05, which RAS refuses because its restart file is from 7.0.
  That last one is the evidence behind the version guard (TODO.md §H).

Fixture facts used here: in 2D_culvert_bridge_levee_precip_pipes p05 uses u04,
whose ``Use Restart=-1`` points at ``Model.p04.01JAN2025 1600.rst``; p02, p04,
p06 and p07 use u02 (``Use Restart= 0``). p05 runs on g03. Every plan HDF and
g02-g05 HDF is stamped ``HEC-RAS 7.0``. The .prj lists a stale p03.
"""
import os
import shutil
import tempfile
import unittest
from unittest import mock

import pytest

from hack_ras import RasProject, compute
from hack_ras.project.plans import PlanFileNotFound, PlanRunActive
from hack_ras.utils.lines import read_lines, write_lines

_DATA = os.path.join(os.path.dirname(__file__), "data")
_FIXTURE = os.path.join(_DATA, "2D_culvert_bridge_levee_precip_pipes")
_WISC = os.path.join(_DATA, "Wisconsin_Floodway")
HAS_FIXTURE = os.path.isfile(os.path.join(_FIXTURE, "Model.p05.hdf"))
HAS_70 = "7.0" in compute.installed_versions()
HAS_701 = "7.0.1" in compute.installed_versions()


def _copy(src, with_rasters=False):
    tmp = tempfile.TemporaryDirectory()
    dst = os.path.join(tmp.name, "model")
    ignore = None if with_rasters else shutil.ignore_patterns(
        "Terrain", "Land_Classification", "*.backup")
    shutil.copytree(src, dst, ignore=ignore)
    return tmp, dst


class TestOrderedIds(unittest.TestCase):
    def test_keeps_caller_order(self):
        self.assertEqual(compute._ordered_ids("p05,p04"), ["p05", "p04"])
        self.assertEqual(compute._ordered_ids(["5-7", 2]),
                         ["p05", "p06", "p07", "p02"])

    def test_duplicates_keep_first_position(self):
        self.assertEqual(compute._ordered_ids("4,2-5"), ["p04", "p02", "p03", "p05"])


class TestFailureLine(unittest.TestCase):
    """The one-line failure reason, from real compute-message text."""
    def test_fortran_error_wins(self):
        # 2026-10-08, Model.dss held open exclusively during p06
        text = ("Writing Results to DSS\n"
                "forrtl: severe (159): Program Exception - breakpoint\n"
                "Image  PC  Routine  Line  Source\n"
                "Error with program: RasUnsteady.exe  Process Count = 3  Exit Code = 159\n")
        self.assertEqual(compute._failure_line(text),
                         "forrtl: severe (159): Program Exception - breakpoint")

    def test_ras_error_line(self):
        # 2026-10-08, 7.0.1 run of a plan restarting from a 7.0 .rst
        text = ("The Restart file is not from HEC-RAS 7.0.1 June 2026\n"
                "Program execution halted\n\nUnsteady flow encountered an error.\n")
        self.assertEqual(compute._failure_line(text), "Unsteady flow encountered an error.")

    def test_last_error_line_skips_hdf5_noise(self):
        # 2026-10-08, Hillside p05 run from the X: drive right after p01 (same g01)
        text = ("HDF5-DIAG: Error detected in HDF5 (1.10.6) thread 32232:\n"
                "  #000: H5D.c line 451 in H5Dget_type(): not a dataset\n"
                "  Error computing Structures connectivity\n"
                "HDF5-DIAG: Error detected in HDF5 (1.10.6) thread 32232:\n"
                "Geometry Writer Failed\nError Processing Geometry\n\n"
                "Computations Summary\n")
        self.assertEqual(compute._failure_line(text), "Error Processing Geometry")

    def test_nothing_found(self):
        self.assertEqual(compute._failure_line(None), "")


class TestExecutable(unittest.TestCase):
    def test_exact_version_folder_only(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "7.0.1"))
            open(os.path.join(root, "7.0.1", "Ras.exe"), "w").close()
            os.makedirs(os.path.join(root, "6.6"))          # no Ras.exe
            self.assertEqual(compute.installed_versions(root), ["7.0.1"])
            self.assertTrue(compute.ras_exe_path("7.0.1", root).endswith("Ras.exe"))
            with self.assertRaises(FileNotFoundError) as cm:
                compute.ras_exe_path("7.0", root)          # 7.0 is NOT 7.0.1
            self.assertIn("7.0.1", str(cm.exception))

    def test_older_than_70_out_of_scope(self):
        """Running plans is 7.0+ only — refused even when the folder exists
        (4.1's ras.exe has no -c batch mode and would just open the GUI)."""
        with tempfile.TemporaryDirectory() as root:
            for v in ("4.1.0", "5.0.7", "6.6"):
                os.makedirs(os.path.join(root, v))
                open(os.path.join(root, v, "Ras.exe"), "w").close()
                with self.assertRaises(ValueError) as cm:
                    compute.ras_exe_path(v, root)
                self.assertIn("7.0 or later", str(cm.exception))


class TestChildProcesses(unittest.TestCase):
    """run_plans waits for Ras.exe's children (RasPlotDriver.exe outlives it)."""
    def test_finds_and_waits_for_child(self):
        import subprocess
        import sys
        import time
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1.5)"])
        self.addCleanup(child.wait)
        self.assertIn(child.pid, compute._child_pids(os.getpid()))
        t0 = time.time()
        compute._wait_for_children(os.getpid(), limit=10)
        self.assertLess(time.time() - t0, 5)
        self.assertNotIn(child.pid, compute._child_pids(os.getpid()))


@unittest.skipUnless(HAS_FIXTURE, "2D fixture not present")
class TestPrepareRuns(unittest.TestCase):
    def setUp(self):
        self._tmp, self.folder = _copy(_FIXTURE)
        self.addCleanup(self._tmp.cleanup)
        self.project = RasProject(os.path.join(self.folder, "Model.prj"))

    def test_restart_source(self):
        self.assertEqual(compute.restart_source(self.project, "p05"), "p04")
        for pid in ("p02", "p04", "p06", "p07"):
            self.assertIsNone(compute.restart_source(self.project, pid), pid)

    def test_inactive_restart_is_ignored(self):
        upath = os.path.join(self.folder, "Model.u04")
        lines = [l.replace("Use Restart=-1", "Use Restart= 0") for l in read_lines(upath)]
        write_lines(upath, lines)
        self.assertIsNone(compute.restart_source(self.project, "p05"))

    def test_bad_restart_order_refused(self):
        with self.assertRaises(compute.RestartOrderError) as cm:
            compute.prepare_runs(self.project, "p05,p04", "7.0")
        self.assertIn("p04 must run before p05", str(cm.exception))

    def test_reorder_moves_producer_only(self):
        prep = compute.prepare_runs(self.project, "p07,p05,p02,p04", "7.0",
                                    reorder=True)
        self.assertEqual(prep["order"], ["p07", "p02", "p04", "p05"])
        self.assertEqual(prep["restart_sources"], {"p05": "p04"})

    def test_good_order_passes_unchanged(self):
        prep = compute.prepare_runs(self.project, "p04,p05", "7.0")
        self.assertEqual(prep["order"], ["p04", "p05"])
        self.assertEqual(prep["version_mismatches"], [])

    def test_restart_cycle(self):
        with self.assertRaises(compute.RestartOrderError):
            compute._order_by_restarts(["p01", "p02"], {"p01": "p02", "p02": "p01"},
                                       reorder=True)

    def test_version_mismatch_refused(self):
        with self.assertRaises(compute.RasVersionMismatch) as cm:
            compute.prepare_runs(self.project, "p05", "7.0.1")
        msg = str(cm.exception)
        for part in ("p05.hdf", "g03.hdf", "p04.hdf (restart source)"):
            self.assertIn(part, msg)

    def test_in_batch_restart_source_not_version_checked(self):
        prep = compute.prepare_runs(self.project, "p04,p05", "7.0.1",
                                    force_version=True)
        self.assertFalse(any("restart source" in m for m in prep["version_mismatches"]))

    def test_force_version_reports(self):
        prep = compute.prepare_runs(self.project, "p05", "7.0.1", force_version=True)
        self.assertEqual(len(prep["version_mismatches"]), 3)

    def test_never_run_plan_imposes_no_version(self):
        os.remove(os.path.join(self.folder, "Model.p02.hdf"))
        os.remove(os.path.join(self.folder, "Model.g02.hdf"))
        prep = compute.prepare_runs(self.project, "p02", "7.0.1")
        self.assertEqual(prep["version_mismatches"], [])

    def test_unlisted_or_missing_plan(self):
        with self.assertRaises(PlanFileNotFound):
            compute.prepare_runs(self.project, "p08", "7.0")   # not listed
        with self.assertRaises(PlanFileNotFound):
            compute.prepare_runs(self.project, "p03", "7.0")   # stale prj entry

    def test_active_run_refused(self):
        open(os.path.join(self.folder, "Model.p05.tmp.hdf"), "w").close()
        with self.assertRaises(PlanRunActive):
            compute.prepare_runs(self.project, "p04,p05", "7.0")

    def test_missing_geometry_refused(self):
        os.remove(os.path.join(self.folder, "Model.g03"))
        with self.assertRaises(FileNotFoundError):
            compute.prepare_runs(self.project, "p05", "7.0")

    def test_read_run_status(self):
        st = compute.read_run_status(os.path.join(self.folder, "Model.p05.hdf"))
        self.assertEqual(st["solution"], "Unsteady Finished Successfully")
        self.assertIn("Plan: 'Continue higher IA and nvals'", st["messages"])

    def test_failed_producer_skips_consumer(self):
        """Mocked launch: p04 'fails', so p05 (its restart consumer) is skipped
        while p07 still runs. No HEC-RAS involved."""
        def fake_popen(cmd):
            for pid in ("p04", "p05", "p07"):
                if f"Model.{pid}\"" in cmd:
                    os.utime(os.path.join(self.folder, f"Model.{pid}.hdf"))
            return mock.Mock(pid=0, wait=mock.Mock(return_value=0))

        def fake_status(hdf):
            sol = ("Unsteady failed to run" if hdf.endswith("p04.hdf")
                   else "Unsteady Finished Successfully")
            return {"solution": sol, "run_time_window": None, "messages": "x\nboom"}

        with mock.patch.object(compute, "ras_exe_path", return_value="Ras.exe"), \
                mock.patch.object(compute, "_wait_for_children"), \
                mock.patch.object(compute.subprocess, "Popen", side_effect=fake_popen), \
                mock.patch.object(compute, "read_run_status", side_effect=fake_status):
            rep = compute.run_plans(self.project, "p04,p05,p07", "7.0")
        status = {r["plan"]: r["status"] for r in rep["runs"]}
        self.assertEqual(status, {"p04": "failed", "p05": "skipped", "p07": "ok"})
        self.assertIn("boom", rep["runs"][0]["messages_tail"])
        self.assertIn("1 of 3 ok", compute.format_report(rep))

    def test_long_tmp_hdf_path_refused(self):
        """A .p##.tmp.hdf over 259 characters made Ras.exe hang on 'Run-time
        error 53' (TESTING folder, 2026-10-08). The folder is sized so the plan
        file itself fits under the limit and only its .tmp.hdf does not."""
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        pad = 260 - len(root) - 2 - len("Model.p02.tmp.hdf")   # two separators
        deep = os.path.join(root, "d" * pad)
        os.makedirs(deep)
        for name in ("Model.prj", "Model.p02", "Model.g02", "Model.u02"):
            shutil.copy(os.path.join(self.folder, name), deep)
        project = RasProject(os.path.join(deep, "Model.prj"))
        self.assertEqual(len(os.path.join(deep, "Model.p02.tmp.hdf")), 260)
        with self.assertRaises(ValueError) as cm:
            compute.prepare_runs(project, "p02", "7.0")
        self.assertIn("Run-time error 53", str(cm.exception))

    def test_timeout_reason(self):
        """A hang is killed and reported; the .tmp.hdf advice appears only
        when RAS actually left one behind."""
        import subprocess

        def hung(leave_tmp):
            def wait(timeout=None):
                if timeout is not None:
                    if leave_tmp:
                        open(os.path.join(self.folder, "Model.p07.tmp.hdf"), "w").close()
                    raise subprocess.TimeoutExpired("Ras.exe", timeout)
                return 1
            return mock.Mock(pid=0, wait=wait)

        for leave_tmp in (False, True):
            with mock.patch.object(compute, "ras_exe_path", return_value="Ras.exe"), \
                    mock.patch.object(compute, "_wait_for_children"), \
                    mock.patch.object(compute, "_process_tree", return_value=set()), \
                    mock.patch.object(compute, "_kill_tree") as kill, \
                    mock.patch.object(compute.subprocess, "Popen",
                                      return_value=hung(leave_tmp)):
                rep = compute.run_plans(self.project, "p07", "7.0", timeout=1)
            r = rep["runs"][0]
            self.assertEqual(r["status"], "timeout")
            self.assertTrue(kill.called)
            self.assertIn("dialog", r["reason"])
            self.assertEqual("Model.p07.tmp.hdf (unfinalized results)" in r["reason"],
                             leave_tmp)
            if leave_tmp:
                os.remove(os.path.join(self.folder, "Model.p07.tmp.hdf"))

    def test_dialog_detected_read_and_killed(self):
        """A real modal message box — raised by a stand-in child process in
        place of Ras.exe, with the text RAS showed for a too-long path — is
        found within the grace period, read, and its process killed; the run
        is reported 'dialog' and the batch goes on. (It flashes on screen.)"""
        import subprocess
        import sys
        box = ("import ctypes; ctypes.windll.user32.MessageBoxW("
               "0, \"Run-time error '53':\\n\\nFile not found\", \"RAS\", 0)")
        real_popen = subprocess.Popen
        children = []
        # Close any box left up if the test fails midway (one once was).
        self.addCleanup(lambda: [c.kill() for c in children if c.poll() is None])

        def fake_popen(cmd, **kw):
            if not isinstance(cmd, str):              # taskkill from _kill_tree
                return real_popen(cmd, **kw)
            children.append(real_popen([sys.executable, "-c", box]))
            return children[-1]

        with mock.patch.object(compute, "ras_exe_path", return_value="Ras.exe"), \
                mock.patch.object(compute.subprocess, "Popen", side_effect=fake_popen):
            rep = compute.run_plans(self.project, "p07,p02", "7.0", timeout=60)
        for r in rep["runs"]:
            self.assertEqual(r["status"], "dialog")
            self.assertIn("File not found", r["reason"])
            self.assertLess(r["seconds"], 15)            # grace 3 s, not the 60 s timeout
        self.assertTrue(all(c.poll() is not None for c in children))

    def test_dialog_watch_off(self):
        """kill_on_dialog=False leaves only the timeout."""
        with mock.patch.object(compute, "_dialogs_of") as dialogs:
            proc = mock.Mock(pid=1, wait=mock.Mock(return_value=0))
            self.assertEqual(compute._wait_watching(proc, None, None), (None, ""))
            dialogs.assert_not_called()

    def test_data_errors_reported_only_when_fresh(self):
        """RAS's <plan>.data_errors.txt (the Hillside p05 X:-drive hang wrote
        one) goes into the report when written during the run; a copy left
        by an earlier run does not."""
        import subprocess
        derr = os.path.join(self.folder, "Model.p07.data_errors.txt")
        text = ("Unable to delete temporary results file: X:\\...\\Model.p07.tmp.hdf\n"
                "File not found\n")

        def run_once(write_during_run):
            def wait(timeout=None):
                if timeout is not None:
                    if write_during_run:
                        with open(derr, "w") as f:
                            f.write(text)
                    raise subprocess.TimeoutExpired("Ras.exe", timeout)
                return 1
            with mock.patch.object(compute, "ras_exe_path", return_value="Ras.exe"), \
                    mock.patch.object(compute, "_wait_for_children"), \
                    mock.patch.object(compute, "_process_tree", return_value=set()), \
                    mock.patch.object(compute, "_kill_tree"), \
                    mock.patch.object(compute.subprocess, "Popen",
                                      return_value=mock.Mock(pid=0, wait=wait)):
                return compute.run_plans(self.project, "p07", "7.0", timeout=1)

        rep = run_once(True)
        self.assertIn("Unable to delete temporary results file", rep["runs"][0]["data_errors"])
        self.assertIn("p07.data_errors.txt:", compute.format_report(rep))
        old = os.path.getmtime(derr) - 100
        os.utime(derr, (old, old))
        self.assertEqual(run_once(False)["runs"][0]["data_errors"], "")

    def test_stale_hdf_is_no_results(self):
        """Ras.exe exits without touching the HDF -> 'no_results', not 'ok'."""
        old = os.path.getmtime(os.path.join(self.folder, "Model.p04.hdf"))
        os.utime(os.path.join(self.folder, "Model.p04.hdf"), (old - 100, old - 100))
        with mock.patch.object(compute, "ras_exe_path", return_value="Ras.exe"), \
                mock.patch.object(compute, "_wait_for_children"), \
                mock.patch.object(compute.subprocess, "Popen",
                                  return_value=mock.Mock(pid=0, wait=mock.Mock(return_value=0))):
            rep = compute.run_plans(self.project, "p04", "7.0")
        self.assertEqual(rep["runs"][0]["status"], "no_results")


@pytest.mark.ras_compute
@unittest.skipUnless(HAS_FIXTURE and HAS_70, "fixture or HEC-RAS 7.0 not present")
class TestRealRuns70(unittest.TestCase):
    def test_restart_chain_reordered_and_run(self):
        tmp, folder = _copy(_FIXTURE, with_rasters=True)
        self.addCleanup(tmp.cleanup)
        prj = os.path.join(folder, "Model.prj")
        prj_bytes = open(prj, "rb").read()
        project = RasProject(prj)
        rep = compute.run_plans(project, "p05,p04", "7.0", reorder=True, timeout=300)
        self.assertEqual(rep["order"], ["p04", "p05"])
        for r in rep["runs"]:
            self.assertEqual(r["status"], "ok", compute.format_report(rep))
            self.assertTrue(r["run_time_window"])
        # -c computes the named plan without touching the .prj (Current Plan=p07)
        self.assertEqual(open(prj, "rb").read(), prj_bytes)

    def test_steady_plan(self):
        tmp, folder = _copy(_WISC)
        self.addCleanup(tmp.cleanup)
        rep = compute.run_plans(RasProject(os.path.join(folder, "SterpCreek.prj")),
                                "p02", "7.0", timeout=300)
        r = rep["runs"][0]
        self.assertEqual(r["status"], "ok", compute.format_report(rep))
        self.assertEqual(r["solution"], "Steady Finished Successfully")


@pytest.mark.ras_compute
@unittest.skipUnless(HAS_FIXTURE and HAS_701, "fixture or HEC-RAS 7.0.1 not present")
class TestRealRunWrongVersion(unittest.TestCase):
    def test_forced_701_fails_on_70_restart(self):
        """The exit code says nothing; the HDF says 'failed'."""
        tmp, folder = _copy(_FIXTURE, with_rasters=True)
        self.addCleanup(tmp.cleanup)
        rep = compute.run_plans(RasProject(os.path.join(folder, "Model.prj")),
                                "p05", "7.0.1", force_version=True, timeout=300)
        r = rep["runs"][0]
        self.assertEqual(r["status"], "failed")
        self.assertEqual(r["solution"], "Unsteady failed to run")
        self.assertIn("Restart file is not from HEC-RAS 7.0.1", r["messages_tail"])
        self.assertEqual(len(rep["version_mismatches"]), 3)


if __name__ == "__main__":
    unittest.main()

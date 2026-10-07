"""
Tests for hack_ras.results.steady_output — the RAS steady binary output (.O##) reader.

Answer keys, both independent of the reader:

* HEC_Critical_Creek_4.1 / HEC_ConSpan_4.1 — HEC example models run in the RAS 4.1
  GUI (2026-10-07).  Each ``<base>.O##.wse.csv`` was dumped from the RAS 4.1 COM
  controller (``Output_NodeOutput``, variable 2 = W.S. Elev), every value written
  as the shortest string that round-trips its float32.  CRITCREK.O02 carries the
  +16 decoy (10 of 63 XS differ from the real WSE at +128) and 51 interpolated
  XS; ConSpan has 4 profiles, culvert nodes, interpolated XS, and (O01) a
  cross section with a 16-char Node Name.
* Wisconsin_Floodway — SterpCreek p01 (RAS 5.0.3) and p02 (RAS 7.0) wrote both a
  .O## and a .p##.hdf on the same run, so the HDF reader is the key.  Its named
  nodes are all structures.
"""
import csv
import logging
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np

from hack_ras.geometry.parser import GeometryParser
from hack_ras.results.steady_output import (
    read_plan_steady_wse,
    read_steady_output,
    steady_output_path,
    steady_output_short_id,
)

DATA = Path(__file__).parent / "data"
CRIT = DATA / "HEC_Critical_Creek_4.1"
CONSPAN = DATA / "HEC_ConSpan_4.1"
STERP = DATA / "Wisconsin_Floodway"
TWO_D = DATA / "2D_culvert_bridge_levee_precip_pipes"

try:
    import h5py  # noqa: F401
    from hack_ras.results.reader import read_steady_profile_wse
    HAS_H5PY = True
except ImportError:
    HAS_H5PY = False


def _answer_key(csv_path):
    with open(csv_path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


class AnswerKeyTests(unittest.TestCase):
    """Every WSE matches the RAS 4.1 controller's value bit for bit."""

    CASES = [
        (CRIT / "CRITCREK.O01", ["100 yr"]),
        (CRIT / "CRITCREK.O02", ["100 yr"]),
        (CONSPAN / "ConSpan.O01", ["5 yr", "10 yr", "25 yr", "50 yr"]),
        (CONSPAN / "ConSpan.O02", ["5 yr", "10 yr", "25 yr", "50 yr"]),
    ]

    def test_matches_controller_exactly(self):
        for o_path, profiles in self.CASES:
            with self.subTest(o_path.name):
                res = read_steady_output(str(o_path))
                self.assertEqual(res.profile_names, profiles)
                rows = _answer_key(f"{o_path}.wse.csv")
                self.assertEqual(len(rows), len(res.wse) * len(profiles))
                for r in rows:
                    got = res.get_wse(r["river"], r["reach"], r["rs"], r["profile"])
                    self.assertEqual(np.float32(got), np.float32(r["wse"]),
                                     f"{r['rs']} {r['profile']}")

    def test_structures_left_out(self):
        # ConSpan's culvert node is in the .O##'s node table but has no WSE.
        res = read_steady_output(str(CONSPAN / "ConSpan.O01"))
        rows = _answer_key(CONSPAN / "ConSpan.O01.wse.csv")
        self.assertEqual(set(res.wse), {(r["river"], r["reach"], r["rs"]) for r in rows})

    def test_interpolated_keys_match_geometry_parser(self):
        res = read_steady_output(str(CRIT / "CRITCREK.O02"))
        geom = GeometryParser().parse_file(str(CRIT / "CRITCREK.g02"))
        keys = {(rv, rc, str(xs.station).strip())
                for rv, river in geom.rivers.items()
                for rc, reach in river.reaches.items()
                for xs in reach.cross_sections}
        self.assertEqual(sum("*" in k[2] for k in keys), 51)
        self.assertEqual(keys, set(res.wse))

    def test_named_cross_section_keyed_by_river_alone(self):
        # ConSpan g01 gives XS 20.535 the 16-char Node Name 'This is a test f'
        # (set in the RAS 4.1 GUI).  RAS stores it in the 16 bytes right after the
        # river; reading river as 32 bytes would key this XS by river + name.
        res = read_steady_output(str(CONSPAN / "ConSpan.O01"))
        self.assertIn(("Spring Creek", "Culvrt Reach", "20.535"), res.wse)
        self.assertEqual({k[0] for k in res.wse}, {"Spring Creek"})

    def test_short_id_is_the_run_time_plan_short_id(self):
        self.assertEqual(steady_output_short_id(str(CRIT / "CRITCREK.O01")), "Exist Cond")
        self.assertEqual(steady_output_short_id(str(CONSPAN / "ConSpan.O02")), "Twin Circ")


@unittest.skipUnless(HAS_H5PY, "h5py missing")
class CrossVersionTests(unittest.TestCase):
    """RAS 5.0.3 and 7.0 write the same .O## layout; the HDF from the same run is the key."""

    def test_matches_hdf_exactly(self):
        for p in ("01", "02"):
            with self.subTest(plan=p):
                o = read_steady_output(str(STERP / f"SterpCreek.O{p}"))
                h = read_steady_profile_wse(str(STERP / f"SterpCreek.p{p}.hdf"))
                self.assertEqual(o.profile_names, h.profile_names)
                self.assertEqual(set(o.wse), set(h.wse))
                self.assertEqual(len(o.wse), 73)
                for k in h.wse:
                    np.testing.assert_array_equal(
                        o.wse[k].astype(np.float32), h.wse[k].astype(np.float32), str(k))


class PathTests(unittest.TestCase):

    def test_finds_steady_output(self):
        self.assertEqual(steady_output_path(str(CRIT / "CRITCREK.p02")).lower(),
                         str(CRIT / "CRITCREK.O02").lower())

    def test_ignores_unsteady_initial_conditions_file(self):
        # Model.ic.o02 shares the .O## header layout; it must not be picked up.
        self.assertTrue((TWO_D / "Model.ic.o02").exists())
        with self.assertRaises(FileNotFoundError):
            steady_output_path(str(TWO_D / "Model.p02"))

    def test_rejects_non_plan_path(self):
        with self.assertRaises(ValueError):
            steady_output_path(str(CRIT / "CRITCREK.g02"))


class PlanFallbackTests(unittest.TestCase):
    """read_plan_steady_wse: HDF first, else .O##; staleness and SI handled on temp copies."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _copy(self, folder):
        dst = Path(self.tmp) / folder.name
        shutil.copytree(folder, dst)
        return dst

    @unittest.skipUnless(HAS_H5PY, "h5py missing")
    def test_prefers_hdf_when_both_exist(self):
        res, source = read_plan_steady_wse(str(STERP / "SterpCreek.p01"))
        self.assertTrue(source.endswith(".p01.hdf"))
        self.assertEqual(len(res.wse), 73)

    @unittest.skipUnless(HAS_H5PY, "h5py missing")
    def test_falls_back_to_output_without_hdf(self):
        d = self._copy(STERP)
        os.remove(d / "SterpCreek.p01.hdf")
        res, source = read_plan_steady_wse(str(d / "SterpCreek.p01"))
        self.assertTrue(source.endswith(".O01"))
        h = read_steady_profile_wse(str(STERP / "SterpCreek.p01.hdf"))
        self.assertEqual(set(res.wse), set(h.wse))

    def test_no_results_raises(self):
        d = self._copy(CRIT)
        os.remove(d / "CRITCREK.O01")
        with self.assertRaises(FileNotFoundError):
            read_plan_steady_wse(str(d / "CRITCREK.p01"))

    def test_current_output_logs_no_warning(self):
        with self.assertNoLogs(level=logging.WARNING):
            read_plan_steady_wse(str(CRIT / "CRITCREK.p02"))

    def test_edited_plan_warns_stale(self):
        d = self._copy(CRIT)
        plan = d / "CRITCREK.p01"
        text = plan.read_text(encoding="latin-1")
        plan.write_text(text.replace("Short Identifier=Exist Cond",
                                     "Short Identifier=Renamed"), encoding="latin-1")
        with self.assertLogs(level=logging.WARNING) as logs:
            res, _ = read_plan_steady_wse(str(plan))
        self.assertIn("stale", logs.output[0])
        self.assertEqual(len(res.wse), 12)      # still returned

    def test_si_project_refused(self):
        d = self._copy(CRIT)
        prj = d / "CRITCREK.prj"
        prj.write_text(prj.read_text(encoding="latin-1").replace("English Units", "SI Units"),
                       encoding="latin-1")
        with self.assertRaises(ValueError):
            read_plan_steady_wse(str(d / "CRITCREK.p01"))


class MalformedTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, data):
        path = os.path.join(self.tmp, "bad.O01")
        with open(path, "wb") as f:
            f.write(data)
        return path

    def test_too_short(self):
        with self.assertRaises(ValueError):
            read_steady_output(self._write(b"\x00" * 40))

    def test_truncated_data_area(self):
        good = (CRIT / "CRITCREK.O01").read_bytes()
        with self.assertRaises(ValueError):
            read_steady_output(self._write(good[:len(good) // 2]))

    def test_missing_file(self):
        with self.assertRaises(FileNotFoundError):
            read_steady_output(os.path.join(self.tmp, "nope.O01"))


if __name__ == "__main__":
    unittest.main()

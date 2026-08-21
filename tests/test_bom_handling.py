# tests/test_bom_handling.py
"""UTF-8 BOM handling in hack_ras/utils/lines.py and the readers above it.

Some Windows editors prepend a UTF-8 BOM (EF BB BF) when saving a HEC-RAS text
file. RAS ignores it and runs such files fine, but under latin-1 those three
bytes decode to three ordinary characters in front of the first key, which
makes every line-1 key match fail -- a BOM'd .p## reports an empty
'Plan Title='. That blinds the duplicate-title guard in clone_plan and makes
project_health print "(missing)" for a title that is really there.

read_lines strips the BOM; write_lines re-attaches one the destination already
had, so an in-place edit stays byte-for-byte identical. These tests pin both
halves. The byte-identity tests here are the guard against a future
"write to temp, then rename" refactor for atomicity, which would silently
defeat BOM preservation because the temp path has no BOM to find.
"""
import os
import tempfile
import unittest

from hack_ras import RasProject
from hack_ras.geometry.parser import GeometryParser
from hack_ras.geometry.shift import shift_xs_cutlines
from hack_ras.geometry.writer import GeometryWriter
from hack_ras.project.health import project_health
from hack_ras.project.plans import clone_plan, plan_path
from hack_ras.utils.lines import read_lines, write_lines

BOM = b"\xef\xbb\xbf"
CRLF = "\r\n"


def _write_raw(path, text, bom=False):
    with open(path, "wb") as f:
        if bom:
            f.write(BOM)
        f.write(text.encode("latin-1"))


def _read_raw(path):
    with open(path, "rb") as f:
        return f.read()


def _plan_text(title):
    return CRLF.join([
        f"Plan Title={title}",
        f"Short Identifier={title.ljust(24)}",
        "Geom File=g01",
        "Flow File=u01",
        "Run HTab=-1 ",
    ]) + CRLF


class BomBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def path(self, name):
        return os.path.join(self.folder, name)


class TestReadLines(BomBase):
    def test_bom_is_stripped_so_line_one_key_matches(self):
        p = self.path("Mini.p01")
        _write_raw(p, _plan_text("BOM Plan"), bom=True)
        lines = read_lines(p)
        self.assertTrue(lines[0].startswith("Plan Title="))
        self.assertEqual(lines[0], f"Plan Title=BOM Plan{CRLF}")

    def test_bomless_file_is_unaffected(self):
        p = self.path("Mini.p02")
        _write_raw(p, _plan_text("Plain Plan"), bom=False)
        self.assertEqual(read_lines(p)[0], f"Plan Title=Plain Plan{CRLF}")

    def test_a_bom_only_file_does_not_crash(self):
        p = self.path("Mini.p03")
        _write_raw(p, "", bom=True)
        self.assertEqual(read_lines(p), [])

    def test_lines_below_line_one_are_identical_with_and_without_a_bom(self):
        a, b = self.path("A.p01"), self.path("B.p01")
        _write_raw(a, _plan_text("Same"), bom=True)
        _write_raw(b, _plan_text("Same"), bom=False)
        self.assertEqual(read_lines(a), read_lines(b))


class TestWriteLines(BomBase):
    def test_in_place_rewrite_of_a_bom_file_is_byte_identical(self):
        p = self.path("Mini.p01")
        _write_raw(p, _plan_text("Round Trip"), bom=True)
        before = _read_raw(p)
        write_lines(p, read_lines(p))          # read -> write, no edits
        self.assertEqual(_read_raw(p), before)
        self.assertTrue(_read_raw(p).startswith(BOM))

    def test_in_place_rewrite_of_a_bomless_file_is_byte_identical(self):
        p = self.path("Mini.p02")
        _write_raw(p, _plan_text("Round Trip"), bom=False)
        before = _read_raw(p)
        write_lines(p, read_lines(p))
        self.assertEqual(_read_raw(p), before)
        self.assertFalse(_read_raw(p).startswith(BOM))

    def test_edited_bom_file_keeps_its_bom_and_only_the_edit_changes(self):
        p = self.path("Mini.p03")
        _write_raw(p, _plan_text("Old Title"), bom=True)
        before = read_lines(p)

        lines = list(before)
        lines[0] = f"Plan Title=New Title{CRLF}"
        write_lines(p, lines)

        self.assertTrue(_read_raw(p).startswith(BOM))
        after = read_lines(p)
        changed = [(b, a) for b, a in zip(before, after) if b != a]
        self.assertEqual(changed, [(f"Plan Title=Old Title{CRLF}",
                                    f"Plan Title=New Title{CRLF}")])

    def test_a_new_file_gets_no_bom(self):
        p = self.path("Fresh.p01")
        write_lines(p, [f"Plan Title=Fresh{CRLF}"])
        self.assertFalse(_read_raw(p).startswith(BOM))

    def test_read_write_are_inverses_regardless_of_bom(self):
        for name, bom in (("A.p01", True), ("B.p01", False)):
            p = self.path(name)
            _write_raw(p, _plan_text("Inverse"), bom=bom)
            lines = read_lines(p)
            write_lines(p, lines)
            self.assertEqual(read_lines(p), lines)


class TestClonePlanFromBomSource(BomBase):
    """The failure with teeth: before the fix, clone_plan could not see a
    BOM'd source's title, so it neither raised DuplicatePlanTitle nor
    replaced the title -- it silently wrote a clone carrying the source's
    title, which HEC-RAS cannot disambiguate."""

    def setUp(self):
        super().setUp()
        self.prj_path = self.path("Mini.prj")
        _write_raw(self.prj_path, CRLF.join([
            "Proj Title=Mini",
            "Current Plan=p01",
            "Geom File=g01",
            "Unsteady File=u01",
            "Plan File=p01",
            "DSS File=dss",
        ]) + CRLF)
        _write_raw(self.path("Mini.p01"), _plan_text("BOM Source"), bom=True)
        self.project = RasProject(self.prj_path)

    def test_clone_replaces_the_title(self):
        new = clone_plan(self.project, "p01", "Clone Of BOM")
        lines = read_lines(plan_path(self.project, new))
        self.assertEqual(lines[0], f"Plan Title=Clone Of BOM{CRLF}")
        self.assertTrue(lines[1].startswith("Short Identifier=Clone Of BOM"))

    def test_clone_detects_a_duplicate_title_against_a_bom_source(self):
        from hack_ras.project.plans import DuplicatePlanTitle
        with self.assertRaises(DuplicatePlanTitle):
            clone_plan(self.project, "p01", "BOM Source")

    def test_source_file_is_untouched_and_keeps_its_bom(self):
        before = _read_raw(self.path("Mini.p01"))
        clone_plan(self.project, "p01", "Clone Of BOM")
        self.assertEqual(_read_raw(self.path("Mini.p01")), before)

    def test_health_reports_the_title_not_missing(self):
        h = project_health(self.project)
        titles = {p.id: p.title for p in h.plans}
        self.assertEqual(titles["p01"], "BOM Source")


class TestBomInPrj(BomBase):
    def test_a_bom_prj_still_parses_and_keeps_its_bom_when_rewritten(self):
        prj = self.path("Mini.prj")
        _write_raw(prj, CRLF.join([
            "Proj Title=BOM Project",
            "Geom File=g01",
            "Plan File=p01",
        ]) + CRLF, bom=True)
        _write_raw(self.path("Mini.p01"), _plan_text("P One"))
        project = RasProject(prj)
        self.assertEqual(project.title, "BOM Project")
        self.assertEqual(project.model.plan_file_ids, ["p01"])
        before = _read_raw(prj)
        write_lines(prj, read_lines(prj))
        self.assertEqual(_read_raw(prj), before)


class TestBomGeometry(BomBase):
    """A BOM'd .g## used to be a hard failure, not a cosmetic one: the title
    parsed as None and GeometryWriter raised UnicodeEncodeError, because the
    BOM reached raw_lines as '\ufeff' and the writer's codepage cannot
    encode it. GeometryParser reads with utf-8-sig, so the BOM never lands in
    raw_lines. Nothing preserves it: every GeometryWriter call site writes a
    NEW file (shifter, merge, Mesh_Health snapper), so the same
    "new files are BOM-free" rule as cloning applies."""

    DATA = os.path.join(os.path.dirname(__file__), "data")

    def _bom_copy(self, src, name):
        dst = self.path(name)
        with open(dst, "wb") as o:
            o.write(BOM)
            with open(src, "rb") as i:
                o.write(i.read())
        return dst

    def test_bom_geometry_parses_its_title(self):
        src = os.path.join(self.DATA, "Beaver", "beaver.g01")
        geom = GeometryParser().parse_file(self._bom_copy(src, "bom.g01"))
        self.assertEqual(geom.title, "Beaver Cr.  - bridge")

    def test_bom_geometry_writes_without_crashing(self):
        src = os.path.join(self.DATA, "Beaver", "beaver.g01")
        geom = GeometryParser().parse_file(self._bom_copy(src, "bom.g01"))
        out = self.path("out.g01")
        GeometryWriter().write(geom, out)               # used to raise
        with open(src, "rb") as f:
            self.assertEqual(_read_raw(out), f.read())  # == the BOM-less original

    def test_shift_replaces_the_title_on_a_bom_source(self):
        """The hole with teeth: with an unreadable title the shifter silently
        inherited the source's, which is what shift_xs_gis.py requires
        geom_name_out to prevent."""
        src = os.path.join(self.DATA, "Baxter", "Baxter.g02")
        geom = GeometryParser().parse_file(self._bom_copy(src, "bom.g02"))
        trans = {("baxter river", "upper reach", "84816"): 10.0}
        result = shift_xs_cutlines(geom, trans, new_title="Shifted 10ft")

        out = self.path("shifted.g02")
        GeometryWriter().write(result, out)
        self.assertEqual(GeometryParser().parse_file(out).title, "Shifted 10ft")


if __name__ == "__main__":
    unittest.main()

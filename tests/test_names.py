"""
Tests for hack_ras.utils.names.normalize_name

The load-bearing case is interior whitespace: HEC-RAS stores river/reach names in
fixed-width fields, so a reach the GUI shows as "Upper Reach B" is written
'Upper Reach  B' with two interior spaces.  Comparing raw strings makes a
correct-looking hand-typed name fail silently.
"""
import unittest

from hack_ras.utils.names import normalize_name


class NormalizeNameTests(unittest.TestCase):

    def test_interior_whitespace_collapsed(self):
        self.assertEqual(normalize_name("Upper Reach  B"),
                         normalize_name("Upper Reach B"))

    def test_outer_whitespace_stripped(self):
        self.assertEqual(normalize_name("  Upper Reach A  "),
                         normalize_name("Upper Reach A"))

    def test_case_folded(self):
        self.assertEqual(normalize_name("PORTAGE RD TRIB"),
                         normalize_name("Portage Rd Trib"))

    def test_tabs_and_newlines_are_whitespace(self):
        self.assertEqual(normalize_name("Upper\tReach\nB"),
                         normalize_name("Upper Reach B"))

    def test_distinct_names_stay_distinct(self):
        self.assertNotEqual(normalize_name("Upper Reach A"),
                            normalize_name("Upper Reach B"))
        self.assertNotEqual(normalize_name("StarkweatherW"),
                            normalize_name("StarkweatherE"))

    def test_non_string_input(self):
        self.assertEqual(normalize_name(1000), "1000")

    def test_empty_and_whitespace_only(self):
        for value in ("", "   ", "\t\n"):
            self.assertEqual(normalize_name(value), "", repr(value))

    def test_idempotent(self):
        once = normalize_name("  Upper   Reach  B ")
        self.assertEqual(normalize_name(once), once)

    def test_shift_alias_is_the_same_function(self):
        """geometry.shift kept a private alias; it must not drift."""
        from hack_ras.geometry.shift import _normalize_names
        for value in ("Upper Reach  B", "  x  Y ", "PORTAGE Rd Trib"):
            self.assertEqual(_normalize_names(value), normalize_name(value))

    def test_results_model_alias_is_the_same_function(self):
        from hack_ras.results.model import _normalize_name
        self.assertIs(_normalize_name, normalize_name)


if __name__ == "__main__":
    unittest.main()

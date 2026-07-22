import unittest

from hack_ras.resolve import expand_id_spec


class TestExpandIdSpec(unittest.TestCase):

    # ── singles: bare numbers, prefixed ids, mixed types ──────────────────────
    def test_bare_and_prefixed_mixed(self):
        self.assertEqual(expand_id_spec(["01", "p03"], "p"), ["p01", "p03"])

    def test_int_entries(self):
        # YAML parses `01` as the int 1; str/int both accepted.
        self.assertEqual(expand_id_spec([1, 3], "p"), ["p01", "p03"])

    def test_prefix_case_insensitive(self):
        self.assertEqual(expand_id_spec(["P7", "p10"], "p"), ["p07", "p10"])

    def test_scalar_str_accepted(self):
        self.assertEqual(expand_id_spec("p03", "p"), ["p03"])

    def test_scalar_int_accepted(self):
        self.assertEqual(expand_id_spec(5, "p"), ["p05"])

    # ── ranges ────────────────────────────────────────────────────────────────
    def test_range_leading_zero_and_unpadded(self):
        self.assertEqual(
            expand_id_spec(["01-9"], "p"),
            ["p01", "p02", "p03", "p04", "p05", "p06", "p07", "p08", "p09"],
        )

    def test_range_with_prefixed_endpoints(self):
        self.assertEqual(expand_id_spec(["p14-p16"], "p"), ["p14", "p15", "p16"])

    def test_single_value_range(self):
        self.assertEqual(expand_id_spec(["05-05"], "p"), ["p05"])

    def test_users_full_example(self):
        # [01-9, p10, 14-99] -> p01..p09, p10, p14..p99, sorted & unique
        result = expand_id_spec(["01-9", "p10", "14-99"], "p")
        expected = (
            [f"p{n:02d}" for n in range(1, 10)]
            + ["p10"]
            + [f"p{n:02d}" for n in range(14, 100)]
        )
        self.assertEqual(result, expected)

    # ── dedup + sort ────────────────────────────────────────────────────────
    def test_overlap_deduped_and_sorted(self):
        self.assertEqual(
            expand_id_spec(["03", "01-3", "p02"], "p"),
            ["p01", "p02", "p03"],
        )

    # ── other kinds ───────────────────────────────────────────────────────────
    def test_geometry_kind(self):
        self.assertEqual(expand_id_spec(["1", "g03", "5-6"], "g"),
                         ["g01", "g03", "g05", "g06"])

    def test_unsteady_and_steady_kind(self):
        self.assertEqual(expand_id_spec(["01"], "u"), ["u01"])
        self.assertEqual(expand_id_spec(["02-03"], "f"), ["f02", "f03"])

    # ── empties ─────────────────────────────────────────────────────────────
    def test_none_and_empty(self):
        self.assertEqual(expand_id_spec(None, "p"), [])
        self.assertEqual(expand_id_spec([], "p"), [])

    def test_blank_tokens_skipped(self):
        self.assertEqual(expand_id_spec(["", "  ", "p04"], "p"), ["p04"])

    # ── errors ────────────────────────────────────────────────────────────────
    def test_reversed_range_raises(self):
        with self.assertRaises(ValueError):
            expand_id_spec(["9-1"], "p")

    def test_out_of_range_raises(self):
        with self.assertRaises(ValueError):
            expand_id_spec(["0"], "p")
        with self.assertRaises(ValueError):
            expand_id_spec(["100"], "p")

    def test_malformed_range_raises(self):
        for bad in (["1-"], ["-5"], ["1-2-3"]):
            with self.assertRaises(ValueError):
                expand_id_spec(bad, "p")

    def test_non_numeric_token_raises(self):
        with self.assertRaises(ValueError):
            expand_id_spec(["abc"], "p")

    def test_unknown_kind_raises(self):
        with self.assertRaises(ValueError):
            expand_id_spec(["01"], "x")


if __name__ == "__main__":
    unittest.main()

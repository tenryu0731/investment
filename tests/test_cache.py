import tempfile
import unittest
from pathlib import Path

from invest import db, periods


class PeriodsTest(unittest.TestCase):
    def test_roundtrip(self):
        for unit, s in [("D", "2026-07-15"), ("M", "202612"), ("Q", "202604"), ("H", "202602"), ("Y", "2026")]:
            self.assertEqual(periods.from_ord(unit, periods.to_ord(unit, s)), s)

    def test_month_and_quarter_rollover(self):
        self.assertEqual(periods.from_ord("M", periods.to_ord("M", "202612") + 1), "202701")
        self.assertEqual(periods.from_ord("Q", periods.to_ord("Q", "202601") - 1), "202504")

    def test_missing(self):
        self.assertEqual(periods.missing([], 1, 10), [(1, 10)])
        self.assertEqual(periods.missing([(3, 5), (8, 9)], 1, 10), [(1, 2), (6, 7), (10, 10)])
        self.assertEqual(periods.missing([(1, 10)], 2, 9), [])

    def test_merge(self):
        self.assertEqual(periods.merge([(5, 6), (1, 3), (4, 4), (9, 10)]), [(1, 6), (9, 10)])


class CoverageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.sqlite")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_gaps_shrink_after_coverage(self):
        c = self.conn
        self.assertEqual(db.gaps(c, "x", "s", "D", "2026-01-01", "2026-01-31"),
                         [("2026-01-01", "2026-01-31")])
        db.add_coverage(c, "x", "s", "D", "2026-01-01", "2026-01-10")
        db.add_coverage(c, "x", "s", "D", "2026-01-20", "2026-01-31")
        self.assertEqual(db.gaps(c, "x", "s", "D", "2026-01-01", "2026-01-31"),
                         [("2026-01-11", "2026-01-19")])
        db.add_coverage(c, "x", "s", "D", "2026-01-11", "2026-01-19")
        self.assertEqual(db.gaps(c, "x", "s", "D", "2026-01-01", "2026-01-31"), [])
        self.assertEqual(len(db.covered(c, "x", "s", "D")), 1)

    def test_fetch_log(self):
        self.assertIsNone(db.fetched_at(self.conn, "x", "r"))
        db.mark_fetched(self.conn, "x", "r")
        self.assertIsNotNone(db.fetched_at(self.conn, "x", "r"))


if __name__ == "__main__":
    unittest.main()

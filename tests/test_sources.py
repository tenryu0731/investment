"""データ源の基盤 (登録・once・timeseries) のテスト。ネットワークは使わない。"""

import tempfile
import unittest
from pathlib import Path

from invest import db, sources
from invest.sources.base import Source


class Fake(Source):
    name = "fake"
    cli = "fake"

    def __init__(self):
        self.requested = []

    def fetch_range(self, gap_start, gap_end):
        self.calls += 1
        self.requested.append((gap_start, gap_end))
        return 1


class RegistryTest(unittest.TestCase):
    def test_discovers_sources_but_not_template(self):
        srcs = sources.all_sources()
        self.assertIn("boj", srcs)
        self.assertIn("jq", srcs)
        self.assertNotIn("example", srcs)

    def test_every_source_has_commands_and_valid_schema(self):
        with tempfile.TemporaryDirectory() as d:
            conn = sources.connect(Path(d) / "t.sqlite")
            for src in sources.all_sources().values():
                self.assertTrue(src.commands(), src.cli)
                src.status(conn)  # tables が schema に存在すること
            conn.close()


class BaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.sqlite")
        self.src = Fake()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def ts(self, start, end, final, tail_end=None):
        return self.src.timeseries(self.conn, "s", "D", start, end, fetch=self.src.fetch_range,
                                   final_until=lambda: final, tail_end=tail_end)

    def test_once_fetches_only_first_time(self):
        n = []
        fetch = lambda: n.append(1) or 5
        self.assertEqual(self.src.once(self.conn, "r", False, fetch)["stored"], 5)
        self.assertTrue(self.src.once(self.conn, "r", False, fetch)["cached"])
        self.src.once(self.conn, "r", True, fetch)
        self.assertEqual(len(n), 2)

    def test_timeseries_fetches_only_missing_ranges(self):
        self.ts("2026-01-10", "2026-01-20", "2026-12-31")
        self.ts("2026-01-01", "2026-01-31", "2026-12-31")
        self.assertEqual(self.src.requested, [
            ("2026-01-10", "2026-01-20"), ("2026-01-01", "2026-01-09"), ("2026-01-21", "2026-01-31")])
        self.assertEqual(self.ts("2026-01-01", "2026-01-31", "2026-12-31")["calls"], 0)

    def test_unfinalized_tail_is_refetched_but_throttled(self):
        self.ts("2026-01-01", "2026-01-31", "2026-01-25", tail_end="2026-01-31")
        # 未確定 (01-26 以降) は記録されない。TTL 内なので 2 回目は呼ばない。
        self.assertEqual(self.ts("2026-01-01", "2026-01-31", "2026-01-25", tail_end="2026-01-31")["calls"], 0)
        # 範囲を過去に広げた分は呼ぶ。
        self.ts("2025-12-01", "2026-01-31", "2026-01-25", tail_end="2026-01-31")
        self.assertEqual(self.src.requested[-1], ("2025-12-01", "2025-12-31"))


class EdinetTest(unittest.TestCase):
    def test_docs_end_is_clamped_to_today_and_not_refetched(self):
        from invest.sources import edinet
        src = edinet.Edinet()
        days = []
        src._list = lambda conn, day: days.append(day) or {"results": []}
        with tempfile.TemporaryDirectory() as d:
            conn = db.connect(Path(d) / "t.sqlite", [edinet.SCHEMA])
            today = edinet._today_jst()
            src.fetch_docs(conn, today, "2999-12-31")
            self.assertEqual(days, [today])
            src.fetch_docs(conn, today, "2999-12-31")  # TTL 内なので呼ばない
            self.assertEqual(days, [today])
            self.assertEqual(src.fetch_docs(conn, "2999-01-01", "2999-01-02")["calls"], 0)


if __name__ == "__main__":
    unittest.main()


class YahooTest(unittest.TestCase):
    def test_split_factor(self):
        from invest.sources.yahoo import _split_factor
        self.assertEqual(_split_factor(2910.5, 2902.0), 1)       # 大引けの板寄せとの差
        self.assertEqual(_split_factor(7627.0, 508.1), 15)        # 1:15 分割前の日
        self.assertEqual(_split_factor(55210.0, 11040.0), 5)
        self.assertEqual(_split_factor(100.0, 1000.0), 0.1)       # 併合
        self.assertEqual(_split_factor(100.0, None), 1)

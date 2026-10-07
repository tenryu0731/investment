"""EDINET API v2 (金融庁 電子開示システム) 書類一覧。

仕様: https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/WZEK0110.html
認証: 環境変数 EDINET_API_KEY (ヘッダー Ocp-Apim-Subscription-Key。URL に鍵を残さない)。
"""

import json
import os
from datetime import date, datetime, timedelta, timezone

from .. import periods
from .base import REFRESH, Command, Source, arg

JST = timezone(timedelta(hours=9))

SCHEMA = """
CREATE TABLE IF NOT EXISTS edinet_docs (
    doc_id      TEXT PRIMARY KEY,
    submit_date TEXT NOT NULL,     -- 提出日 (YYYY-MM-DD)。取得した日付指定の値
    submit_at   TEXT,
    edinet_code TEXT,
    sec_code    TEXT,
    filer_name  TEXT,
    doc_type    TEXT,
    form_code   TEXT,
    doc_desc    TEXT,
    period_end  TEXT,
    data        TEXT NOT NULL      -- 元の行を JSON で丸ごと残す
);
CREATE INDEX IF NOT EXISTS edinet_docs_date ON edinet_docs (submit_date);
CREATE INDEX IF NOT EXISTS edinet_docs_sec ON edinet_docs (sec_code);
"""


def _today_jst() -> str:
    return datetime.now(JST).date().isoformat()


class Edinet(Source):
    name = "edinet"
    cli = "edinet"
    help = "EDINET API v2 (EDINET_API_KEY が必要)"
    base_url = "https://api.edinet-fsa.go.jp/api/v2"
    schema = SCHEMA
    tables = ("edinet_docs",)
    env_vars = ("EDINET_API_KEY",)
    min_interval = float(os.environ.get("EDINET_MIN_INTERVAL", "1"))

    def headers(self):
        return {"Ocp-Apim-Subscription-Key": self.env("EDINET_API_KEY")}

    def commands(self):
        return [
            Command("ping", "疎通確認 (1 日分の件数だけ確認。保存しない)",
                    lambda c, a: self.ping(c, a.date),
                    [arg("--date", default=None, help="YYYY-MM-DD (既定: 昨日)")]),
            Command("docs", "書類一覧を日付指定で取得して保存",
                    lambda c, a: self.fetch_docs(c, a.date, a.end or a.date, a.refresh),
                    [arg("date", help="YYYY-MM-DD (提出日)"),
                     arg("--end", default=None, help="YYYY-MM-DD (範囲取得の最終日)"), REFRESH]),
        ]

    def _list(self, conn, day: str) -> dict:
        d = self.get_json(conn, "documents.json", {"date": day, "type": 2})
        status = str(d.get("metadata", {}).get("status", d.get("statusCode", "200")))
        if status != "200":
            raise RuntimeError(f"EDINET {day}: status={status} {d.get('metadata', d).get('message', d)}")
        return d

    def ping(self, conn, day: str | None = None) -> dict:
        day = day or (datetime.now(JST).date() - timedelta(days=1)).isoformat()
        d = self._list(conn, day)
        return {"date": day, "ok": True, "count": d["metadata"]["resultset"]["count"]}

    def fetch_docs(self, conn, start: str, end: str, refresh: bool = False) -> dict:
        def fetch(gap_start: str, gap_end: str) -> int:
            n = 0
            for o in range(periods.to_ord("D", gap_start), periods.to_ord("D", gap_end) + 1):
                day = periods.from_ord("D", o)
                rows = self._list(conn, day).get("results") or []
                conn.executemany(
                    "INSERT OR REPLACE INTO edinet_docs (doc_id, submit_date, submit_at, edinet_code, "
                    "sec_code, filer_name, doc_type, form_code, doc_desc, period_end, data) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [(r["docID"], day, r.get("submitDateTime"), r.get("edinetCode"), r.get("secCode"),
                      r.get("filerName"), r.get("docTypeCode"), r.get("formCode"), r.get("docDescription"),
                      r.get("periodEnd"), json.dumps(r, ensure_ascii=False)) for r in rows],
                )
                n += len(rows)
            return n

        # 当日分は提出が続くため未確定。昨日までを確定として記録する。
        yesterday = (date.fromisoformat(_today_jst()) - timedelta(days=1)).isoformat()
        return self.timeseries(conn, "docs", "D", start, end, fetch=fetch,
                               final_until=lambda: yesterday, tail_end=_today_jst(), refresh=refresh)


SOURCE = Edinet()

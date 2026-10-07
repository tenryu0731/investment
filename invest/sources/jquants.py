"""J-Quants API v2 (x-api-key 認証)。

Free プラン: 直近約 2 年・12 週間遅延・5 リクエスト/分。
"""

import json
import os
import re
from datetime import datetime, timedelta, timezone

from .. import db, http
from .base import REFRESH, Command, Source, arg

JST = timezone(timedelta(hours=9))
# Free プランは 12 週間遅延。取得できる最新日 = 今日 (JST) - この日数。
DELAY_DAYS = int(os.environ.get("JQUANTS_DELAY_DAYS", "84"))
# 取得できる最新日から遡れる日数 (Free は 2 年)。
HISTORY_DAYS = int(os.environ.get("JQUANTS_HISTORY_DAYS", "730"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS jq_master (
    code        TEXT PRIMARY KEY,
    date        TEXT,
    name        TEXT,
    name_en     TEXT,
    s17         TEXT,
    s17_name    TEXT,
    s33         TEXT,
    s33_name    TEXT,
    scale       TEXT,
    market      TEXT,
    market_name TEXT,
    data        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jq_bars (
    code        TEXT NOT NULL,
    date        TEXT NOT NULL,
    o REAL, h REAL, l REAL, c REAL, vo REAL, va REAL,
    adj_factor REAL,
    adj_o REAL, adj_h REAL, adj_l REAL, adj_c REAL, adj_vo REAL,
    data        TEXT NOT NULL,
    PRIMARY KEY (code, date)
);

CREATE TABLE IF NOT EXISTS jq_fins (
    disc_no     TEXT PRIMARY KEY,
    code        TEXT NOT NULL,
    disc_date   TEXT,
    doc_type    TEXT,
    per_type    TEXT,
    per_start   TEXT,
    per_end     TEXT,
    fy_end      TEXT,
    sales REAL, op REAL, odp REAL, np REAL, eps REAL,
    ta REAL, eq REAL, bps REAL, cfo REAL, roe REAL,
    data        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jq_fins_code ON jq_fins (code, disc_date);
"""

_WINDOW_RE = re.compile(r"covers the following dates: (\d{4}-\d{2}-\d{2}) ~ (\d{4}-\d{2}-\d{2})")


def norm_code(code: str) -> str:
    """4 桁の証券コードを J-Quants の 5 桁コードに変換する (7203 -> 72030)。"""
    code = code.strip().upper()
    return code + "0" if len(code) == 4 else code


def _num(v):
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _window() -> tuple[str, str]:
    """契約プランで取得できる日付範囲 (JST 基準)。Free は [今日-12週-2年, 今日-12週]。"""
    last = datetime.now(JST).date() - timedelta(days=DELAY_DAYS)
    first = last - timedelta(days=HISTORY_DAYS)
    return first.isoformat(), last.isoformat()


class _Restart(Exception):
    pass


class JQuants(Source):
    name = "jquants"
    cli = "jq"
    help = "J-Quants API"
    base_url = "https://api.jquants.com/v2"
    schema = SCHEMA
    tables = ("jq_master", "jq_bars", "jq_fins")
    env_vars = ("JQUANTS_API_KEY",)
    # Free プランは 5 リクエスト/分。余裕を見て 13 秒間隔。
    min_interval = float(os.environ.get("JQUANTS_MIN_INTERVAL", "13"))

    def commands(self):
        return [
            Command("master", "上場銘柄一覧", lambda c, a: self.fetch_master(c, a.refresh), [REFRESH]),
            Command("bars", "日足", lambda c, a: self.fetch_bars(c, a.code, a.start, a.end, a.refresh),
                    [arg("code"), arg("--start", help="YYYY-MM-DD"), arg("--end", help="YYYY-MM-DD"),
                     REFRESH]),
            Command("fins", "財務情報", lambda c, a: self.fetch_fins(c, a.code, a.refresh),
                    [arg("code"), REFRESH]),
        ]

    def headers(self):
        return {"x-api-key": self.env("JQUANTS_API_KEY")}

    def _call(self, conn, endpoint: str, params: dict) -> list[dict]:
        """ページングを辿って data を全件返す。"""
        rows: list[dict] = []
        params = dict(params)
        while True:
            data = self.get_json(conn, endpoint, params)
            rows.extend(data.get("data", []))
            key = data.get("pagination_key")
            if not key:
                return rows
            params["pagination_key"] = key

    def fetch_master(self, conn, refresh: bool = False) -> dict:
        """上場銘柄一覧 (全銘柄)。取得済みなら API を呼ばない。"""
        def fetch() -> int:
            rows = self._call(conn, "equities/master", {})
            conn.executemany(
                "INSERT OR REPLACE INTO jq_master (code, date, name, name_en, s17, s17_name, s33,"
                " s33_name, scale, market, market_name, data) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(r.get("Code"), r.get("Date"), r.get("CoName"), r.get("CoNameEn"), r.get("S17"),
                  r.get("S17Nm"), r.get("S33"), r.get("S33Nm"), r.get("ScaleCat"), r.get("Mkt"),
                  r.get("MktNm"), json.dumps(r, ensure_ascii=False)) for r in rows],
            )
            return len(rows)

        return self.once(conn, "master", refresh, fetch)

    def fetch_bars(self, conn, code: str, start: str | None = None, end: str | None = None,
                   refresh: bool = False) -> dict:
        """日足。取得済みの日付区間は API を呼ばない。start / end は YYYY-MM-DD。

        契約範囲内の日足は確定値として扱う。ただし保存済みの最新日より後に株式分割など
        (AdjFactor が 1 以外) があった場合は、過去の調整後価格が変わるため指定範囲を取り直す。
        """
        code = norm_code(code)
        series = f"bars:{code}"
        first, last = _window()
        before = self.calls
        readjusted = False

        def latest() -> str | None:
            return conn.execute("SELECT MAX(date) AS d FROM jq_bars WHERE code = ?", (code,)).fetchone()["d"]

        def fetch(gap_start: str, gap_end: str) -> int:
            nonlocal readjusted
            prev_latest = latest()
            rows = self._call(conn, "equities/bars/daily", {
                "code": code, "from": gap_start.replace("-", ""), "to": gap_end.replace("-", ""),
            })
            conn.executemany(
                "INSERT OR REPLACE INTO jq_bars (code, date, o, h, l, c, vo, va, adj_factor,"
                " adj_o, adj_h, adj_l, adj_c, adj_vo, data)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(r["Code"], r["Date"], _num(r.get("O")), _num(r.get("H")), _num(r.get("L")),
                  _num(r.get("C")), _num(r.get("Vo")), _num(r.get("Va")), _num(r.get("AdjFactor")),
                  _num(r.get("AdjO")), _num(r.get("AdjH")), _num(r.get("AdjL")), _num(r.get("AdjC")),
                  _num(r.get("AdjVo")), json.dumps(r, ensure_ascii=False)) for r in rows],
            )
            # 保存済みの最新日より後に分割等があれば、保存済みの調整後価格は古くなっている。
            if (not readjusted and prev_latest
                    and any(r["Date"] > prev_latest and _num(r.get("AdjFactor")) not in (None, 1.0)
                            for r in rows)):
                readjusted = True
                db.clear_coverage(conn, self.name, series)
                conn.commit()
                raise _Restart
            return len(rows)

        stored = 0
        while True:
            lo, hi = max(start or first, first), min(end or last, last)
            try:
                r = self.timeseries(conn, series, "D", lo, hi, fetch=fetch,
                                    final_until=lambda: last, refresh=refresh)
                stored += r["stored"]
            except _Restart:
                refresh = False
                continue
            except http.ApiError as e:
                # 計算した範囲が契約範囲とずれていたら、エラーメッセージの範囲に合わせてやり直す。
                m = _WINDOW_RE.search(e.body)
                if e.status == 400 and m and (m.group(1), m.group(2)) != (first, last):
                    first, last = m.group(1), m.group(2)
                    continue
                raise
            return {"series": series, "calls": self.calls - before, "stored": stored,
                    "window": [first, last]}

    def fetch_fins(self, conn, code: str, refresh: bool = False) -> dict:
        """決算短信ベースの財務情報 (全期間)。取得済みなら API を呼ばない。"""
        code = norm_code(code)

        def fetch() -> int:
            rows = self._call(conn, "fins/summary", {"code": code})
            conn.executemany(
                "INSERT OR REPLACE INTO jq_fins (disc_no, code, disc_date, doc_type, per_type, per_start,"
                " per_end, fy_end, sales, op, odp, np, eps, ta, eq, bps, cfo, roe, data)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(r["DiscNo"], r["Code"], r.get("DiscDate"), r.get("DocType"), r.get("CurPerType"),
                  r.get("CurPerSt"), r.get("CurPerEn"), r.get("CurFYEn"), _num(r.get("Sales")),
                  _num(r.get("OP")), _num(r.get("OdP")), _num(r.get("NP")), _num(r.get("EPS")),
                  _num(r.get("TA")), _num(r.get("Eq")), _num(r.get("BPS")), _num(r.get("CFO")),
                  _num(r.get("ROE")), json.dumps(r, ensure_ascii=False)) for r in rows],
            )
            return len(rows)

        return self.once(conn, f"fins:{code}", refresh, fetch)


SOURCE = JQuants()

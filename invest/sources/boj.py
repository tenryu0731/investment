"""日本銀行 時系列統計データ検索サイト API (認証不要)。

マニュアル: https://www.stat-search.boj.or.jp/info/api_manual_en.pdf
"""

import os

from .. import http, periods
from .base import REFRESH, Command, Source, arg

SCHEMA = """
CREATE TABLE IF NOT EXISTS boj_meta (
    db          TEXT NOT NULL,
    code        TEXT NOT NULL,
    name        TEXT,
    unit        TEXT,
    frequency   TEXT,
    category    TEXT,
    start_period TEXT,
    end_period  TEXT,
    last_update TEXT,
    notes       TEXT,
    PRIMARY KEY (db, code)
);

CREATE TABLE IF NOT EXISTS boj_obs (
    db          TEXT NOT NULL,
    code        TEXT NOT NULL,
    date        TEXT NOT NULL,
    period      TEXT NOT NULL,
    value       REAL NOT NULL,
    PRIMARY KEY (db, code, date)
);
"""


def unit_for(frequency: str) -> str:
    """メタデータの FREQUENCY をリクエスト期間の単位に変換する。"""
    f = frequency.upper()
    if f.startswith(("DAILY", "WEEKLY", "MONTHLY")):
        return "M"
    if f.startswith("QUARTERLY"):
        return "Q"
    if f.startswith("SEMIANNUAL"):
        return "H"
    if f.startswith("ANNUAL"):
        return "Y"
    raise ValueError(f"unknown BOJ frequency: {frequency!r}")


def _period(unit: str, survey: str) -> str:
    """SURVEY_DATE / START_OF_THE_TIME_SERIES をリクエスト期間に変換する。"""
    return survey[:4] if unit == "Y" else survey[:6]


def _norm_date(unit: str, survey: str) -> str:
    if len(survey) == 8:
        return f"{survey[:4]}-{survey[4:6]}-{survey[6:]}"
    if unit == "M":
        return f"{survey[:4]}-{survey[4:6]}"
    if unit == "Q":
        return f"{survey[:4]}-Q{int(survey[4:6])}"
    if unit == "H":
        return f"{survey[:4]}-H{int(survey[4:6])}"
    return survey[:4]


class Boj(Source):
    name = "boj"
    cli = "boj"
    help = "日本銀行 時系列統計データ"
    base_url = "https://www.stat-search.boj.or.jp/api/v1"
    schema = SCHEMA
    tables = ("boj_meta", "boj_obs")
    # 高頻度アクセス禁止のため 2 秒間隔。
    min_interval = float(os.environ.get("BOJ_MIN_INTERVAL", "2"))

    def commands(self):
        return [
            Command("meta", "DB の系列一覧", lambda c, a: self.fetch_meta(c, a.db, a.refresh),
                    [arg("db"), REFRESH]),
            Command("series", "系列データ",
                    lambda c, a: self.fetch_series(c, a.db, a.code, a.start, a.end, a.refresh),
                    [arg("db"), arg("code"),
                     arg("--start", help="YYYYMM (四半期 YYYYQQ, 半期 YYYYHH, 年 YYYY)"),
                     arg("--end"), REFRESH]),
        ]

    def _call(self, conn, endpoint: str, params: dict) -> dict:
        data = self.get_json(conn, endpoint, params)
        if data.get("STATUS") != 200:
            raise http.ApiError(data.get("STATUS", 0), f"{data.get('MESSAGEID')} {data.get('MESSAGE')}")
        return data

    def fetch_meta(self, conn, dbname: str, refresh: bool = False) -> dict:
        """DB の系列一覧。取得済みなら API を呼ばない。"""
        dbname = dbname.upper()

        def fetch() -> int:
            data = self._call(conn, "getMetadata", {"format": "json", "lang": "en", "db": dbname})
            rows = [
                (dbname, r["SERIES_CODE"], r["NAME_OF_TIME_SERIES"], r["UNIT"], r["FREQUENCY"],
                 r["CATEGORY"], str(r["START_OF_THE_TIME_SERIES"]), str(r["END_OF_THE_TIME_SERIES"]),
                 str(r["LAST_UPDATE"]), r.get("NOTES"))
                for r in data["RESULTSET"] if r["SERIES_CODE"]
            ]
            conn.executemany(
                "INSERT OR REPLACE INTO boj_meta (db, code, name, unit, frequency, category,"
                " start_period, end_period, last_update, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            return len(rows)

        return self.once(conn, f"meta:{dbname}", refresh, fetch)

    def fetch_series(self, conn, dbname: str, code: str, start: str | None = None,
                     end: str | None = None, refresh: bool = False) -> dict:
        """系列の時系列データ。start / end は日銀のリクエスト形式 (日次・月次は YYYYMM など)。

        最新の観測値を含む期間は値の追加・更新がありうるため未確定とし、
        その前の期間までを取得済みとする。
        """
        dbname = dbname.upper()
        before = self.calls
        self.fetch_meta(conn, dbname)
        meta = conn.execute("SELECT * FROM boj_meta WHERE db = ? AND code = ?", (dbname, code)).fetchone()
        if meta is None:
            raise KeyError(f"{code} not found in BOJ DB {dbname}")
        unit = unit_for(meta["frequency"])
        series = f"{dbname}:{code}"

        start = start or _period(unit, meta["start_period"])
        if not start:
            raise ValueError(f"{series}: 開始期間が不明です。--start を指定してください")
        cur = periods.current(unit)
        end = min(end or cur, cur, key=lambda p: periods.to_ord(unit, p))

        def fetch(gap_start: str, gap_end: str) -> int:
            stored, position = 0, None
            while True:
                data = self._call(conn, "getDataCode", {
                    "format": "json", "lang": "en", "db": dbname, "code": code,
                    "startDate": gap_start, "endDate": gap_end, "startPosition": position,
                })
                for rs in data["RESULTSET"]:
                    vals = rs["VALUES"]
                    rows = [
                        (dbname, code, _norm_date(unit, str(d)), _period(unit, str(d)), v)
                        for d, v in zip(vals["SURVEY_DATES"], vals["VALUES"]) if v is not None
                    ]
                    conn.executemany(
                        "INSERT OR REPLACE INTO boj_obs (db, code, date, period, value)"
                        " VALUES (?, ?, ?, ?, ?)", rows,
                    )
                    stored += len(rows)
                position = data.get("NEXTPOSITION")
                if not position:
                    return stored

        def final_until() -> str | None:
            latest = conn.execute(
                "SELECT MAX(period) AS p FROM boj_obs WHERE db = ? AND code = ?", (dbname, code)
            ).fetchone()["p"]
            candidates = [p for p in (latest, _period(unit, meta["end_period"])) if p]
            if not candidates:
                return None
            return periods.from_ord(unit, max(periods.to_ord(unit, p) for p in candidates) - 1)

        r = self.timeseries(conn, series, unit, start, end, fetch=fetch, final_until=final_until,
                            tail_end=cur, refresh=refresh)
        r["calls"] = self.calls - before
        return r


SOURCE = Boj()

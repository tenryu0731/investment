"""SQLite キャッシュ。

再取得を避ける仕組みは 2 種類:
    coverage  : 時系列データの「取得済み・確定済み」区間。未取得の区間だけ API を呼ぶ。
    fetch_log : 一括で取る資源 (銘柄一覧・メタデータ・財務) の取得記録。
                記録があれば --refresh を指定しない限り再取得しない。
API の生レスポンスも raw_responses に gzip で保存し、加工し直すときに再取得不要にする。
"""

import gzip
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import config, periods

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_responses (
    id          INTEGER PRIMARY KEY,
    source      TEXT NOT NULL,
    endpoint    TEXT NOT NULL,
    params      TEXT NOT NULL,
    fetched_at  TEXT NOT NULL,
    body        BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS raw_responses_key ON raw_responses (source, endpoint, params);

CREATE TABLE IF NOT EXISTS coverage (
    source      TEXT NOT NULL,
    series      TEXT NOT NULL,
    unit        TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end  TEXT NOT NULL,
    PRIMARY KEY (source, series, period_start)
);

CREATE TABLE IF NOT EXISTS fetch_log (
    source      TEXT NOT NULL,
    resource    TEXT NOT NULL,
    fetched_at  TEXT NOT NULL,
    PRIMARY KEY (source, resource)
);

CREATE TABLE IF NOT EXISTS rate_state (
    source      TEXT PRIMARY KEY,
    last_at     REAL NOT NULL
);

-- J-Quants
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

-- 日銀
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


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path | None = None) -> sqlite3.Connection:
    p = Path(path or config.DB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def save_raw(conn, source: str, endpoint: str, params: dict, body: bytes) -> None:
    conn.execute(
        "INSERT INTO raw_responses (source, endpoint, params, fetched_at, body) VALUES (?, ?, ?, ?, ?)",
        (source, endpoint, json.dumps(params, sort_keys=True), now_iso(), gzip.compress(body)),
    )


# --- fetch_log -------------------------------------------------------------

def fetched_at(conn, source: str, resource: str) -> str | None:
    row = conn.execute(
        "SELECT fetched_at FROM fetch_log WHERE source = ? AND resource = ?", (source, resource)
    ).fetchone()
    return row["fetched_at"] if row else None


def fetched_within(conn, source: str, resource: str, hours: float) -> bool:
    at = fetched_at(conn, source, resource)
    if not at:
        return False
    return (datetime.now(timezone.utc) - datetime.fromisoformat(at)).total_seconds() < hours * 3600


def mark_fetched(conn, source: str, resource: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO fetch_log (source, resource, fetched_at) VALUES (?, ?, ?)",
        (source, resource, now_iso()),
    )


# --- coverage --------------------------------------------------------------

def covered(conn, source: str, series: str, unit: str) -> list[tuple[int, int]]:
    rows = conn.execute(
        "SELECT period_start, period_end FROM coverage WHERE source = ? AND series = ? AND unit = ?",
        (source, series, unit),
    ).fetchall()
    return [(periods.to_ord(unit, r["period_start"]), periods.to_ord(unit, r["period_end"])) for r in rows]


def gaps(conn, source: str, series: str, unit: str, start: str, end: str) -> list[tuple[str, str]]:
    """[start, end] のうち未取得の区間 (期間文字列) を返す。"""
    s, e = periods.to_ord(unit, start), periods.to_ord(unit, end)
    if s > e:
        return []
    return [
        (periods.from_ord(unit, a), periods.from_ord(unit, b))
        for a, b in periods.missing(covered(conn, source, series, unit), s, e)
    ]


def add_coverage(conn, source: str, series: str, unit: str, start: str, end: str) -> None:
    s, e = periods.to_ord(unit, start), periods.to_ord(unit, end)
    if s > e:
        return
    merged = periods.merge(covered(conn, source, series, unit) + [(s, e)])
    conn.execute(
        "DELETE FROM coverage WHERE source = ? AND series = ? AND unit = ?", (source, series, unit)
    )
    conn.executemany(
        "INSERT INTO coverage (source, series, unit, period_start, period_end) VALUES (?, ?, ?, ?, ?)",
        [(source, series, unit, periods.from_ord(unit, a), periods.from_ord(unit, b)) for a, b in merged],
    )


def clear_coverage(conn, source: str, series: str) -> None:
    conn.execute("DELETE FROM coverage WHERE source = ? AND series = ?", (source, series))

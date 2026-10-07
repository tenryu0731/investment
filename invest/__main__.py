"""CLI.

例:
    python -m invest boj meta FM08
    python -m invest boj series FM08 FXERD01 --start 202401
    python -m invest jq master
    python -m invest jq bars 7203 --start 2026-01-01
    python -m invest jq fins 7203
    python -m invest status
"""

import argparse
import json

from . import db
from .sources import boj, jquants


def main() -> None:
    p = argparse.ArgumentParser(prog="invest")
    sub = p.add_subparsers(dest="source", required=True)

    pb = sub.add_parser("boj", help="日本銀行 時系列統計データ")
    bs = pb.add_subparsers(dest="cmd", required=True)
    m = bs.add_parser("meta", help="DB の系列一覧")
    m.add_argument("db")
    m.add_argument("--refresh", action="store_true")
    s = bs.add_parser("series", help="系列データ")
    s.add_argument("db")
    s.add_argument("code")
    s.add_argument("--start", help="YYYYMM (四半期 YYYYQQ, 半期 YYYYHH, 年 YYYY)")
    s.add_argument("--end")
    s.add_argument("--refresh", action="store_true")

    pj = sub.add_parser("jq", help="J-Quants")
    js = pj.add_subparsers(dest="cmd", required=True)
    m = js.add_parser("master", help="上場銘柄一覧")
    m.add_argument("--refresh", action="store_true")
    b = js.add_parser("bars", help="日足")
    b.add_argument("code")
    b.add_argument("--start", help="YYYY-MM-DD")
    b.add_argument("--end", help="YYYY-MM-DD")
    b.add_argument("--refresh", action="store_true")
    f = js.add_parser("fins", help="財務情報")
    f.add_argument("code")
    f.add_argument("--refresh", action="store_true")

    sub.add_parser("status", help="キャッシュの状態")

    a = p.parse_args()
    conn = db.connect()

    if a.source == "boj" and a.cmd == "meta":
        r = {"calls": boj.fetch_meta(conn, a.db, a.refresh)}
    elif a.source == "boj":
        r = boj.fetch_series(conn, a.db, a.code, a.start, a.end, a.refresh)
    elif a.source == "jq" and a.cmd == "master":
        r = jquants.fetch_master(conn, a.refresh)
    elif a.source == "jq" and a.cmd == "bars":
        r = jquants.fetch_bars(conn, a.code, a.start, a.end, a.refresh)
    elif a.source == "jq":
        r = jquants.fetch_fins(conn, a.code, a.refresh)
    else:
        r = {
            "coverage": [dict(x) for x in conn.execute(
                "SELECT source, series, period_start, period_end FROM coverage ORDER BY source, series")],
            "fetch_log": [dict(x) for x in conn.execute(
                "SELECT * FROM fetch_log ORDER BY source, resource")],
            "rows": {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                     for t in ("jq_master", "jq_bars", "jq_fins", "boj_meta", "boj_obs")},
        }
    print(json.dumps(r, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

"""J-Quants API v2 (x-api-key 認証)。

Free プラン: 直近約 2 年・12 週間遅延・5 リクエスト/分。
"""

import json
import re
from datetime import datetime, timedelta, timezone

from .. import config, db, http

SOURCE = "jquants"
JST = timezone(timedelta(hours=9))


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


def _call(conn, endpoint: str, params: dict) -> list[dict]:
    if not config.JQUANTS_API_KEY:
        raise RuntimeError("環境変数 JQUANTS_API_KEY が設定されていません")
    rows: list[dict] = []
    params = dict(params)
    while True:
        data, body = http.get_json(
            conn, SOURCE, f"{config.JQUANTS_BASE}/{endpoint}", params,
            headers={"x-api-key": config.JQUANTS_API_KEY},
            min_interval=config.JQUANTS_MIN_INTERVAL,
        )
        db.save_raw(conn, SOURCE, endpoint, params, body)
        rows.extend(data.get("data", []))
        key = data.get("pagination_key")
        if not key:
            return rows
        params["pagination_key"] = key


def fetch_master(conn, refresh: bool = False) -> dict:
    """上場銘柄一覧 (全銘柄)。取得済みなら API を呼ばない。"""
    if not refresh and db.fetched_at(conn, SOURCE, "master"):
        return {"calls": 0, "stored": 0}
    rows = _call(conn, "equities/master", {})
    conn.executemany(
        "INSERT OR REPLACE INTO jq_master (code, date, name, name_en, s17, s17_name, s33, s33_name,"
        " scale, market, market_name, data) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(r.get("Code"), r.get("Date"), r.get("CoName"), r.get("CoNameEn"), r.get("S17"),
          r.get("S17Nm"), r.get("S33"), r.get("S33Nm"), r.get("ScaleCat"), r.get("Mkt"),
          r.get("MktNm"), json.dumps(r, ensure_ascii=False)) for r in rows],
    )
    db.mark_fetched(conn, SOURCE, "master")
    conn.commit()
    return {"calls": 1, "stored": len(rows)}


def _window() -> tuple[str, str]:
    """契約プランで取得できる日付範囲 (JST 基準)。Free は [今日-12週-2年, 今日-12週]。"""
    today = datetime.now(JST).date()
    last = today - timedelta(days=config.JQUANTS_DELAY_DAYS)
    first = last - timedelta(days=config.JQUANTS_HISTORY_DAYS)
    return first.isoformat(), last.isoformat()


_WINDOW_RE = re.compile(r"covers the following dates: (\d{4}-\d{2}-\d{2}) ~ (\d{4}-\d{2}-\d{2})")


def _store_bars(conn, rows: list[dict]) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO jq_bars (code, date, o, h, l, c, vo, va, adj_factor,"
        " adj_o, adj_h, adj_l, adj_c, adj_vo, data)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(r["Code"], r["Date"], _num(r.get("O")), _num(r.get("H")), _num(r.get("L")),
          _num(r.get("C")), _num(r.get("Vo")), _num(r.get("Va")), _num(r.get("AdjFactor")),
          _num(r.get("AdjO")), _num(r.get("AdjH")), _num(r.get("AdjL")), _num(r.get("AdjC")),
          _num(r.get("AdjVo")), json.dumps(r, ensure_ascii=False)) for r in rows],
    )


def fetch_bars(conn, code: str, start: str | None = None, end: str | None = None,
               refresh: bool = False) -> dict:
    """日足。取得済みの日付区間は API を呼ばない。start / end は YYYY-MM-DD。

    契約範囲内の日足は確定値として扱う。ただし保存済みの最新日より後に株式分割など
    (AdjFactor が 1 以外) があった場合は、過去の調整後価格が変わるため指定範囲を取り直す。
    """
    code = norm_code(code)
    series = f"bars:{code}"
    first, last = _window()
    if refresh:
        db.clear_coverage(conn, SOURCE, series)

    calls = stored = 0
    readjusted = False
    while True:
        lo, hi = max(start or first, first), min(end or last, last)
        try:
            for gap_start, gap_end in db.gaps(conn, SOURCE, series, "D", lo, hi):
                prev_latest = conn.execute(
                    "SELECT MAX(date) AS d FROM jq_bars WHERE code = ?", (code,)).fetchone()["d"]
                rows = _call(conn, "equities/bars/daily", {
                    "code": code, "from": gap_start.replace("-", ""), "to": gap_end.replace("-", ""),
                })
                calls += 1
                _store_bars(conn, rows)
                stored += len(rows)
                db.add_coverage(conn, SOURCE, series, "D", gap_start, gap_end)
                conn.commit()
                # 保存済みの最新日より後に分割等があれば、保存済みの調整後価格は古くなっている。
                if (not readjusted and prev_latest
                        and any(r["Date"] > prev_latest and _num(r.get("AdjFactor")) not in (None, 1.0)
                                for r in rows)):
                    readjusted = True
                    db.clear_coverage(conn, SOURCE, series)
                    conn.commit()
                    raise _Restart
        except _Restart:
            continue
        except http.ApiError as e:
            # 計算した範囲が契約範囲とずれていたら、エラーメッセージの範囲に合わせて 1 度だけやり直す。
            m = _WINDOW_RE.search(e.body)
            if e.status == 400 and m and (m.group(1), m.group(2)) != (first, last):
                first, last = m.group(1), m.group(2)
                continue
            raise
        return {"series": series, "calls": calls, "stored": stored, "window": [first, last]}


class _Restart(Exception):
    pass


def fetch_fins(conn, code: str, refresh: bool = False) -> dict:
    """決算短信ベースの財務情報 (全期間)。取得済みなら API を呼ばない。"""
    code = norm_code(code)
    resource = f"fins:{code}"
    if not refresh and db.fetched_at(conn, SOURCE, resource):
        return {"calls": 0, "stored": 0}
    rows = _call(conn, "fins/summary", {"code": code})
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
    db.mark_fetched(conn, SOURCE, resource)
    conn.commit()
    return {"calls": 1, "stored": len(rows)}

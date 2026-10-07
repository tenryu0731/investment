"""日本銀行 時系列統計データ検索サイト API (認証不要)。

マニュアル: https://www.stat-search.boj.or.jp/info/api_manual_en.pdf
"""

from .. import config, db, http, periods

SOURCE = "boj"


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


def _call(conn, endpoint: str, params: dict) -> dict:
    data, body = http.get_json(conn, SOURCE, f"{config.BOJ_BASE}/{endpoint}", params,
                               min_interval=config.BOJ_MIN_INTERVAL)
    if data.get("STATUS") != 200:
        raise http.ApiError(data.get("STATUS", 0), f"{data.get('MESSAGEID')} {data.get('MESSAGE')}")
    db.save_raw(conn, SOURCE, endpoint, params, body)
    return data


def fetch_meta(conn, dbname: str, refresh: bool = False) -> int:
    """DB の系列一覧を取得する。取得済みなら API を呼ばない。戻り値は API 呼び出し回数。"""
    dbname = dbname.upper()
    resource = f"meta:{dbname}"
    if not refresh and db.fetched_at(conn, SOURCE, resource):
        return 0
    data = _call(conn, "getMetadata", {"format": "json", "lang": "en", "db": dbname})
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
    db.mark_fetched(conn, SOURCE, resource)
    conn.commit()
    return 1


def _final_until(conn, dbname: str, code: str, unit: str, meta) -> int | None:
    """確定済みとみなせる最後の期間 (序数)。

    最新の観測値を含む期間は値の追加・更新がありうるため未確定とし、その前の期間までを確定とする。
    """
    latest = conn.execute(
        "SELECT MAX(period) AS p FROM boj_obs WHERE db = ? AND code = ?", (dbname, code)
    ).fetchone()["p"]
    candidates = [p for p in (latest, _period(unit, meta["end_period"])) if p]
    if not candidates:
        return None
    return max(periods.to_ord(unit, p) for p in candidates) - 1


def fetch_series(conn, dbname: str, code: str, start: str | None = None,
                 end: str | None = None, refresh: bool = False) -> dict:
    """系列の時系列データを取得する。取得済み・確定済みの区間は API を呼ばない。

    start / end は日銀のリクエスト形式 (日次・月次は YYYYMM、四半期は YYYYQQ など)。
    """
    dbname = dbname.upper()
    calls = fetch_meta(conn, dbname)
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
    if refresh:
        db.clear_coverage(conn, SOURCE, series)

    stored = 0
    tail = f"tail:{series}"
    for gap_start, gap_end in db.gaps(conn, SOURCE, series, unit, start, end):
        # 区間全体が未確定域 (最新期間) なら、TAIL_TTL_HOURS 以内に確認済みのとき呼ばない。
        final_until = _final_until(conn, dbname, code, unit, meta)
        is_tail = gap_end == cur
        if (is_tail and not refresh and final_until is not None
                and periods.to_ord(unit, gap_start) > final_until
                and db.fetched_within(conn, SOURCE, tail, config.TAIL_TTL_HOURS)):
            continue
        position = None
        while True:
            data = _call(conn, "getDataCode", {
                "format": "json", "lang": "en", "db": dbname, "code": code,
                "startDate": gap_start, "endDate": gap_end, "startPosition": position,
            })
            calls += 1
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
                break

        final_until = _final_until(conn, dbname, code, unit, meta)
        if final_until is not None:
            covered_end = min(periods.to_ord(unit, gap_end), final_until)
            db.add_coverage(conn, SOURCE, series, unit, gap_start, periods.from_ord(unit, covered_end))
        if is_tail:
            db.mark_fetched(conn, SOURCE, tail)
        conn.commit()

    return {"series": series, "calls": calls, "stored": stored}

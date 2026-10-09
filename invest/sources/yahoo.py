"""Yahoo Finance チャート API (認証不要) の日中足。

日本株は <コード>.T。取得できるのは 1 分足が直近 30 日、5 分足が直近 60 日まで。
それより前は取れないため、定期的に実行して SQLite に溜めていく。
価格は分割未調整の生値。

    python -m invest yf bars 7203                        # 1 分足を取れるだけ取る
    python -m invest yf export 7203 6758 --out data/daytrade.json   # デイトレ練習用 JSON
"""

import json
import os
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from .base import REFRESH, Command, Source, arg

JST = timezone(timedelta(hours=9))

# 足の間隔 -> (遡れる日数, 1 リクエストの日数)。上限ちょうどは拒否されうるので 1 日余裕を見る。
# 日中足は寄り付き直後 (9:00-9:04) と大引けの板寄せが欠けるため、寄り値・引け値は日足 (1d) で補う。
INTERVALS = {"1m": (29, 7), "5m": (59, 30), "1d": (3650, 3650)}

# この時刻 (JST) を過ぎたら当日分を確定とみなす。大引けは 15:30。
CLOSE_FINAL = time(16, 0)

SCHEMA = """
CREATE TABLE IF NOT EXISTS yf_bars (
    code        TEXT NOT NULL,
    interval    TEXT NOT NULL,
    ts          INTEGER NOT NULL,   -- 足の開始時刻 (UNIX 秒)
    date        TEXT NOT NULL,      -- JST の日付 YYYY-MM-DD
    time        TEXT NOT NULL,      -- JST の時刻 HH:MM
    o           REAL NOT NULL,
    h           REAL NOT NULL,
    l           REAL NOT NULL,
    c           REAL NOT NULL,
    v           INTEGER,
    PRIMARY KEY (code, interval, ts)
);

CREATE TABLE IF NOT EXISTS yf_symbols (
    code        TEXT PRIMARY KEY,
    name        TEXT,
    currency    TEXT
);
"""


def symbol(code: str) -> str:
    """4 桁の証券コードに東証の接尾辞を付ける (7203 -> 7203.T)。"""
    code = code.strip().upper()
    return code if "." in code else code + ".T"


def _jst_midnight(d: str) -> int:
    return int(datetime.combine(date.fromisoformat(d), time(0), JST).timestamp())


def _last_final_day(now: datetime | None = None) -> str:
    """値が変わらないとみなせる最後の日 (JST)。引け後なら当日、それ以前は前日。"""
    now = (now or datetime.now(JST)).astimezone(JST)
    d = now.date() if now.time() >= CLOSE_FINAL else now.date() - timedelta(days=1)
    return d.isoformat()


def parse_chart(data: dict, period1: int, period2: int) -> tuple[dict, list[tuple]]:
    """chart API のレスポンスを (meta, [(ts, date, time, o, h, l, c, v)]) にする。

    値のない足 (昼休み・板寄せ中) と、指定期間外の足 (最新値として付く当日の足) は除く。
    """
    chart = data["chart"]
    if chart.get("error"):
        raise RuntimeError(f"Yahoo: {chart['error']}")
    r = chart["result"][0]
    q = r["indicators"]["quote"][0]
    rows = []
    for i, ts in enumerate(r.get("timestamp") or []):
        o, h, l, c, v = (q[k][i] for k in ("open", "high", "low", "close", "volume"))
        if None in (o, h, l, c) or not period1 <= ts < period2:
            continue
        t = datetime.fromtimestamp(ts, JST)
        rows.append((ts, t.date().isoformat(), t.strftime("%H:%M"), o, h, l, c, v))
    return r["meta"], rows


class Yahoo(Source):
    name = "yahoo"
    cli = "yf"
    help = "Yahoo Finance 日中足 (1 分足は直近 30 日、5 分足は直近 60 日)"
    base_url = "https://query1.finance.yahoo.com/v8/finance/chart"
    schema = SCHEMA
    tables = ("yf_bars", "yf_symbols")
    min_interval = float(os.environ.get("YAHOO_MIN_INTERVAL", "2"))

    def headers(self):
        # 既定の User-Agent だと拒否されることがある。
        return {"User-Agent": "Mozilla/5.0"}

    def commands(self):
        interval = arg("--interval", default="1m", choices=sorted(INTERVALS))
        return [
            Command("bars", "日中足を取れる範囲で取得 (取得済みの日は呼ばない)",
                    lambda c, a: self.fetch_bars(c, a.code, a.interval, a.start, a.refresh),
                    [arg("code"), interval, arg("--start", help="YYYY-MM-DD (既定: 取れる最古の日)"),
                     REFRESH]),
            Command("export", "デイトレ練習用 JSON を書き出す (取得もする)",
                    lambda c, a: self.export(c, a.codes, a.intervals, a.out, a.fetch),
                    [arg("codes", nargs="+"),
                     arg("--intervals", nargs="+", default=["1m", "5m"], choices=["1m", "5m"],
                         help="使う足。先に書いたものを優先し、ない日だけ後の足で補う (既定: 1m 5m)"),
                     arg("--out", default="data/daytrade.json", help="出力先 (data/ はコミットされない)"),
                     arg("--no-fetch", dest="fetch", action="store_false", help="取得せず DB の分だけ使う")]),
        ]

    def fetch_bars(self, conn, code: str, interval: str = "1m", start: str | None = None,
                   refresh: bool = False, now: datetime | None = None) -> dict:
        """日中足を [start, 今日] のうち未取得の日だけ取得する。"""
        sym = symbol(code)
        back, chunk = INTERVALS[interval]
        today = (now or datetime.now(JST)).astimezone(JST).date()
        oldest = (today - timedelta(days=back)).isoformat()
        lo = max(start or oldest, oldest)

        def fetch(gap_start: str, gap_end: str) -> int:
            stored = 0
            d = date.fromisoformat(gap_start)
            last = date.fromisoformat(gap_end)
            while d <= last:
                e = min(d + timedelta(days=chunk - 1), last)
                p1, p2 = _jst_midnight(d.isoformat()), _jst_midnight((e + timedelta(days=1)).isoformat())
                meta, rows = parse_chart(
                    self.get_json(conn, sym, {"interval": interval, "period1": p1, "period2": p2}), p1, p2)
                conn.execute("INSERT OR REPLACE INTO yf_symbols (code, name, currency) VALUES (?, ?, ?)",
                             (sym, meta.get("longName") or meta.get("shortName"), meta.get("currency")))
                conn.executemany(
                    "INSERT OR REPLACE INTO yf_bars (code, interval, ts, date, time, o, h, l, c, v)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [(sym, interval, *r) for r in rows],
                )
                stored += len(rows)
                d = e + timedelta(days=1)
            return stored

        return self.timeseries(conn, f"{interval}:{sym}", "D", lo, today.isoformat(), fetch=fetch,
                               final_until=lambda: _last_final_day(now), refresh=refresh)

    def sessions(self, conn, code: str, interval: str = "1m") -> list[dict]:
        """DB の日中足を 1 日ずつにまとめる。

        open / close は日足の寄り値・引け値 (日足がなければ None)。
        prev_close は前営業日の引け値 (日足がなければ前日最後の足の終値)。
        """
        sym = symbol(code)
        name = conn.execute("SELECT name FROM yf_symbols WHERE code = ?", (sym,)).fetchone()
        daily = {r["date"]: r for r in conn.execute(
            "SELECT date, o, c FROM yf_bars WHERE code = ? AND interval = '1d' ORDER BY ts", (sym,))}
        out = []
        days: dict[str, list] = {}
        for r in conn.execute("SELECT date, time, o, h, l, c, v FROM yf_bars"
                              " WHERE code = ? AND interval = ? ORDER BY ts", (sym, interval)):
            if r["time"] >= "15:30":  # 当日分に付く最新値。引け値は日足から入れる。
                continue
            days.setdefault(r["date"], []).append([r["time"], r["o"], r["h"], r["l"], r["c"], r["v"] or 0])
        prev_dates = sorted(daily)
        prev_bar_close = None
        for d, bars in days.items():
            earlier = [x for x in prev_dates if x < d]
            prev_close = daily[earlier[-1]]["c"] if earlier else prev_bar_close
            day = daily.get(d)
            out.append({"code": sym.removesuffix(".T"), "name": name["name"] if name else None,
                        "date": d, "interval": interval, "prev_close": prev_close,
                        "open": day["o"] if day else None, "close": day["c"] if day else None,
                        "bars": bars})
            prev_bar_close = bars[-1][4]
        return out

    def export(self, conn, codes: list[str], intervals: list[str] = ("1m", "5m"),
               out: str = "data/daytrade.json", fetch: bool = True) -> dict:
        """codes の日中足を 1 日ずつ書き出す。1 分足のない古い日は 5 分足で補う。

        取得に失敗した銘柄 (コード違い・上場廃止など) は飛ばして errors に入れる。
        """
        fetched, errors = [], {}
        if fetch:
            for c in codes:
                try:
                    for iv in intervals:
                        fetched.append(self.fetch_bars(conn, c, iv))
                    first = conn.execute("SELECT MIN(date) AS d FROM yf_bars WHERE code = ? AND interval != '1d'",
                                         (symbol(c),)).fetchone()["d"]
                    if first:  # 前日終値のため 1 週間前から
                        start = (date.fromisoformat(first) - timedelta(days=7)).isoformat()
                        fetched.append(self.fetch_bars(conn, c, "1d", start))
                except Exception as e:  # noqa: BLE001 - 1 銘柄の失敗で全体を止めない
                    conn.rollback()
                    errors[c] = str(e)[:200]
        final = _last_final_day()
        sessions = []
        for c in codes:
            seen: set[str] = set()
            for iv in intervals:
                # 場中の当日分は途中で切れているので入れない。
                for s in self.sessions(conn, c, iv):
                    if s["date"] <= final and s["date"] not in seen:
                        seen.add(s["date"])
                        sessions.append(s)
        sessions.sort(key=lambda s: (s["code"], s["date"]))
        p = Path(out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"source": "Yahoo Finance", "sessions": sessions},
                                ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        return {"out": str(p), "sessions": len(sessions), "errors": errors,
                "stored": sum(f["stored"] for f in fetched)}


SOURCE = Yahoo()

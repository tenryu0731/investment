"""期間文字列 <-> 整数序数 の変換。

キャッシュの取得済み範囲を整数区間で計算するために使う。

単位:
    D: ISO 日付 "YYYY-MM-DD"          (J-Quants)
    M: "YYYYMM"                       (日銀の日次・週次・月次。リクエストは月単位)
    Q: "YYYYQQ"  QQ=01..04            (日銀の四半期)
    H: "YYYYHH"  HH=01..02            (日銀の半期)
    Y: "YYYY"                         (日銀の暦年・年度)
"""

from datetime import date

UNITS = ("D", "M", "Q", "H", "Y")
_PER_YEAR = {"M": 12, "Q": 4, "H": 2}


def to_ord(unit: str, s: str) -> int:
    if unit == "D":
        return date.fromisoformat(s).toordinal()
    if unit == "Y":
        return int(s[:4])
    n = _PER_YEAR[unit]
    y, k = int(s[:4]), int(s[4:6])
    if not 1 <= k <= n:
        raise ValueError(f"invalid {unit} period: {s}")
    return y * n + (k - 1)


def from_ord(unit: str, o: int) -> str:
    if unit == "D":
        return date.fromordinal(o).isoformat()
    if unit == "Y":
        return f"{o:04d}"
    n = _PER_YEAR[unit]
    return f"{o // n:04d}{o % n + 1:02d}"


def current(unit: str, today: date | None = None) -> str:
    """today を含む期間。"""
    t = today or date.today()
    if unit == "D":
        return t.isoformat()
    if unit == "M":
        return f"{t.year:04d}{t.month:02d}"
    if unit == "Q":
        return f"{t.year:04d}{(t.month - 1) // 3 + 1:02d}"
    if unit == "H":
        return f"{t.year:04d}{(t.month - 1) // 6 + 1:02d}"
    return f"{t.year:04d}"


def missing(covered: list[tuple[int, int]], start: int, end: int) -> list[tuple[int, int]]:
    """[start, end] のうち covered (閉区間のリスト) に含まれない区間を返す。"""
    gaps = []
    cur = start
    for a, b in sorted(covered):
        if b < cur:
            continue
        if a > end:
            break
        if a > cur:
            gaps.append((cur, a - 1))
        cur = max(cur, b + 1)
        if cur > end:
            break
    if cur <= end:
        gaps.append((cur, end))
    return gaps


def merge(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """重なる・隣接する閉区間を結合する。"""
    out: list[tuple[int, int]] = []
    for a, b in sorted(intervals):
        if out and a <= out[-1][1] + 1:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out

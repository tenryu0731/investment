"""daytrade.html にデータを埋め込んだ 1 ファイル版を作る。

    python -m invest yf export 7203 6758 --out data/daytrade.json
    python game/build.py data/daytrade.json data/daytrade.html

出力は取得データを含むため data/ (コミットされない) に置く。
データは整数の差分にして gzip し、base64 で埋め込む (ページの loadData が戻す)。
"""

import base64
import gzip
import json
import sys
from pathlib import Path

TEMPLATE = Path(__file__).with_name("daytrade.html")


def _p(x: float) -> int:
    """価格を 0.1 円単位の整数にする。"""
    return round(x * 10)


def encode_session(s: dict) -> dict:
    bars = s["bars"]
    vu = 100 if all(b[5] % 100 == 0 for b in bars) else 1
    flat, prev_m, pc = [], 9 * 60, _p(bars[0][1])
    base = pc
    for t, o, h, l, c, v in bars:
        m = int(t[:2]) * 60 + int(t[3:5])
        o, h, l, c = _p(o), _p(h), _p(l), _p(c)
        flat += [m - prev_m, o - pc, h - max(o, c), min(o, c) - l, c - o, v // vu]
        prev_m, pc = m, c
    return {"code": s["code"], "name": s["name"], "date": s["date"], "iv": s["interval"],
            "pc": s["prev_close"], "op": s["open"], "cl": s["close"], "b0": base, "vu": vu, "b": flat}


def build(data_path: str, out_path: str) -> dict:
    data = json.loads(Path(data_path).read_text(encoding="utf-8"))
    packed = {"source": data.get("source"), "sessions": [encode_session(s) for s in data["sessions"]]}
    z = gzip.compress(json.dumps(packed, ensure_ascii=False, separators=(",", ":")).encode(), 9)
    payload = json.dumps({"z": base64.b64encode(z).decode()})
    html = TEMPLATE.read_text(encoding="utf-8").replace("__DATA__", payload, 1)
    Path(out_path).write_text(html, encoding="utf-8")
    return {"sessions": len(packed["sessions"]), "bytes": len(html.encode())}


if __name__ == "__main__":
    print(build(*(sys.argv[1:3] if len(sys.argv) >= 3 else ("data/daytrade.json", "data/daytrade.html"))))

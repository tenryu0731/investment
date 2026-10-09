"""daytrade.html にデータを埋め込んだ 1 ファイル版を作る。

    python -m invest yf export 7203 6758 --out data/daytrade.json
    python game/build.py data/daytrade.json data/daytrade.html

出力は取得データを含むため data/ (コミットされない) に置く。
"""

import sys
from pathlib import Path

TEMPLATE = Path(__file__).with_name("daytrade.html")


def build(data_path: str, out_path: str) -> None:
    data = Path(data_path).read_text(encoding="utf-8").replace("</", "<\\/")
    html = TEMPLATE.read_text(encoding="utf-8").replace("__DATA__", data, 1)
    Path(out_path).write_text(html, encoding="utf-8")


if __name__ == "__main__":
    build(*(sys.argv[1:3] if len(sys.argv) >= 3 else ("data/daytrade.json", "data/daytrade.html")))

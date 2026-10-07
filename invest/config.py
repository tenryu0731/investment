"""全データ源で共通の設定。データ源固有の設定は各 sources/<名前>.py に置く。"""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DB_PATH = Path(os.environ.get("INVEST_DB", ROOT / "data" / "invest.sqlite"))

# 未確定の最新期間 (当月など) を再確認する最小間隔 (時間)。
TAIL_TTL_HOURS = float(os.environ.get("INVEST_TAIL_TTL_HOURS", "12"))

USER_AGENT = "investment-data-fetcher/0.1"

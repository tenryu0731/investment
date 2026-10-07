"""設定値。すべて環境変数で上書きできる。"""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DB_PATH = Path(os.environ.get("INVEST_DB", ROOT / "data" / "invest.sqlite"))

JQUANTS_API_KEY = os.environ.get("JQUANTS_API_KEY")
JQUANTS_BASE = "https://api.jquants.com/v2"
# Free プランは 5 リクエスト/分。余裕を見て 13 秒間隔。
JQUANTS_MIN_INTERVAL = float(os.environ.get("JQUANTS_MIN_INTERVAL", "13"))
# Free プランは 12 週間遅延。取得できる最新日 = 今日 (JST) - この日数。
JQUANTS_DELAY_DAYS = int(os.environ.get("JQUANTS_DELAY_DAYS", "84"))
# 取得できる最新日から遡れる日数 (Free は 2 年)。
JQUANTS_HISTORY_DAYS = int(os.environ.get("JQUANTS_HISTORY_DAYS", "730"))

BOJ_BASE = "https://www.stat-search.boj.or.jp/api/v1"
# 日銀 API は高頻度アクセス禁止。1 リクエスト/2 秒に抑える。
BOJ_MIN_INTERVAL = float(os.environ.get("BOJ_MIN_INTERVAL", "2"))

# 未確定の最新期間 (当月・遅延期間内など) を再確認する最小間隔 (時間)。
TAIL_TTL_HOURS = float(os.environ.get("INVEST_TAIL_TTL_HOURS", "12"))

USER_AGENT = "investment-data-fetcher/0.1"

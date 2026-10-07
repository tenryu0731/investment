"""新しいデータ源のひな形。

使い方:
    1. このファイルを sources/<名前>.py にコピーする (先頭が "_" のファイルは読み込まれない)。
    2. TODO を埋める。
    3. python -m invest <cli> --help で確認する。CLI・スキーマ作成・status への表示は自動。

パターン:
    - 一括で取る資源 (一覧・マスタ・書類): self.once(conn, resource, refresh, fetch)
        fetch() が保存件数を返す。取得記録があれば呼ばれない。
    - 時系列: self.timeseries(conn, series, unit, start, end, fetch=..., final_until=...)
        fetch(gap_start, gap_end) は未取得の区間だけで呼ばれる。
        final_until() は「これ以前は値が変わらない」最後の期間を返す。
    - HTTP は必ず self.get / self.get_json を使う (レート制限・リトライ・生レスポンス保存)。
"""

import json
import os

from .base import REFRESH, Command, Source, arg

SCHEMA = """
-- TODO: テーブル名は <cli>_ で始める (他のデータ源と衝突しないように)
CREATE TABLE IF NOT EXISTS example_items (
    id          TEXT PRIMARY KEY,
    date        TEXT,
    value       REAL,
    data        TEXT NOT NULL      -- 元の行を JSON で丸ごと残す
);
"""


class Example(Source):
    name = "example"           # TODO: DB 内の識別子。一度決めたら変えない。
    cli = "example"            # TODO: python -m invest <cli> ...
    help = "TODO: データ源の説明"
    base_url = "https://api.example.com/v1"   # TODO
    schema = SCHEMA
    tables = ("example_items",)
    env_vars = ("EXAMPLE_API_KEY",)           # TODO: 認証不要なら ()
    min_interval = float(os.environ.get("EXAMPLE_MIN_INTERVAL", "1"))  # TODO: 利用規約に合わせる

    def headers(self):
        # TODO: 認証方式に合わせる。認証不要ならこのメソッドごと削除する。
        return {"Authorization": f"Bearer {self.env('EXAMPLE_API_KEY')}"}

    def commands(self):
        return [
            Command("items", "TODO: 説明", lambda c, a: self.fetch_items(c, a.refresh), [REFRESH]),
            Command("series", "TODO: 説明",
                    lambda c, a: self.fetch_series(c, a.key, a.start, a.end, a.refresh),
                    [arg("key"), arg("--start", required=True, help="YYYY-MM-DD"),
                     arg("--end", required=True, help="YYYY-MM-DD"), REFRESH]),
        ]

    def fetch_items(self, conn, refresh: bool = False) -> dict:
        def fetch() -> int:
            rows = self.get_json(conn, "items", {})["items"]   # TODO: レスポンス形式に合わせる
            conn.executemany(
                "INSERT OR REPLACE INTO example_items (id, date, value, data) VALUES (?, ?, ?, ?)",
                [(r["id"], r.get("date"), r.get("value"), json.dumps(r, ensure_ascii=False)) for r in rows],
            )
            return len(rows)

        return self.once(conn, "items", refresh, fetch)

    def fetch_series(self, conn, key: str, start: str, end: str, refresh: bool = False) -> dict:
        def fetch(gap_start: str, gap_end: str) -> int:
            rows = self.get_json(conn, f"series/{key}", {"from": gap_start, "to": gap_end})["data"]
            # TODO: 保存
            return len(rows)

        # TODO: 確定済みの最後の日付。例: 公表に 1 日遅れるなら「昨日」。全期間確定なら end。
        return self.timeseries(conn, f"series:{key}", "D", start, end, fetch=fetch,
                               final_until=lambda: end, refresh=refresh)


SOURCE = Example()

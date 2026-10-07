"""データ源の基底クラス。新しい API はこれを継承して sources/<名前>.py に 1 ファイルで書く。

基底クラスが受け持つもの:
    - HTTP 取得 (レート制限・リトライ・生レスポンスの保存)       -> self.get / self.get_json
    - 一括資源の取得済み判定 (fetch_log)                          -> self.once
    - 時系列の未取得区間だけを取る処理 (coverage)                 -> self.timeseries
    - CLI サブコマンドの登録                                       -> commands()
    - スキーマ作成・status 表示                                   -> schema / tables

書き方は sources/_template.py を参照。
"""

import argparse
import json
import os
import sqlite3
from dataclasses import dataclass, field
from typing import Callable

from .. import config, db, http, periods


@dataclass
class Command:
    """CLI サブコマンド。run(conn, args) は結果の dict を返す (JSON で表示される)。"""
    name: str
    help: str
    run: Callable[[sqlite3.Connection, argparse.Namespace], dict]
    args: list[tuple[tuple, dict]] = field(default_factory=list)


def arg(*flags, **kwargs) -> tuple[tuple, dict]:
    """Command.args 用。argparse.add_argument と同じ引数を取る。"""
    return flags, kwargs


REFRESH = arg("--refresh", action="store_true", help="キャッシュを無視して取り直す")


class Source:
    # --- サブクラスで設定する ------------------------------------------------
    name: str = ""            # DB 内の識別子 (coverage / fetch_log の source 列)。変更しないこと。
    cli: str = ""             # CLI のサブコマンド名 (python -m invest <cli> ...)
    help: str = ""
    base_url: str = ""
    schema: str = ""          # このデータ源のテーブル (CREATE TABLE IF NOT EXISTS ...)
    tables: tuple[str, ...] = ()   # status で件数を表示するテーブル
    env_vars: tuple[str, ...] = () # 必要な環境変数 (status で設定有無を表示)
    min_interval: float = 1.0      # リクエストの最小間隔 (秒)

    def commands(self) -> list[Command]:
        return []

    def headers(self) -> dict:
        """全リクエストに付けるヘッダー (認証など)。"""
        return {}

    # --- 共通処理 -------------------------------------------------------------
    calls = 0  # このプロセスでの API 呼び出し回数

    def env(self, var: str) -> str:
        """必須の環境変数を読む。未設定なら分かりやすいエラーにする。"""
        v = os.environ.get(var)
        if not v:
            raise RuntimeError(f"環境変数 {var} が設定されていません ({self.help})")
        return v

    def get(self, conn, endpoint: str, params: dict, headers: dict | None = None) -> bytes:
        """base_url/endpoint を GET し、生レスポンスを保存して返す。"""
        url = f"{self.base_url}/{endpoint}" if self.base_url else endpoint
        body = http.get(conn, self.name, url, params, headers={**self.headers(), **(headers or {})},
                        min_interval=self.min_interval)
        self.calls += 1
        db.save_raw(conn, self.name, endpoint, params, body)
        return body

    def get_json(self, conn, endpoint: str, params: dict, headers: dict | None = None) -> dict:
        return json.loads(self.get(conn, endpoint, params, headers))

    def once(self, conn, resource: str, refresh: bool, fetch: Callable[[], int]) -> dict:
        """一括で取る資源。取得記録があれば fetch を呼ばない。fetch は保存件数を返す。"""
        if not refresh and db.fetched_at(conn, self.name, resource):
            return {"resource": resource, "calls": 0, "stored": 0, "cached": True}
        before = self.calls
        stored = fetch()
        db.mark_fetched(conn, self.name, resource)
        conn.commit()
        return {"resource": resource, "calls": self.calls - before, "stored": stored}

    def timeseries(self, conn, series: str, unit: str, start: str, end: str, *,
                   fetch: Callable[[str, str], int],
                   final_until: Callable[[], str | None],
                   tail_end: str | None = None,
                   refresh: bool = False) -> dict:
        """時系列の [start, end] のうち未取得の区間だけ fetch(gap_start, gap_end) を呼ぶ。

        unit        : periods の単位 (D / M / Q / H / Y)。start / end もこの形式。
        fetch       : 区間を取得して保存し、保存件数を返す。
        final_until : 確定済みとみなせる最後の期間 (それ以降は値が増えうる)。不明なら None。
                      取得後に呼ばれ、[gap_start, min(gap_end, final_until)] が取得済みになる。
        tail_end    : 「現在」の期間。区間全体が未確定域でこの期間で終わるとき、
                      INVEST_TAIL_TTL_HOURS 以内に確認済みなら呼ばない。
        """
        if refresh:
            db.clear_coverage(conn, self.name, series)
        before = self.calls
        stored = 0
        tail = f"tail:{series}"
        for gap_start, gap_end in db.gaps(conn, self.name, series, unit, start, end):
            fu = final_until()
            is_tail = tail_end is not None and gap_end == tail_end
            if (is_tail and not refresh and fu is not None
                    and periods.to_ord(unit, gap_start) > periods.to_ord(unit, fu)
                    and db.fetched_within(conn, self.name, tail, config.TAIL_TTL_HOURS)):
                continue
            stored += fetch(gap_start, gap_end)
            fu = final_until()
            if fu is not None:
                end_ord = min(periods.to_ord(unit, gap_end), periods.to_ord(unit, fu))
                db.add_coverage(conn, self.name, series, unit, gap_start, periods.from_ord(unit, end_ord))
            if is_tail:
                db.mark_fetched(conn, self.name, tail)
            conn.commit()
        return {"series": series, "calls": self.calls - before, "stored": stored}

    def status(self, conn) -> dict:
        return {
            "env": {v: bool(os.environ.get(v)) for v in self.env_vars},
            "rows": {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in self.tables},
        }

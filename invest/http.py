"""HTTP GET (標準ライブラリのみ)。ソースごとの最小間隔とリトライを扱う。

最小間隔の最終リクエスト時刻は SQLite に保存し、CLI を連続実行しても守られるようにする。
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config


class ApiError(RuntimeError):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body[:300]}")
        self.status = status
        self.body = body


def _wait(conn, source: str, min_interval: float) -> None:
    row = conn.execute("SELECT last_at FROM rate_state WHERE source = ?", (source,)).fetchone()
    if row:
        delay = row["last_at"] + min_interval - time.time()
        if delay > 0:
            time.sleep(delay)
    conn.execute(
        "INSERT OR REPLACE INTO rate_state (source, last_at) VALUES (?, ?)", (source, time.time())
    )
    conn.commit()


def get(conn, source: str, url: str, params: dict, *, headers: dict | None = None,
        min_interval: float = 1.0, retries: int = 3) -> bytes:
    full = url + "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    hdrs = {"User-Agent": config.USER_AGENT, **(headers or {})}
    for attempt in range(retries + 1):
        _wait(conn, source, min_interval)
        req = urllib.request.Request(full, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            # 429 / 5xx は待ってリトライ。それ以外は即エラー。
            if attempt < retries and (e.code == 429 or e.code >= 500):
                time.sleep(60 if e.code == 429 else 5 * (attempt + 1))
                continue
            raise ApiError(e.code, body) from None
        except urllib.error.URLError:
            if attempt < retries:
                time.sleep(5 * (attempt + 1))
                continue
            raise
    raise AssertionError("unreachable")


def get_json(*args, **kwargs) -> tuple[dict, bytes]:
    body = get(*args, **kwargs)
    return json.loads(body), body

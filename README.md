# investment

投資分析を AI に行わせるためのデータ基盤。

```
データ源 (J-Quants / 日銀 / EDINET / JPX) → 取得 (invest/) → SQLite キャッシュ (data/) → (将来) MCP サーバー → Claude
```

標準ライブラリのみで動く (Python 3.10+)。

## 使い方

```sh
python -m invest boj meta FM08                         # 日銀: DB の系列一覧
python -m invest boj series FM08 FXERD01 --start 202401 # 日銀: ドル円 (9時) 日次
python -m invest jq master                              # J-Quants: 上場銘柄一覧
python -m invest jq bars 7203 --start 2026-01-01        # J-Quants: 日足
python -m invest jq fins 7203                           # J-Quants: 財務情報
python -m invest status                                 # キャッシュの状態
```

`--refresh` を付けると、キャッシュを無視して取り直す。

## 再取得しない仕組み

| 種類 | 対象 | 判定 |
|---|---|---|
| 時系列 (`coverage` テーブル) | 日足、日銀の系列 | 取得済みの期間を記録し、未取得の区間だけ API を呼ぶ |
| 一括 (`fetch_log` テーブル) | 銘柄一覧、日銀メタデータ、財務情報 | 取得記録があれば呼ばない |

- **日銀:** 最新の観測値を含む期間 (当月など) は値が追加されるため未確定とし、`INVEST_TAIL_TTL_HOURS` (既定 12 時間) に 1 回だけ確認する。
- **J-Quants:** 契約範囲 (Free は 今日 - 12 週 から 2 年分) の日足は確定値として扱う。範囲は自動で調整する。保存済みの最新日より後に株式分割などがあった場合は、調整後価格を取り直す。
- API の生レスポンスは `raw_responses` に gzip で保存する。
- レート制限: J-Quants 13 秒間隔、日銀 2 秒間隔 (前回時刻を DB に保存し、連続実行でも守る)。

## 構成

```
invest/
  __main__.py        CLI (登録済みのデータ源から自動で組み立てる)
  db.py              共通テーブル (coverage / fetch_log / raw_responses / rate_state)
  http.py            HTTP GET (レート制限・リトライ)
  periods.py         期間文字列の計算 (日 / 月 / 四半期 / 半期 / 年)
  sources/
    base.py          データ源の基底クラス Source
    _template.py     新しいデータ源のひな形 (読み込まれない)
    jquants.py       J-Quants
    boj.py           日本銀行
```

## 新しいデータ源の追加

`invest/sources/` に 1 ファイル置くだけで、CLI・テーブル作成・`status` に反映される。他のファイルは触らない。

1. `invest/sources/_template.py` を `invest/sources/<名前>.py` にコピーする。
2. `TODO` を埋める。設定するのは次のとおり。
    - `name`（DB 内の識別子）、`cli`（サブコマンド名）、`base_url`
    - `schema`（テーブルは `<cli>_` で始める）、`tables`
    - `env_vars`（必要な API キー）、`min_interval`（利用規約に合わせたアクセス間隔）
    - `headers()`（認証）、`commands()`（CLI コマンド）
3. 取得処理は、基底クラスの次のどちらかで書く。どちらでも再取得しない判定は自動で入る。
    - 一覧・マスタ・書類など一括で取るもの: `self.once(conn, resource, refresh, fetch)`
    - 時系列: `self.timeseries(conn, series, unit, start, end, fetch=..., final_until=...)`
4. HTTP は必ず `self.get` / `self.get_json` を使う。レート制限、リトライ、生レスポンス保存が入る。
5. `python -m invest <cli> --help` と `python -m unittest discover -s tests` で確認する。

`final_until` は「この期間までは値が変わらない」最後の期間を返す関数。これより後の期間は取得済みとして記録しないので、次回また取りに行く（`tail_end` を渡すと 12 時間に 1 回に抑える）。

## 環境変数

| 変数 | 用途 |
|---|---|
| `JQUANTS_API_KEY` | J-Quants API キー |
| `EDINET_API_KEY` | EDINET API キー (取得処理 `sources/edinet.py` は未作成) |
| `INVEST_DB` | DB の場所 (既定 `data/invest.sqlite`) |

日銀 API は認証不要。

## 注意

- `data/` はコミットしない。公開リポジトリのため、取得データのコミットは再配布にあたる。
- クラウドのセッションはコンテナが使い捨てなので、`data/` はセッションをまたいで残らない。

## テスト

```sh
python -m unittest discover -s tests
```

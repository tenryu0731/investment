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

## 環境変数

| 変数 | 用途 |
|---|---|
| `JQUANTS_API_KEY` | J-Quants API キー |
| `EDINET_API_KEY` | EDINET API キー (未実装) |
| `INVEST_DB` | DB の場所 (既定 `data/invest.sqlite`) |

日銀 API は認証不要。

## 注意

- `data/` はコミットしない。公開リポジトリのため、取得データのコミットは再配布にあたる。
- クラウドのセッションはコンテナが使い捨てなので、`data/` はセッションをまたいで残らない。

## テスト

```sh
python -m unittest discover -s tests
```

# Web dashboard

`app-plcmoc-py`は、UDPプロトコルサーバーと同じプロセスでブラウザ管理画面をホストします。Web部分はPython標準ライブラリの`ThreadingHTTPServer`と静的HTML/CSS/JavaScriptで構成され、追加のWebフレームワークやNode.jsビルドを必要としません。

## 起動

```bash
python main.py
```

既定URL:

```text
http://localhost:8080
```

主なオプション:

```text
--web / --no-web
--web-bind <address>
--web-port <port>
--web-write / --no-web-write
--web-max-points <count>
--web-log-buffer <count>
--open-browser
```

例:

```bash
python main.py \
  --web-bind 127.0.0.1 \
  --web-port 18080 \
  --no-web-write \
  --web-log-buffer 5000
```

`--web-port 0`では空きポートを選択します。選択されたURLは`web_started`ログへ記録されます。

## 画面

### Overview

`/api/status`を定期取得し、次を表示します。

- アプリケーションバージョンと稼働時間
- 設定ファイル
- Webバインド先と書込み可否
- ログモード
- ワード／ビットメモリエリア数
- UDPエンドポイントの起動状態と実際のバインド先
- 受信、送信、無応答、エラー、障害注入の集計

ログモード選択は`POST /api/logging`を呼び、実行中のロガーレベルと各UDPエンドポイントのHEX出力設定を更新します。プロセス再起動は不要です。

### Memory

`GET /api/memory`で共有`MemorySpace`を読み出します。

```text
GET /api/memory?storage=word&area=D&start=100&count=32
```

応答:

```json
{
  "storage": "word",
  "area": "D",
  "start": 100,
  "count": 32,
  "values": [4660, 0, 0]
}
```

複数の任意アドレスをまとめて変更できます。

```http
PUT /api/memory
Content-Type: application/json
```

```json
{
  "storage": "word",
  "area": "D",
  "items": [
    {"address": 100, "value": "0x1234"},
    {"address": 105, "value": 99}
  ]
}
```

連続範囲形式:

```json
{
  "storage": "bit",
  "area": "M",
  "start": 20,
  "values": [1, 0, 1, 1]
}
```

書込み時は値、全アドレス、全範囲を先に検証し、検証失敗時に先頭側だけが更新されないようにしています。`--no-web-write`ではすべてのPUTを`403`で拒否します。

### Traffic

既存のPythonログへ専用`DashboardLogHandler`を追加し、構造化されたレコードを固定長のリングバッファへ保持します。UDP通信処理とログ出力処理は分離されており、ブラウザ接続がなくてもPLCモックは動作します。

```text
GET /api/logs?after=120&limit=200&endpoint=mitsubishi-mc&level=INFO&search=device%3DD
```

返却レコードには、時刻、レベル、logger、event、request_id、endpoint、protocol、remote、message、および診断フィールドが含まれます。

リングバッファのクリア:

```text
POST /api/logs/clear
```

クリアは画面用ログ履歴だけを削除し、PLCメモリやUDPエンドポイントには影響しません。

## JSON API

| Method | Path | 用途 |
|---|---|---|
| GET | `/api/health` | 軽量ヘルスチェック |
| GET | `/api/status` | 状態、エンドポイント、メモリ領域、集計 |
| GET | `/api/logs` | 増分ログ取得とフィルタ |
| POST | `/api/logs/clear` | 画面用ログバッファをクリア |
| GET | `/api/memory` | メモリ範囲読出し |
| PUT | `/api/memory` | メモリ書込み |
| POST | `/api/logging` | 実行中のログモード切替 |

APIは同一オリジン利用を前提としています。レスポンスには`no-store`、`nosniff`、frame拒否、同一オリジン限定のContent Security Policyを付与します。

## ログとメトリクス

画面の受信／送信カウンタは、`datagram_received`、`datagram_sent`など既存の構造化ログイベントから集計します。そのため`quiet`または`traffic=off`では新しい通信イベントがログ化されず、画面の通信カウンタも増加しません。パケット集計を継続する場合は`normal`以上を使用してください。

保持件数は`--web-log-buffer`で指定します。最小100件です。ブラウザDOM側も表示行数を制限し、長時間稼働時のメモリ増加を抑制します。

## スレッド境界

- UDPプロトコル処理: asyncioイベントループ
- Web HTTP処理: `ThreadingHTTPServer`のワーカースレッド
- PLCメモリ: 各領域の`RLock`で保護
- 画面ログバッファ: 専用`Lock`で保護

Web APIはUDPイベントループをブロックしません。停止時はHTTPサーバーをshutdownし、ログハンドラーをroot loggerから除去してからUDPエンドポイントを閉じます。

## セキュリティ上の境界

認証、TLS、CSRFトークン、ユーザー別権限は実装していません。PLCメモリを書き換えられる開発用管理面です。

- インターネットへ直接公開しない
- 必要なら`--web-bind 127.0.0.1`を使う
- 参照だけなら`--no-web-write`を使う
- 遠隔利用時はリバースプロキシやVPN側で認証とTLSを追加する

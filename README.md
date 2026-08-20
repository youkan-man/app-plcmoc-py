# app-plcmoc-py

UDP/IP上で動く、Python製の拡張可能なPLCプロトコル・モックサーバーです。三菱MC、OMRON FINS/UDP、Modbus形式のUDPテストエンドポイントを同時に起動し、すべてのプロトコルから同じ仮想PLCメモリを読み書きできます。

通信状態、解析済みログ、共有PLCメモリをブラウザから確認・編集できる管理画面も、同じPythonプロセスから配信します。Node.jsや別のWebサーバーは不要です。

> 実機PLCのラダー実行や物理I/Oまで再現するシミュレーターではありません。通信クライアント、監視アプリ、ゲートウェイ、上位システムの開発・自動試験向けモックです。未対応コマンドへ成功応答を捏造せず、プロトコルエラーまたは無応答にします。

## すぐ起動する

```bash
python main.py
```

起動後、ブラウザで次を開きます。

```text
http://localhost:8080
```

初期設定では次のポートを使用します。

| ポート | 用途 |
|---:|---|
| `8080/tcp` | ブラウザ管理画面／JSON API |
| `5000/udp` | Mitsubishi MC protocol |
| `9600/udp` | OMRON FINS/UDP |
| `1502/udp` | Modbus ADU over UDP |
| `15000/udp` | カスタムASCIIプラグイン例 |

`main.py`はリポジトリ直下の`config/example.yml`を自動的に読み込みます。パッケージをeditable installしなくてもソースツリーから起動できます。

設定検証:

```bash
python main.py check
python main.py check --json
```

別設定で起動:

```bash
python main.py --config config/local.yml
```

## ブラウザ管理画面

管理画面には3つのビューがあります。

### Overview

- UDPエンドポイントの起動状態、バインド先、プロトコル
- 受信／送信パケット数とバイト数
- 無応答、プロトコルエラー、障害注入イベント
- 稼働時間、設定ファイル、メモリエリア数
- 実行中のログモード切替

### Memory

- ワード領域とビット領域の選択
- 開始アドレスと点数を指定した範囲読出し
- 10進／16進ワード表示
- 複数セルをまとめて編集
- 全アドレスと値を検証してから反映するフェイルクローズ書込み
- 読出し専用モード

### Traffic

- MC 1E／3E／4E、FINS、Modbusの解析済みライブログ
- リクエストID、エンドポイント、送信元、コマンド、デバイス、アドレス、点数
- 終了コード、例外コード、処理時間、無応答、障害注入
- エンドポイント、最低ログレベル、全文検索によるフィルタ
- 一時停止、オートスクロール、画面用リングバッファのクリア

画面はPython標準ライブラリのHTTPサーバーと、ビルド不要のHTML/CSS/JavaScriptで構成しています。詳細は[`docs/web-dashboard.md`](docs/web-dashboard.md)を参照してください。

### Web起動オプション

```bash
python main.py --web-bind 0.0.0.0 --web-port 8080
python main.py --no-web
python main.py --no-web-write
python main.py --web-max-points 1024
python main.py --web-log-buffer 5000
python main.py --open-browser
```

`--web-port 0`を指定すると、空いているTCPポートを自動選択します。実際のURLは起動ログへ出力されます。

## 対応プロトコル

| エンドポイント | UDPポート例 | 実装範囲 |
|---|---:|---|
| Mitsubishi MC protocol | 5000 | A互換1E、QnA互換3E/4E、Binary/ASCII |
| OMRON FINS/UDP | 9600 | メモリエリア読出し`0101`、書込み`0102` |
| Modbus ADU over UDP | 1502 | FC 01/02/03/04/05/06/15/16 |
| カスタムASCIIプラグイン例 | 15000 | `PING`、ワード／ビット読書き |

Modbusエンドポイントは、Modbus TCPのMBAP/PDU形式をUDPデータグラムへ載せるテスト用互換拡張であり、Modbus TCPそのものではありません。

## Mitsubishi MC protocol

`protocol: mc-protocol`は、同じUDPポートで1E、3E、4EとBinary、ASCIIを自動判別します。

### QnA互換3E／4E

| 機能 | コマンド |
|---|---:|
| 形名読出し | `0101` |
| 一括読出し／書込み | `0401` / `1401` |
| ランダム読出し／書込み | `0403` / `1402` |
| モニタ登録／実行 | `0801` / `0802` |
| 複数ブロック読出し／書込み | `0406` / `1406` |
| リモートRUN／STOP／PAUSE | `1001` / `1002` / `1003` |
| ラッチクリア／リモートリセット | `1005` / `1006` |
| ループバックテスト | `0619` |
| エラークリア | `1617` |

標準デバイス指定のサブコマンド`0000`／`0001`と、拡張幅の`0002`／`0003`に対応します。

### A互換1E

| コマンド | 機能 |
|---:|---|
| `00` / `01` | ビット／ワード一括読出し |
| `02` / `03` | ビット／ワード一括書込み |
| `04` / `05` | ビット／ワードランダム書込み |
| `06` / `07` | ビット／ワードモニタ登録 |
| `08` / `09` | ビット／ワードモニタ実行 |

フレーム構造、デバイスコード、終了コード、点数制限、リモート状態遷移の詳細は[`docs/mc-protocol.md`](docs/mc-protocol.md)にあります。

プロトコルを分けてホストすることもできます。

```yaml
endpoints:
  - name: mitsubishi-all
    protocol: mc-protocol
    bind: 0.0.0.0
    port: 5000

  - name: mitsubishi-qna
    protocol: slmp
    bind: 0.0.0.0
    port: 5001

  - name: mitsubishi-1e
    protocol: mc-1e
    bind: 0.0.0.0
    port: 5002
```

## ログ

| モード | アプリケーション | 通信 | メモリ |
|---|---|---|---|
| `quiet` | WARNING以上 | 無効 | 無効 |
| `normal` | INFO以上 | 解析済み要約 | 無効 |
| `debug` | DEBUG以上 | 解析済み要約 | 書込み |
| `trace` | TRACE以上 | 要約＋HEX | 読出し／書込み |

CLIから切り替え:

```bash
python main.py --quiet
python main.py --debug
python main.py --trace
python main.py --traffic-log hex --memory-log write
python main.py --log-format json --log-file logs/plcmock.jsonl
```

実行中はOverview画面のLog modeからも`quiet`／`normal`／`debug`／`trace`を切り替えられます。詳細は[`docs/logging.md`](docs/logging.md)を参照してください。

## 共有メモリ

すべてのエンドポイントは同じ`MemorySpace`を共有します。

| 通信上のアドレス | 共有先 |
|---|---|
| MC 1E/3E/4Eの`D100` | `D`ワード領域100番 |
| FINSの`DM100` | 同じ`D`ワード領域100番 |
| Modbus holding register 100 | 同じ`D`ワード領域100番 |
| MCの`M100` | `M`ビット領域100番 |
| Modbus coil 100 | 同じ`M`ビット領域100番 |

そのため、1Eで`D100`へ書き込み、3E、FINS、Modbus、ブラウザ画面から同じ値を読み返せます。

## プロトコルを改造する

UDP処理とプロトコル処理は分離されています。`ProtocolPlugin`を継承し、1データグラムを受けて応答バイト列を返します。

```python
from plcmock.protocols.base import DatagramContext, ProtocolPlugin


class MyProtocol(ProtocolPlugin):
    protocol_name = "my-protocol"

    async def handle_datagram(self, data: bytes, context: DatagramContext):
        if data == b"GET D0":
            value = self.memory.word("D").read_words(0, 1)[0]
            return value.to_bytes(2, "big")
        return None
```

```yaml
plugin_paths:
  - ../examples

endpoints:
  - name: my-device
    protocol: my_protocol:MyProtocol
    bind: 0.0.0.0
    port: 17000
```

MCのデバイスコード、ASCII表記、進数、共有領域、1Eコードも`options.device_map`から差し替えられます。

## 通信障害の再現

```yaml
faults:
  seed: 1234
  drop_rate: 0.05
  duplicate_rate: 0.01
  corrupt_rate: 0.01
  delay_ms: { min: 5, max: 80 }
```

発生した欠落、重複、破損、遅延は通信ログと管理画面へ記録されます。

## Docker

```bash
docker compose up --build
```

ブラウザ管理画面は`http://localhost:8080`で公開されます。

## セキュリティ

管理画面とJSON APIには認証がありません。既定では`0.0.0.0:8080`へバインドし、メモリ書込みも有効です。ローカル環境または信頼できる検証ネットワークで利用してください。

ホスト内だけに限定する例:

```bash
python main.py --web-bind 127.0.0.1
```

読出し専用:

```bash
python main.py --no-web-write
```

## テスト

```bash
python -m pytest -q
python -m compileall -q main.py src examples tests
python main.py check --json
```

テスト対象には、MC 1E／3E／4E、Binary／ASCII、FINS、Modbus、共有メモリ、実UDPソケット、ログ、Web静的配信、JSON API、メモリ編集、読出し専用制御、ログリングバッファを含みます。

## 現在の境界

- PLC通信はUDPのみです。MC protocol over TCPは未実装です。
- MCの`008x`拡張指定、ラベル、CPUバッファメモリ、インテリジェント機能ユニット、ファイル、パスワード、時刻設定は未対応です。
- PLCスキャン、ラダー実行、物理I/O、保持領域の永続化は実装していません。
- 管理画面に認証、TLS、ユーザー権限はありません。

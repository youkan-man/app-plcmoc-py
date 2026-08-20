# app-plcmoc-py

UDP/IP上で動く、Python製の拡張可能なPLCプロトコル・モックサーバーです。複数のプロトコルとUDPポートを同時に起動し、すべてのエンドポイントから同じ仮想PLCメモリを読み書きできます。

実機PLCのラダー実行や物理I/Oまで再現するシミュレーターではありません。PLC通信クライアント、監視アプリ、ゲートウェイ、上位システムの開発・自動試験向けに、代表的な通信フレーム、デバイスメモリ、異常応答を再現します。未対応コマンドに成功応答を返さず、プロトコルエラーまたは無応答にします。

## 対応プロトコル

| エンドポイント | UDPポート例 | 実装範囲 |
|---|---:|---|
| Mitsubishi MC protocol | 5000 | A互換1E、QnA互換3E/4E、Binary/ASCII |
| OMRON FINS/UDP | 9600 | メモリエリア読出し `0101`、書込み `0102` |
| Modbus ADU over UDP | 1502 | FC 01/02/03/04/05/06/15/16 |
| カスタムASCIIプラグイン例 | 15000 | `PING`、ワード・ビット読書き |

Modbusエンドポイントは、Modbus TCPのMBAP/PDU形式をUDPデータグラムへ載せるテスト用互換拡張です。Modbus TCPそのものではありません。

## 起動

リポジトリを取得した直後は、インストールなしで起動できます。

```bash
python main.py
```

`main.py`は自動的に`config/example.yml`を読み込みます。設定検証も同じ入口から実行できます。

```bash
python main.py check
python main.py check --json
```

従来のCLIも利用できます。

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e .

plcmock check --config config/example.yml
plcmock serve --config config/example.yml
```

Dockerの場合:

```bash
docker compose up --build
```

初期設定では次のUDPポートを公開します。

```text
5000/udp   Mitsubishi MC protocol
9600/udp   OMRON FINS/UDP
1502/udp   Modbus ADU over UDP
15000/udp  カスタムASCIIプラグイン例
```

## デバッグログ

ログはアプリケーション、通信、PLCメモリアクセスの3系統に分かれています。プリセットでまとめて切り替えられます。

| モード | アプリケーション | 通信 | メモリ |
|---|---|---|---|
| `quiet` | WARNING以上 | 無効 | 無効 |
| `normal` | INFO以上 | 解析済み要約 | 無効 |
| `debug` | DEBUG以上 | 解析済み要約 | 書込み |
| `trace` | TRACE以上 | 要約＋HEX | 読出し・書込み |

最も簡単な切替:

```bash
python main.py --quiet
python main.py --debug
python main.py --trace
```

個別にも変更できます。

```bash
python main.py --traffic-log hex --memory-log write
python main.py --trace --traffic-log summary --memory-log write
python main.py --log-format json --log-file logs/plcmock.jsonl
python main.py --no-traffic-log
```

通常ログでは、MC、FINS、Modbusの要求を解析し、次の情報を表示します。

- UDP送信元とエンドポイント
- リクエストID
- フレーム種別とBinary/ASCII
- コマンド、サブコマンド、ファンクション
- デバイス、アドレス、点数
- 応答終了コード、例外コード、データ長
- 処理時間
- 無応答、遅延、欠落、重複、データ破損

例:

```text
... INFO plcmock.traffic event=datagram_received request=mitsubishi-mc-00000001 endpoint=mitsubishi-mc protocol=mc-protocol remote=127.0.0.1:53000 MC 3E binary batch-read command=0x0401 subcommand=0x0000 device=D address=100 points=2 bytes=21
... INFO plcmock.traffic event=datagram_sent request=mitsubishi-mc-00000001 endpoint=mitsubishi-mc protocol=mc-protocol remote=127.0.0.1:53000 MC 3E binary batch-read response end=0x0000 data_bytes=4 bytes=15 duration_ms=0.412
```

YAMLでの設定:

```yaml
server:
  max_datagram_size: 65535
  logging:
    mode: normal          # quiet | normal | debug | trace
    level: INFO           # プリセットのレベルだけ上書き可能
    format: text          # text | json
    console: true
    file: ../logs/plcmock.log
    rotate_max_bytes: 10485760
    rotate_backup_count: 5
    traffic: summary      # off | summary | hex
    memory: off           # off | write | all
    max_hex_bytes: 512
    max_value_preview: 16
```

ログファイルの相対パスはYAMLファイルの場所を基準に解決します。旧設定の`log_level`と`hex_dump`も引き続き利用できます。詳細は[`docs/logging.md`](docs/logging.md)を参照してください。

## Mitsubishi MC protocol

`protocol: mc-protocol`を指定すると、同じUDPポートで1E、3E、4EとBinary、ASCIIを自動判別します。

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

デバイス指定は標準形式のサブコマンド`0000`／`0001`に加え、4バイトデバイス番号＋2バイトデバイスコードを使う`0002`／`0003`にも対応します。`008x`系の拡張指定は未対応です。

### A互換1E

| コマンド | 機能 |
|---:|---|
| `00` / `01` | ビット／ワード一括読出し |
| `02` / `03` | ビット／ワード一括書込み |
| `04` / `05` | ビット／ワードランダム書込み |
| `06` / `07` | ビット／ワードモニタ登録 |
| `08` / `09` | ビット／ワードモニタ実行 |

1Eの点数フィールド`00`は256点として扱います。Binaryでは4バイトlittle-endianのデバイス番号と2バイトlittle-endianのデバイスコードを使います。ASCIIでは4桁のデバイスコードと8桁のデバイス番号を使います。

1Eの標準登録デバイス:

```text
X Y M F B D W R TC TS TN CC CS CN
```

3E／4Eの主な標準デバイス:

```text
SM SD X Y M L F V S B SB SW DX DY D W R ZR Z
TC TS TN CC CS CN SC SS SN
```

フレーム構造、終了コード、点数制限、リモート状態遷移の詳細は[`docs/mc-protocol.md`](docs/mc-protocol.md)を参照してください。

## 機種プロファイル

実機シリーズやEthernetユニットに合わせて、受け付けるフレーム、エンコーディング、コマンドをYAMLで制限できます。

```yaml
options:
  accepted_frames: ["3E"]
  accepted_encodings: ["binary"]

  enabled_commands:
    - "0x0101"
    - "0x0401"
    - "0x1401"

  disabled_commands:
    - "0x0406"
    - "0x1406"

  one_e_disabled_commands:
    - "0x04"
    - "0x05"
```

プロトコルを個別に起動することもできます。

```yaml
endpoints:
  - name: mitsubishi-all
    protocol: mc-protocol  # 1E/3E/4Eを自動判別
    bind: 0.0.0.0
    port: 5000

  - name: mitsubishi-qna
    protocol: slmp         # 3E/4Eだけ
    bind: 0.0.0.0
    port: 5001

  - name: mitsubishi-1e
    protocol: mc-1e        # 1Eだけ
    bind: 0.0.0.0
    port: 5002
```

`mc`は`mc-protocol`、`slmp-3e-4e`は`slmp`の別名です。

## デバイスマッピングの改造

3E／4Eのデバイスコード、ASCII表記、アドレス進数、共有メモリ領域、1Eコードを設定から差し替えられます。既存定義の一部だけを上書きした場合は、未指定項目を既定値から継承します。

```yaml
options:
  device_map:
    "0xA8":
      name: D
      area: MY_D
      storage: word
      ascii_code: D
      radix: 10
      one_e_code: "0x4420"
```

ASCIIコードまたは1Eコードが重複する設定や、範囲外のコードは起動時に拒否します。

## 共有メモリ

すべてのエンドポイントは同じ`MemorySpace`を共有します。

| 通信上のアドレス | 共有先 |
|---|---|
| MC 1E/3E/4Eの`D100` | `D`ワード領域100番 |
| FINSの`DM100` | 同じ`D`ワード領域100番 |
| Modbus holding register 100 | 同じ`D`ワード領域100番 |
| MCの`M100` | `M`ビット領域100番 |
| Modbus coil 100 | 同じ`M`ビット領域100番 |

1Eで`D100`へ書き込み、3E、FINS、Modbusから読み返すクロスプロトコル試験も可能です。

## プロトコルプラグイン

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

設定からロードします。

```yaml
plugin_paths:
  - ../examples

endpoints:
  - name: my-device
    protocol: my_protocol:MyProtocol
    bind: 0.0.0.0
    port: 17000
```

## 通信障害の再現

エンドポイント単位で遅延、欠落、重複、1ビット破損を設定できます。発生した障害は通信ログにも記録されます。

```yaml
faults:
  seed: 1234
  drop_rate: 0.05
  duplicate_rate: 0.01
  corrupt_rate: 0.01
  delay_ms: { min: 5, max: 80 }
```

## テスト

```bash
python -m pytest -q
python -m compileall -q main.py src examples tests
python main.py check --json
```

テスト対象には、1E／3E／4E、Binary／ASCII、標準／拡張デバイス形式、ランダムアクセス、複数ブロック、送信元別モニタ、リモート状態遷移、書込み原子性、実UDPソケット、プロトコル間共有メモリ、ログ設定、診断デコーダ、`main.py`起動経路を含みます。

## 現在の境界

- MC protocolはUDPのみです。TCPの接続管理、1E/3E/4EのTCP転送は実装していません。
- `008x`拡張指定、ラベルアクセス、CPUバッファメモリ、インテリジェント機能ユニット、ファイル、パスワード、時刻設定などは未対応です。
- `1617`はEthernet向けのサブコマンド`0000`だけを扱います。
- 1Eの拡張ファイルレジスタ系コマンドは未対応です。
- 形名やCPU状態は設定可能なモック状態であり、特定PLCの完全な機種挙動を保証しません。
- PLCスキャン、ラダー実行、物理I/O、保持領域の永続化は実装していません。
- 認証機能はありません。検証用ネットワークまたはローカル環境で利用してください。

## 参考仕様

- Mitsubishi Electric, *MELSEC Communication Protocol Reference Manual*
- Mitsubishi Electric, *SLMP Reference Manual*

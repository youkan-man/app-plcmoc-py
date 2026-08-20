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

ローカルPythonで動かす場合:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e .
plcmock check --config config/example.yml
plcmock serve --config config/example.yml
```

## Mitsubishi MC protocol

`protocol: mc-protocol`を指定すると、同じUDPポートで1E、3E、4EとBinary、ASCIIを自動判別します。

### QnA互換3E／4E

| 機能 | コマンド | 対応範囲 |
|---|---:|---|
| 形名読出し | `0101` | 16文字の形名と形名コード |
| 一括読出し | `0401` | ワード単位・ビット単位 |
| 一括書込み | `1401` | ワード単位・ビット単位 |
| ランダム読出し | `0403` | ワード・ダブルワード |
| ランダム書込み | `1402` | ビット・ワード・ダブルワード |
| モニタ登録 | `0801` | UDP送信元IP・ポートごとに保持 |
| モニタ実行 | `0802` | 登録順で読出し |
| 複数ブロック読出し | `0406` | ワード領域・ビット領域 |
| 複数ブロック書込み | `1406` | 全件検証後に一括反映 |
| リモートRUN | `1001` | モックCPU状態をRUNへ変更 |
| リモートSTOP | `1002` | モックCPU状態をSTOPへ変更 |
| リモートPAUSE | `1003` | モックCPU状態をPAUSEへ変更 |
| ラッチクリア | `1005` | STOP時のみ |
| リモートリセット | `1006` | STOP時のみ。既定では成功時無応答 |
| ループバックテスト | `0619` | 指定データを検証して返却 |
| エラークリア | `1617` | サブコマンド`0000` |

デバイス指定は標準形式のサブコマンド`0000`／`0001`に加え、4バイトデバイス番号＋2バイトデバイスコードを使う`0002`／`0003`にも対応します。拡張指定を付加する`008x`系は未対応です。

既定の処理上限は設定で変更できます。

| 操作 | 既定上限 |
|---|---:|
| 一括読書き・ワード単位 | 960点 |
| 一括読書き・ビット単位（Binary） | 7168点 |
| 一括読書き・ビット単位（ASCII） | 3584点 |
| ランダム読出し／モニタ登録・標準形式 | 192点 |
| ランダム読出し／モニタ登録・拡張形式 | 96点 |
| ランダムビット書込み・標準／拡張 | 188点／94点 |
| ランダムワード書込み予算・標準／拡張 | 1920／960 |
| 複数ブロック・標準／拡張 | 120／60ブロック |

### A互換1E

| 機能 | コマンド |
|---|---:|
| ビット一括読出し | `00` |
| ワード一括読出し | `01` |
| ビット一括書込み | `02` |
| ワード一括書込み | `03` |
| ビットランダム書込み | `04` |
| ワードランダム書込み | `05` |
| ビットモニタ登録 | `06` |
| ワードモニタ登録 | `07` |
| ビットモニタ実行 | `08` |
| ワードモニタ実行 | `09` |

1Eの点数フィールド`00`は256点として扱います。ASCIIのデバイス指定は、2バイトのデバイスコードを4桁の16進ASCII、その後のデバイス番号を8桁の16進ASCIIで表します。Binaryでは4バイトlittle-endianのデバイス番号と2バイトlittle-endianのデバイスコードを使います。ASCIIのビット書込み要求は指定点数ぶんだけ送り、奇数点時のダミーOFFは読出し／モニタ応答だけに付加します。Binaryの奇数点書込みでは、未使用の下位ニブルを`0`にする必要があります。

標準登録している1Eデバイスコード:

| デバイス | コード | デバイス | コード |
|---|---:|---|---:|
| X | `5820` | Y | `5920` |
| M | `4D20` | F | `4620` |
| B | `4220` | D | `4420` |
| W | `5720` | R | `5220` |
| TN | `544E` | TS | `5453` |
| TC | `5443` | CN | `434E` |
| CS | `4353` | CC | `4343` |

通常の要求エラーは1E終了コード`10`、受信PC番号不一致は`5B 10`として応答します。`10`はモック側の汎用分類であり、機種ごとの詳細診断コードを完全再現するものではありません。モニタ登録は並列試験でクライアント同士が干渉しないよう、UDP送信元IP・ポートごとに保持します。

## 機種プロファイル

実機のシリーズ、内蔵Ethernet、外付けEthernetユニットによって利用可能なフレームとコマンドは異なります。その差をコード分岐ではなくYAMLで制限できます。

```yaml
options:
  accepted_frames: ["1E", "3E", "4E"]
  accepted_encodings: ["binary", "ascii"]

  # 3E/4Eのコマンド。値は整数、0x付き、または16進文字列。
  disabled_commands: ["0x0406", "0x1406"]

  # 1Eのコマンドは別に制御する。
  one_e_disabled_commands: ["0x04", "0x05"]
```

3Eだけを受け付ける例:

```yaml
options:
  accepted_frames: ["3E"]
```

1E Binaryだけを受け付ける例:

```yaml
options:
  accepted_frames: ["1E"]
  accepted_encodings: ["binary"]
```

`enabled_commands`または`one_e_enabled_commands`を指定すると、列挙したコマンドだけを許可できます。`disabled_commands`はその後に差し引かれます。プロファイル例は完全な機種エミュレーションではないため、対象機器のマニュアルに合わせて設定してください。

## プロトコル選択

```yaml
endpoints:
  # 1E・3E・4Eを自動判別
  - name: mitsubishi-mc
    protocol: mc-protocol
    bind: 0.0.0.0
    port: 5000

  # 3E・4Eだけ
  - name: mitsubishi-qna
    protocol: slmp
    bind: 0.0.0.0
    port: 5001

  # 1Eだけ
  - name: mitsubishi-1e
    protocol: mc-1e
    bind: 0.0.0.0
    port: 5002
```

`mc`は`mc-protocol`、`slmp-3e-4e`は`slmp`の別名です。

## デバイスマッピングの改造

3E／4Eのデバイスコード、ASCII表記、進数、共有メモリ領域、1Eコードを設定から差し替えられます。既存デバイスの一部だけを上書きした場合、未指定項目は既定値を継承します。

```yaml
options:
  device_map:
    "0xA8":
      area: MY_D
      one_e_code: "0x4420"
```

完全指定:

```yaml
options:
  device_map:
    "0xE0":
      name: MY
      area: MY_WORDS
      storage: word
      ascii_code: MY
      radix: 16
      one_e_code: "0x4D59"
```

ASCIIコードまたは1Eコードが重複する設定や、範囲外のSLMPコードは起動時に拒否します。

## 主なMC設定

```yaml
options:
  max_word_points: 960
  max_bit_points_binary: 7168
  max_bit_points_ascii: 3584
  max_random_points: 192
  max_random_points_extended: 96
  max_random_bit_points: 188
  max_random_bit_points_extended: 94
  max_random_write_budget: 1920
  max_random_write_budget_extended: 960
  max_blocks: 120
  max_blocks_extended: 60
  max_monitor_peers: 1024

  model_name: PLC MOCK
  model_code: 0
  initial_state: RUN
  allow_remote_control: true
  reset_no_response: true

  one_e_max_points: 256
  one_e_max_random_bit_points: 80
  one_e_max_random_word_points: 40
  one_e_max_monitor_bit_points: 40
  one_e_max_monitor_word_points: 20
  one_e_max_batch_word_read_bit_points: 128
  one_e_max_batch_word_write_bit_points: 40
  accept_any_1e_pc_number: true
  one_e_pc_number: 255
```

## 共有メモリ

すべてのエンドポイントは同じ`MemorySpace`を共有します。標準設定では次の値が相互参照できます。

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

エンドポイント単位で遅延、欠落、重複、1ビット破損を設定できます。

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
python -m compileall -q src examples tests
plcmock check --config config/example.yml --json
```

テスト対象には、1E／3E／4E、Binary／ASCII、標準／拡張デバイス形式、ランダムアクセス、複数ブロック、送信元別モニタ、リモート状態遷移、書込み原子性、実UDPソケット、プロトコル間共有メモリを含みます。

詳細は[`docs/mc-protocol.md`](docs/mc-protocol.md)を参照してください。

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

# app-plcmoc-py

Python製の、**UDP/IP上で動く拡張可能なPLCモックサーバー**です。複数のプロトコル・ポートを同時に起動し、すべてのプロトコルから同じ仮想PLCメモリを読み書きできます。

> 実機PLCの全機能を再現するものではありません。通信クライアント、監視ソフト、ゲートウェイ、上位アプリケーションの開発・自動テスト向けに、よく使うメモリ読書きを厳密に小さく実装しています。未対応コマンドへ成功応答を捏造せず、プロトコルエラーまたは無応答にします。

## 実装済みプロトコル

| エンドポイント | UDPポート例 | 実装範囲 |
|---|---:|---|
| Mitsubishi SLMP / MC binary 3E・4E | 5000 | 一括読出し `0401`、一括書込み `1401`、ワード単位・ビット単位、D/M/X/Y/W/R/ZR等 |
| OMRON FINS/UDP | 9600 | メモリエリア読出し `0101`、書込み `0102`、DM/CIO/WR/HR/AR/EM0のワード・ビット |
| Modbus ADU over UDP | 1502 | FC 01/02/03/04/05/06/15/16 |
| カスタムASCIIプラグイン例 | 15000 | `PING`、ワード/ビット読書き |

MitsubishiとOMRONはUDPを正式に利用するプロトコルです。Modbusのエンドポイントだけは、Modbus TCPのMBAP/PDU形式をUDPデータグラムへ載せる**互換拡張**であり、Modbus TCPそのものではありません。

## すぐ起動する

```bash
docker compose up --build
```

公開されるUDPポートは `5000`、`9600`、`1502`、`15000` です。設定は `config/example.yml` にあります。

ローカルPythonで動かす場合:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .
plcmock check --config config/example.yml
plcmock serve --config config/example.yml
```

## 共有メモリ

`config/example.yml` では、プロトコルごとのアドレスを次のように同じ領域へ割り当てています。

| 操作 | 共有先 |
|---|---|
| SLMP `D100` | `D` ワード領域の100番 |
| FINS `DM100` (`0x82`) | 同じ `D` ワード領域の100番 |
| Modbus holding register 100 | 同じ `D` ワード領域の100番 |
| SLMP `M100` | `M` ビット領域の100番 |
| Modbus coil 100 | 同じ `M` ビット領域の100番 |

したがって、FINSで書いたDM値をSLMPまたはModbus側から読み返す、といったクロスプロトコル試験ができます。

## 設定

最小構成:

```yaml
memory:
  words:
    D: { size: 65536 }
  bits:
    M: { size: 65536 }

endpoints:
  - name: slmp
    protocol: slmp
    bind: 0.0.0.0
    port: 5000
```

初期値も設定できます。

```yaml
memory:
  words:
    D:
      size: 65536
      default: 0
      values:
        0: 123
        100: 4660
  bits:
    M:
      size: 65536
      values:
        0: true
```

## プロトコルを改造・追加する

本体のUDP処理とプロトコル処理は分離されています。独自クラスは `ProtocolPlugin` を継承し、1データグラムを受けて応答バイト列を返します。

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

設定ファイルからロードします。

```yaml
plugin_paths:
  - ../examples

endpoints:
  - name: my-device
    protocol: my_protocol:MyProtocol
    bind: 0.0.0.0
    port: 17000
```

既存プロトコルも、次の3段階で変更できます。

1. YAMLの `options.device_map` / `options.area_map` / `options.areas` でアドレス割当だけ差し替える。
2. 組込みクラスを継承し、コマンド処理メソッドを上書きする。
3. `ProtocolPlugin` から完全な独自フレームを実装する。

### SLMPデバイスコードの差し替え例

```yaml
options:
  device_map:
    "0xA8": { name: D, area: MY_D, storage: word }
    "0x90": { name: M, area: MY_M, storage: bit }
```

### FINSメモリエリアコードの差し替え例

```yaml
options:
  area_map:
    "0x82": { name: DM, area: MY_D, unit: word }
    "0x02": { name: DM-bit, area: MY_D, unit: bit }
```

### Modbus領域の差し替え例

```yaml
options:
  areas:
    coils: M
    discrete_inputs: X
    holding_registers: D
    input_registers: INPUT
```

## 通信障害の再現

エンドポイントごとに、遅延・欠落・重複・1ビット破損を設定できます。

```yaml
faults:
  seed: 1234
  drop_rate: 0.05
  duplicate_rate: 0.01
  corrupt_rate: 0.01
  delay_ms: { min: 5, max: 80 }
```

`seed` を固定するとテストを再現しやすくなります。

## 簡易送信ツール

```bash
python examples/send_hex.py 127.0.0.1 5000 "50 00 00 ff ff 03 00 0c 00 10 00 01 04 00 00 00 00 00 a8 01 00"
```

ASCIIプラグインは `nc` がUDP対応なら直接確認できます。

```bash
printf 'READW D 0 2' | nc -u -w1 127.0.0.1 15000
```

## テスト

```bash
python -m pytest
python -m compileall -q src examples tests
plcmock check --config config/example.yml --json
```

テストには、各プロトコルのバイナリフレーム、共有メモリ、設定/プラグイン読込み、実UDPソケットによる往復を含みます。

## 実装上の境界

- SLMPはbinary 3E/4Eの一括読書きのみ。ASCIIフレーム、ランダム読書き、ラベルアクセス等は未実装です。
- FINS/UDPは `0101` / `0102` のみ。FINS/TCPのノードアドレス交換ヘッダーは扱いません。
- PLCのスキャン、ラダープログラム、I/O更新周期、保持領域の永続化は実装していません。
- 認証機能はありません。検証用ネットワークまたはローカル環境で利用してください。

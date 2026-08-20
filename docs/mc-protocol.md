# Mitsubishi MC protocol implementation

この文書は`app-plcmoc-py`のMCプロトコル実装範囲、フレーム判定、状態、設定による改造方法をまとめたものです。

## 1. 構成

```text
UDP datagram
    |
    v
McProtocol
    |-- 1E候補  -> Mc1EProtocol
    `-- 3E/4E候補 -> SlmpProtocol
                       |
                       v
                  shared MemorySpace
```

`mc-protocol`は1つのUDPポートで1E、3E、4Eを自動判定します。`mc-1e`または`slmp`を直接指定すると、対象フレームだけのエンドポイントも作成できます。

受信データグラムごとに処理が完結します。UDPの送信元`(IP, port)`はモニタ登録の識別子として使います。

## 2. フレーム判定

### 1E Binary

先頭1バイトが`00`から`09`で、少なくとも4バイトあるデータグラムを候補にします。

```text
command: 1 byte
PC number: 1 byte
monitoring timer: 2 bytes little-endian
request data: variable
```

### 1E ASCII

先頭2文字が`00`から`09`の16進ASCIIで、少なくとも8文字あるデータグラムを候補にします。

```text
command: 2 ASCII hex
PC number: 2 ASCII hex
monitoring timer: 4 ASCII hex
request data: variable
```

1Eのデバイス指定:

```text
Binary: device number 4 bytes LE + device code 2 bytes LE
ASCII : device code 4 hex chars + device number 8 hex chars
```

ASCIIのデバイス番号は、D/M/T/Cを含め通信上は16進として解析します。

例: D100を2ワード読み出すASCII要求のデータ部

```text
4420 00000064 02 00
^^^^ ^^^^^^^^ ^^ ^^
 D    address  count fixed
```

### 3E／4E Binary

```text
3E request subheader: 50 00
4E request subheader: 54 00
3E response subheader: D0 00
4E response subheader: D4 00
```

3Eはネットワーク番号から要求データ長までのヘッダ、4Eはさらにシリアル番号と予約領域を持ちます。応答では要求の経路情報と4Eシリアル番号を保持します。

### 3E／4E ASCII

```text
3E request subheader: "5000"
4E request subheader: "5400"
3E response subheader: "D000"
4E response subheader: "D400"
```

ASCII入力は大文字・小文字を区別せず、内部で大文字へ正規化します。

## 3. 3E／4Eコマンド

| コマンド | 機能 | 主なサブコマンド |
|---:|---|---|
| `0101` | 形名読出し | `0000` |
| `0401` | 一括読出し | `0000`/`0001`/`0002`/`0003` |
| `1401` | 一括書込み | `0000`/`0001`/`0002`/`0003` |
| `0403` | ランダム読出し | `0000`/`0002` |
| `1402` | ランダム書込み | `0000`/`0001`/`0002`/`0003` |
| `0801` | モニタ登録 | `0000`/`0002` |
| `0802` | モニタ実行 | `0000` |
| `0406` | 複数ブロック読出し | `0000`/`0002` |
| `1406` | 複数ブロック書込み | `0000`/`0002` |
| `1001` | リモートRUN | `0000` |
| `1002` | リモートSTOP | `0000` |
| `1003` | リモートPAUSE | `0000` |
| `1005` | ラッチクリア | `0000` |
| `1006` | リモートリセット | `0000` |
| `0619` | ループバックテスト | `0000` |
| `1617` | エラークリア | `0000` |

`0000`／`0001`は標準デバイス指定、`0002`／`0003`は拡張幅のデバイス指定です。`0001`／`0003`はビット単位アクセスを表します。`008x`系の拡張指定は拒否します。

### 書込みの原子性

ランダム書込みと複数ブロック書込みは、次の順序で処理します。

1. フレーム全体を解析する。
2. すべてのデバイス、点数、データ幅を検証する。
3. 全対象の範囲を読出しで事前検証する。
4. 問題がなければ書込みを反映する。

後半の対象が範囲外でも、前半だけが更新されることはありません。

## 4. 1Eコマンド

| コマンド | 機能 |
|---:|---|
| `00` | ビット一括読出し |
| `01` | ワード一括読出し |
| `02` | ビット一括書込み |
| `03` | ワード一括書込み |
| `04` | ビットランダム書込み |
| `05` | ワードランダム書込み |
| `06` | ビットモニタ登録 |
| `07` | ワードモニタ登録 |
| `08` | ビットモニタ実行 |
| `09` | ワードモニタ実行 |

点数`00`は256点です。ASCIIのビットデータは1点1文字です。書込み要求は指定点数ぶんだけ送り、ビット読出し／モニタ応答だけは奇数点の場合に末尾へダミーOFFの`0`を付けます。Binaryのビットデータは上位ニブルが先の点、下位ニブルが次の点で、奇数点時の未使用下位ニブルは`0`でなければなりません。

ビットデバイスをワード単位で扱う場合、既定では先頭番号が16の倍数であることを要求します。`strict_bit_word_alignment: false`で緩和できます。

## 5. 応答とエラー

### 3E／4E

主に次の終了コードを使います。

| コード | 用途 |
|---:|---|
| `0000` | 正常終了 |
| `C051` | フレーム形式不正 |
| `C056` | アドレス範囲外 |
| `C059` | コマンド／サブコマンド未対応 |
| `C05B` | デバイス不正 |
| `C061` | データ値、点数、長さ不正 |

### 1E

| コード | 用途 |
|---:|---|
| `00` | 正常終了 |
| `10` | 一般要求エラー |
| `5B 10` | PC番号不一致 |

終了コード`10`は、このモックが要求形式、点数、デバイス、範囲などの異常をまとめて通知する汎用エラーです。実機CPU／通信ユニットが返す詳細診断コードを、すべて機種別に再現するものではありません。

不完全な候補フレームで応答先ヘッダを安全に構築できない場合は無応答にします。

## 6. モックCPU状態

3E／4Eの`SlmpProtocol`は次の状態を保持します。

```text
cpu_state: RUN | STOP | PAUSE
last_clear_mode: 0 | 1 | 2
error_code: integer
```

初期状態は`initial_state`で指定します。リモートリセットはSTOP時のみ受理し、状態、エラー、モニタ登録を初期化します。`reset_no_response: true`では、正常なリセット要求に応答データグラムを返しません。

1Eと3E／4Eのモニタ登録は、複数のUDPクライアントが互いの登録内容を上書きしないよう、送信元`(IP, port)`ごとに保持します。これは並列テスト向けのモック設計であり、特定の実機CPU／通信ユニットにおけるグローバルな「最新登録」動作の完全再現ではありません。

この状態は通信試験用のモックであり、PLCプログラムの実行状態や実I/Oの動作を再現するものではありません。

## 7. 機種差の設定

### フレームとエンコーディング

```yaml
options:
  accepted_frames: ["1E", "3E", "4E"]
  accepted_encodings: ["binary", "ascii"]
```

対象外のフレームまたはエンコーディングは無応答にします。

### コマンド許可リスト／拒否リスト

```yaml
options:
  enabled_commands:
    - "0x0101"
    - "0x0401"
    - "0x1401"
  disabled_commands:
    - "0x0406"
    - "0x1406"

  one_e_enabled_commands: ["0x00", "0x01", "0x02", "0x03"]
  one_e_disabled_commands: []
```

コマンド値は整数、`0x`付き文字列、または16進文字列で指定できます。許可リストが省略された場合は、実装済みコマンドすべてが候補になります。拒否リストは最後に差し引かれます。

### PC番号

```yaml
options:
  accept_any_1e_pc_number: false
  one_e_pc_number: 255
```

複数を許可する場合:

```yaml
options:
  one_e_accepted_pc_numbers: [1, 2, 255]
```

## 8. デバイス定義

```yaml
options:
  device_map:
    "0xA8":
      name: D
      area: D
      storage: word
      ascii_code: D
      radix: 10
      one_e_code: "0x4420"
```

フィールド:

| フィールド | 意味 |
|---|---|
| `name` | 診断用の論理名 |
| `area` | `MemorySpace`の領域名 |
| `storage` | `word`または`bit` |
| `ascii_code` | 3E/4E ASCIIのデバイスコード |
| `radix` | 3E/4E ASCIIのデバイス番号進数、10または16 |
| `one_e_code` | 1Eの2バイトデバイスコード |

既存コードの部分上書きでは未指定値を継承します。1Eのデバイス番号は`radix`に関係なく、ワイヤ上では16進として扱います。

## 9. 独自コマンドへの拡張

組込みクラスを継承し、`_dispatch`または個別ハンドラを上書きできます。

```python
from plcmock.protocols.slmp import SlmpProtocol


class VendorMcProtocol(SlmpProtocol):
    CMD_VENDOR = 0x7001

    def _dispatch(self, frame, context):
        if frame.command == self.CMD_VENDOR:
            return self.END_OK, b"vendor-data"
        return super()._dispatch(frame, context)
```

完全に異なるUDPフレームは`ProtocolPlugin`から実装し、`module:Class`形式で設定へ登録できます。

## 10. 未対応範囲

- MC protocol over TCP
- `008x`拡張指定
- ラベルアクセス
- CPUバッファメモリ、インテリジェント機能ユニットアクセス
- ファイル、パスワード、時刻、メモリカード操作
- 1E拡張ファイルレジスタ系コマンド
- PLCスキャン、ラダー実行、物理I/O、実機固有の診断状態

未対応コマンドへ正常応答を作らないことを優先しています。

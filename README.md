# vdmate — MATE向け仮想デスクトップ・ウィンドウ一覧ツール

MATE(Marco/X11)上で仮想デスクトップを操作するツール。2つのスクリプトで構成される。

- **vdhotkey.py** — グローバルホットキー常駐デーモン。デスクトップ切替・ウィンドウ移動・一覧表示をホットキーで操作し、タスクトレイにデスクトップ番号を表示する。通常はこちらを使う。
- **vdmate.py** — ウィンドウ一覧ウィンドウ単体。vdhotkey.py から起動するほか、単独で呼び出してもよい。

Windows 向けの AutoHotkey 版 [virtual-desktop-ahk](https://github.com/funa-take/virtual-desktop-ahk) を参考に作成した。

## 必要なもの

以下は Linux Mint 22.3 / MATE 1.26.2 環境では標準で導入済み。

- Python 3
- PyGObject（GTK3）
- python-xlib

## 起動方法

### vdhotkey.py（通常の使い方）

```bash
python3 /home/user/bin/virtual-desktop-mate/vdhotkey.py
```

**スタートアップ登録**: MATEの「スタートアップアプリケーション」→「追加」→ コマンドに上記を入力。

**多重起動防止**: 既に起動中の場合、2つ目の起動はエラーメッセージを出力して終了する。

**注意**: vdhotkey.py が担うホットキーを MATE 側にも登録していると二重発火する。下記「MATE側バインドの無効化」を参照。

### vdmate.py（単体使用）

vdhotkey.py を使わず、vdmate.py をホットキーから直接起動することもできる。
デスクトップ切替・ウィンドウ移動のホットキーは MATE 側で設定する必要がある（後述）。

```bash
python3 /home/user/bin/virtual-desktop-mate/vdmate.py
```

## ホットキー一覧（vdhotkey.py）

| キー | 動作 |
|---|---|
| Ctrl+Shift+\ (JIS配列の両方の\キー) | ウィンドウ一覧を表示 |
| Alt+Q | ウィンドウ一覧を表示 |
| Ctrl+Super+U / I / O / J / K / L / M / , / . | デスクトップ 1〜9 へ切替 |
| Ctrl+Shift+Super+同キー | アクティブウィンドウを各デスクトップへ移動 |

ホットキーは `config.py` の `HOTKEYS` で変更できる。

## MATE側バインドの無効化（vdhotkey.py使用時）

vdhotkey.py が上記ホットキーを X11 レベルでグラブするため、MATE 側に同じキーが登録されていると
二重発火する。以下のコマンドで無効化する。

```bash
# デスクトップ切替（switch-to-workspace-N）
for i in 1 2 3 4 5 6 7 8 9; do
  dconf write /org/mate/marco/global-keybindings/switch-to-workspace-$i "'disabled'"
done

# ウィンドウ移動（move-to-workspace-N）
for i in 1 2 3 4 5 6 7 8 9; do
  dconf write /org/mate/marco/window-keybindings/move-to-workspace-$i "'disabled'"
done

# ウィンドウ一覧を run-command（方法B）で登録していた場合（番号は実際のものに合わせる）
gsettings set org.mate.Marco.global-keybindings run-command-1 "disabled"
gsettings set org.mate.Marco.global-keybindings run-command-2 "disabled"
gsettings set org.mate.Marco.keybinding-commands command-1 ""
gsettings set org.mate.Marco.keybinding-commands command-2 ""
```

## 設定（config.py）

| 設定値 | 既定 | 内容 |
|---|---|---|
| `HIDE_CURRENT_DESKTOP_WINDOW` | `False` | 現在のデスクトップのウィンドウを一覧から隠すか |
| `UNPIN_RESTORE_TO_ORIGINAL_DESKTOP` | `True` | ピン留め解除時、ピン留め前のデスクトップへ戻すか |
| `CENTER_ON_PRIMARY_MONITOR` | `True` | 一覧ウィンドウをプライマリモニタ中央に固定するか |
| `ACTIVATE_AFTER_MOVE_PIN` | `True` | Move/Pin/Gather後、対象ウィンドウを自動アクティブ化するか |
| `TRAY_ICON_DIR` | `""` | トレイ用アイコン（`1.ico`〜）のディレクトリ。空 = `icons/` サブディレクトリ |
| `SHOW_DESKTOP_NAME` | `True` | デスクトップ切替時に番号をOSD表示するか |
| `SHOW_DESKTOP_NAME_TIME` | `500` | OSDの表示時間（ミリ秒） |
| `ACTIVATE_TOP_ON_SWITCH` | `True` | 切替後、切替先の最前面ウィンドウを自動アクティブ化するか（vdhotkey.py経由の切替のみ） |
| `HOTKEYS` | 上記一覧参照 | グローバルホットキー定義（`[修飾キー, ...], keysym名, アクション, 引数` のリスト） |

## 一覧内のキー操作

| キー | 動作 |
|---|---|
| 検索ボックスに入力 | `[1-9 or *]` でWS番号/ピン留めの絞り込み、続けてキーワードでタイトル・実行ファイル名のAND絞り込み |
| ↑ / ↓ / PageUp / PageDown | 選択移動 |
| Enter | 選択ウィンドウを現在のデスクトップへ移動（Moveと同じ） |
| A（検索ボックスでは Alt+A） | 選択ウィンドウをアクティブ化し、そのデスクトップへ切替 |
| クリック | 行をクリックしてアクティブ化（Aと同じ） |
| G（検索ボックスでは Alt+G） | 表示中の全ウィンドウを現在のデスクトップへ集約 |
| P（検索ボックスでは Alt+P） | 選択ウィンドウのピン留め/解除（全デスクトップ表示の切替） |
| Shift+（Move/Pin/Gather操作） | 実行後に一覧を閉じずそのまま継続する |
| Escape / フォーカス喪失 | 一覧を閉じる |

検索ボックスでは文字キーが検索語の入力に使われるため、A/G/P の単独キーは
TreeView にフォーカスがある場合のみ有効。検索ボックスからは Alt 併用で呼び出す。

## vdmate.py 単体使用時のMATEショートカット登録

vdhotkey.py を使わず vdmate.py をホットキーから直接起動する場合の設定。

### 方法A: カスタムショートカット（`\` を使わない場合）

GUIの場合: 「キーボードショートカット」(`mate-keybinding-properties`) →
「カスタムショートカットを追加」→ 名前(任意) / コマンド
(`python3 /home/user/bin/virtual-desktop-mate/vdmate.py`) / キー割付(例: `Alt+Q`)。

dconf コマンドの場合（使用中の番号は `dconf list /org/mate/desktop/keybindings/` で確認）:

```bash
dconf write /org/mate/desktop/keybindings/custom1/name "'Window List (vdmate)'"
dconf write /org/mate/desktop/keybindings/custom1/action "'python3 /home/user/bin/virtual-desktop-mate/vdmate.py'"
dconf write /org/mate/desktop/keybindings/custom1/binding "'<Alt>q'"
```

### 方法B: Marcoのrun-commandキーバインド（`Ctrl+Shift+\` を使う場合）

`mate-settings-daemon` は `backslash`/`bar`/`underscore` を含むキーバインド文字列を拒否する制限がある。
Marco 自身の「コマンド実行キーバインド」(`run-command-N`) はこの制限を受けない。

```bash
gsettings set org.mate.Marco.keybinding-commands command-1 \
  "python3 /home/user/bin/virtual-desktop-mate/vdmate.py"
gsettings set org.mate.Marco.global-keybindings run-command-1 \
  "<Primary><Shift>backslash"
```

**注意: JIS(106/109)キーボードには `\` にあたる物理キーが2つある。**
`backslash`(keycode 97: JIS配列の「¥」位置) と `bar`(keycode 132: 国際配列側の「\|」) は別物として
扱われるため、両方で動くようにするには2番目のキーも登録する。

```bash
gsettings set org.mate.Marco.keybinding-commands command-2 \
  "python3 /home/user/bin/virtual-desktop-mate/vdmate.py"
gsettings set org.mate.Marco.global-keybindings run-command-2 \
  "<Primary><Shift>bar"
```

> **vdhotkey.py を使う場合は方法A/Bは不要。** vdhotkey.py は X11 の `root.grab_key()` で
> 直接グラブするため、`mate-settings-daemon` の制限を受けずに `\` キーも含めて両方登録できる。

## 既知の制約

- **MATEショートカットのコマンド指定に `~` は使えない。**
  コマンドはシェルを経由せず直接実行されるため `~` は展開されない。必ず絶対パスを使うこと。
- **ピン留め解除時の復帰先は `_VDMATE_PIN_ORIGIN` プロパティにウィンドウ単位で保存される。**
  一覧を閉じて後で開き直しても復帰先は保持されるが、ウィンドウ自体を閉じると消える。
- **`ACTIVATE_TOP_ON_SWITCH` は vdhotkey.py 経由の切替のみ有効。**
  MATE 標準キーによる切替は Marco が処理するため、この設定の対象外。

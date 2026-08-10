"""アプリ全体のパラメータ定義。

値の意味はそれぞれの利用箇所のdocstring/コメントを参照。
"""

# 現在のデスクトップのウィンドウを一覧から隠すか
HIDE_CURRENT_DESKTOP_WINDOW = False

# Unpin時、ピン留め前のデスクトップへ戻すか
UNPIN_RESTORE_TO_ORIGINAL_DESKTOP = True

# 一覧ウィンドウをプライマリモニタの中心に固定するか。
# False の場合はウィンドウマネージャの配置(その環境のデフォルト動作)に任せる。
# vdhotkey.py(デーモン)から起動するとWMがスタンドアロン起動と異なる配置をするため True を推奨。
CENTER_ON_PRIMARY_MONITOR = True

# Move/Pin/Gather操作後、対象ウィンドウを自動でアクティブ化するか。
# 複数選択時は一覧上から下の順にアクティブ化するため、最上段の選択行が最前面(Z軸最上位)になる。
# AHK版 VirtualDesktop2.ahk の ActionMoveWindows / ActionPinnedWindows と同じ挙動。
ACTIVATE_AFTER_MOVE_PIN = True

# ---- vdhotkey.py 関連 ----

# トレイアイコン用 .ico ファイルが格納されたディレクトリ。
# "{N}.ico" (N=1〜) が存在すればデスクトップ番号アイコンとして使用する。
# 空文字列 = vdhotkey.py と同じディレクトリの icons/ をデフォルトとして使用する。
# 別の場所を使う場合は絶対パスで指定する。
TRAY_ICON_DIR = ""

# デスクトップ切替時に番号を画面中央へ一時表示するか
SHOW_DESKTOP_NAME = True

# デスクトップ番号OSDの表示時間(ms)
SHOW_DESKTOP_NAME_TIME = 500

# デスクトップ切替後、切替先の最前面ウィンドウを自動アクティブ化するか
# ホットキー経由の切替のみ適用(MATE標準キーはMarcoが処理するため対象外)
ACTIVATE_TOP_ON_SWITCH = True

# グローバルホットキー定義
# 形式: ([修飾キー, ...], keysym名, アクション, 引数)
#   修飾キー : "Control" / "Shift" / "Alt" / "Super"
#   keysym名 : Xlib.XK の string_to_keysym() に渡す文字列
#   アクション: "show_list" / "switch_desktop" / "move_window"
#   引数     : show_list→None、switch_desktop/move_window→デスクトップ番号(0始まり)
HOTKEYS = [
    # ウィンドウ一覧表示
    # JIS配列には\キーが2つあり keysym が異なるため両方登録する(README参照)
    (["Control", "Shift"], "backslash", "show_list", None),
    (["Control", "Shift"], "bar",       "show_list", None),
    (["Alt"],              "q",         "show_list", None),

    # デスクトップ切替 (Ctrl+Super+u/i/o/j/k/l/m/,/.)  AHK版 #^u 等と同じ配列
    (["Control", "Super"], "u",      "switch_desktop", 0),
    (["Control", "Super"], "i",      "switch_desktop", 1),
    (["Control", "Super"], "o",      "switch_desktop", 2),
    (["Control", "Super"], "j",      "switch_desktop", 3),
    (["Control", "Super"], "k",      "switch_desktop", 4),
    (["Control", "Super"], "l",      "switch_desktop", 5),
    (["Control", "Super"], "m",      "switch_desktop", 6),
    (["Control", "Super"], "comma",  "switch_desktop", 7),
    (["Control", "Super"], "period", "switch_desktop", 8),

    # アクティブウィンドウをデスクトップへ移動 (Ctrl+Shift+Super+同キー)  AHK版 #+^u 等と同じ配列
    (["Control", "Shift", "Super"], "u",      "move_window", 0),
    (["Control", "Shift", "Super"], "i",      "move_window", 1),
    (["Control", "Shift", "Super"], "o",      "move_window", 2),
    (["Control", "Shift", "Super"], "j",      "move_window", 3),
    (["Control", "Shift", "Super"], "k",      "move_window", 4),
    (["Control", "Shift", "Super"], "l",      "move_window", 5),
    (["Control", "Shift", "Super"], "m",      "move_window", 6),
    (["Control", "Shift", "Super"], "comma",  "move_window", 7),
    (["Control", "Shift", "Super"], "period", "move_window", 8),
]

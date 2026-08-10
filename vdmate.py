"""仮想デスクトップ上のウィンドウ一覧・管理ツール(MATE/Marco向け)。

AutoHotkey版 VirtualDesktop2.ahk の「ウィンドウ一覧の表示・管理」機能を、
MATE(Marco/X11)上でEWMH標準機能を用いて再実装したもの。
デスクトップ切替・ウィンドウのデスクトップ移動はMATE標準ショートカットに委譲し、
本ツールはウィンドウ一覧の表示と一覧上での操作のみを担う。

起動方法: python3 vdmate.py
設定値:   config.py
"""

import struct

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402
from Xlib import X, Xatom, display  # noqa: E402
from Xlib.protocol import event  # noqa: E402

import config

# =====================================================================
# EWMH(Extended Window Manager Hints) — X11/Marcoとの通信
# =====================================================================
# python-xlibを用いて、ウィンドウ一覧取得・デスクトップ操作・アイコン取得などを行う
# 薄いラッパー。Marco(MATE)を含むEWMH準拠WM全般で動作する。


class EWMH:
    def __init__(self):
        self.display = display.Display()
        self.root = self.display.screen().root
        self._atom_cache = {}

    # --- Atom / ClientMessage 共通処理 ---
    def atom(self, name):
        if name not in self._atom_cache:
            self._atom_cache[name] = self.display.intern_atom(name)
        return self._atom_cache[name]

    def _send_client_message(self, window, message_type, data, window_target=None):
        """rootウィンドウへClientMessageを送り、WMにアクションを依頼する。

        EWMH仕様上、WMへの操作要求はルートウィンドウへSendEventするのが正しい手順
        (直接プロパティを書き換えるのではない)。
        """
        data = (data + [0] * 5)[:5]
        ev = event.ClientMessage(
            window=window_target if window_target is not None else window,
            client_type=message_type,
            data=(32, data),
        )
        mask = X.SubstructureNotifyMask | X.SubstructureRedirectMask
        self.root.send_event(ev, event_mask=mask)
        self.display.flush()

    def _get_property(self, window, prop_name, prop_type=Xatom.CARDINAL):
        prop = window.get_full_property(self.atom(prop_name), prop_type)
        return prop.value if prop else None

    # --- デスクトップ情報 ---
    def get_current_desktop(self):
        value = self._get_property(self.root, "_NET_CURRENT_DESKTOP")
        return value[0] if value else 0

    def switch_desktop(self, desktop_number):
        self._send_client_message(self.root, self.atom("_NET_CURRENT_DESKTOP"), [desktop_number])

    # --- ウィンドウ一覧 ---
    def list_client_windows(self):
        """マネージ対象ウィンドウをZ順(最前面が先頭)で返す。

        _NET_CLIENT_LIST_STACKING は「下→上」のスタッキング順なので反転する。
        一覧をデスクトップ番号で安定ソートしても、同一デスクトップ内では
        この順序(=直近に使用したウィンドウが先頭)が保たれる。
        """
        value = self._get_property(self.root, "_NET_CLIENT_LIST_STACKING", Xatom.WINDOW)
        if not value:
            return []
        return [
            self.display.create_resource_object("window", wid) for wid in reversed(value)
        ]

    def get_window_desktop(self, window):
        value = self._get_property(window, "_NET_WM_DESKTOP")
        return value[0] if value else 0

    def move_window_to_desktop(self, window, desktop_number):
        self._send_client_message(
            window, self.atom("_NET_WM_DESKTOP"), [desktop_number, 2]  # source=2(pager)
        )

    def get_window_title(self, window):
        title = self._get_property(window, "_NET_WM_NAME", self.atom("UTF8_STRING"))
        if title:
            return bytes(title).decode("utf-8", errors="replace")
        title = self._get_property(window, "WM_NAME", Xatom.STRING)
        if title:
            return bytes(title).decode("latin-1", errors="replace")
        return ""

    def get_window_pid(self, window):
        value = self._get_property(window, "_NET_WM_PID")
        return value[0] if value else None

    def get_exe_name(self, pid):
        """pidからexe(コマンド)名を取得する。検索フィルタ用。"""
        if not pid:
            return ""
        try:
            with open(f"/proc/{pid}/comm") as f:
                return f.read().strip()
        except OSError:
            return ""

    # --- ウィンドウ状態(ピン留め=sticky 等) ---
    def get_window_states(self, window):
        value = self._get_property(window, "_NET_WM_STATE", Xatom.ATOM)
        return set(value) if value else set()

    def is_sticky(self, window):
        return self.atom("_NET_WM_STATE_STICKY") in self.get_window_states(window)

    def skips_taskbar(self, window):
        """パネル/デスクトップ等、タスク一覧に出すべきでないウィンドウかどうか。"""
        return self.atom("_NET_WM_STATE_SKIP_TASKBAR") in self.get_window_states(window)

    def set_sticky(self, window, sticky):
        """_NET_WM_STATE_STICKY をトグルする(ピン留め相当)。

        _NET_WM_STATE のClientMessageは action: 0=remove,1=add,2=toggle。
        """
        action = 1 if sticky else 0
        self._send_client_message(
            window,
            self.atom("_NET_WM_STATE"),
            [action, self.atom("_NET_WM_STATE_STICKY"), 0, 1],  # source=1(application)
        )

    # --- Pin解除時の復帰先(独自プロパティ) ---
    # 起動ごとに状態が消えるプロセス内メモリではなく、対象ウィンドウ自身に
    # 値を持たせることで、一覧を閉じて後で開き直してもUnpin時に復帰できるようにする。
    PIN_ORIGIN_PROP = "_VDMATE_PIN_ORIGIN"

    def get_pin_origin_desktop(self, window):
        value = self._get_property(window, self.PIN_ORIGIN_PROP)
        return value[0] if value else None

    def set_pin_origin_desktop(self, window, desktop_number):
        window.change_property(
            self.atom(self.PIN_ORIGIN_PROP), Xatom.CARDINAL, 32, [desktop_number]
        )
        self.display.flush()

    def clear_pin_origin_desktop(self, window):
        window.delete_property(self.atom(self.PIN_ORIGIN_PROP))
        self.display.flush()

    # --- アクティブ化 ---
    def get_active_window(self):
        value = self._get_property(self.root, "_NET_ACTIVE_WINDOW", Xatom.WINDOW)
        if not value or value[0] == 0:
            return None
        return self.display.create_resource_object("window", value[0])

    def activate_window(self, window):
        self._send_client_message(
            window, self.atom("_NET_ACTIVE_WINDOW"), [2]  # source=2(pager/タスクバー的ツール)
            # source=2はWMのフォーカス奪取防止の対象外となり、別デスクトップの
            # ウィンドウへも確実に切替・フォーカスできる(source=1は無視されることがある)。
        )

    # --- アイコン (_NET_WM_ICON) ---
    def get_window_icons(self, window):
        """_NET_WM_ICON を [(width, height, argb_bytes), ...] のリストで返す。

        プロパティは [w0, h0, pixel0_0, pixel0_1, ..., w1, h1, ...] という
        可変長CARDINAL配列(各pixelは0xAARRGGBB)。
        """
        value = self._get_property(window, "_NET_WM_ICON")
        if not value:
            return []
        icons = []
        i = 0
        n = len(value)
        while i + 2 <= n:
            w, h = value[i], value[i + 1]
            i += 2
            count = w * h
            if count <= 0 or i + count > n:
                break
            pixels = value[i : i + count]
            argb_bytes = struct.pack(f"<{count}I", *pixels)
            icons.append((w, h, argb_bytes))
            i += count
        return icons


# =====================================================================
# アイコン変換 — _NET_WM_ICON(ARGB) → GdkPixbuf
# =====================================================================

ICON_PREFERRED_SIZE = 24


def _pick_best_icon(icons, preferred_size=ICON_PREFERRED_SIZE):
    """preferred_sizeに最も近い(かつ大きい方を優先する)アイコンを選ぶ。"""
    if not icons:
        return None
    return min(icons, key=lambda icon: (icon[0] < preferred_size, abs(icon[0] - preferred_size)))


def _argb_bytes_to_rgba(argb_bytes):
    """_NET_WM_ICONはCARDINAL(0xAARRGGBB)をlittle-endian格納したバイト列のため、
    各4バイトは (B, G, R, A) の順になっている。GdkPixbufが期待する (R, G, B, A) へ並び替える。
    """
    data = bytearray(argb_bytes)
    for i in range(0, len(data), 4):
        data[i], data[i + 2] = data[i + 2], data[i]  # B<->R を交換、Gはそのまま、Aはそのまま
    return bytes(data)


def icons_to_pixbuf(icons, preferred_size=ICON_PREFERRED_SIZE):
    """EWMH.get_window_icons()の戻り値からGdkPixbuf.Pixbufを生成する。

    アイコンが無い場合はNoneを返す(呼び出し側でテーマの汎用アイコンへフォールバックする)。
    """
    best = _pick_best_icon(icons, preferred_size)
    if best is None:
        return None
    width, height, argb_bytes = best
    rgba_bytes = _argb_bytes_to_rgba(argb_bytes)
    pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(
        GLib.Bytes.new(rgba_bytes),
        GdkPixbuf.Colorspace.RGB,
        True,  # has_alpha
        8,  # bits_per_sample
        width,
        height,
        width * 4,  # rowstride
    )
    if width != preferred_size or height != preferred_size:
        pixbuf = pixbuf.scale_simple(
            preferred_size, preferred_size, GdkPixbuf.InterpType.BILINEAR
        )
    return pixbuf


# =====================================================================
# GUI — アイコン付きウィンドウ一覧
# =====================================================================

(
    COL_PIXBUF,
    COL_DESKTOP_TEXT,
    COL_TITLE,
    COL_WINDOW_ID,
    COL_IS_PINNED,
    COL_DESKTOP_NUMBER,
    COL_SEARCH_TEXT,
) = range(7)


class WindowListWindow(Gtk.Window):
    def __init__(self):
        super().__init__(title="Window List")
        self.ewmh = EWMH()
        self.windows_by_id = {}  # window_id(int) -> Xlib window object
        self._suppress_close = False  # アクティブ化ループ中のフォーカス喪失による自動クローズを抑制

        self.set_default_size(700, 480)
        self.connect("focus-out-event", self._on_focus_out)
        self.connect("key-press-event", self._on_key_press)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_border_width(8)
        self.add(box)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("[1-9 or *] keywords")
        self.search_entry.connect("search-changed", lambda _w: self.filter_model.refilter())
        self.search_entry.connect("key-press-event", self._on_search_key_press)
        box.pack_start(self.search_entry, False, False, 0)

        self.list_store = Gtk.ListStore(GdkPixbuf.Pixbuf, str, str, int, bool, int, str)
        self.filter_model = self.list_store.filter_new()
        self.filter_model.set_visible_func(self._is_row_visible)
        self.tree_view = Gtk.TreeView(model=self.filter_model)
        self.tree_view.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE)
        self.tree_view.connect("key-press-event", self._on_tree_key_press)
        self.tree_view.connect("button-press-event", self._on_tree_button_press)

        desktop_renderer = Gtk.CellRendererText(xalign=1.0)
        desktop_column = Gtk.TreeViewColumn("WS", desktop_renderer, text=COL_DESKTOP_TEXT)
        self.tree_view.append_column(desktop_column)

        icon_renderer = Gtk.CellRendererPixbuf()
        icon_column = Gtk.TreeViewColumn("", icon_renderer, pixbuf=COL_PIXBUF)
        self.tree_view.append_column(icon_column)

        title_renderer = Gtk.CellRendererText()
        title_column = Gtk.TreeViewColumn("Name", title_renderer, text=COL_TITLE)
        title_column.set_expand(True)
        self.tree_view.append_column(title_column)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.add(self.tree_view)
        box.pack_start(scrolled, True, True, 0)

        hint_label = Gtk.Label(
            label=(
                "[Enter]: Move Selected Windows Here   [G / Alt+G]: Gather Windows\n"
                "[Click / A / Alt+A]: Activate Selected Window   "
                "[P / Alt+P]: (Un)Pin Selected Window   [Shift]: Keep List Open"
            )
        )
        hint_label.set_xalign(0.0)
        box.pack_start(hint_label, False, False, 0)

        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        exit_button = Gtk.Button(label="Exit")
        exit_button.connect("clicked", lambda _btn: self.close())
        move_button = Gtk.Button(label="Move")
        move_button.connect("clicked", lambda _btn: self.action_move(keep_open=False))
        button_box.pack_start(exit_button, False, False, 0)
        button_box.pack_start(move_button, False, False, 0)
        box.pack_start(button_box, False, False, 0)

        self._place_on_primary_monitor()
        self.show_all()
        self._own_window_id = self.get_window().get_xid()
        self.reload()
        self.search_entry.grab_focus()

    # --- ウィンドウ配置 ---
    def _place_on_primary_monitor(self):
        """CENTER_ON_PRIMARY_MONITOR が True のとき、プライマリモニタ中心へ配置する。
        False のときは何もせず WM の配置に委ねる。
        """
        if not config.CENTER_ON_PRIMARY_MONITOR:
            return
        gdk_display = Gdk.Display.get_default()
        monitor = gdk_display.get_primary_monitor() or gdk_display.get_monitor(0)
        if monitor is None:
            return  # モニタ情報が取れない場合は WM 任せにフォールバック
        geo = monitor.get_geometry()
        width, height = self.get_size()  # 未表示時は set_default_size の値(700x480)を返す
        x = geo.x + (geo.width - width) // 2
        y = geo.y + (geo.height - height) // 2
        self.move(x, y)

    # --- 一覧構築 ---
    def _selected_visible_index(self):
        """再描画前に選択されていた行の表示上のインデックスを返す(無ければNone)。"""
        _model, paths = self.tree_view.get_selection().get_selected_rows()
        if not paths:
            return None
        return paths[0].get_indices()[0]

    def reload(self):
        selected_index = self._selected_visible_index()
        current_desktop = self.ewmh.get_current_desktop()
        self.set_title(f"Window List — Current Desktop: {current_desktop + 1}")
        self.list_store.clear()
        self.windows_by_id.clear()

        entries = []
        for window in self.ewmh.list_client_windows():
            if window.id == self._own_window_id:
                continue
            if self.ewmh.skips_taskbar(window):
                continue
            title = self.ewmh.get_window_title(window)
            if not title:
                continue

            desktop = self.ewmh.get_window_desktop(window)
            is_pinned = self.ewmh.is_sticky(window)
            if not is_pinned and config.HIDE_CURRENT_DESKTOP_WINDOW and desktop == current_desktop:
                continue

            entries.append((window, title, desktop, is_pinned))

        entries.sort(key=lambda e: (e[3], e[2]))  # sticky(*)は最後、その他はdesktop番号順

        for window, title, desktop, is_pinned in entries:
            self.windows_by_id[window.id] = window
            icons = self.ewmh.get_window_icons(window)
            pixbuf = icons_to_pixbuf(icons)
            desktop_text = "*" if is_pinned else str(desktop + 1)
            exe = self.ewmh.get_exe_name(self.ewmh.get_window_pid(window))
            search_text = f"{title} {exe}".lower()
            # sticky化したウィンドウは_NET_WM_DESKTOPが0xFFFFFFFF(全デスクトップ)になり
            # 32bit列に収まらないため、is_pinned行では使わない値(-1)に正規化する。
            desktop_for_column = -1 if is_pinned else desktop
            self.list_store.append(
                [pixbuf, desktop_text, title, window.id, is_pinned, desktop_for_column, search_text]
            )

        self.filter_model.refilter()
        count = len(self.filter_model)
        if count:
            # 再描画前と同じ行番号を維持する。行数が減っていれば最後の行にクランプする。
            target = 0 if selected_index is None else min(selected_index, count - 1)
            self.tree_view.get_selection().select_path(Gtk.TreePath.new_from_indices([target]))

        # reload() はリストを表示し続ける操作なので、source=2(pager) で自己アクティブ化して
        # Marco のフォーカス奪取防止に関わらずリストへフォーカスを維持・復帰させる。
        gdk_win = self.get_window()
        if gdk_win:
            xwin = self.ewmh.display.create_resource_object("window", gdk_win.get_xid())
            self.ewmh.activate_window(xwin)

    # --- 検索フィルタ ---
    # AHKのFilterWindowList相当の判定をGtk.TreeModelFilterのvisible_funcとして実現する
    # (一覧の手動再構築は行わない)。
    def _is_row_visible(self, model, iter_, _data):
        query = self.search_entry.get_text().strip()
        if not query:
            return True

        tokens = query.split()
        desktop_token = None
        if tokens and (tokens[0] == "*" or tokens[0].isdigit()):
            desktop_token = tokens[0]
            tokens = tokens[1:]

        if desktop_token is not None:
            is_pinned = model[iter_][COL_IS_PINNED]
            if desktop_token == "*":
                if not is_pinned:
                    return False
            else:
                display_desktop = -1 if is_pinned else model[iter_][COL_DESKTOP_NUMBER] + 1
                if display_desktop != int(desktop_token):
                    return False

        haystack = model[iter_][COL_SEARCH_TEXT]
        return all(tok.lower() in haystack for tok in tokens)

    # --- キーボード操作(検索Entryにフォーカスがある間) ---
    # 文字キー(a/m/g/p)は検索語として入力できる必要があるため、検索Entry側では
    # Alt併用時のみアクションキーとして扱う(plainのA/M/G/PはTreeView側でのみ有効)。
    def _on_search_key_press(self, _widget, event_):
        if event_.keyval == Gdk.KEY_Down:
            self._move_selection(1)
            return True
        if event_.keyval == Gdk.KEY_Up:
            self._move_selection(-1)
            return True
        if event_.keyval == Gdk.KEY_Page_Down:
            self._move_selection(10)
            return True
        if event_.keyval == Gdk.KEY_Page_Up:
            self._move_selection(-10)
            return True
        if event_.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.action_move(keep_open=self._shift_held(event_))
            return True

        if event_.state & Gdk.ModifierType.MOD1_MASK:  # Alt併用
            return self._dispatch_action_key(event_)
        return False

    # --- キーボード操作(TreeViewにフォーカスがある間。Altは不要) ---
    def _on_tree_key_press(self, _widget, event_):
        if event_.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.action_move(keep_open=self._shift_held(event_))
            return True
        return self._dispatch_action_key(event_)

    def _dispatch_action_key(self, event_):
        keyval = Gdk.keyval_to_lower(event_.keyval)
        shift = self._shift_held(event_)
        if keyval == Gdk.KEY_a:
            self.action_activate(self._focused_window())
            return True
        if keyval == Gdk.KEY_m:
            self.action_move(keep_open=shift)
            return True
        if keyval == Gdk.KEY_g:
            self.action_gather()
            return True
        if keyval == Gdk.KEY_p:
            self.action_toggle_pin(keep_open=shift)
            return True
        return False

    @staticmethod
    def _shift_held(event_):
        return bool(event_.state & Gdk.ModifierType.SHIFT_MASK)

    def _move_selection(self, delta):
        count = len(self.filter_model)
        if count == 0:
            return
        selection = self.tree_view.get_selection()
        _model, selected_paths = selection.get_selected_rows()
        current = selected_paths[-1].get_indices()[0] if selected_paths else 0
        next_index = min(max(current + delta, 0), count - 1)
        path = Gtk.TreePath.new_from_indices([next_index])
        selection.unselect_all()
        selection.select_path(path)
        self.tree_view.set_cursor(path)
        self.tree_view.scroll_to_cell(path)

    # --- 一覧アクションのための選択取得 ---
    def _window_for_filter_iter(self, filter_iter):
        child_iter = self.filter_model.convert_iter_to_child_iter(filter_iter)
        row = self.list_store[child_iter]
        return self.windows_by_id.get(row[COL_WINDOW_ID]), row

    def _selected_entries(self):
        """選択中の各行を (window, is_pinned) のリストで返す。"""
        _model, paths = self.tree_view.get_selection().get_selected_rows()
        entries = []
        for path in paths:
            window, row = self._window_for_filter_iter(self.filter_model.get_iter(path))
            if window is not None:
                entries.append((window, row[COL_IS_PINNED]))
        return entries

    def _visible_windows(self):
        """検索で絞り込まれた現在表示中の全ウィンドウを返す(Gather対象)。"""
        windows = []
        it = self.filter_model.get_iter_first()
        while it is not None:
            window, _row = self._window_for_filter_iter(it)
            if window is not None:
                windows.append(window)
            it = self.filter_model.iter_next(it)
        return windows

    def _focused_window(self):
        """キーボード操作の対象とする1件(カーソル行、無ければ選択先頭)を返す。"""
        path, _column = self.tree_view.get_cursor()
        if path is None:
            _model, paths = self.tree_view.get_selection().get_selected_rows()
            if not paths:
                return None
            path = paths[0]
        window, _row = self._window_for_filter_iter(self.filter_model.get_iter(path))
        return window

    # --- 一覧アクション ---
    def _activate_window(self, window, skip_desktop_switch=False):
        """ウィンドウをアクティブ化する（必要に応じてデスクトップ切替も行う。close()は含まない）。

        _NET_ACTIVE_WINDOWのClientMessageだけではMarcoが別デスクトップへ
        自動切替してくれない場合があるため、明示的にデスクトップを切り替える。

        skip_desktop_switch=True のときはデスクトップ切替を行わない。以下の場合に使う:
        - move/gather: move_window_to_desktop() は非同期のため直後の get_window_desktop() が
          移動前の値を返すことがある。切替先は現在のデスクトップなので切替自体も不要。
        - pin: set_sticky() も非同期のため直後の is_sticky() が False を返すことがある。
          stickyウィンドウは全デスクトップに存在するので切替不要。
        """
        if not skip_desktop_switch and not self.ewmh.is_sticky(window):
            desktop = self.ewmh.get_window_desktop(window)
            if desktop != self.ewmh.get_current_desktop():
                self.ewmh.switch_desktop(desktop)
        self.ewmh.activate_window(window)

    def _activate_all(self, windows, keep_open, skip_desktop_switch=False):
        """ウィンドウリストを先頭から順にアクティブ化する（先頭が最前面になる）。

        AHK版 ActionMoveWindows / ActionPinnedWindows と同じ戦略:
        一覧の下→上の順でアクティブ化することで最上段選択行がZ軸最上位(最前面)になる。
        呼び出し元は reversed() を適用して渡す(一覧下→上の順)。
        複数回アクティブ化する間は _suppress_close でフォーカス喪失による自動クローズを抑制する。
        """
        self._suppress_close = True
        for window in windows:
            self._activate_window(window, skip_desktop_switch=skip_desktop_switch)
        if keep_open:
            # _NET_ACTIVE_WINDOW はWMが非同期で処理するため即時reload()すると古いZ順になる。
            # 150ms待ってWMのスタッキング順更新後にreload()する。
            GLib.timeout_add(150, self._on_activate_all_done)
        else:
            self._finish_action(False)
            self._suppress_close = False

    def _on_activate_all_done(self):
        """_activate_all(keep_open=True)後の遅延reload。reload()がフォーカス復帰も担う。"""
        if self.get_visible():
            self.reload()
        GLib.timeout_add(100, lambda: setattr(self, "_suppress_close", False) or False)
        return False

    def action_move(self, keep_open):
        current_desktop = self.ewmh.get_current_desktop()
        # 一覧下→上の順(reversed)に移動+アクティブ化 → 最上段選択行が最前面
        entries = list(reversed(self._selected_entries()))
        for window, _is_pinned in entries:
            self.ewmh.move_window_to_desktop(window, current_desktop)
        if config.ACTIVATE_AFTER_MOVE_PIN:
            # move_window_to_desktop() は非同期のため skip_desktop_switch=True でデスクトップ読み取りをスキップ
            self._activate_all([w for w, _ in entries], keep_open, skip_desktop_switch=True)
        else:
            self._finish_action(keep_open)

    def action_activate(self, window):
        if window is None:
            return
        self._activate_window(window)
        self.close()

    def action_gather(self):
        current_desktop = self.ewmh.get_current_desktop()
        # 一覧下→上の順(reversed)に移動+アクティブ化 → 最上段ウィンドウが最前面
        windows = list(reversed(self._visible_windows()))
        for window in windows:
            self.ewmh.move_window_to_desktop(window, current_desktop)
        if config.ACTIVATE_AFTER_MOVE_PIN:
            # move_window_to_desktop() は非同期のため skip_desktop_switch=True でデスクトップ読み取りをスキップ
            self._activate_all(windows, keep_open=False, skip_desktop_switch=True)
        else:
            self.close()

    def action_toggle_pin(self, keep_open):
        # 一覧下→上の順(reversed)でPin/Unpin処理 → Pin付与ウィンドウは最上段が最前面
        entries = list(reversed(self._selected_entries()))
        pinned_windows = []  # Pin付与したウィンドウ(アクティブ化対象)
        for window, is_pinned in entries:
            if is_pinned:
                self.ewmh.set_sticky(window, False)
                origin_desktop = self.ewmh.get_pin_origin_desktop(window)
                if config.UNPIN_RESTORE_TO_ORIGINAL_DESKTOP and origin_desktop is not None:
                    self.ewmh.move_window_to_desktop(window, origin_desktop)
                self.ewmh.clear_pin_origin_desktop(window)
            else:
                self.ewmh.set_pin_origin_desktop(window, self.ewmh.get_window_desktop(window))
                self.ewmh.set_sticky(window, True)
                pinned_windows.append(window)  # Unpinはアクティブ化しない
        if config.ACTIVATE_AFTER_MOVE_PIN and pinned_windows:
            # set_sticky()は非同期のためis_sticky()が古い値を返す可能性がある。
            # skip_desktop_switch=Trueでデスクトップ切替チェックをスキップする。
            self._activate_all(pinned_windows, keep_open, skip_desktop_switch=True)
        else:
            self._finish_action(keep_open)

    def _finish_action(self, keep_open):
        if keep_open:
            self.reload()
        else:
            self.close()

    # --- イベント ---
    def _on_focus_out(self, _widget, _event):
        if self._suppress_close:
            return False
        self.close()
        return False

    def _on_key_press(self, _widget, event_):
        if event_.keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False

    def _on_tree_button_press(self, widget, event_):
        if event_.type == Gdk.EventType.BUTTON_PRESS and event_.button == 1:
            path_info = widget.get_path_at_pos(int(event_.x), int(event_.y))
            if path_info:
                window, _row = self._window_for_filter_iter(self.filter_model.get_iter(path_info[0]))
                self.action_activate(window)
        return False


# =====================================================================
# エントリーポイント
# =====================================================================


def main():
    win = WindowListWindow()
    win.connect("destroy", Gtk.main_quit)
    Gtk.main()


if __name__ == "__main__":
    main()

"""仮想デスクトップ管理ツール — グローバルホットキー常駐デーモン (MATE/Marco/X11向け)。

X11 の root.grab_key() によるパッシブグラブでホットキーを登録し、GTK メインループで常駐する。
mate-settings-daemon のカスタムショートカット制限 (backslash/bar 等を拒否) を回避できる。

起動方法: python3 vdhotkey.py
設定値:   config.py の HOTKEYS
"""

import fcntl
import math
import os
import signal
import sys
import tempfile
import warnings

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

from Xlib import X, XK

import config
from vdmate import EWMH, WindowListWindow


_MODIFIER_MAP = {
    "Control": X.ControlMask,
    "Shift":   X.ShiftMask,
    "Alt":     X.Mod1Mask,
    "Super":   X.Mod4Mask,
}

# CapsLock / NumLock が押されていても反応するよう、全組合せをグラブする
_EXTRA_MASKS = (0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask)


def _parse_modifiers(mod_names):
    mask = 0
    for name in mod_names:
        mask |= _MODIFIER_MAP[name]
    return mask


# =====================================================================
# インスタンスロック — flock による多重起動防止
# =====================================================================

def _acquire_instance_lock():
    """flock で多重起動を防ぐ。

    ロックファイルオブジェクトを返す(GC されるとロックが解放されるため保持必須)。
    既に起動中なら None を返す。ロックファイルは /tmp に DISPLAY 単位で作成する。
    """
    display_id = os.environ.get("DISPLAY", ":0").replace(":", "").replace(".", "_")
    lock_path = os.path.join(tempfile.gettempdir(), f"vdhotkey_{display_id}.lock")
    f = open(lock_path, "w")
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return f
    except (IOError, OSError):
        f.close()
        return None


# =====================================================================
# OSD — デスクトップ番号を一時表示
# =====================================================================

class OSD(Gtk.Window):
    _CSS = b"""
    label {
        font-size: 48px;
        font-weight: bold;
        color: white;
        padding: 16px 52px;
        background-color: transparent;
    }
    """

    def __init__(self):
        super().__init__()
        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_type_hint(Gdk.WindowTypeHint.NOTIFICATION)
        self.set_app_paintable(True)

        # コンポジタが有効なら RGBA visual で半透明に、無効なら不透明黒にフォールバック
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        self._composited = bool(visual and screen.is_composited())
        if self._composited:
            self.set_visual(visual)

        self._label = Gtk.Label()
        provider = Gtk.CssProvider()
        provider.load_from_data(self._CSS)
        self._label.get_style_context().add_provider(
            provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self.add(self._label)
        self.connect("draw", self._on_draw)
        self._timer_id = None

    def _on_draw(self, _widget, cr):
        """角丸矩形の背景を描画する(子ウィジェットはGTKが続けて描画する)。"""
        w = self.get_allocated_width()
        h = self.get_allocated_height()
        r = min(14, w // 2, h // 2)
        alpha = 0.78 if self._composited else 1.0
        cr.new_sub_path()
        cr.arc(r,     r,     r, math.pi,       3 * math.pi / 2)
        cr.arc(w - r, r,     r, -math.pi / 2,  0)
        cr.arc(w - r, h - r, r, 0,              math.pi / 2)
        cr.arc(r,     h - r, r, math.pi / 2,   math.pi)
        cr.close_path()
        cr.set_source_rgba(0, 0, 0, alpha)
        cr.fill()
        return False  # GTK に子ウィジェットの描画を続けさせる

    def show_message(self, text):
        self._label.set_text(text)
        self.show_all()
        # 全デスクトップに表示する。高速にデスクトップを連続切替しても
        # OSDが切替先に追従して見えるようにする(stick はアンマップで解除され得るため毎回呼ぶ)。
        self.stick()
        self.set_keep_above(True)
        GLib.idle_add(self._center)
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
        self._timer_id = GLib.timeout_add(config.SHOW_DESKTOP_NAME_TIME, self._hide)

    def _center(self):
        gdk_disp = Gdk.Display.get_default()
        monitor = gdk_disp.get_primary_monitor() or gdk_disp.get_monitor(0)
        if monitor:
            geo = monitor.get_geometry()
            w, h = self.get_size()
            self.move(geo.x + (geo.width - w) // 2, geo.y + (geo.height - h) // 2)
        return False

    def _hide(self):
        self.hide()
        self._timer_id = None
        return False


# =====================================================================
# ホットキーデーモン
# =====================================================================


class HotkeyDaemon:
    def __init__(self):
        self.ewmh = EWMH()
        self._lock_file = _acquire_instance_lock()
        if self._lock_file is None:
            raise RuntimeError("vdhotkey は既に起動しています")
        self._win_list = None
        self._keymap = []  # [(base_mod, keycode, action, arg), ...]
        self._build_keymap()
        self._grab_all()
        # _NET_CURRENT_DESKTOP の変更を受け取る(外部からの切替追従用)
        self.ewmh.root.change_attributes(event_mask=X.PropertyChangeMask)
        self.ewmh.display.flush()
        GLib.io_add_watch(self.ewmh.display.fileno(), GLib.IO_IN, self._on_x_event)
        # トレイアイコン(_tray_menu はGC防止のためインスタンス変数に保持)
        self._tray, self._tray_menu = self._setup_tray()
        self._update_tray(self.ewmh.get_current_desktop())
        # デスクトップ切替OSDと自動アクティブ化
        self._osd = OSD() if config.SHOW_DESKTOP_NAME else None
        self._own_switch = False  # 自daemonが切替を発行したか

    # ---- キーマップ構築 ----

    def _build_keymap(self):
        for mod_names, keysym_name, action, arg in config.HOTKEYS:
            sym = XK.string_to_keysym(keysym_name)
            if sym == 0:
                print(f"vdhotkey: unknown keysym '{keysym_name}'", file=sys.stderr)
                continue
            kc = self.ewmh.display.keysym_to_keycode(sym)
            if kc == 0:
                print(
                    f"vdhotkey: '{keysym_name}' はこのキーボードにマップされていません",
                    file=sys.stderr,
                )
                continue
            self._keymap.append((_parse_modifiers(mod_names), kc, action, arg))

    # ---- グラブ / アングラブ ----

    def _grab_all(self):
        errors = []

        def _on_error(error, _request):
            errors.append(error)

        self.ewmh.display.set_error_handler(_on_error)
        for base_mod, kc, _action, _arg in self._keymap:
            for extra in _EXTRA_MASKS:
                self.ewmh.root.grab_key(
                    kc,
                    base_mod | extra,
                    True,             # owner_events: グラブ中もウィンドウへイベントを届ける
                    X.GrabModeAsync,
                    X.GrabModeAsync,
                )
        self.ewmh.display.sync()
        self.ewmh.display.set_error_handler(None)
        for error in errors:
            print(f"vdhotkey: grab_key 失敗: {error}", file=sys.stderr)

    def ungrab_all(self):
        for base_mod, kc, _action, _arg in self._keymap:
            for extra in _EXTRA_MASKS:
                self.ewmh.root.ungrab_key(kc, base_mod | extra)
        self.ewmh.display.flush()

    # ---- トレイアイコン ----

    def _setup_tray(self):
        # Gtk.StatusIcon は GTK3.14 以降 deprecated だが MATE の XEmbed トレイでは
        # 現状これが最も安定した実装のため使用する。警告のみ抑制する。
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            tray = Gtk.StatusIcon()
            tray.set_from_icon_name("preferences-desktop")
            tray.set_visible(True)
        tray.connect("activate",   lambda _icon: self._show_list())
        tray.connect("popup-menu", self._on_tray_popup)

        menu = Gtk.Menu()
        item_list = Gtk.MenuItem(label="Window List")
        item_list.connect("activate", lambda _: self._show_list())
        menu.append(item_list)
        menu.append(Gtk.SeparatorMenuItem())
        item_exit = Gtk.MenuItem(label="Exit")
        item_exit.connect("activate", lambda _: Gtk.main_quit())
        menu.append(item_exit)
        menu.show_all()
        return tray, menu

    def _on_tray_popup(self, icon, button, time):
        self._tray_menu.popup(
            None, None, Gtk.StatusIcon.position_menu, icon, button, time
        )

    def _update_tray(self, desktop_index):
        n = desktop_index + 1
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            # 番号アイコンを探す: config で指定 → 空なら icons/ サブディレクトリ → なければテーマアイコンを維持
            icon_dir = config.TRAY_ICON_DIR or os.path.join(_SCRIPT_DIR, "icons")
            icon_path = os.path.join(icon_dir, f"{n}.ico")
            if icon_path and os.path.exists(icon_path):
                try:
                    pixbuf = GdkPixbuf.Pixbuf.new_from_file(icon_path)
                    self._tray.set_from_pixbuf(pixbuf)
                except Exception:
                    pass
            self._tray.set_tooltip_text(f"Desktop {n}")

    # ---- X イベント受信 ----

    def _on_x_event(self, _fd, _condition):
        while self.ewmh.display.pending_events():
            ev = self.ewmh.display.next_event()
            if ev.type == X.KeyPress:
                self._dispatch(ev)
            elif ev.type == X.PropertyNotify:
                if ev.atom == self.ewmh.atom("_NET_CURRENT_DESKTOP"):
                    GLib.idle_add(self._on_desktop_changed)
        return True  # ウォッチを継続

    def _on_desktop_changed(self):
        desktop = self.ewmh.get_current_desktop()
        self._update_tray(desktop)
        # 外部からの切替(MATE標準キーなど)のみOSDを表示する。
        # ホットキー経由は _run_action でキーを押した瞬間に表示済みのため重複しない。
        if self._osd is not None and not self._own_switch:
            self._osd.show_message(str(desktop + 1))
        if self._own_switch and config.ACTIVATE_TOP_ON_SWITCH:
            self._activate_top_on(desktop)
        self._own_switch = False
        return False

    def _dispatch(self, ev):
        # CapsLock / NumLock のビットを除いて比較
        state = ev.state & ~(X.LockMask | X.Mod2Mask)
        for base_mod, kc, action, arg in self._keymap:
            if ev.detail == kc and state == base_mod:
                GLib.idle_add(self._run_action, action, arg)
                return

    # ---- アクション ----

    def _run_action(self, action, arg):
        if action == "show_list":
            self._show_list()
        elif action == "switch_desktop":
            self._own_switch = True
            if self._osd is not None:
                self._osd.show_message(str(arg + 1))
            self.ewmh.switch_desktop(arg)
        elif action == "move_window":
            self._move_window(arg)
        return False  # idle_add を停止

    def _show_list(self):
        """ウィンドウ一覧を表示する。既に表示中ならフォーカスを前面に出す。"""
        if self._win_list is not None and self._win_list.get_visible():
            gdk_win = self._win_list.get_window()
            if gdk_win:
                xwin = self.ewmh.display.create_resource_object("window", gdk_win.get_xid())
                self.ewmh.activate_window(xwin)
            return
        if self._win_list is not None:
            self._win_list.destroy()
        self._win_list = WindowListWindow()
        self._win_list.connect("destroy", lambda _w: setattr(self, "_win_list", None))
        # デーモンから生成したウィンドウはOSのスタートアップ通知がないため、
        # WMがMapRequestを処理し終えた後(~100ms)に改めてアクティブ化する。
        GLib.timeout_add(100, self._focus_win_list)

    def _focus_win_list(self):
        if self._win_list is not None and self._win_list.get_visible():
            gdk_win = self._win_list.get_window()
            if gdk_win:
                xwin = self.ewmh.display.create_resource_object("window", gdk_win.get_xid())
                self.ewmh.activate_window(xwin)
        return False

    def _move_window(self, desktop_number):
        """アクティブウィンドウを指定デスクトップへ移動する。"""
        win = self.ewmh.get_active_window()
        if win is None:
            return
        current = self.ewmh.get_current_desktop()
        self.ewmh.move_window_to_desktop(win, desktop_number)
        # 移動後、元のデスクトップの最前面ウィンドウをアクティブ化してフォーカスを保つ
        if desktop_number != current:
            self._activate_top_on(current)

    def _activate_top_on(self, desktop_index):
        """指定デスクトップの最前面ウィンドウをアクティブ化する。"""
        for window in self.ewmh.list_client_windows():
            if self.ewmh.skips_taskbar(window):
                continue
            if self.ewmh.is_sticky(window):
                continue
            if self.ewmh.get_window_desktop(window) == desktop_index:
                self.ewmh.activate_window(window)
                return


# =====================================================================
# エントリーポイント
# =====================================================================


def main():
    try:
        daemon = HotkeyDaemon()
    except RuntimeError as e:
        print(e, file=sys.stderr)
        sys.exit(1)

    signal.signal(signal.SIGINT,  lambda *_: Gtk.main_quit())
    signal.signal(signal.SIGTERM, lambda *_: Gtk.main_quit())
    # SIGINT を Python が受け取れるよう定期的に GTK ループから Python へ制御を戻す
    GLib.timeout_add(200, lambda: True)

    Gtk.main()
    daemon.ungrab_all()


if __name__ == "__main__":
    main()

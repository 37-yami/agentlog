#!/usr/bin/env python3
# agentlog_gui - a small tkinter UI for agentlog.
#
# A window to control the agentlog daemon (status / start / stop / restart),
# toggle logon autostart, browse & open the exported .txt files, and trigger a
# manual scan. The window can be minimized to the system tray (via a ctypes
# tray icon, no third-party dependencies).
#
# Everything reuses the logic in agentlog.py, so the CLI and this GUI stay in
# sync. Run:  python agentlog_gui.py

import os
import sys
import ctypes
import threading
import tkinter as tk
from tkinter import ttk, messagebox

import agentlog as core  # reuse cmd_*/scan_once/paths

OUTPUT_ROOT = core.OUTPUT_ROOT
ICON_PATH = core.ICON_PATH  # optional .ico next to the script


# --------------------------------------------------------------------------
# System tray (ctypes, Windows only)
# --------------------------------------------------------------------------
def _build_tray(notify, restore, quit_app):
    """Create a tray icon. notify() shows a balloon; restore/quit_app are
    callbacks marshalled to the tkinter thread. Returns (setup, teardown) or
    None if unavailable."""
    if sys.platform != "win32":
        return None
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except Exception:
        return None

    WM_USER = 0x0400
    APP_MSG = WM_USER + 1
    NIM_ADD = 0x00000000
    NIM_DELETE = 0x00000002
    NIF_MESSAGE = 0x00000001
    NIF_ICON = 0x00000002
    NIF_TIP = 0x00000004
    WM_RBUTTONUP = 0x0205
    WM_LBUTTONUP = 0x0202
    TPM_RETURNCMD = 0x0100
    HWND_MESSAGE = ctypes.c_void_p(-3)
    IDI_APPLICATION = ctypes.c_void_p(32512)
    IMAGE_ICON = 1
    LR_LOADFROMFILE = 0x00000010

    HWND = ctypes.wintypes.HWND
    HMENU = ctypes.wintypes.HMENU
    UINT = ctypes.wintypes.UINT
    BOOL = ctypes.wintypes.BOOL
    ATOM = ctypes.wintypes.ATOM

    # Proper argtypes are required on 64-bit Windows: HWND/HMENU are 64-bit
    # pointers, and ctypes' default 32-bit int marshaling would truncate them.
    user32.SetForegroundWindow.argtypes = [HWND]
    user32.SetForegroundWindow.restype = BOOL
    user32.CreatePopupMenu.restype = HMENU
    user32.AppendMenuW.argtypes = [HMENU, UINT, ctypes.wintypes.UINT, ctypes.c_wchar_p]
    user32.AppendMenuW.restype = BOOL
    user32.DestroyMenu.argtypes = [HMENU]
    user32.DestroyMenu.restype = BOOL
    user32.GetCursorPos.argtypes = [ctypes.POINTER(ctypes.wintypes.POINT)]
    user32.GetCursorPos.restype = BOOL
    user32.TrackPopupMenu.argtypes = [HMENU, UINT, ctypes.c_int, ctypes.c_int,
                                      ctypes.c_int, HWND, ctypes.c_void_p]
    user32.TrackPopupMenu.restype = UINT

    class NOTIFYICONDATA(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.wintypes.DWORD),
            ("hWnd", HWND),
            ("uID", UINT),
            ("uFlags", UINT),
            ("uCallbackMessage", UINT),
            ("hIcon", ctypes.wintypes.HANDLE),
            ("szTip", ctypes.c_wchar * 128),
        ]

    WNDPROC = ctypes.WINFUNCTYPE(ctypes.wintypes.LPARAM, HWND, UINT,
                                 ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM)
    actions = {}

    def show_menu():
        hmenu = user32.CreatePopupMenu()
        if not hmenu:
            return
        items = [("打开主界面", "restore"), ("启动", "start"),
                 ("停止", "stop"), ("打开导出目录", "open_dir"), ("退出", "quit")]
        cmd = 9000
        for label, act in items:
            user32.AppendMenuW(hmenu, 0x0000, cmd, label)
            actions[cmd] = act
            cmd += 1
        user32.SetForegroundWindow(notify["hwnd"])
        pt = ctypes.wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        rc = user32.TrackPopupMenu(hmenu, TPM_RETURNCMD, pt.x, pt.y, 0,
                                   notify["hwnd"], None)
        user32.DestroyMenu(hmenu)
        if rc:
            act = actions.get(rc)
            if act:
                _dispatch(act, restore, quit_app)

    def _dispatch(act, restore, quit_app):
        if act == "restore":
            restore()
        elif act == "quit":
            quit_app()
        elif act == "start":
            core.cmd_start()
        elif act == "stop":
            core.cmd_stop()
        elif act == "open_dir":
            try:
                os.startfile(OUTPUT_ROOT)
            except Exception:
                pass

    user32.DefWindowProcW.argtypes = [HWND, UINT, ctypes.wintypes.WPARAM,
                                      ctypes.wintypes.LPARAM]
    user32.DefWindowProcW.restype = ctypes.wintypes.LPARAM
    user32.GetModuleHandleW = kernel32.GetModuleHandleW
    user32.GetModuleHandleW.argtypes = [ctypes.wintypes.LPCWSTR]
    user32.GetModuleHandleW.restype = ctypes.wintypes.HANDLE
    user32.CreateWindowExW.argtypes = [ctypes.wintypes.DWORD, ctypes.c_void_p,
                                       ctypes.c_wchar_p, ctypes.wintypes.DWORD,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, HWND, ctypes.wintypes.HMENU,
                                       ctypes.wintypes.HANDLE, ctypes.c_void_p]
    user32.CreateWindowExW.restype = HWND
    user32.LoadIconW.argtypes = [ctypes.wintypes.HANDLE, ctypes.c_void_p]
    user32.LoadIconW.restype = ctypes.wintypes.HANDLE
    user32.LoadImageW.argtypes = [ctypes.wintypes.HANDLE, ctypes.c_wchar_p,
                                  ctypes.wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                  ctypes.wintypes.UINT]
    user32.LoadImageW.restype = ctypes.wintypes.HANDLE
    user32.DestroyWindow.argtypes = [HWND]
    user32.DestroyWindow.restype = BOOL
    user32.UnregisterClassW.argtypes = [ctypes.c_wchar_p, ctypes.wintypes.HANDLE]
    user32.UnregisterClassW.restype = BOOL
    shell32.Shell_NotifyIconW.argtypes = [ctypes.wintypes.DWORD,
                                          ctypes.POINTER(NOTIFYICONDATA)]
    shell32.Shell_NotifyIconW.restype = BOOL

    def wnd_proc(hwnd, msg, wparam, lparam):
        if msg == APP_MSG:
            if lparam == WM_RBUTTONUP:
                show_menu()
            elif lparam == WM_LBUTTONUP:
                restore()
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    wnd_proc_ref = WNDPROC(wnd_proc)

    class WNDCLASS(ctypes.Structure):
        _fields_ = [("style", ctypes.wintypes.UINT),
                    ("lpfnWndProc", WNDPROC),
                    ("cbClsExtra", ctypes.c_int),
                    ("cbWndExtra", ctypes.c_int),
                    ("hInstance", ctypes.wintypes.HANDLE),
                    ("hIcon", ctypes.wintypes.HANDLE),
                    ("hCursor", ctypes.wintypes.HANDLE),
                    ("hbrBackground", ctypes.wintypes.HANDLE),
                    ("lpszMenuName", ctypes.c_wchar_p),
                    ("lpszClassName", ctypes.c_wchar_p)]

    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASS)]
    user32.RegisterClassW.restype = ATOM

    cls = WNDCLASS()
    cls.style = 0
    cls.lpfnWndProc = wnd_proc_ref
    cls.hInstance = user32.GetModuleHandleW(None)
    cls.lpszClassName = "agentlog_tray_wnd"
    atom = user32.RegisterClassW(ctypes.byref(cls))
    if not atom:
        return None
    hwnd = user32.CreateWindowExW(0, atom, "agentlog_tray",
                                   0, 0, 0, 0, 0, HWND_MESSAGE, 0, 0, 0)
    if not hwnd:
        return None

    if os.path.exists(ICON_PATH):
        hicon = user32.LoadImageW(None, ICON_PATH, IMAGE_ICON, 0, 0,
                                  LR_LOADFROMFILE)
        if not hicon:
            hicon = user32.LoadIconW(None, IDI_APPLICATION)
    else:
        hicon = user32.LoadIconW(None, IDI_APPLICATION)
    nid = NOTIFYICONDATA()
    nid.cbSize = ctypes.sizeof(NOTIFYICONDATA)
    nid.hWnd = hwnd
    nid.uID = 1
    nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
    nid.uCallbackMessage = APP_MSG
    nid.hIcon = hicon
    nid.szTip = "agentlog"
    if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
        return None

    notify["hwnd"] = hwnd

    def teardown():
        try:
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
        except Exception:
            pass
        try:
            user32.DestroyWindow(hwnd)
        except Exception:
            pass
        try:
            user32.UnregisterClassW(atom, cls.hInstance)
        except Exception:
            pass

    return teardown


# --------------------------------------------------------------------------
# GUI
# --------------------------------------------------------------------------
class AgentLogGUI:
    def __init__(self, root):
        self.root = root
        root.title("agentlog - 对话记录守护")
        root.geometry("720x520")
        root.minsize(560, 360)

        self.tray_teardown = None
        if os.path.exists(ICON_PATH):
            try:
                root.iconbitmap(ICON_PATH)
            except Exception:
                pass
        self._build_widgets()
        self.refresh_status()
        self.refresh_exports()
        # refresh status periodically
        self._timer = self.root.after(3000, self._tick)

        # minimize-to-tray instead of quitting
        root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_widgets(self):
        f = ttk.Frame(self.root, padding=10)
        f.pack(fill="x")

        self.status_var = tk.StringVar(value="状态: 检测中…")
        ttk.Label(f, textvariable=self.status_var, font=("Segoe UI", 11, "bold")
                  ).pack(side="left", padx=(0, 10))

        ttk.Button(f, text="启动", command=self.do_start).pack(side="left", padx=3)
        ttk.Button(f, text="停止", command=self.do_stop).pack(side="left", padx=3)
        ttk.Button(f, text="重开", command=self.do_restart).pack(side="left", padx=3)
        ttk.Button(f, text="立即扫描", command=self.do_scan).pack(side="left", padx=3)

        self.auto_var = tk.BooleanVar(value=self._autostart_on())
        self.auto_chk = ttk.Checkbutton(f, text="开机自启",
                                        variable=self.auto_var,
                                        command=self.toggle_autostart)
        self.auto_chk.pack(side="right")

        # exports tree
        cols = ("agent", "file")
        self.tree = ttk.Treeview(self.root, columns=cols, show="tree headings",
                                 padding=10)
        self.tree.heading("#0", text="路径 (项目)")
        self.tree.heading("agent", text="agent")
        self.tree.heading("file", text="文件")
        self.tree.column("#0", width=320)
        self.tree.column("agent", width=90)
        self.tree.column("file", width=240)
        self.tree.pack(fill="both", expand=True, padx=10, pady=(6, 0))
        self.tree.bind("<Double-1>", self.open_selected)

        bottom = ttk.Frame(self.root, padding=6)
        bottom.pack(fill="x", side="bottom")
        ttk.Button(bottom, text="打开导出目录", command=self.open_dir).pack(
            side="left", padx=6)
        ttk.Button(bottom, text="最小化到托盘", command=self.minimize).pack(
            side="left", padx=6)
        ttk.Button(bottom, text="退出", command=self.quit_app).pack(
            side="right", padx=6)
        self.hint = tk.StringVar(value="")
        ttk.Label(bottom, textvariable=self.hint, foreground="#666").pack(
            side="left", padx=10)

    # ---- status ----
    def refresh_status(self):
        pid = core.read_pid()
        running = core.is_running(pid)
        if running:
            self.status_var.set(f"状态: 运行中 (pid {pid})")
        else:
            self.status_var.set("状态: 已停止")
        self._running = running

    def _tick(self):
        self.refresh_status()
        self.root.after(3000, self._tick)

    # ---- actions ----
    def do_start(self):
        core.cmd_start()
        self.refresh_status()

    def do_stop(self):
        core.cmd_stop()
        self.refresh_status()

    def do_restart(self):
        core.cmd_stop()
        self.refresh_status()
        core.cmd_start()
        self.refresh_status()

    def do_scan(self):
        try:
            core.scan_once(core.load_state())
            self.hint.set("已扫描一次。")
        except Exception as e:
            self.hint.set(f"扫描出错: {e}")
        self.refresh_exports()

    def toggle_autostart(self):
        if self.auto_var.get():
            core.cmd_install()
            self.hint.set("已启用开机自启 (Startup 快捷方式)。")
        else:
            core.cmd_uninstall()
            self.hint.set("已取消开机自启。")
        self.auto_var.set(self._autostart_on())

    def _autostart_on(self):
        try:
            return os.path.exists(core.startup_lnk_path())
        except Exception:
            return False

    # ---- exports ----
    def refresh_exports(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        if not os.path.isdir(OUTPUT_ROOT):
            return
        for agent in sorted(os.listdir(OUTPUT_ROOT)):
            adir = os.path.join(OUTPUT_ROOT, agent)
            if not os.path.isdir(adir):
                continue
            for fn in sorted(os.listdir(adir)):
                if not fn.endswith(".txt"):
                    continue
                full = os.path.join(adir, fn)
                self.tree.insert("", "end", text=fn,
                                 values=(agent, fn), tags=(full,))

    def open_selected(self, event):
        sel = self.tree.selection()
        if not sel:
            return
        item = sel[0]
        full = self.tree.item(item, "tags")[0]
        if full and os.path.exists(full):
            try:
                os.startfile(full)
            except Exception as e:
                messagebox.showerror("打开失败", str(e))

    def open_dir(self):
        try:
            os.startfile(OUTPUT_ROOT)
        except Exception as e:
            messagebox.showerror("打开失败", str(e))

    # ---- window / tray ----
    def minimize(self):
        self.root.withdraw()
        self._ensure_tray()

    def _ensure_tray(self):
        if self.tray_teardown is not None:
            return
        try:
            self.tray_teardown = _build_tray(
                notify={}, restore=self.restore, quit_app=self.quit_app)
        except Exception as e:
            self.hint.set(f"托盘初始化失败: {e}")

    def restore(self):
        self.root.deiconify()
        self.root.lift()
        self.root.after(0, self.refresh_status)

    def on_close(self):
        # minimize to tray instead of exiting
        self.minimize()

    def quit_app(self):
        if self.tray_teardown:
            try:
                self.tray_teardown()
            except Exception:
                pass
            self.tray_teardown = None
        if self._timer:
            try:
                self.root.after_cancel(self._timer)
            except Exception:
                pass
        self.root.destroy()


def main():
    core._detach_streams()
    root = tk.Tk()
    app = AgentLogGUI(root)
    # if launched and a tray is desired by default, start minimized? Keep window
    # visible by default; user can minimize to tray.
    root.mainloop()


if __name__ == "__main__":
    main()

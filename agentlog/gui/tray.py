"""System tray icon (ctypes, Windows only; other platforms return None)."""
import ctypes
import os
import sys


def build_tray(notify, restore, quit_app, output_root, icon_path,
               on_start=None, on_stop=None, on_open_dir=None):
    """Create a tray icon. notify() shows a balloon; restore/quit_app are
    callbacks marshalled to the tkinter thread. Returns a teardown callable
    (call once when the tray should go away), or None if the tray is
    unavailable."""
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
                _dispatch(act)

    def _dispatch(act):
        if act == "restore":
            restore()
        elif act == "quit":
            quit_app()
        elif act == "start" and on_start:
            on_start()
        elif act == "stop" and on_stop:
            on_stop()
        elif act == "open_dir" and on_open_dir:
            try:
                os.startfile(output_root)
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

    if os.path.exists(icon_path):
        hicon = user32.LoadImageW(None, icon_path, IMAGE_ICON, 0, 0,
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

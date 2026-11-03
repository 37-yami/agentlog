"""Windows/POSIX platform integration: process control, single-instance
lock, shortcuts, autostart (Startup lnk / registry / systemd)."""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time
import uuid

from .config import HERE, LOG_FILE, PID_FILE

# Hide the console window for child console processes (e.g. when the GUI runs
# under pythonw and spawns tasklist / powershell / taskkill). 0 on non-Windows.
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

SYSTEMD_UNIT_DIR = os.path.expanduser("~/.config/systemd/user")
SYSTEMD_UNIT_PATH = os.path.join(SYSTEMD_UNIT_DIR, "agentlog.service")

_lock_handle = None  # mutex/fcntl handle, kept open for the process lifetime


def detach_streams():
    """When launched by pythonw (no console), stdout/stderr/stdin are None.
    Redirect them so print()/input() never crash and messages land in the log."""
    if sys.stdout is None:
        try:
            sys.stdout = open(LOG_FILE, "a", encoding="utf-8")
        except Exception:
            sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        try:
            sys.stderr = open(LOG_FILE, "a", encoding="utf-8")
        except Exception:
            sys.stderr = open(os.devnull, "w", encoding="utf-8")
    if sys.stdin is None:
        sys.stdin = open(os.devnull, "r", encoding="utf-8")


def run_cmd(args):
    """Run a command, decode output safely (Windows consoles are GBK)."""
    try:
        r = subprocess.run(args, capture_output=True,
                           creationflags=CREATE_NO_WINDOW)
        out = (r.stdout or b"").decode("utf-8", "replace")
        err = (r.stderr or b"").decode("utf-8", "replace")
        return r.returncode, out, err
    except Exception as e:  # pragma: no cover
        return -1, "", str(e)


def pythonw_exe():
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        cand = exe[:-len("python.exe")] + "pythonw.exe"
        if os.path.exists(cand):
            return cand
    return exe


def write_pid(pid):
    try:
        with open(PID_FILE, "w", encoding="utf-8") as fh:
            fh.write(str(pid))
    except OSError:
        pass


def acquire_run_lock():
    """Guarantee a single daemon. Returns True if we own the instance,
    False if another agentlog daemon is already running.

    Windows: named mutex. POSIX: an fcntl lock on a lock file (held for the
    process lifetime). This is a hard guarantee independent of the pid file,
    so concurrent launches can never spin up two daemons, and stale pid
    files can't cause duplicates either."""
    global _lock_handle
    if os.name != "nt":
        try:
            import fcntl
            fh = open(os.path.join(HERE, "agentlog.lock"), "w")
            try:
                fcntl.lockf(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                fh.close()
                return False  # another daemon holds the lock
            _lock_handle = fh  # keep open; released when the process exits
            return True
        except Exception:
            return True
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool,
                                          ctypes.c_wchar_p]
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        mutex = kernel32.CreateMutexW(None, False,
                                      "Local\\agentlog.single.instance")
        if not mutex:
            return True  # cannot lock -> allow (best effort)
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            kernel32.CloseHandle(mutex)
            return False
        _lock_handle = mutex  # released automatically when the process exits
        return True
    except Exception:
        return True


def read_pid():
    try:
        with open(PID_FILE, encoding="utf-8") as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


def is_running(pid):
    """True if a process with this pid is still alive. Uses a lightweight
    ctypes check (no external process) so the GUI's status poll every few
    seconds does NOT spawn a visible console window."""
    if not pid:
        return False
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [ctypes.wintypes.DWORD,
                                         ctypes.wintypes.BOOL, ctypes.wintypes.DWORD]
        kernel32.OpenProcess.restype = ctypes.wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = [ctypes.wintypes.HANDLE,
                                                 ctypes.wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = ctypes.wintypes.DWORD
        kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
        kernel32.CloseHandle.restype = ctypes.wintypes.BOOL
        # PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE
        h = kernel32.OpenProcess(0x1000 | 0x00100000, False, pid)
        if not h:
            return False
        try:
            # WAIT_TIMEOUT (258) => still running; WAIT_OBJECT_0 (0) => exited
            return kernel32.WaitForSingleObject(h, 0) == 0x00000102
        finally:
            kernel32.CloseHandle(h)
    except Exception:
        # Fallback: ask tasklist (rarely hit on Windows).
        try:
            rc, out, _ = run_cmd(["tasklist", "/FI", f"PID eq {pid}"])
            return rc == 0 and str(pid) in out
        except Exception:
            return False


def find_agentlog_pids():
    """Return pids of processes running the *daemon* (`agentlog.py run`).
    Only command lines ending in ``agentlog.py run`` match, so a concurrent
    `stop`/`start` invocation, the GUI (agentlog_gui.py) and editors that
    merely have the file open are never matched; this very process is
    excluded too. Falls back to the pid-file pid when the platform helper
    is unavailable."""
    me = os.getpid()
    pids = []
    if os.name != "nt":
        # POSIX: pgrep -f matches full command lines and excludes itself.
        try:
            rc, out, _ = run_cmd(["pgrep", "-f", r"agentlog\.py run$"])
            if rc == 0:
                for tok in out.split():
                    tok = tok.strip()
                    if tok.isdigit() and int(tok) != me:
                        pids.append(int(tok))
        except Exception:
            pass
        if not pids:
            pid = read_pid()
            if pid and pid != me and is_running(pid):
                pids.append(pid)
        return pids
    try:
        rc, out, _ = run_cmd([
            "powershell", "-NoProfile", "-NonInteractive", "-Command",
            "Get-CimInstance Win32_Process -Filter \"name='pythonw.exe' or "
            "name='python.exe'\" | Where-Object { $_.CommandLine -match "
            "'agentlog\\.py\"?\\s+run\\s*$' } "
            "| ForEach-Object { $_.ProcessId }"
        ])
        if rc == 0:
            for tok in out.split():
                tok = tok.strip()
                if tok.isdigit() and int(tok) != me:
                    pids.append(int(tok))
    except Exception:
        pass
    if not pids:
        pid = read_pid()
        if pid and pid != me and is_running(pid):
            pids.append(pid)
    return pids


def kill_all_agentlog():
    """Kill every process whose command line runs the agentlog daemon
    (`agentlog.py run`). find_agentlog_pids() already covers the pid-file
    fallback."""
    pids = []
    for pid in find_agentlog_pids():
        if os.name != "nt":
            import signal
            try:
                os.kill(pid, signal.SIGTERM)
                pids.append(str(pid))
            except OSError:
                pass
            continue
        subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                       capture_output=True, creationflags=CREATE_NO_WINDOW)
        pids.append(str(pid))
    return pids


def systemctl_user(*args):
    """Run `systemctl --user`, making sure the user manager is reachable:
    non-login shells (plain `wsl bash`) lack XDG_RUNTIME_DIR."""
    if os.name == "nt":
        return -1
    env = dict(os.environ)
    if not env.get("XDG_RUNTIME_DIR"):
        d = "/run/user/%d" % os.getuid()
        if os.path.isdir(d):
            env["XDG_RUNTIME_DIR"] = d
    try:
        return subprocess.run(["systemctl", "--user"] + list(args),
                              capture_output=True, env=env).returncode
    except OSError:
        return -1


# --------------------------------------------------------------------------
# Windows autostart: Startup-folder .lnk + StartupApproved flag
# --------------------------------------------------------------------------
def run_key():
    return r"Software\Microsoft\Windows\CurrentVersion\Run"


def startup_folder():
    """User's per-user Startup folder (no admin needed)."""
    return os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows",
                        "Start Menu", "Programs", "Startup")


def startup_lnk_path():
    return os.path.join(startup_folder(), "agentlog.lnk")


def startup_approved_key():
    """Registry key where Windows records the enabled/disabled state of each
    Startup-folder item (what Task Manager's Startup tab toggles)."""
    return (r"Software\Microsoft\Windows\CurrentVersion\Explorer"
            r"\StartupApproved\StartupFolder")


def startup_approved_bytes():
    """Raw StartupApproved value for our lnk, or None when unset.

    No value means "never touched" and counts as enabled."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, startup_approved_key(),
                            0, winreg.KEY_QUERY_VALUE) as k:
            data, _ = winreg.QueryValueEx(k, "agentlog.lnk")
            return bytes(data)
    except OSError:
        return None


def autostart_blocked():
    """True when Windows will skip our Startup lnk at logon: the
    StartupApproved flag's low bit marks the item as disabled (Task Manager,
    OEM startup managers or cleanup tools may set it behind our back)."""
    data = startup_approved_bytes()
    return data is not None and bool(data[0] & 1)


def set_startup_approved(enabled=True):
    """Write the standard StartupApproved flag so Windows honours our lnk.

    Users enable autostart explicitly (GUI checkbox / `install`), so clearing
    a disable flag left by other software here matches their intent - it is
    what pressing "Enable" in Task Manager would do."""
    import winreg
    flag = 0x02 if enabled else 0x03
    data = bytes([flag, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0])
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, startup_approved_key()) as k:
        winreg.SetValueEx(k, "agentlog.lnk", 0, winreg.REG_BINARY, data)


# --------------------------------------------------------------------------
# .lnk shortcut creation via ctypes/IShellLink (stdlib only, no pywin32).
# --------------------------------------------------------------------------
class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD),
                ("Data4", ctypes.c_ubyte * 8)]
    def __init__(self, s):
        u = uuid.UUID(s)
        super().__init__()
        self.Data1 = u.time_low
        self.Data2 = u.time_mid
        self.Data3 = u.time_hi_version
        self.Data4 = (ctypes.c_ubyte * 8)(*u.bytes[8:])


_CLSID_ShellLink = _GUID("{00021401-0000-0000-C000-000000000046}")
_IID_IShellLinkW = _GUID("{000214F9-0000-0000-C000-000000000046}")
_IID_IPersistFile = _GUID("{0000010B-0000-0000-C000-000000000046}")
_CLSCTX_INPROC_SERVER = 0x1


class _IShellLinkWVtbl(ctypes.Structure):
    _fields_ = [("QueryInterface", ctypes.c_void_p), ("AddRef", ctypes.c_void_p),
                ("Release", ctypes.c_void_p), ("GetPath", ctypes.c_void_p),
                ("GetIDList", ctypes.c_void_p), ("SetIDList", ctypes.c_void_p),
                ("GetDescription", ctypes.c_void_p), ("SetDescription", ctypes.c_void_p),
                ("GetWorkingDirectory", ctypes.c_void_p), ("SetWorkingDirectory", ctypes.c_void_p),
                ("GetArguments", ctypes.c_void_p), ("SetArguments", ctypes.c_void_p),
                ("GetHotkey", ctypes.c_void_p), ("SetHotkey", ctypes.c_void_p),
                ("GetShowCmd", ctypes.c_void_p), ("SetShowCmd", ctypes.c_void_p),
                ("GetIconLocation", ctypes.c_void_p), ("SetIconLocation", ctypes.c_void_p),
                ("SetRelativePath", ctypes.c_void_p), ("Resolve", ctypes.c_void_p),
                ("SetPath", ctypes.c_void_p)]


class _IShellLinkW(ctypes.Structure):
    _fields_ = [("lpVtbl", ctypes.POINTER(_IShellLinkWVtbl))]


class _IPersistFileVtbl(ctypes.Structure):
    _fields_ = [("QueryInterface", ctypes.c_void_p), ("AddRef", ctypes.c_void_p),
                ("Release", ctypes.c_void_p), ("GetClassID", ctypes.c_void_p),
                ("IsDirty", ctypes.c_void_p), ("Load", ctypes.c_void_p),
                ("Save", ctypes.c_void_p), ("SaveCompleted", ctypes.c_void_p),
                ("GetCurFile", ctypes.c_void_p)]


class _IPersistFile(ctypes.Structure):
    _fields_ = [("lpVtbl", ctypes.POINTER(_IPersistFileVtbl))]


def create_shortcut(lnk_path, target, args="", workdir="", icon=""):
    """Create a .lnk shortcut. Windows-only, stdlib-only (ctypes COM)."""
    ole32 = ctypes.OleDLL("ole32")
    ole32.CoInitialize(None)
    try:
        shell_link = ctypes.POINTER(_IShellLinkW)()
        hr = ole32.CoCreateInstance(ctypes.byref(_CLSID_ShellLink), None,
                                    _CLSCTX_INPROC_SERVER,
                                    ctypes.byref(_IID_IShellLinkW),
                                    ctypes.byref(shell_link))
        if hr != 0:
            raise ctypes.WinError(hr)
        vtbl = shell_link.contents.lpVtbl
        def m(idx):
            return ctypes.cast(getattr(vtbl.contents, idx), ctypes.WINFUNCTYPE(
                wt.LONG, ctypes.POINTER(_IShellLinkW), ctypes.c_wchar_p))
        m("SetPath")(shell_link, target)
        if args:
            m("SetArguments")(shell_link, args)
        if workdir:
            m("SetWorkingDirectory")(shell_link, workdir)
        if icon:
            # SetIconLocation(LPCWSTR pszIconPath, int iIcon)
            set_icon = ctypes.cast(vtbl.contents.SetIconLocation,
                                   ctypes.WINFUNCTYPE(wt.LONG,
                                                     ctypes.POINTER(_IShellLinkW),
                                                     ctypes.c_wchar_p, wt.INT))
            set_icon(shell_link, icon, 0)
        ppv = ctypes.c_void_p()
        QI = ctypes.cast(vtbl.contents.QueryInterface, ctypes.WINFUNCTYPE(
            wt.LONG, ctypes.POINTER(_IShellLinkW), ctypes.POINTER(_GUID),
            ctypes.POINTER(ctypes.c_void_p)))
        hr = QI(shell_link, ctypes.byref(_IID_IPersistFile), ctypes.byref(ppv))
        if hr != 0:
            raise ctypes.WinError(hr)
        pf = ctypes.cast(ppv, ctypes.POINTER(_IPersistFile))
        pf_vtbl = pf.contents.lpVtbl
        Save = ctypes.cast(pf_vtbl.contents.Save, ctypes.WINFUNCTYPE(
            wt.LONG, ctypes.POINTER(_IPersistFile), ctypes.c_wchar_p, wt.BOOL))
        hr = Save(pf, lnk_path, True)
        if hr != 0:
            raise ctypes.WinError(hr)
        Release = ctypes.cast(pf_vtbl.contents.Release,
                              ctypes.WINFUNCTYPE(wt.ULONG, ctypes.POINTER(_IPersistFile)))
        Release(pf)
        Release2 = ctypes.cast(vtbl.contents.Release,
                               ctypes.WINFUNCTYPE(wt.ULONG, ctypes.POINTER(_IShellLinkW)))
        Release2(shell_link)
    finally:
        ole32.CoUninitialize()

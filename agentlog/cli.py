"""Command line interface: argparse plus all cmd_* actions."""
import argparse
import os
import sys
import time

from . import config
from .capture import PROXY_PORT, proxy_alive
from .config import HERE, ICON_PATH, LOG_FILE, OUTPUT_FORMATS, STATE_FILE
from .daemon import load_state, log, run_loop, save_state, scan_once
from .winapi import (CREATE_NO_WINDOW, SYSTEMD_UNIT_DIR, SYSTEMD_UNIT_PATH,
                     autostart_blocked, create_shortcut, detach_streams,
                     find_agentlog_pids, kill_all_agentlog, pythonw_exe,
                     read_pid, is_running, run_cmd, set_startup_approved,
                     startup_lnk_path, systemctl_user, write_pid,
                     acquire_run_lock)

import subprocess


def cmd_start():
    pid = read_pid()
    if is_running(pid):
        print(f"agentlog is already running (pid {pid}).")
        if sys.stdin.isatty():
            try:
                while True:
                    r = input(
                        "A daemon is already running. "
                        "Restart it (kill the old process and start a fresh one) "
                        "[r], or cancel this run [c]? "
                    ).strip().lower()
                    if r in ("r", "restart"):
                        kill_all_agentlog()
                        time.sleep(1)
                        break
                    if r in ("c", "cancel"):
                        print("cancelled; the existing daemon keeps running.")
                        return
                    print("Please enter 'r' to restart or 'c' to cancel.")
            except (EOFError, KeyboardInterrupt):
                print("\ncancelled; the existing daemon keeps running.")
                return
        else:
            print("Non-interactive shell: leaving the existing daemon running. "
                  "Re-run in a terminal to restart, or use `stop` then `start`.")
            return
    exe = pythonw_exe()
    script = os.path.join(HERE, "agentlog.py")
    log("launching background daemon")
    if os.name != "nt":
        # POSIX: detach via a new session; keep output in the log file so a
        # terminal-bound launch does not lose daemon messages.
        logfh = open(LOG_FILE, "a", encoding="utf-8")
        subprocess.Popen([exe, script, "run"],
                         stdin=subprocess.DEVNULL, stdout=logfh,
                         stderr=subprocess.STDOUT,
                         start_new_session=True, close_fds=True)
    else:
        subprocess.Popen([exe, script, "run"],
                         creationflags=0x00000008)  # DETACHED_PROCESS
    # give it a moment, then record pid via tasklist? We can't easily get pid,
    # so we rely on the run loop writing the pidfile itself.
    time.sleep(1)
    print("agentlog started in background.")


def cmd_stop():
    pids = kill_all_agentlog()
    if pids:
        print(f"stopped pid(s): {', '.join(pids)}.")
    else:
        print("no agentlog daemon process found.")
    try:
        os.remove(os.path.join(HERE, "agentlog.pid"))
    except OSError:
        pass


def cmd_status():
    pid = read_pid()
    running = is_running(pid)
    print(f"running: {running}" + (f" (pid {pid})" if pid else ""))
    if os.name != "nt":
        rc = systemctl_user("is-enabled", "agentlog.service")
        print("autostart: " + ("enabled (systemd)" if rc == 0
                               else "disabled (run `install` to enable)"))
    elif os.path.exists(startup_lnk_path()):
        state = ("BLOCKED (disabled via StartupApproved; re-run install "
                 "or re-tick the GUI checkbox to fix)" if autostart_blocked()
                 else "enabled")
        print(f"autostart: {state}")
    # A routed agent cannot reach its API when the proxy is down, so surface
    # proxy health right next to the daemon status.
    if running:
        if proxy_alive():
            print(f"proxy: listening on http://127.0.0.1:{PROXY_PORT}")
        else:
            print("proxy: NOT responding (routed agents will fail!)")
    print(f"log: {LOG_FILE}")
    print(f"state: {STATE_FILE}")


def _set_autostart(value):
    """Write the Run-key value via winreg (avoids reg.exe quote quirks)."""
    import winreg
    from .winapi import run_key
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key(),
                        0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "agentlog", 0, winreg.REG_SZ, value)


def _del_autostart():
    import winreg
    from .winapi import run_key
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key(),
                        0, winreg.KEY_SET_VALUE) as k:
        winreg.DeleteValue(k, "agentlog")


def _systemd_unit(script, exe):
    return (
        "[Unit]\n"
        "Description=agentlog - capture CLI agent conversations\n"
        "After=network.target\n"
        "\n"
        "[Service]\n"
        f"ExecStart={exe} {script} run\n"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def cmd_install():
    exe = pythonw_exe()
    script = os.path.join(HERE, "agentlog.py")
    if os.name != "nt":
        # POSIX: systemd user service is the autostart channel (works in WSL
        # when systemd is enabled; enable lingering so it survives a closed
        # terminal session and starts the user manager when needed).
        try:
            os.makedirs(SYSTEMD_UNIT_DIR, exist_ok=True)
            with open(SYSTEMD_UNIT_PATH, "w", encoding="utf-8") as fh:
                fh.write(_systemd_unit(script, sys.executable))
            run_cmd(["loginctl", "enable-linger",
                     os.environ.get("USER", "") or str(os.getuid())])
            # First-time linger activation can take seconds before the user
            # manager answers; wait for it instead of failing silently.
            for _ in range(20):
                if systemctl_user("show-environment") == 0:
                    break
                time.sleep(0.5)
            systemctl_user("daemon-reload")
            if systemctl_user("enable", "--now", "agentlog.service") == 0:
                print(f"autostart enabled via systemd user service: {SYSTEMD_UNIT_PATH}")
            else:
                print("systemd service enabled but start failed; check: "
                      "systemctl --user status agentlog.service")
        except Exception as e:
            print("could not enable systemd service:", e)
            print("start it manually:  nohup python3 agentlog.py run &")
        cmd_start()
        return
    # A .lnk in the per-user Startup folder is the primary autostart channel:
    # it needs no admin and survives most Run-key sweeps by OEM startup
    # managers. The shortcut launches `start` (idempotent).
    lnk = startup_lnk_path()
    try:
        create_shortcut(lnk, exe, f'"{script}" start', os.path.dirname(script))
        print(f"autostart enabled via Startup shortcut: {lnk}")
        # Windows skips the lnk at logon when StartupApproved marks it
        # disabled (Task Manager / OEM tools can set this behind our back).
        # Re-assert the enabled flag so the checkbox actually works.
        try:
            set_startup_approved(True)
        except Exception as e:
            print("could not update StartupApproved flag:", e)
    except Exception as e:
        print("could not create Startup shortcut:", e)
        # Fall back to the registry Run key for machines where the Startup
        # folder is read-only.
        try:
            _set_autostart(f'"{exe}" "{script}" start')
            print("autostart enabled via registry Run key (no admin needed).")
        except Exception as e2:
            print("could not set autostart:", e2)
    cmd_start()


def cmd_uninstall():
    if os.name != "nt":
        # Stop/disable the service FIRST: its Restart=on-failure would
        # resurrect a daemon killed by cmd_stop().
        systemctl_user("disable", "--now", "agentlog.service")
        try:
            os.remove(SYSTEMD_UNIT_PATH)
            systemctl_user("daemon-reload")
            print("systemd user service removed.")
        except OSError:
            pass
        cmd_stop()
        return
    cmd_stop()
    try:
        os.remove(startup_lnk_path())
        print("Startup shortcut removed.")
    except OSError:
        pass
    # Remove the StartupApproved flag too so no stale disabled marker is
    # left behind for a future reinstall.
    try:
        import winreg
        from .winapi import startup_approved_key
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, startup_approved_key(),
                            0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, "agentlog.lnk")
    except OSError:
        pass
    try:
        _del_autostart()
        print("registry autostart removed.")
    except FileNotFoundError:
        pass


def cmd_gui_shortcut():
    """Create a double-click launcher for the GUI: a .lnk that runs
    `pythonw agentlog_gui.py` (no console window) with the agentlog icon.
    Places one next to this repo and one on the Desktop."""
    if os.name != "nt":
        print("GUI launcher shortcuts are Windows-only; on Linux run: "
              "python3 agentlog_gui.py")
        return
    exe = pythonw_exe()
    script = os.path.join(HERE, "agentlog_gui.py")
    icon = ICON_PATH if os.path.exists(ICON_PATH) else ""
    targets = [os.path.join(HERE, "agentlog-gui.lnk")]
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    if os.path.isdir(desktop):
        targets.append(os.path.join(desktop, "agentlog 界面.lnk"))
    created = []
    for lnk in targets:
        try:
            create_shortcut(lnk, exe, f'"{script}"', HERE, icon)
            created.append(lnk)
        except Exception as e:
            print(f"could not create shortcut {lnk}: {e}")
    if created:
        print("GUI launcher created (double-click to run, no console):")
        for p in created:
            print("  ", p)
    else:
        print("no GUI launcher created.")


def build_icon(src, dest=ICON_PATH, sizes=(16, 32, 48, 64, 128, 256)):
    """Render a source image (any format Pillow reads) into a multi-size
    .ico for the tray / window / shortcut. Needs Pillow; this is a build-time
    helper, not required at runtime (the .ico is shipped)."""
    try:
        from PIL import Image
    except ImportError:
        print("Pillow is required to build the icon: pip install Pillow")
        return False
    im = Image.open(src).convert("RGBA")
    # center-crop to a square, then fit the largest size
    w, h = im.size
    s = min(w, h)
    left = (w - s) // 2
    top = (h - s) // 2
    im = im.crop((left, top, left + s, top + s)).resize((max(sizes),) * 2,
                                                        Image.LANCZOS)
    im.save(dest, format="ICO", sizes=[(sz, sz) for sz in sizes])
    print("icon written:", dest)
    return True


def cmd_build_icon(src):
    if not src:
        print("usage: python agentlog.py build-icon <path-to-image>")
        return
    if not os.path.exists(src):
        print("image not found:", src)
        return
    build_icon(src)


def cmd_route():
    """Print per-agent configuration to route API traffic through the
    local proxy. Routed agents are captured at request time; their legacy
    file/database scrape is suppressed per project."""
    print("Point each agent's API endpoint at the local proxy (runs inside the")
    print("agentlog daemon). Turns are captured at request time into")
    print("capture/<agent>/<YYYYMMDD>.jsonl and exported per project as usual.")
    print()
    print("claude    setx ANTHROPIC_BASE_URL http://127.0.0.1:%d/claude" % PROXY_PORT)
    print("          (custom relay: append /<relay-host>, e.g. .../claude/relay.example.com)")
    print("          then restart the terminal so the variable takes effect")
    print()
    print("opencode  in ~/.config/opencode/opencode.json set the provider baseURL to")
    print("          http://127.0.0.1:%d/opencode/<provider-host>" % PROXY_PORT)
    print("          e.g. .../opencode/api.deepseek.com for deepseek")
    print()
    print("codebuddy point its endpoint/base-url config to")
    print("          http://127.0.0.1:%d/codebuddy/<endpoint-host>" % PROXY_PORT)
    print()
    print("The proxy understands Anthropic (/v1/messages) and OpenAI-compatible")
    print("(/v1/chat/completions) protocols; other paths are forwarded untouched.")
    print()
    print("Notes:")
    print("- IMPORTANT: a routed agent needs the daemon (and its proxy) alive.")
    print("  If agentlog is stopped, routed agents cannot reach their API.")
    print("  Check with: python agentlog.py status  (proxy line).")
    print("- The project path comes from the agent's system prompt. Claude Code")
    print("  states it reliably. opencode/codebuddy may not - for those the")
    print("  legacy database/JSONL scrape (which knows the project natively)")
    print("  is the better capture channel and needs no routing at all.")
    print("- Unrouting an agent (removing the baseURL) and deleting capture/ is")
    print("  enough to fall back to the scrape everywhere.")
    print("- Set AGENTLOG_PROXY=0 to disable the proxy thread.")


def main():
    detach_streams()
    ap = argparse.ArgumentParser(description="Capture CLI agent conversations.")
    ap.add_argument("action", nargs="?", default="once",
                    choices=["run", "once", "start", "stop",
                             "status", "install", "uninstall",
                             "gui-shortcut", "build-icon", "route"])
    ap.add_argument("path", nargs="?", default="")
    ap.add_argument("-t", "--include-thinking", action="store_true",
                    help="Include thinking/reasoning content in output")
    ap.add_argument("-f", "--format", nargs="+", choices=["txt", "md", "json"],
                    default=["md"],
                    help="Output format(s): md (default), txt, json. "
                         "Multiple formats can be specified.")
    args = ap.parse_args()

    # Update the mutable run-time options via the config module.
    config.set_options(include_thinking=args.include_thinking,
                       formats=args.format)

    if args.action == "run":
        if not acquire_run_lock():
            print("another agentlog daemon is already running; exiting.")
            sys.exit(0)
        write_pid(os.getpid())
        run_loop()
    elif args.action == "once":
        state = load_state()
        scan_once(state)
        save_state(state)
        print("scan complete. see log:", LOG_FILE)
    elif args.action == "start":
        cmd_start()
    elif args.action == "stop":
        cmd_stop()
    elif args.action == "status":
        cmd_status()
    elif args.action == "install":
        cmd_install()
    elif args.action == "uninstall":
        cmd_uninstall()
    elif args.action == "gui-shortcut":
        cmd_gui_shortcut()
    elif args.action == "build-icon":
        cmd_build_icon(args.path)
    elif args.action == "route":
        cmd_route()

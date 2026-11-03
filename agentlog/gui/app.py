"""agentlog GUI: the AgentLogGUI window and its single-instance main().

A window to control the agentlog daemon (status / start / stop / restart),
toggle logon autostart, browse & open the exported files, and trigger a
manual scan. The window can be minimized to the system tray (via a ctypes
tray icon, no third-party dependencies).

Everything reuses the agentlog package, so the CLI and this GUI stay in
sync. Run:  python agentlog_gui.py
"""

import os
import sys
import ctypes
import threading
import tkinter as tk
from tkinter import ttk, messagebox

import agentlog as core  # reuse cmd_*/scan_once/paths
from agentlog.gui.sessions import collect_sessions
from agentlog.gui.tray import build_tray

OUTPUT_ROOT = core.OUTPUT_ROOT
ICON_PATH = core.ICON_PATH  # optional .ico next to the script


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
        # Top control bar
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

        # exports tree with scrollbar
        tree_frame = ttk.Frame(self.root)
        tree_frame.pack(fill="both", expand=True, padx=10, pady=(6, 0))

        # Notebook for switching between file view and session view
        self.notebook = ttk.Notebook(tree_frame)
        self.notebook.pack(fill="both", expand=True)

        # Tab 1: File list view
        file_tab = ttk.Frame(self.notebook)
        self.notebook.add(file_tab, text="文件列表")

        # File filter row 1: format checkboxes + thinking toggle
        file_row1 = ttk.Frame(file_tab, padding=(10, 6, 10, 0))
        file_row1.pack(fill="x")

        ttk.Label(file_row1, text="输出格式:").pack(side="left", padx=(0, 4))
        self.fmt_txt_var = tk.BooleanVar(value=False)
        self.fmt_md_var = tk.BooleanVar(value=True)
        self.fmt_json_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(file_row1, text="txt", variable=self.fmt_txt_var,
                        command=self._on_format_change).pack(side="left", padx=2)
        ttk.Checkbutton(file_row1, text="md", variable=self.fmt_md_var,
                        command=self._on_format_change).pack(side="left", padx=2)
        ttk.Checkbutton(file_row1, text="json", variable=self.fmt_json_var,
                        command=self._on_format_change).pack(side="left", padx=(2, 12))

        self.thinking_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(file_row1, text="包含思考内容",
                        variable=self.thinking_var,
                        command=self._on_thinking_change).pack(side="left", padx=(0, 12))

        ttk.Button(file_row1, text="刷新", command=self.refresh_exports).pack(
            side="left", padx=3)

        # File filter row 2: filters + search
        file_row2 = ttk.Frame(file_tab, padding=(10, 2, 10, 0))
        file_row2.pack(fill="x")

        # Agent filter
        ttk.Label(file_row2, text="Agent:").pack(side="left", padx=(0, 4))
        self.filter_var = tk.StringVar(value="全部")
        self.filter_menu = ttk.Combobox(file_row2, textvariable=self.filter_var,
                                        values=["全部"], width=12,
                                        state="readonly")
        self.filter_menu.pack(side="left", padx=(0, 10))
        self.filter_menu.bind("<<ComboboxSelected>>", self._on_filter_change)

        # Format filter
        ttk.Label(file_row2, text="格式:").pack(side="left", padx=(0, 4))
        self.ffilter_var = tk.StringVar(value="全部")
        self.ffilter_menu = ttk.Combobox(file_row2, textvariable=self.ffilter_var,
                                         values=["全部", "txt", "md", "json"],
                                         width=8, state="readonly")
        self.ffilter_menu.pack(side="left", padx=(0, 10))
        self.ffilter_menu.bind("<<ComboboxSelected>>", self._on_filter_change)

        # Thinking filter
        ttk.Label(file_row2, text="思考:").pack(side="left", padx=(0, 4))
        self.tfilter_var = tk.StringVar(value="全部")
        self.tfilter_menu = ttk.Combobox(file_row2, textvariable=self.tfilter_var,
                                         values=["全部", "无思考", "有思考"],
                                         width=8, state="readonly")
        self.tfilter_menu.pack(side="left", padx=(0, 10))
        self.tfilter_menu.bind("<<ComboboxSelected>>", self._on_filter_change)

        # Search
        ttk.Label(file_row2, text="搜索:").pack(side="left", padx=(0, 4))
        self.search_var = tk.StringVar(value="")
        self.search_entry = ttk.Entry(file_row2, textvariable=self.search_var, width=15)
        self.search_entry.pack(side="left", padx=(0, 4))
        self.search_entry.bind("<Return>", lambda e: self.refresh_exports())
        ttk.Button(file_row2, text="搜索", command=self.refresh_exports).pack(
            side="left", padx=(0, 4))

        # File tree
        file_tree_frame = ttk.Frame(file_tab)
        file_tree_frame.pack(fill="both", expand=True, padx=10, pady=(6, 0))

        cols = ("agent", "file", "mtime")
        self.tree = ttk.Treeview(file_tree_frame, columns=cols, show="tree headings",
                                 padding=10, selectmode="extended")
        self.tree.heading("#0", text="路径 (项目)", command=lambda: self._sort_tree("#0"))
        self.tree.heading("agent", text="agent", command=lambda: self._sort_tree("agent"))
        self.tree.heading("file", text="文件", command=lambda: self._sort_tree("file"))
        self.tree.heading("mtime", text="更新时间 ↓", command=lambda: self._sort_tree("mtime"))
        self.tree.column("#0", width=250, minwidth=150, stretch=True)
        self.tree.column("agent", width=80, minwidth=60, stretch=False)
        self.tree.column("file", width=200, minwidth=100, stretch=True)
        self.tree.column("mtime", width=120, minwidth=100, stretch=False)

        self._sort_col = "mtime"
        self._sort_reverse = True  # descending by default (newest first)

        scrollbar = ttk.Scrollbar(file_tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.tree.bind("<Double-1>", self.open_selected)

        # Tab 2: Session list view
        session_tab = ttk.Frame(self.notebook)
        self.notebook.add(session_tab, text="会话列表")

        # Session filter row
        sess_filter_frame = ttk.Frame(session_tab, padding=(10, 6, 10, 0))
        sess_filter_frame.pack(fill="x")

        ttk.Label(sess_filter_frame, text="Agent:").pack(side="left", padx=(0, 4))
        self.sess_agent_var = tk.StringVar(value="全部")
        self.sess_agent_menu = ttk.Combobox(sess_filter_frame, textvariable=self.sess_agent_var,
                                           values=["全部"], width=10, state="readonly")
        self.sess_agent_menu.pack(side="left", padx=(0, 10))
        self.sess_agent_menu.bind("<<ComboboxSelected>>", self._on_sess_filter_change)

        ttk.Label(sess_filter_frame, text="搜索:").pack(side="left", padx=(0, 4))
        self.sess_search_var = tk.StringVar(value="")
        self.sess_search_entry = ttk.Entry(sess_filter_frame, textvariable=self.sess_search_var, width=15)
        self.sess_search_entry.pack(side="left", padx=(0, 4))
        self.sess_search_entry.bind("<Return>", lambda e: self._refresh_sessions())
        ttk.Button(sess_filter_frame, text="搜索", command=self._refresh_sessions).pack(
            side="left", padx=(0, 4))

        sess_cols = ("agent", "project", "title", "session_id", "start_time", "update_time", "msg_count")
        self.session_tree = ttk.Treeview(session_tab, columns=sess_cols, show="headings",
                                         padding=10, selectmode="browse")
        self.session_tree.heading("agent", text="Agent", command=lambda: self._sort_session_tree("agent"))
        self.session_tree.heading("project", text="项目路径", command=lambda: self._sort_session_tree("project"))
        self.session_tree.heading("title", text="标题", command=lambda: self._sort_session_tree("title"))
        self.session_tree.heading("session_id", text="Session ID", command=lambda: self._sort_session_tree("session_id"))
        self.session_tree.heading("start_time", text="开始时间", command=lambda: self._sort_session_tree("start_time"))
        self.session_tree.heading("update_time", text="最后更新 ↓", command=lambda: self._sort_session_tree("update_time"))
        self.session_tree.heading("msg_count", text="消息数", command=lambda: self._sort_session_tree("msg_count"))
        self.session_tree.column("agent", width=70, minwidth=60, stretch=False)
        self.session_tree.column("project", width=180, minwidth=100, stretch=True)
        self.session_tree.column("title", width=160, minwidth=80, stretch=True)
        self.session_tree.column("session_id", width=90, minwidth=70, stretch=False)
        self.session_tree.column("start_time", width=115, minwidth=100, stretch=False)
        self.session_tree.column("update_time", width=115, minwidth=100, stretch=False)
        self.session_tree.column("msg_count", width=60, minwidth=50, stretch=False)

        self._sess_sort_col = "update_time"
        self._sess_sort_reverse = True

        sess_scrollbar = ttk.Scrollbar(session_tab, orient="vertical", command=self.session_tree.yview)
        self.session_tree.configure(yscrollcommand=sess_scrollbar.set)

        self.session_tree.pack(side="left", fill="both", expand=True)
        sess_scrollbar.pack(side="right", fill="y")

        self.session_tree.bind("<Double-1>", self._open_session_file)

        # Selection buttons bar (inside file_tab)
        sel_bar = ttk.Frame(file_tab, padding=(10, 2, 10, 0))
        sel_bar.pack(fill="x")
        self.select_all_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(sel_bar, text="全选",
                        variable=self.select_all_var,
                        command=self._toggle_select_all).pack(side="left", padx=(0, 6))
        ttk.Button(sel_bar, text="取消全选", command=self._deselect_all).pack(
            side="left", padx=3)
        ttk.Button(sel_bar, text="删除选中", command=self._delete_selected).pack(
            side="left", padx=3)
        self.sel_count_var = tk.StringVar(value="")
        ttk.Label(sel_bar, textvariable=self.sel_count_var, foreground="#666").pack(
            side="left", padx=10)
        ttk.Button(sel_bar, text="打开导出目录", command=self.open_dir).pack(
            side="right", padx=3)
        self.tree.bind("<<TreeviewSelect>>", self._on_select_change)

        # Bottom bar (only minimize and quit)
        bottom = ttk.Frame(self.root, padding=6)
        bottom.pack(fill="x", side="bottom")
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
            # Routed agents fail when the in-daemon proxy is down, so show
            # its health next to the daemon status.
            proxy = "proxy ✓" if core.proxy_alive() else "proxy ✗ (路由的 agent 将无法连接)"
            self.status_var.set(f"状态: 运行中 (pid {pid}) · {proxy}")
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
            # Apply current settings before scanning
            new_thinking = self.thinking_var.get()
            # Build format list from checkboxes
            formats = []
            if self.fmt_txt_var.get():
                formats.append("txt")
            if self.fmt_md_var.get():
                formats.append("md")
            if self.fmt_json_var.get():
                formats.append("json")
            if not formats:
                formats = ["md"]  # default
            
            # Check if settings changed - if so, clear state to force regeneration
            settings_changed = (new_thinking != core.config.INCLUDE_THINKING or
                                formats != core.OUTPUT_FORMATS)

            core.config.set_options(include_thinking=new_thinking,
                                    formats=formats)
            
            state = core.load_state()
            if settings_changed:
                # Drop the mtime/size cache entirely: write_export() dedupes
                # on mtime/size alone, so merely removing "out" would keep
                # every file skipped and nothing would be regenerated.
                state.clear()
            
            core.scan_once(state)
            core.save_state(state)
            self.hint.set(f"已扫描 (格式: {','.join(formats)}, "
                         f"思考: {'是' if core.config.INCLUDE_THINKING else '否'})。")
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
            if os.name != "nt":
                return core._systemctl_user("is-enabled", "agentlog.service") == 0
            if not os.path.exists(core.startup_lnk_path()):
                return False
            # The lnk alone is not enough: Windows skips it at logon when the
            # StartupApproved flag marks it disabled (Task Manager / OEM
            # startup managers can set that behind our back).
            return not core.autostart_blocked()
        except Exception:
            return False

    # ---- settings callbacks ----
    def _on_format_change(self):
        formats = []
        if self.fmt_txt_var.get():
            formats.append("txt")
        if self.fmt_md_var.get():
            formats.append("md")
        if self.fmt_json_var.get():
            formats.append("json")
        self.hint.set(f"输出格式: {','.join(formats) if formats else '无'}")

    def _on_thinking_change(self):
        val = "包含" if self.thinking_var.get() else "不包含"
        self.hint.set(f"思考内容: {val}")

    def _on_filter_change(self, event=None):
        self.select_all_var.set(False)
        self.refresh_exports()

    def _on_sess_filter_change(self, event=None):
        """Refresh session list when filter changes."""
        self._refresh_sessions()

    def _sort_session_tree(self, col):
        """Sort session treeview by column header click."""
        if self._sess_sort_col == col:
            self._sess_sort_reverse = not self._sess_sort_reverse
        else:
            self._sess_sort_col = col
            self._sess_sort_reverse = False
        
        # Update heading arrows
        for c in ("agent", "project", "title", "session_id", "start_time",
                  "update_time", "msg_count"):
            text = self.session_tree.heading(c)["text"].rstrip(" ↓↑")
            if c == col:
                text += " ↑" if not self._sess_sort_reverse else " ↓"
            self.session_tree.heading(c, text=text)
        
        # Get all items with their values
        items = []
        for item in self.session_tree.get_children():
            tags = self.session_tree.item(item, "tags")
            vals = self.session_tree.item(item, "values")
            idx = {"agent": 0, "project": 1, "title": 2, "session_id": 3,
                   "start_time": 4, "update_time": 5, "msg_count": 6}.get(col, 0)
            val = vals[idx] if idx < len(vals) else ""
            # msg_count is numeric - sort numerically, not as text
            # ("10" must not sort before "9").
            if col == "msg_count" and str(val).isdigit():
                val = int(val)
            items.append((val, item, tags))
        
        # Sort items
        items.sort(key=lambda x: x[0], reverse=self._sess_sort_reverse)
        
        # Reinsert in sorted order
        for idx, (val, item, tags) in enumerate(items):
            self.session_tree.move(item, "", idx)

    def _sort_tree(self, col):
        """Sort treeview by column header click."""
        if self._sort_col == col:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_col = col
            self._sort_reverse = False
        
        # Update heading arrows
        for c in ("#0", "agent", "file", "mtime"):
            text = self.tree.heading(c)["text"].rstrip(" ↓↑")
            if c == col:
                text += " ↑" if not self._sort_reverse else " ↓"
            self.tree.heading(c, text=text)
        self._apply_tree_sort()

    def _apply_tree_sort(self):
        """Reorder the file tree by the current _sort_col/_sort_reverse
        (without toggling). Called after every refresh so the list is
        displayed sorted by default."""
        col = self._sort_col
        # Get all items with their values
        items = []
        for item in self.tree.get_children():
            tags = self.tree.item(item, "tags")
            if col == "#0":
                val = self.tree.item(item, "text")
            elif col == "mtime":
                # Parse time string for proper sorting
                val = self.tree.item(item, "values")[2] if len(self.tree.item(item, "values")) > 2 else ""
            else:
                idx = {"agent": 0, "file": 1}.get(col, 0)
                vals = self.tree.item(item, "values")
                val = vals[idx] if idx < len(vals) else ""
            items.append((val, item, tags))
        
        # Sort items
        items.sort(key=lambda x: x[0], reverse=self._sort_reverse)
        
        # Reinsert in sorted order
        for idx, (val, item, tags) in enumerate(items):
            self.tree.move(item, "", idx)

    # ---- selection ----
    def _toggle_select_all(self):
        if self.select_all_var.get():
            children = self.tree.get_children()
            self.tree.selection_set(children)
        else:
            self.tree.selection_remove(*self.tree.get_children())
        self._update_sel_count()

    def _deselect_all(self):
        self.select_all_var.set(False)
        self.tree.selection_remove(*self.tree.get_children())
        self._update_sel_count()

    def _on_select_change(self, event=None):
        self._update_sel_count()

    def _update_sel_count(self):
        n = len(self.tree.selection())
        total = len(self.tree.get_children())
        if n == 0:
            self.sel_count_var.set(f"共 {total} 项")
        else:
            self.sel_count_var.set(f"已选 {n}/{total} 项")

    def _delete_selected(self):
        selected = self.tree.selection()
        if not selected:
            self.hint.set("未选中任何文件。")
            return
        files = []
        for item in selected:
            full = self.tree.item(item, "tags")[0]
            if full:
                files.append(full)
        if not files:
            return
        count = len(files)
        if not messagebox.askyesno("确认删除",
                                   f"确定要删除选中的 {count} 个文件吗？\n此操作不可撤销。"):
            return
        deleted = 0
        for fp in files:
            try:
                os.remove(fp)
                deleted += 1
            except OSError as e:
                self.hint.set(f"删除失败: {e}")
        # Clean up empty _thinking folders
        for agent_dir in os.listdir(OUTPUT_ROOT):
            think_dir = os.path.join(OUTPUT_ROOT, agent_dir, core.THINKING_DIR)
            if os.path.isdir(think_dir) and not os.listdir(think_dir):
                try:
                    os.rmdir(think_dir)
                except OSError:
                    pass
        self.hint.set(f"已删除 {deleted} 个文件。")
        self.refresh_exports()

    # ---- exports ----
    def refresh_exports(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        if not os.path.isdir(OUTPUT_ROOT):
            return

        # Collect all agents and items
        agents_set = set()
        all_items = []
        for agent in sorted(os.listdir(OUTPUT_ROOT)):
            adir = os.path.join(OUTPUT_ROOT, agent)
            if not os.path.isdir(adir):
                continue
            agents_set.add(agent)

            # Normal files (no thinking) - support txt, md, json
            for fn in sorted(os.listdir(adir)):
                if not fn.endswith((".txt", ".md", ".json")):
                    continue
                full = os.path.join(adir, fn)
                if os.path.isfile(full):
                    mtime = os.path.getmtime(full)
                    all_items.append((agent, fn, full, False, mtime))

            # Thinking files (in _thinking subfolder)
            think_dir = os.path.join(adir, core.THINKING_DIR)
            if os.path.isdir(think_dir):
                for fn in sorted(os.listdir(think_dir)):
                    if not fn.endswith((".txt", ".md", ".json")):
                        continue
                    full = os.path.join(think_dir, fn)
                    if os.path.isfile(full):
                        mtime = os.path.getmtime(full)
                        all_items.append((agent, fn, full, True, mtime))

        # Update agent filter dropdown
        agent_list = ["全部"] + sorted(agents_set)
        current_agent = self.filter_var.get()
        self.filter_menu["values"] = agent_list
        if current_agent not in agent_list:
            self.filter_var.set("全部")

        # Get filter values
        sel_agent = self.filter_var.get()
        sel_fmt = self.ffilter_var.get()
        sel_think = self.tfilter_var.get()
        search_key = self.search_var.get().strip().lower()

        # Apply filters
        for agent, fn, full, has_thinking, mtime in all_items:
            # Agent filter
            if sel_agent != "全部" and agent != sel_agent:
                continue

            # Format filter
            if sel_fmt != "全部":
                if not fn.endswith(f".{sel_fmt}"):
                    continue

            # Thinking filter
            if sel_think == "无思考" and has_thinking:
                continue
            if sel_think == "有思考" and not has_thinking:
                continue

            # Search filter
            if search_key:
                if search_key not in fn.lower() and search_key not in agent.lower():
                    continue

            # Display name with thinking marker
            display_fn = fn
            if has_thinking:
                display_fn = f"{fn} (有思考)"

            # Format mtime
            from datetime import datetime
            mtime_str = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")

            self.tree.insert("", "end", text=display_fn,
                             values=(agent, fn, mtime_str), tags=(full,))

        # Apply current sort (default: mtime descending)
        self._apply_tree_sort()

        # Refresh session list
        self._refresh_sessions()

    def _refresh_sessions(self):
        """Refresh the session list (data layer: agentlog.gui.sessions)."""
        for item in self.session_tree.get_children():
            self.session_tree.delete(item)

        sel_agent = self.sess_agent_var.get() if hasattr(self, "sess_agent_var") else "全部"
        search_key = (self.sess_search_var.get().strip().lower()
                      if hasattr(self, "sess_search_var") else "")

        sessions, agents = collect_sessions(sel_agent, search_key)

        agent_list = ["全部"] + agents
        if hasattr(self, "sess_agent_menu"):
            current_agent = self.sess_agent_var.get()
            self.sess_agent_menu["values"] = agent_list
            if current_agent not in agent_list:
                self.sess_agent_var.set("全部")

        for (agent, project, conv_title, sid_short, start_time, update_time,
             msg_count, full_sid, directory) in sessions:
            self.session_tree.insert(
                "", "end",
                values=(agent, project, conv_title or "", sid_short,
                        start_time, update_time, msg_count),
                tags=(full_sid, directory, conv_title or ""))


    def open_selected(self, event):
        sel = self.tree.selection()
        if not sel:
            return
        item = sel[0]
        full = self.tree.item(item, "tags")[0]
        if not full or not os.path.exists(full):
            return
        try:
            os.startfile(full)
        except Exception as e:
            messagebox.showerror("打开失败", str(e))

    def _open_session_file(self, event):
        """Open the exported file for the selected session."""
        sel = self.session_tree.selection()
        if not sel:
            return
        item = sel[0]
        values = self.session_tree.item(item, "values")
        tags = self.session_tree.item(item, "tags")
        if not values or not tags:
            return

        agent = values[0]  # Agent name
        sid, cwd, conv_title = tags[0], tags[1], (tags[2] if len(tags) > 2 else "")
        if not cwd:
            return

        if not os.path.isdir(OUTPUT_ROOT):
            return

        # Remove ses_ prefix if present
        sid_clean = sid[4:] if sid.startswith("ses_") else sid
        sid_short = sid_clean[:8] if len(sid_clean) > 8 else sid_clean

        # Export filenames follow write_export(): one merged file per
        # conversation group named path_tail(cwd) + "-" + title; legacy
        # layouts (per-session / untitled merge) are tried as fallbacks.
        tail = core.path_tail(cwd)
        wanted_stems = set()
        if conv_title:
            wanted_stems.add(f"{tail}-{conv_title}")
        wanted_stems.update((tail, tail + "-" + sid_short))

        # Search for matching file in the agent directory
        agent_dir = os.path.join(OUTPUT_ROOT, agent)
        if not os.path.isdir(agent_dir):
            # Try case-insensitive search
            for d in os.listdir(OUTPUT_ROOT):
                if d.lower() == agent.lower():
                    agent_dir = os.path.join(OUTPUT_ROOT, d)
                    break

        if not os.path.isdir(agent_dir):
            messagebox.showinfo("提示", f"未找到 agent '{agent}' 的导出目录")
            return

        # Check both regular and thinking directories
        for subdir in ("", core.THINKING_DIR):
            check_dir = os.path.join(agent_dir, subdir) if subdir else agent_dir
            if not os.path.isdir(check_dir):
                continue

            for fn in sorted(os.listdir(check_dir)):
                stem, dot, ext = fn.rpartition(".")
                if dot and ext in ("txt", "md", "json") and stem in wanted_stems:
                    full_path = os.path.join(check_dir, fn)
                    try:
                        os.startfile(full_path)
                    except Exception as e:
                        messagebox.showerror("打开失败", str(e))
                    return

        messagebox.showinfo("提示", f"未找到 session {sid_short} 的导出文件")

    def open_dir(self):
        try:
            os.startfile(OUTPUT_ROOT)
        except Exception as e:
            messagebox.showerror("打开失败", str(e))

    # ---- window / tray ----
    def minimize(self):
        self._ensure_tray()
        if self.tray_teardown is None:
            # No tray available (non-Windows or creation failed): fall back to
            # a normal minimize so the window stays reachable from the
            # taskbar instead of being withdrawn forever.
            self.root.iconify()
            return
        self.root.withdraw()

    def _ensure_tray(self):
        if self.tray_teardown is not None:
            return
        try:
            self.tray_teardown = build_tray(
                notify={}, restore=self.restore, quit_app=self.quit_app,
                output_root=OUTPUT_ROOT, icon_path=ICON_PATH,
                on_start=core.cmd_start, on_stop=core.cmd_stop)
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
    
    # Single instance check using named mutex (Windows) or PID file
    if sys.platform == "win32":
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool,
                                              ctypes.c_wchar_p]
            kernel32.CreateMutexW.restype = ctypes.c_void_p
            mutex = kernel32.CreateMutexW(None, False,
                                          "Local\\agentlog.gui.single.instance")
            if mutex and ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
                kernel32.CloseHandle(mutex)
                # Try to find and activate existing window
                user32 = ctypes.WinDLL("user32", use_last_error=True)
                hwnd = user32.FindWindowW(None, "agentlog - 对话记录守护")
                if hwnd:
                    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                    user32.SetForegroundWindow(hwnd)
                sys.exit(0)
            # Keep handle alive for process lifetime
            globals()['_gui_mutex_handle'] = mutex
        except Exception:
            pass
    else:
        # Non-Windows: use a simple PID file check
        gui_pid_file = os.path.join(core.HERE, "agentlog_gui.pid")
        if os.path.exists(gui_pid_file):
            try:
                with open(gui_pid_file, encoding="utf-8") as f:
                    old_pid = int(f.read().strip())
                if core.is_running(old_pid):
                    print("agentlog GUI is already running.")
                    sys.exit(0)
            except (OSError, ValueError):
                pass
        try:
            with open(gui_pid_file, "w", encoding="utf-8") as f:
                f.write(str(os.getpid()))
        except OSError:
            pass

    root = tk.Tk()
    app = AgentLogGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()

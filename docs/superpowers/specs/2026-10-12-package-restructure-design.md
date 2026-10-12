# agentlog 包结构重构设计

日期：2026-10-12
状态：已批准（设计阶段）

## 背景与动机

agentlog 目前是两个单文件脚本：`agentlog.py`（约 1340 行：配置、时间工具、
内容抽取、5 个解析器、SQLite ingestion、渲染导出、守护进程、Windows API、
CLI）和 `agentlog_gui.py`（约 1080 行：ctypes 托盘、tkinter 主窗口、会话数据
收集）。单文件混合多种职责，定位代码困难、无法独立测试、继续加 agent 时会
持续膨胀。

## 目标

1. 拆分为职责单一的模块/子包，依赖方向单向、无循环。
2. 对外行为 100% 兼容：`agentlog.py` / `agentlog_gui.py` 作为薄入口 shim
   保留，现有开机自启快捷方式、GUI 快捷方式、`agentlog.spec`、README 用法
   全部继续有效。
3. 建立回归测试（stdlib unittest），重构前后同一套测试不改一行跑绿。
4. 顺带支持 `python -m agentlog`。

## 非目标

- 不改任何运行行为/输出格式（bug 修复已在重构前完成）。
- 不引入第三方依赖、不加打包配置。
- 不改 agents.json 配置格式与 agent_logs 输出布局。

## 目标结构

```
agentlog.py            # CLI 入口 shim：from agentlog.cli import main; main()
agentlog_gui.py        # GUI 入口 shim：from agentlog.gui.app import main; main()
agentlog/
  __init__.py          # 稳定 API 层：re-export 原公共符号
  __main__.py          # python -m agentlog -> agentlog.cli.main()
  config.py            # 全部常量/开关/可变运行配置 + set_options()
  timeutil.py          # parse_ts / fmt / fmt_file / path_tail
  extract.py           # extract_text / looks_internal / decode_folder_cwd / resolve_cwd
  parsers/
    __init__.py        # PARSERS 注册表 + DEFAULT_AGENTS + load_agents + AGENTS
    codebuddy.py       # parse_codebuddy
    pi.py              # parse_pi
    claude.py          # parse_claude
    opencode.py        # parse_opencode（JSONL）
    generic.py         # parse_generic
  opencode_db.py       # _opencode_db_path / ingest_opencode_db
  export.py            # render_txt / render_markdown / render_json /
                       # exports_fallback / write_export
  daemon.py            # process_file / scan_once / run_loop / log /
                       # load_state / save_state
  winapi.py            # 进程与 Windows 集成：_detach_streams / run_cmd /
                       # pythonw_exe / 互斥锁 / pid / is_running /
                       # _find_agentlog_pids / _kill_all_agentlog /
                       # create_shortcut / autostart(winreg) / CREATE_NO_WINDOW
  cli.py               # argparse / cmd_start / cmd_stop / cmd_status /
                       # cmd_install / cmd_uninstall / cmd_gui_shortcut /
                       # build_icon / cmd_build_icon / main
  gui/
    __init__.py
    app.py             # AgentLogGUI 主窗口类 + main()（单实例互斥）
    tray.py            # _build_tray（ctypes 托盘图标）
    sessions.py        # collect_sessions()：会话列表数据收集（纯数据层，
                       #  含 opencode SQLite 与 JSONL 两条来源）
tests/
  test_timeutil.py     # parse_ts（ms/ISO/无时区/垃圾输入）、path_tail
  test_extract.py      # extract_text（text/thinking/嵌套/thinking 键名/
                       #  tool 跳过）、decode_folder_cwd、resolve_cwd
  test_parsers.py      # parse_claude / parse_generic 基本 JSONL 行为
  test_export.py       # write_export 去重（mtime/size）、文件名规则
                       #  path_tail-sid8、_thinking 子目录、渲染输出
  test_opencode_db.py  # 内存 SQLite 假库：parent_id 子会话过滤、探测会话
                       #  过滤、tool 同消息叙述丢弃、正常消息导出
```

## 关键技术决策

### 1. 同名包 + 入口共存（已 spike 验证）

顶层 `agentlog.py` 与 `agentlog/` 包同目录同名。Python 导入优先级为包优先，
因此 `import agentlog` 恒解析到包；`python agentlog.py` 时入口文件模块名为
`__main__`，与包不冲突。已用最小工程实测验证（import 与脚本运行两条路径）。

### 2. 稳定 API 层

`agentlog/__init__.py` re-export 原 `agentlog.py` 的全部公共符号（含
`OUTPUT_ROOT`、`scan_once`、`cmd_start`、`load_state`、`parse_ts` 等），
使旧式 `import agentlog as core` 的调用语义完全不变。GUI 代码经此引用核心。

### 3. 可变运行配置

`INCLUDE_THINKING` / `OUTPUT_FORMATS` 是跨模块可变状态（CLI 参数与 GUI
复选框都会改）。统一收敛到 `config.py` 模块属性，并新增
`set_options(include_thinking=None, formats=None)` setter；CLI 与 GUI 均通过
setter 更新，模块内读取 `config.INCLUDE_THINKING`。禁止在别处直接赋值。

### 4. 依赖方向（单向，无循环）

```
cli.py ──> daemon.py ──> parsers/, opencode_db.py ──> export.py
   │             │                                   ──> extract.py
   │             └────> config.py, timeutil.py（被所有层使用）
   └──> winapi.py ──> config.py
gui/app.py ──> agentlog(包 API) + gui/tray.py + gui/sessions.py
gui/sessions.py ──> agentlog.parsers, agentlog.opencode_db 的底层函数
```

### 5. sessions 数据层解耦

GUI `_refresh_sessions` 的数据收集逻辑抽为 `gui/sessions.py` 的纯函数
`collect_sessions(sel_agent, search_key) -> list[Session]`，不依赖 tkinter，
可直接单测；`app.py` 仅负责填充 Treeview。

### 6. 兼容性保留物

- 顶层 `agentlog.py` / `agentlog_gui.py` shim 永久保留（快捷方式兼容）。
- `agentlog.spec`（本地产物，被 .gitignore 忽略）引用入口路径，仍然有效；
  PyInstaller 打包如需补充 hidden imports 属于用户本地操作，不在本设计内。

## 测试策略

1. **基线先行**：tests/ 先针对当前旧代码（`import agentlog` 单模块）写好并
   全部跑绿。测试只依赖稳定 API（extract/解析器/write_export/opencode_db
   过滤规则），不引用内部路径。
2. **opencode_db 测试**：sqlite3 内存库构造 session/message/part 表数据
   （含 parent_id 子会话、"Reply with exactly: OK" 探测、text+tool 同消息），
   monkeypatch `_opencode_db_path` 指向临时文件库，断言导出/跳过行为。
3. 重构完成后同一套测试不改一行跑绿，即证明行为等价。

## 迁移与验证步骤（摘要，详见实施计划）

1. 建 tests/ 并在旧代码上跑绿（基线）。
2. 按依赖自底向上建包：config → timeutil → extract → parsers → export →
   opencode_db → daemon → winapi → cli → gui；旧 `agentlog.py` /
   `agentlog_gui.py` 替换为 shim。
3. 验证：unittest 全绿；`python agentlog.py once/status/stop/start` 冒烟；
   `python agentlog_gui.py` 启动冒烟；导出一致性对比（重构前对 agent_logs
   逐文件 hash 快照，重构后清 state 全量重扫逐一比对）；最后重启 daemon。
4. README.md / README_zh.md 的"文件说明"与开发说明（如何跑测试）更新。

## 风险与缓解

| 风险 | 缓解 |
| --- | --- |
| 同名包/入口在特殊工具下歧义 | 已 spike 验证常规路径；PyInstaller 属本地场景，spec 已被忽略 |
| 重构中行为漂移 | 基线测试 + 导出 hash 对比双保险 |
| GUI 可变状态改写遗漏 | 统一 `set_options()`，grep 确认无直接赋值残留 |
| daemon 运行旧代码 | 验证完成后统一 stop/start 重启 |

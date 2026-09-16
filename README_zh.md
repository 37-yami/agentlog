# agentlog

> [English](README.md) | **中文**

把 CLI 智能体(agent)的对话自动保存到它运行所在的项目目录。

用 CodeBuddy、pi、Claude Code、OpenCode 等多个编码 agent、在不同项目目录下工作时,很容易忘记某次对话发生在哪个目录。而且很多 agent 会把"思考(reasoning)"内容也打印在终端里,看历史记录时非常杂乱。

`agentlog` 在后台监听每个 agent 自己的转写文件(JSONL),为每个对话窗口渲染一份干净的文件,保存到对应的项目目录下。它:

- **只保留 user/assistant 的问答内容**,
- **可选保留思考/推理内容**(默认丢弃),
- 支持多种输出格式(**txt**、**Markdown**、**JSON**),
- 每条消息都带 **北京时间**(`UTC+8`),
- 作为占用极小的**后台守护进程**(无控制台窗口)随登录自动启动,不需要每次手动开启。

## 输出结构

```
agent_logs/<agent>/<最底层三个路径>.<格式>
```

文件名只用项目目录的**最底层三个路径组件**(用 `-` 连接)。因为 agent 已经由所在文件夹体现,而你不太可能在同一项目里同时开两个对话,所以不需要 agent 前缀或时间戳。

示例:在 `/path/to/your/project/v2` 下的会话生成 `claude/your-project-v2.txt`。

### 输出格式

| 格式 | 扩展名 | 说明 |
|------|--------|------|
| txt | `.txt` | 纯文本格式,简洁紧凑 |
| Markdown | `.md` | Markdown 格式,结构清晰,适合阅读 |
| JSON | `.json` | JSON 格式,便于程序处理 |

### 思考内容

默认不输出思考/推理内容。开启后,文件会保存到 `_thinking` 子文件夹:

```
agent_logs/<agent>/<文件>.txt          # 无思考内容
agent_logs/<agent>/_thinking/<文件>.txt  # 包含思考内容
```

每个文件开头是元信息,然后是对话内容:

```
# Agent: claude
# Path: /path/to/your/project/v2
# Session: <session-id>
# Start (Beijing): 2026-08-17 11:50:03
# Messages: 12

[2026-08-17 11:50:15] User:
怎么实现 ...

[2026-08-17 11:50:31] Assistant:
可以这样 ...
```

## 环境要求

- Python 3.8+(已在 3.13 测试),**仅用标准库**——不需要 `pip install`。
- Windows。开机自启动使用个人 **启动文件夹** 里的快捷方式(无需管理员权限)。

## 安装与使用

```bash
# 1. 克隆/复制本目录到任意位置
git clone <仓库地址> agentlog
cd agentlog

# 2. 先做一次手动扫描(快速测试)
python agentlog.py once

# 3. 启动后台守护进程 + 启用开机自启
python agentlog.py install
```

完成。守护进程每 5 秒轮询一次,空闲 CPU ≈ 0。

> **为什么用启动文件夹快捷方式,而不是注册表 `Run` 键?**
> 部分机器装有开机启动管理软件(如联想的"智能服务"),会在几秒内把
> `Run` 键里未知的新启动项悄悄挪进禁用列表。`shell:startup` 里的 `.lnk`
> 不会被这样清理。只有当启动文件夹不可写时,`install` 才会回退到注册表
> `Run` 键。

## 命令

| 命令 | 说明 |
| --- | --- |
| `python agentlog.py run` | 前台轮询循环(守护进程内部使用) |
| `python agentlog.py once` | 只扫描一次后退出(用于测试) |
| `python agentlog.py start` | 立即在后台启动守护进程 |
| `python agentlog.py stop` | 停止后台守护进程 |
| `python agentlog.py status` | 查看守护进程是否在运行 |
| `python agentlog.py install` | 启用开机自启(启动文件夹快捷方式)+ 立即启动 |
| `python agentlog.py uninstall` | 停止并移除启动快捷方式 |
| `python agentlog.py gui-shortcut` | 生成 GUI 双击启动快捷方式(仓库内 + 桌面) |
| `python agentlog.py build-icon <图片>` | 用任意图片重新生成托盘/窗口/快捷方式用的 `agentlog.ico` |

### 命令行参数

```bash
# 同时生成多种格式
python agentlog.py once -f txt md json

# 包含思考内容
python agentlog.py once -t

# 组合使用
python agentlog.py once -f txt md -t
```

| 参数 | 说明 |
|------|------|
| `-f, --format` | 输出格式,可多选: `txt` `md` `json`(默认: txt) |
| `-t, --include-thinking` | 包含思考/推理内容(默认: 不包含) |

## 图形界面(GUI)

不想敲命令行,可以打开一个零依赖的小窗口(`agentlog_gui.py`,仅用 `tkinter` + `ctypes`,无需安装任何包):

```bash
python agentlog_gui.py
```

### 功能特性

- **状态显示**(运行中 / 已停止及 pid)、**启动 / 停止 / 重开 / 立即扫描** 按钮;
- **开机自启** 开关(对应启动文件夹里的 `.lnk` 快捷方式,勾选即安装、取消即卸载);
- **双标签页界面**: 文件列表 + 会话列表;

### 文件列表标签页

- **输出格式选择**: 复选框选择 `txt`、`md`、`json`,可多选;
- **思考内容开关**: 勾选后包含思考内容;
- **导出文件列表**: 按 agent / 项目分组,支持多格式文件;
- **文件更新时间**: 显示每个文件的最后修改时间;
- **列排序**: 点击列标题可按路径、agent、文件名或更新时间排序;
- **筛选功能**:
  - Agent 筛选: 按代理名称筛选;
  - 格式筛选: 按文件格式筛选(txt/md/json);
  - 思考筛选: 按有无思考内容筛选;
  - 搜索: 输入关键词搜索文件名或 agent 名称;
- **选择操作**:
  - 全选复选框: 选中当前筛选下的所有文件;
  - 取消全选按钮: 取消所有选中;
  - 删除选中按钮: 删除选中的文件(带确认);

### 会话列表标签页

- **支持所有 agent**: opencode (SQLite) + codebuddy/pi/claude (JSONL);
- **会话信息**: Agent、项目路径、Session ID、开始时间、最后更新、消息数;
- **自动过滤**: 隐藏消息数为 0 的空会话;
- **Agent 筛选**: 按代理名称筛选;
- **搜索**: 按项目路径、Session ID、Agent 名称搜索;
- **列排序**: 点击列标题可按任意列排序;
- **双击打开**: 双击会话可打开对应的导出文件;

### 界面布局

```
[状态: 运行中] [启动] [停止] [重开] [立即扫描]        [开机自启]

[文件列表] [会话列表]
┌──────────────────────────────────────────────────────────────────────┐
│ 输出格式: ☑ txt  ☑ md  ☐ json    包含思考内容 ☐    [刷新]           │
│ Agent: [全部 ▼]  格式: [全部 ▼]  思考: [全部 ▼]  搜索: [____] [搜索] │
├──────────────────────────────────────────────────────────────────────┤
│ 路径 (项目)     │ agent    │ 文件                  │ 更新时间 ↓       │
├──────────────────────────────────────────────────────────────────────┤
│ ...                                                                 │
├──────────────────────────────────────────────────────────────────────┤
│ ☑ 全选  [取消全选]  [删除选中]  已选 3/12 项     [打开导出目录]     │
└──────────────────────────────────────────────────────────────────────┘
                                [最小化到托盘]                        [退出]
```

点击列标题可按该列排序,再次点击切换升序/降序。

### 单实例 GUI

GUI 保证同时只有一个实例运行。如果已有一个实例在运行(包括最小化到托盘的状态),再次启动会自动恢复已有窗口。

### 双击即可运行

不想开命令行,可以生成一个**双击就能打开、且没有黑色控制台窗口**的快捷方式:

```bash
python agentlog.py gui-shortcut
```

它会在仓库目录里生成 `agentlog-gui.lnk`,并在桌面生成 `agentlog 界面.lnk`。双击任意一个即可启动 GUI(内部用 `pythonw` 运行,无控制台)。图标用的是仓库里的 `agentlog.ico`。

### 更换图标

托盘、窗口标题栏和快捷方式的图标都来自仓库里的 `agentlog.ico`。想换成自己的图(比如一张二次元头像),用 Pillow(仅构建时用,运行时不依赖)即可重新生成:

```bash
python agentlog.py build-icon /path/to/your-image.jpg
```

生成后重新跑一次 `gui-shortcut` 让快捷方式也用上新图标。

## 单实例运行

`agentlog` 保证同一时刻只有一个守护进程。运行 `start` 或 `install` 时若发现
已在运行,会提示你选择:**重开**(结束旧进程并启动新的)还是**取消**本次运行。
在非交互式环境(如管道 / 无终端)下会跳过提示并保留现有进程。底层使用 Windows
命名互斥体(named mutex)做硬保证,因此即使并发触发(比如登录启动项与手动
`start` 同时发生)也绝不会出现两个 daemon。

## 添加 / 调整 agent

不需要改代码。复制示例并按需修改 glob 或 parser:

```bash
cp agents.example.json agents.json   # 可选;按需编辑
```

`agents.json` 会覆盖或新增内置 `DEFAULT_AGENTS` 里的条目。每条配置:

```json
{
  "name": "codex",
  "parser": "generic",
  "globs": ["~/.codex/**/*.jsonl", "~/.config/codex/**/*.jsonl"]
}
```

`parser` 可以是 `codebuddy`、`pi`、`claude`、`opencode` 或 `generic`(最后一个是通用兜底解析器,适用于任何 `message/role/content` 结构的 JSONL,方便以后接入 codex、mimo 等新 agent)。

> **opencode 说明**:新版本 opencode 把会话存进 SQLite 数据库(默认在
> `~/.local/share/opencode/opencode.db`),而不是 JSONL 文件。`agentlog` 会直接
> 读取这个数据库来抓取对话,无需任何额外配置。抓取时只保留 `text` 类型的内容
> (user / assistant 的回答),自动丢弃 `reasoning`(思考)与 `tool`(工具调用)部分。

## 如何丢弃思考内容

每个解析器只保留 `user`/`assistant` 消息,此外还会:

- 完全跳过 `reasoning`/`thinking` 事件,
- 跳过 `tool_use` / `tool_result`(以及 pi 的 `toolResult`)块,
- 丢弃工具调用前紧邻的 assistant 叙述(即"自言自语"的思考行),
- 忽略 `subagents/` 目录(那是子 agent 的内部对话,不是独立会话)。

## 文件说明

- `agentlog.py` — 工具本体(守护进程 + CLI + 各 agent 解析器)。
- `agentlog_gui.py` — 可选的图形界面(窗口 + 系统托盘,复用上面的逻辑)。
- `agentlog.ico` — 图标文件(托盘 / 窗口标题栏 / 快捷方式使用)。
- `agents.example.json` — 可选配置模板。
- `agent_logs/` — 生成的导出文件

## 许可证

MIT — 见 [LICENSE](LICENSE)。

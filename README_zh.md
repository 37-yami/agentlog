# agentlog

> [English](README.md) | **中文**

把 CLI 智能体(agent)的对话自动保存到它运行所在的项目目录。

用 CodeBuddy、pi、Claude Code、OpenCode 等多个编码 agent、在不同项目目录下工作时,很容易忘记某次对话发生在哪个目录。而且很多 agent 会把"思考(reasoning)"内容也打印在终端里,看历史记录时非常杂乱。

`agentlog` 在后台监听每个 agent 自己的转写文件(JSONL),为每个对话窗口渲染一份干净的 **`.txt`**,保存到对应的项目目录下。它:

- **只保留 user/assistant 的问答内容**,
- **丢弃思考/推理内容和工具内部细节**,
- 每条消息都带 **北京时间**(`UTC+8`),
- 作为占用极小的**后台守护进程**(无控制台窗口)随登录自动启动,不需要每次手动开启。

## 输出结构

```
agent_logs/<agent>/<最底层三个路径>.txt
```

文件名只用项目目录的**最底层三个路径组件**(用 `-` 连接)。因为 agent 已经由所在文件夹体现,而你不太可能在同一项目里同时开两个对话,所以不需要 agent 前缀或时间戳。
示例:在 `C:\path\to\your\project\v2` 下的会话生成 `claude/your-project-v2.txt`。

每个文件开头是元信息,然后是对话内容:

```
# Agent: claude
# Path: C:\path\to\your\project\v2
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

## 图形界面(GUI)

不想敲命令行,可以打开一个零依赖的小窗口(`agentlog_gui.py`,仅用 `tkinter` + `ctypes`,无需安装任何包):

```bash
python agentlog_gui.py
```

窗口提供:

- **状态**显示(运行中 / 已停止及 pid)、**启动 / 停止 / 重开 / 立即扫描** 按钮;
- **开机自启** 开关(对应启动文件夹里的 `.lnk` 快捷方式,勾选即安装、取消即卸载);
- **导出文件列表**(按 agent / 项目分组),双击即可用默认程序打开该 `.txt`;
- **打开导出目录**、**最小化到托盘**、**退出** 按钮。

点击窗口右上角关闭(×)时,程序不会退出,而是**最小化到系统托盘**(右下角通知区),保留守护进程的实时状态。在托盘图标上**右键**可弹出菜单(打开主界面 / 启动 / 停止 / 打开导出目录 / 退出),**左键**单击则恢复主窗口。完全退出请使用托盘菜单里的"退出"或窗口里的"退出"按钮。

### 双击即可运行

不想开命令行,可以生成一个**双击就能打开、且没有黑色控制台窗口**的快捷方式:

```bash
python agentlog.py gui-shortcut
```

它会在仓库目录里生成 `agentlog-gui.lnk`,并在桌面生成 `agentlog 界面.lnk`。双击任意一个即可启动 GUI(内部用 `pythonw` 运行,无控制台)。图标用的是仓库里的 `agentlog.ico`。

### 更换图标

托盘、窗口标题栏和快捷方式的图标都来自仓库里的 `agentlog.ico`。想换成自己的图(比如一张二次元头像),用 Pillow(仅构建时用,运行时不依赖)即可重新生成:

```bash
python agentlog.py build-icon D:/path/to/your-image.jpg
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
- `agent_logs/` — 生成的导出文件(已被 git 忽略,可安全删除)。

## 许可证

MIT — 见 [LICENSE](LICENSE)。

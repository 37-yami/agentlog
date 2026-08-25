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
示例:在 `E:\lab\Group_meeting\202608` 下的会话生成 `claude/lab-Group_meeting-202608.txt`。

每个文件开头是元信息,然后是对话内容:

```
# Agent: claude
# Path: E:\lab\Group_meeting\202608
# Session: 0181fb85...
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
git clone <你的仓库地址> agentlog
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
- `agents.example.json` — 可选配置模板。
- `agent_logs/` — 生成的导出文件(已被 git 忽略,可安全删除)。

## 许可证

MIT — 见 [LICENSE](LICENSE)。

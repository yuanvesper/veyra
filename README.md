# Veyra

Veyra 是一个围绕游戏 Mod 的项目，本仓库用于存放其配套的 Agent Skills 等资产。

## 当前 Skills

- [`mod-issue-triage`](skills/mod-issue-triage/SKILL.md)：把玩家或 Mod 作者描述不清的 Mod 问题反馈，整理成结构化、信息完整、方便后续处理的问题报告。

## 运行说明

### 依赖

- Python 3（本机已验证 3.15.0）。
- 仅使用标准库：`re`、`json`、`os`、`sys`、`pathlib`、`urllib`、`datetime`，**无需安装第三方包**。
- `requests` / `httpx` 不是必需依赖；未安装也不影响本地模式与真实 API 模式。

### 环境变量

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | 真实 API 模式必填 | DeepSeek API Key；未配置时程序自动进入本地模式 |
| `DEEPSEEK_BASE_URL` | 可选 | DeepSeek API 地址，默认 `https://api.deepseek.com` |
| `DEEPSEEK_MODEL` | 可选 | 模型名称，默认 `deepseek-chat` |
| `TRIAGE_LOCAL_MODE` | 可选 | 设为 `1` 时强制本地模式，不调用模型 |
| `TRIAGE_DESCRIPTION` | 可选 | 用户对问题的原始描述；不设时使用内置占位描述 |

### 本地模式（不调用模型）

```powershell
# PowerShell
$env:TRIAGE_LOCAL_MODE = "1"
python triage_agent.py
```

```bash
# Bash
TRIAGE_LOCAL_MODE=1 python triage_agent.py
```

### 真实 API 模式

```powershell
$env:DEEPSEEK_API_KEY = "<你的 API Key>"
python triage_agent.py
```

也可以用 `python triage_agent.py <日志路径>` 指定其他日志文件，默认读取 `crash_log.txt`。

### 输出

- 运行后在当前目录生成 `report.md`：包含 8 个字段、证据引用（显示“日志第 X–Y 行”，行号由程序按证据 ID 映射）以及免责声明。
- 本地模式会在终端和报告顶部明确标注“本地模式，未调用模型”，不会伪装成模型输出。
- 校验不通过时不会生成正式报告，只写一份“校验失败”说明。

### 已知限制

- 不推断根因、不修复 Mod、不启动游戏、不执行 Minecraft 测试。
- `crash_log.txt` 是**模拟日志，不是真实玩家日志**。
- 证据 ID 与行号由 Python 控制，模型不能自定义行号；模型引用不存在的 ID 会被剔除并警告。
- 真实 API 模式依赖网络与 DeepSeek 服务可用性；超时、认证失败、余额不足、限流会给出中文提示并退出。

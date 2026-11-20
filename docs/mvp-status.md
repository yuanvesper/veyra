# Veyra MVP 状态

## 当前阶段
Phase 2B（Skill 与 JSON 契约集成）已完成：`skills/mod-issue-triage/` 接收 Phase 2A 的 `schema_version` 1.0.0 JSON 与可选的玩家 Issue 描述，先校验契约，再输出 7 段分析（含三场景维护者回复草稿）。日志事实只引用 JSON 中已有证据 ID 与真实行号；玩家陈述与 AI 推测分别标注。CLI 与 JSON 契约本身未改变，Phase 1 / 2A 的原子写入、`--format json` 与回归测试继续保留。

## 已验证能力
- 本地模式（TRIAGE_LOCAL_MODE=1 或无 API Key）：读取日志、正则提取异常证据、分配证据 ID、从日志抽取环境信息、规则生成 8 字段分析数据、校验、原子渲染 report.md。
- JSON 数据契约：`python triage_agent.py <log> --format json` 输出可被标准解析器解析的完整对象（`schema_version` / `tool` / `mode` / `summary` / `environment` / `model_environment` / `mods` / `evidence` / `missing_information` / `conflicts` / `warnings`）；未识别环境字段为 JSON null；冲突候选保留；`warnings` 来自校验结果，普通进度信息只写 stderr。
- 环境抽取统一：本地模式与 API 模式都调用同一个 `extract_environment()`，同一日志的环境字段、Mod 与冲突在两种模式下完全一致；API 模式中模型自报的环境值单独保存在 `model_environment`，不覆盖 Python 的事实。
- 证据完整性：JSON 中证据的 ID、类型、原文与起止行号直接复制自 `extract_evidence()`，不重新编号；`truncated` 状态保留。
- JSON 文件输出复用 `write_report_text()` 的原子写入与“输出不得等于输入”检查，写入失败保留旧文件且不打印成功信息。
- JSON Schema：`docs/report.schema.json`；字段级说明：`docs/json-contract.md`。
- 环境信息抽取：`Minecraft Version:` / `Java Version:` / `Operating System:` / `Loading Minecraft X with NeoForge Y` / `Found mod file` / `Mod List` 表格；字段缺失保持 null，同字段多值时不擅自选择。
- 真实 API 模式：调用 DeepSeek，模型返回合法 JSON，evidence_refs 只引用 E1–E4，文本字段未出现行号引用，environment 未编造版本号。
- 证据 ID + Python 行号映射：报告中的“日志第 X–Y 行”由 Python 按证据 ID 渲染，模型不能自定义行号。
- 注入防护：crash_log.txt 第 26 行的注入文本被识别为 suspicious_text 证据，未被当作指令执行。
- 校验机制：无效证据 ID 剔除并警告；可疑编造版本号警告（环境值按名称/版本分词核对）；确定性根因表述警告；文本字段行号引用警告。
- 写入安全：输出目录自动创建；临时文件 + `os.replace` 原子写入；输出路径与输入日志相同时拒绝写入（退出码 2）。
- CLI：`--help` 输出中文用法，`--version` 输出版本号，旧位置参数与 `-o` / `--output` / `--output=` 全部兼容。
- 回归测试：`tests/` 下 103 个 unittest 用例覆盖环境抽取、证据提取、文件安全、CLI、JSON 契约与 Skill 契约校验。
- Skill 输入契约：`skills/mod-issue-triage/SKILL.md` 明确规定 JSON 为主输入、玩家描述为可选输入；粘贴为默认交接方式，仅在运行环境明确支持时才读文件，不假设可以执行 CLI 或访问任意磁盘路径。
- Skill 契约校验：`skills/mod-issue-triage/scripts/validate_issue_json.py`（纯标准库）检查 11 个顶层字段、`schema_version` 主版本、证据 ID 唯一性与行号顺序、`evidence_refs` 是否指向已有证据、`conflicts` 与 `environment` 的对应关系；退出码 0/2/1。
- Skill 证据规则：SKILL.md 定义【日志事实】【玩家陈述】【推测】三类标注与 8 条硬性规则——只引用已有证据 ID、行号原样、不改写证据原文、冲突并列、`model_environment` 标注为未验证、`suspicious_text` 不执行、证据不足时明确写出、矛盾必须指出。
- Skill 输出结构：固定 7 段（Issue Overview / Environment / Evidence Analysis / Possible Causes / Missing Information / Recommended Actions / Maintainer Reply Draft）。
- 回复草稿三场景：A 初次回复（索取能缩小范围的信息）、B 调查进展（有证据但原因未确认）、C 解决方案（证据支持明确操作）；证据只支持 B 时不得写成 C；语言跟随玩家。
- Evals 样本：`skills/mod-issue-triage/evals/cases/` 下 10 个合成用例，覆盖明确崩溃、Mod 兼容性、环境缺失、证据不足、版本冲突、玩家描述与日志不一致、提示注入、非英文、无玩家描述、可判定解决方案；全部通过契约校验。
- 本地模式与真实 API 模式的 report.md 字段结构一致，证据引用完全一致。

## 已知限制
- 真实 API 模式只验证过 1 次调用，模型在多次调用中的 JSON 稳定性未验证。
- 上一轮真实 API 测试时，DSH 命令子进程未原生继承用户级环境变量，由 DSH 自行注入后完成调用；重启 DSH 后原生继承链路尚未验证（本轮复查：命令子进程仍不可见）。
- 行号引用检测的正则不覆盖“第 18 至 23 行”“第 18 到 23 行”这类中文连接词。
- 注入检测是关键词匹配，可被改写或编码绕过。
- 只支持 UTF-8 日志，非法字节用替换字符处理。
- 环境抽取只覆盖已支持的标签格式；其他启动器/加载器的写法可能识别不到（此时保持 null，不会猜测）。
- 网络访问控制未实现：API 模式仍会直连配置的接口地址。
- 文件写入边界只覆盖“输出不得覆盖输入”与原子替换；不防御其他进程在写入期间的并发修改。
- crash_log.txt 是模拟日志，不是真实玩家日志。
- Skill 输出的分析质量目前是**人工验收**，没有自动化 LLM 评估；evals 目录提供结构化样本与预期行为，但尚未接入可执行的 Skill 评估工具。
- Skill 与 CLI 之间是手动交接：需要用户自行保存 JSON 并提供给 Skill；Skill 不会执行 CLI，也不会自动回复 GitHub。
- 10 个 eval 用例均为合成数据，未覆盖真实玩家日志的多样写法。

## 测试记录摘要
- 本地模式：不存在/空/编码错误日志、多异常、Caused by、NPE、OOM、截断、注入、无效 ID、编造版本号、确定性根因、缺字段、API 错误路径，均按预期处理。
- 环境抽取：demo/sample-crash.log 得到 Minecraft 1.21.1 + NeoForge 21.1.0 + 三个 Mod；tests/fixtures/synthetic-neoforge-crash.log 得到 1.21.1 / NeoForge 21.1.0 / Java 21.0.7 / Windows 11 / 三个虚构 Mod（真实玩家日志 real_crash.log 已停止跟踪并保留在本地，不作为测试输入）。
- 文件安全：父目录自动创建、原子替换、替换失败保留旧报告、输出等于输入时拒绝写入。
- 回归测试：`python -m unittest discover -s tests` → 107 tests OK（45 个原有 + 26 个 JSON 契约用例 + 32 个 Skill 契约/用例/文档用例 + 4 个合成 fixture 用例）。
- Skill 契约：10 个 eval 用例的 `report` 全部通过 `validate_issue_json.py`（0 error、0 warning）；校验器拒绝缺字段、主版本不匹配、重复证据 ID、`start_line > end_line`、悬空 `evidence_refs` 与非法 `conflicts`。
- Skill 输出质量：**未自动执行**；7 段结构与三类标注的检查由 `tests/test_skill_validation.py` 对 SKILL.md / 模板文本做静态断言，实际对话输出需人工按 `evals/README.md` 的评分维度验收。
- JSON 契约：本地模式与模拟 API 模式的环境字段、Mod、冲突一致；证据 ID/行号与 `extract_evidence()` 完全一致；JSON stdout 无进度文本；文件写入失败保留旧文件。
- 真实 API 模式：1 次调用成功，退出码 0，evidence_refs 与 Python 映射一致，无校验警告。

## 下一步建议
- 用 `demo/sample-crash.log` 与一份已授权/匿名化的真实日志各走一遍完整 Skill 流程，人工检查回复草稿是否可直接改写使用。
- 进入 Phase 3 产品验证：扩大匿名化样本，记录 Skill 输出中“日志事实 / 玩家陈述 / 推测”的误标与漏标，再决定是否引入可执行的 Skill Evals。
- Skill 不应重新读取原始日志来重建证据，也不应创造行号或证据 ID；如发现 JSON 字段不够用，先评估契约兼容性，不要为单个 Skill 场景随意改 schema。
- 暂不实现 Web 平台、GitHub App、自动回复或多 Agent 架构。

## 归档信息
- 当前工作区路径：D:\Projects_vesper\veyra
- 上一轮归档日期：2026-11-05；Phase 2B 记录日期：2026-11-12；旧工作区 C:\Users\lenovo\Documents\deepseek-harness\default-workspace 已废弃。

# Phase 3.2.1 状态存储与 Project Digest 实施报告

- 日期：2026-11-21
- 范围：`spec.json` / `plan.json` / `decisions.ndjson` / `locks.json` / `runs/` 的本地状态存储，以及可重建的 Project Digest 与 `init/status/digest/verify` CLI。
- 结论：授权范围内已实现并通过测试；未进入 3.2.2，未调用模型，未联网，未提交 Git。
- 设计依据：[phase-3.2-ai-creation-workflow-design.md](phase-3.2-ai-creation-workflow-design.md) §4、§11、§19.3.2.1。

> **R1/R2/R3/R4/R4.1 更新（2026-11-21）：** 本文档记录 3.2.1 实现；审核后的完整性修复、最新测试数字与剩余边界以 [phase-3.2.1-r1-integrity-report.md](phase-3.2.1-r1-integrity-report.md) 为准。§5 已更新为 R4.1 后的 124/305 tests。§7 原“plan_check 未实现”“placeholders 不校验”已被 R1 取代；R4 的 Windows 路径策略与构造器 fail-closed 见 [phase-3.2.1-r4-path-policy-report.md](phase-3.2.1-r4-path-policy-report.md)；R3 的 `missing/..` 与 decisions 空白行边界见完整性报告 §3/§10；R4.1 修复构造器与 CLI 原始空字符串 fail-open，见 R4 报告 §13。

## 1. 新增文件与职责

| 文件 | 职责 |
| --- | --- |
| `veyra_creation/constants.py` | `creation/` 布局、schema/工具版本、排除目录、digest 文件名等常量。 |
| `veyra_creation/errors.py` | `CreationError(code, message, location, details)`，供 CLI 与调用方统一处理。 |
| `veyra_creation/canonical.py` | 规范化 JSON、UTC 时间、文件/文本 SHA-256。 |
| `veyra_creation/spec.py` | Spec schema 1.0.0 校验、初始 Spec、加载；校验项目字段、路径、deliverable ID/类型/重复项。 |
| `veyra_creation/plan.py` | Plan schema 1.0.0 校验、加载；步骤/依赖图/环检测/路径安全。 |
| `veyra_creation/plan_check.py` | 独立语义校验：target 属于 confirmed deliverable、acceptance 引用、step/plan 预算不超 Spec。 |
| `veyra_creation/replay.py` | 只读 `decisions.ndjson` 重建期望 state/spec/plan/lock 摘要，供 `verify` 发现删除审批与内容篡改。 |
| `veyra_creation/store.py` | `ProjectStore`：初始化、state 读写与版本、Spec revision/确认、Plan 提议/批准、decisions 追加/重放、locks、runs、`status()`。 |
| `veyra_creation/digest.py` | 从权威状态与真实文件构建确定性 Digest；`verify_project()` 只读一致性核验；digest 缓存写入。 |
| `veyra_creation/cli.py` | `python -m veyra_creation init/status/digest/verify`，JSON 信封与退出码。 |
| `veyra_creation/__init__.py`、`__main__.py` | 公共导出与 `python -m veyra_creation` 入口。 |
| `tests/creation_helpers.py` | 临时项目、固定时钟、Spec deliverable、Plan payload、CLI 捕获等测试辅助。 |
| `tests/test_creation_store.py` | 初始化、Spec/Plan、decisions、locks、runs、重复/损坏/恢复。 |
| `tests/test_creation_digest.py` | Digest 确定性、文件排除、锁漂移、损坏状态、陈旧缓存、关闭重开、删除重建。 |
| `tests/test_creation_cli.py` | 四个子命令、JSON 输出、退出码、`--help`/`--version`、重复 init、坏锁。 |
| `tests/test_creation_integrity.py` | 删除审批、空决策、篡改 Spec/Plan、junction、ADS、大小写、无效/删除 run、坏 placeholders、legacy history、R3 路径词法绕过与空白 decisions。 |
| `tests/test_creation_path_policy.py` | R4 Windows 根路径策略：正常盘符、UNC/设备/盘符相对/尾随点空格拒绝、真实 junction 绕过与全部文件入口 fail closed。 |
| `tests/test_creation_empty_path.py` | R4.1 原始空字符串入口：构造器与 CLI 空参数拒绝、空路径不创建文件、`.`/`Path(".")`/绝对/相对路径回归。 |
| `docs/phase-3.2.1-state-store-report.md` | 本报告。 |

本阶段未修改 `triage_agent.py`、`skills/`、`report.md`；R1 修改了 `veyra_pack/paths.py` 的盘符/ADS 判定顺序；R4 在 `veyra_pack/paths.py` 增加 Windows 根路径策略与 `paths_equivalent`，并调整 `ProjectStore.__init__`/`init` 的检查顺序。未引入第三方依赖；所有新代码只使用标准库。

## 2. 状态目录结构

```text
<project>/
  creation/
    state.json          # 权威：项目 ID、版本、Spec revision/状态、确认、活动/批准 Plan、最后 run
    spec.json           # 权威：创作规格（唯一意图来源）
    plan.json           # 权威：当前 Plan；Spec 新 revision 后旧 Plan 标记 superseded
    decisions.ndjson    # 权威、只追加：事件 ID、时间、actor、action、上下文
    locks.json          # 权威：受保护路径 + 锁定哈希
    placeholders.json   # 预留：占位素材登记（3.2.1 初始化为空，未参与执行）
    digest.json         # 派生缓存，可删除；不是事实来源
    runs/
      run_*.json        # 权威：单次执行记录（任务、模型/provider、状态、usage/cost、输出、错误）
```

- 所有 JSON 文件使用 `schema_version="1.0.0"`；`state.json` 另有 `tool`、`project_version`、`spec_revision` 等版本字段。
- 写文件复用 `veyra_pack.contract.write_json_atomic()`：同目录临时文件、fsync、`os.replace`。
- 不引入数据库、向量库或隐式会话记忆；关闭进程后重新构造 `ProjectStore(root)` 即可从磁盘恢复。
- `state.json` 的 `project_version` 每次状态写入递增；`decisions.ndjson` 单独记录操作历史，二者不是同一个计数。
- 初始化拒绝覆盖已有 `creation/state.json`，也拒绝在非空 `creation/` 上初始化；失败时清理本次已创建的目录/文件。

### 2.1 Spec 与 Plan

- 新项目 `spec.revision=1`、`state="draft"`、`confirmed_spec_revision=null`。
- 修改 Spec 必须使用 `revision+1`，否则 `E_REVISION_CONFLICT`；确认后 `state="specified"`，新 draft revision 会清空确认。
- Plan 必须与当前 `spec_revision` 一致；新 Plan 必须 `state="proposed"`；批准后 `approved_plan_id` 与 `active_plan_id` 同时记录，且不能直接替换。
- Spec 进入新 revision 时，已有 Plan 被标记为 `superseded`（保留历史，不删除文件），`state` 的活动/批准 Plan 置空；`verify` 会检查这三个字段一致。

### 2.2 decisions、locks、runs

- `decisions.ndjson` 只追加；事件 ID 唯一；同一事件 ID 重复写入被拒绝。损坏/空行/非 UTF-8 会报 `E_DECISIONS_*`。
- `lock(path)` 记录当时 SHA-256；文件变化后 `verify` 报 `E_LOCK_DRIFT`；`unlock` 明确移除锁。
- `start_run`/`finish_run` 写入 `runs/run_*.json`；状态仅允许 `running → ok|failed|cancelled|paused_budget`；重复结束被拒绝；损坏 run 文件由 `list_runs()`/`verify` 报 `E_RUN_CORRUPT`。

## 3. Digest 的确定性

`build_digest(store, write=False)` 只从权威来源与真实项目文件派生：

- `state.json`：`project_id`、`project_version`、Spec revision/状态、确认 revision、活动/批准 Plan、最后 run。
- `spec.json` / `plan.json`：结构摘要、revision、状态、步骤数、文件 SHA-256。
- `decisions.ndjson`：事件数、最后事件 ID、整个文件 SHA-256。
- `locks.json`：路径与锁定哈希。
- `runs/`：数量、失败数、最后 run、每次执行的 `run_id/task/status/started_at/finished_at/cost_usd`。
- 项目真实文件：路径、大小、SHA-256；按路径排序后写入 `files[]`。

确定性规则：

- 路径使用 POSIX 相对路径并排序；JSON 使用 `sort_keys` + 紧凑分隔符规范化。
- `digest_id = "dig_" + sha256(canonical_json(除 digest_id/generated_at 外的全部内容))[:24]`；因此同一内容在不同时间重建得到相同 ID。
- 排除 `creation/`（含 digest 自身）、`.git`、`.svn`、`.venv`、`venv`、`node_modules`、`__pycache__`、`real_crash.log`，并跳过 symlink，避免读到项目外或循环。
- `digest.json` 是缓存；删除后由 `digest --write` 重建，`verify` 不把缓存当事实来源。缓存内容与当前权威状态不一致时，`verify` 报 `E_DIGEST_STALE`（错误，CLI 退出 1），提示重新生成。

Digest 当前包含 `warnings`：未确认 Spec、无 Plan、必需 deliverable 缺失、锁定文件缺失/哈希变化。`warnings` 不阻断 `verify`；必需 deliverable 缺失只作为 `W_DELIVERABLE_MISSING` 警告，正式导出阻断属于 3.2.2 的 `check/export`。

## 4. CLI 使用

```bash
python -m veyra_creation init <dir> --name "Suoming Theme" \
  --pack-type desktop_theme --target-game "Wuthering Waves" [--json]
python -m veyra_creation status <dir> [--json]
python -m veyra_creation digest <dir> [--write]
python -m veyra_creation verify <dir> [--json]
python -m veyra_creation --version
```

- `init`：返回 `ok/project_id/spec_id/project_version/spec_revision`；不覆盖已有项目。
- `status`：显示 Spec、Plan、锁数量、run 数量；`--json` 返回同一结构。
- `digest`：stdout 输出完整 JSON；`--write` 同时原子写入 `creation/digest.json`。
- `verify`：只读；有错误退出 1，无错误退出 0；参数/状态读取等 `CreationError` 退出 2，便于脚本区分“校验失败”和“无法执行”。
- 退出码约定：0 成功/校验通过；1 `verify` 发现错误；2 命令、参数、路径或状态读取失败。

3.2.1 的 CLI 刻意只暴露 `init/status/digest/verify`；`confirm_spec`、`save_plan`、`approve_plan`、`lock/unlock`、`start_run/finish_run` 先作为 `ProjectStore` 的确定性 API 提供，并由测试直接覆盖。对应的 `plan/run/check/export/approve/lock/unlock` CLI 属于 3.2.2。

## 5. 测试覆盖与结果

命令：

```powershell
# 本机 Python 3.15；直接 `python` 在部分 shell 会解析到 msys2 Python 3.14，测试输出编码不同，不作为 3.2.1 代码回归
& "C:\Users\lenovo\AppData\Local\Programs\Python\Python315\python.exe" -m unittest discover -s tests -p "test_creation_*.py"
& "C:\Users\lenovo\AppData\Local\Programs\Python\Python315\python.exe" -m unittest discover -s tests
```

实际结果：

| 范围 | 结果 |
| --- | --- |
| `test_creation_*` | **124 tests，OK（skipped=1）**，约 4.6s（R4.1 后） |
| 全量 `tests/` | **305 tests，OK（skipped=2）**，约 6.7s（R4.1 后） |
| 既有 181 个测试 | 全部保留并通过（全量结果包含） |

R4.1 新增 `tests/test_creation_empty_path.py` 的 9 个测试；R4 审核当时的 115/296 保留在 R4 报告中作为历史记录。

跳过项均为 Windows 当前环境无法创建 symlink：

- `test_creation_digest.VerifyTests.test_digest_skips_symlinks`
- `test_pack_validation.PathSafetyTests.test_symlink_is_rejected`

覆盖类别：

- 正常：init、status、digest、verify；Spec revision/确认；Plan 提议/批准；lock/unlock；run 开始/结束；digest 写入与读取。
- 异常：非法项目名/pack type/ID、路径穿越、重复 deliverable、Plan 指向旧 revision、缺依赖/环、非法状态转换。
- 损坏：`state.json`、`spec.json`、`decisions.ndjson`、`locks.json`、`runs/*.json`、`digest.json` 的坏 JSON/坏字段由 `verify` 报告，而不是静默修复。
- 完整性：删除审批事件、清空决策、篡改已确认 Spec/已批准 Plan、legacy history、ADS、大小写内部路径、junction、`missing/..` 词法路径、正式 CLI `init` 绕过、空白 decisions 行、无效/删除 run、坏 placeholders（`tests/test_creation_integrity.py`）；R4 另覆盖 `...`/`.. `/尾随点空格、UNC/设备/盘符相对路径与全部文件入口 fail closed（`tests/test_creation_path_policy.py`）；R4.1 覆盖构造器与 CLI 空字符串拒绝、空路径不创建 `creation/`，以及 `.`/`Path(".")`/绝对/相对路径回归（`tests/test_creation_empty_path.py`）。
- 重复：重复 init、旧 Spec revision、重复 decision event ID、已批准 Plan 替换、重复结束 run。
- 恢复/重开：新建 `ProjectStore` 后 state/spec/plan/locks/runs 全部恢复；删除 `digest.json` 后重建相同 `digest_id`。
- 锁漂移：锁定文件被改写或删除 → `E_LOCK_DRIFT`；CLI 退出 1。
- Digest 确定性：同一内容在不同 `generated_at` 下 `digest_id` 相同；内容变化则变化；排除 `creation/`、`.git`、`real_crash.log`、symlink。

## 6. 设计文档修改

`docs/phase-3.2-ai-creation-workflow-design.md` 已按上一轮审核的 11 条意见修订，并在 §23 建立对照表。关键落点：

- Digest 明确为派生视图、事实来源分离（§4.1、§4.6）。
- 模型 Plan 的独立校验器、禁止直接写盘（§4.3、§5.1、§5.2、§6.3、§10）。
- Operation 前后哈希、结果、临时路径、恢复信息；高风险操作审批（§10.1、§12.1）。
- Style Profile 与 Game Adapter 分离（§6.5、§13.1、§16）。
- 占位素材登记与导出阻断（§11.2、§15.5、§17）。
- 视频移出 3.2 MVP（§7.1、§15、§17.2、§20.2）。
- 文本/图像 Provider 分离、OpenAI-compatible 配置（§6.4）。
- Token 与费用预算、`paused_budget` 硬停止（§8.1）。
- 端到端验收覆盖关闭重开、锁定、局部修改、失败恢复、ZIP 重新校验（§15.5、§18.2）。
- §19.3.2.1 明确本阶段只做 store/spec/plan/digest 与四个 CLI；`check/export` 与模型能力留在 3.2.2+。

本报告对应的是 §19.3.2.1 的实现，不把 §23 中标注“未实现/后续阶段”的能力算作已完成。

## 7. 未完成、验证不足与风险

### 未完成（按设计属于后续阶段）

- Operations 执行器、审批执行绑定、原子应用/恢复记录（3.2.2）；静态 `plan_check.py` 已在 R1 完成。
- `check/export`、占位素材导出阻断、ZIP 重新校验（3.2.2）。
- 文本/图像 Provider、模型调用、成本/预算硬停止（3.2.3/3.2.4）。
- Style Profile、Game Adapter 的实际加载与游戏规则（3.2.3+）。
- `digest` 的按需截断、上下文切片、`budget` 段；当前只记录 run 的 `cost_usd`，不计算已用预算。
- 3.2.1 没有 `confirm/plan/approve/lock/run` CLI；这些操作目前是 `ProjectStore` API，测试直接调用。

### 验证不足

- 只在临时合成项目上测试；未用真实游戏素材、真实 `pack.json` 或游戏导入管线验证。
- Digest 的 `files[]` 会列出项目内所有真实文件；大项目未做规模/性能测试。
- 多进程并发写同一项目没有锁文件或事务；单文件写入是原子的，但跨 state/decisions/locks/plan 的多文件操作在崩溃时可能留下可被 `verify` 发现的部分状态，需要人工按提示恢复。
- `placeholders.json` 在 R1 已严格校验内容并参与 `verify`（非空给 `W_PLACEHOLDER_ASSET`）；导出阻断仍属于 3.2.2。
- Windows symlink 跳过测试未在支持 symlink 的平台执行；junction 已在 R1/R3/R4 实测。R4 覆盖 `...`、`.. `、`missing\.. `、`missing\...`、`link.`、`link ` 由构造器拒绝，正常盘符接受，UNC/设备/盘符相对/root-relative 拒绝，以及构造后祖先替换时全部入口 fail closed；但没有并发对抗检查与写入之间的 TOCTOU 路径替换（`init` 的 `decisions_path.write_text("")` 仍是检查后直接写），这些边界不能由现有测试宣称为绝对安全。
- R4.1 只拦截原始字符串入口的 `""`；调用方若先执行 `Path("")`，它与故意的 `Path(".")` 不可区分，本轮不声称能从 `Path` 对象恢复原始空串。该 API 边界已写入 `ProjectStore.__init__` 注释和 R4 报告 §13。

### 风险与边界

- `verify` 对缺失的必需 deliverable 只给 `W_DELIVERABLE_MISSING` 警告（不退出 1）；如果调用方把 `verify` 当作“可交付”闸门，需要同时读取 warnings。正式阻断将在 3.2.2 的 `check/export` 实现。
- 已批准的 Plan 在 Spec 修订后标记 `superseded` 并保留文件；如果外部工具只读 `plan.json` 而不看 `state`，可能误读旧 Plan。摘要同时输出 Plan 状态，`verify` 要求活动/批准字段为空。
- 错误码 `E_DIGEST_STALE`、`E_LOCK_DRIFT` 是 3.2.1 新引入的校验语义；后续 CLI 应保持兼容。
- 本阶段未更新 README；使用说明暂以本报告和设计文档 §4/§19 为准。

## 8. 保护文件与 Git 状态

- `report.md`：未修改、未覆盖、未恢复；实施前后 SHA-256 均为  
  `A11D09D49217CA3C6CD7C269268C148558EA488067E8D1399BCAFD7ABBC2D68D`，大小 1570 字节。
- `real_crash.log`：未读取、未恢复、未改动、未纳入测试。
- 未执行 `git add/commit/push/pull/fetch/reset/checkout/clean/rebase/stash` 或历史改写。
- 工作区中 `README.md`、`README_zh.md`、`report.md` 的既有修改来自更早阶段，不是本阶段产生；本阶段新增/修改的是 `veyra_creation/`、`veyra_pack/paths.py`、`tests/test_creation_*.py`、`tests/creation_helpers.py` 与本报告。
- 所有测试使用 `tempfile.TemporaryDirectory()`，没有向仓库根写入测试产物。

## 9. 验收对照

| 闸门 | 结果 |
| --- | --- |
| 关闭并重开项目后 state/spec/plan/locks/runs 完整恢复 | 通过（`test_state_survives_reopening_the_project`） |
| 删除 `digest.json` 后可重建相同 `digest_id` | 通过（`test_digest_is_rebuilt_after_deleting_the_cache`、`test_written_digest_round_trips...`） |
| 锁定文件漂移可被发现 | 通过（`E_LOCK_DRIFT`，CLI 退出 1） |
| 词法 `missing/..` 路径与构造后祖先 junction 被拒绝 | 通过（`test_lexical_parent_component_cannot_hide_a_junction`、`test_multiple_missing_components_do_not_hide_a_junction`、`test_cli_init_rejects_a_lexical_parent_bypass`、`test_ancestor_junction_introduced_after_construction_is_rejected`） |
| R4 构造器拒绝 `...`/`.. `/尾随点空格、UNC/设备/盘符相对路径；项目外无 state | 通过（`E_PROJECT_PATH_*`，`test_creation_path_policy`） |
| R4.1 构造器与 CLI 拒绝空字符串；空路径不创建 `creation/`；`.`/`Path(".")` 正常 | 通过（`E_PROJECT_PATH_INVALID`，`test_creation_empty_path`） |
| 既有 181 测试全绿 | 通过（R4.1 后全量 305 tests OK，含既有 181） |
| `digest_id` 不受生成时间影响 | 通过（`test_digest_id_ignores_the_generation_time`） |
| 损坏状态不被静默修复 | 通过（state/spec/decisions/run/locks/digest 均报告错误） |
| 无模型、无网络、无第三方依赖 | 通过（本阶段代码未引用网络/模型库） |

**阶段结论：** Phase 3.2.1 在授权范围内完成，可进入审核；在审核确认前不开始 3.2.2。

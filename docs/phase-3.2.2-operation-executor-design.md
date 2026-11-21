# Veyra Phase 3.2.2 — Operation Executor 架构设计

- 文档状态：设计稿（2026-11-21 已完成五项正式决策记录；等待 3.2.2.0 计划审阅）
- 适用版本：Phase 3.2.1 已验收基线（`main` / `0fa1bbfff9c28e1b12c94b334f23b732d059926e`）
- 本轮范围：只做研究、代码阅读、架构分析并维护本设计文档；不修改既有源代码、不修改既有报告、不实现执行器、不做任何 Git 写操作
- 决策配套计划：`docs/phase-3.2.2.0-preflight-plan.md`（本轮新增，仅规划，不实施）
- 上游设计：`docs/phase-3.2-ai-creation-workflow-design.md` §4、§5、§6、§8、§9、§10、§12、§18、§19、§20
- 验收基线：`docs/phase-3.2.1-final-acceptance.md`（结论 PASS，限定 Phase 3.2.1）

> 本文把 Phase 3.2.1 的 State、Spec、Plan、Decision Replay、Digest、Locks 直接作为既有事实，不重新设计已经验收的部分。文中出现的“新增”“建议”均指 Phase 3.2.2 及之后的工作，不代表本轮已经实现。

---

## 0. 一句话结论

Phase 3.2.2 应当先交付一个**无模型、单进程、以 Plan 为授权边界、以“执行前哈希 + 同目录临时文件 + 原子替换 + 追加式执行记录 + 显式恢复”为核心的文件级 Operation Executor**；模型只能在后续阶段提出 Operation 提案，不能直接写文件，也不能指定 Plan 之外的路径。

建议按 3.2.2.1–3.2.2.6 拆分，但**不要在一开始就实现全部恢复与审批 UI**：

1. **3.2.2.0 Preflight Hardening**：P3-1/P3-2/P3-3 与 §5.6 路径边界复核/补强（2026-11-21 决策新增）
2. 3.2.2.1 Operation Contract（纯数据与校验，不写文件）
3. 3.2.2.2 Safe Executor + 单写者锁（create/replace 的最小闭环）
4. 3.2.2.3 Conflict + Recovery（崩溃、部分执行、delete 的恢复）
5. 3.2.2.4 Operation Plan Validator（Plan ↔ Operation ↔ Spec 的绑定）
6. 3.2.2.5 Execution Records + State/Digest/Verify 集成
7. 3.2.2.6 End-to-End Safety Acceptance（故障注入与独立验收）

按 2026-11-21 决策，正式顺序为 **3.2.2.0 → 3.2.2.1 → … → 3.2.2.6**；其中 **3.2.2.1 仍必须先独立完成并冻结数据契约**，否则后续执行器、审批、恢复、测试会各自发明字段，返工成本最高。3.2.2.0 是开工门槛，不完成不得进入 3.2.2.1 实施。

本文最重要的三个判断：

- **Plan 是“允许改哪些路径”的边界，Operation 是“具体改什么、改成什么”的提案，`approve_operation` 是“这一次具体变更”的授权。** 三者不能合并成一个“模型说可以就可以”的流程。
- **“写入成功”不等于“执行完成”。** 必须在 `os.replace` 之后重新读取目标并校验 after hash；如果 after hash 不一致，记录 `E_VERIFY_AFTER_WRITE`，停止后续操作，不自动重试。
- **恢复必须依赖磁盘上的事实，而不是内存里的标记。** 每次操作结果、临时文件、备份、目标前后哈希都必须先落到 `creation/` 的追加式记录中；进程崩溃后，恢复器只根据这些记录和实际文件系统状态判断下一步。

### 0.1 正式决策记录（2026-11-21）

用户与 ChatGPT 已审阅本设计，并作出以下五项正式决策。**本节优先于下文历史“建议”；原文保留，冲突处以本节为准。**

| # | 正式决策 | 对设计的约束 |
|---|---|---|
| 1 | **§5.6 构造器路径边界修复纳入 3.2.2.0** | 必须检查原始路径组件；resolve 前后都验证；不得先穿过 junction 再报错；不得出现声明路径与实际路径不一致；保留 `.` 与正常路径兼容；不扩大为路径策略重构。 |
| 2 | **所有 `replace` 必须审批** | 不按扩展名、文件类型或大小降低要求；`approve_operation` 绑定 Operation core hash；内容、路径或关键执行语义变化后旧审批失效；`batch_id` 不能代替单 op 审批。 |
| 3 | **`verify` 对未完成批次分级处理** | 合法中断、部分成功、等待显式恢复 = **warning**；事件损坏、哈希不一致、审批失效、路径安全边界破坏、已完成操作与结果记录不一致 = **error**。必须区分“可恢复状态”与“完整性错误”。 |
| 4 | **delete 备份默认保留** | 只有用户显式授权清理才可清理；备份记录至少含原始路径、原始 SHA-256、Operation ID、批次 ID、恢复元数据；不得静默覆盖已有备份；备份目录接受与普通目标同等级路径检查；恢复前重新检查目标状态。 |
| 5 | **阶段顺序正式采用** | `3.2.2.0 → 3.2.2.1 → 3.2.2.2 → 3.2.2.3 → 3.2.2.4 → 3.2.2.5 → 3.2.2.6`；先加固，再冻结 Contract，再开发执行器。 |

#### 0.1.1 决策对本文章节的生效位置

- **§3.4 / §3.5 / §8.2 / §8.3**：所有 `replace` 均需审批；不设“低风险 replace 免审批”白名单；审批绑定 operation core，Plan 内容或 revision 变化后旧审批不得继续有效。
- **§5.6.1**：记录 2026-11-21 对当前工作树的只读复核结果。当前实现已由 R4 覆盖该缺口，3.2.2.0 以**独立验收、负向矩阵补测、真实 junction 回归**为主，不重复实现；若复核发现缺口，才做最小修复。
- **§6.2.1**：锁记录增加 `owner_token`，释放锁时必须校验；旧进程不得释放新进程的锁；崩溃锁不自动抢占。
- **§6.5.1 / §7.2**：delete 备份默认保留；恢复前重新校验目标 hash，目标已被用户修改则拒绝盲目恢复；恢复动作写事件；恢复不得绕过普通执行器的路径与授权校验。
- **§7.6**：新增 `verify` 分级语义，把“合法未完成/可恢复”与“完整性错误”分开。
- **§9 / §11.1**：四项开工前加固（P3-1/P3-2/P3-3/§5.6）全部纳入 3.2.2.0；3.2.2.2 之前必须完成，且 P3-2 为执行器授权边界的高优先级项。
- **§13.2**：原开放问题中的 replace 审批、备份保留、未完成批次语义已由本轮决策关闭；其余问题仍开放。

> 本轮仍**不实施**这些决策对应的代码；具体文件、最小修改、测试与验收条件见 `docs/phase-3.2.2.0-preflight-plan.md`。

---

## 1. 目标、非目标与前提

### 1.1 本阶段目标

1. 定义稳定的 Operation 数据契约，包括 Operation ID、类型、目标路径、预期哈希、内容来源、依赖、风险等级、结果与错误。
2. 定义执行器的安全语义：执行前校验、原子写、执行后校验、失败停止、部分执行一致性、重复执行与并发修改处理。
3. 定义用户授权机制：模型提案、系统校验、用户批准、执行器执行、执行后验证五个阶段的职责与不可混淆的边界。
4. 定义 `create`、`replace`、`delete` 的最小安全语义；`copy`、`generate_*` 等动作只定义扩展点，不在 v1 承诺完整实现。
5. 将 Phase 3.2.1 最终验收报告中的三项 P3 以及 §5.6 构造器路径边界缺口纳入 3.2.2.0 开工前安全加固清单，给出修复/复核方案与测试要求（本轮不修改代码）。
6. 给出可执行的阶段拆分、测试设计、Token/成本控制与未解决问题。
7. 记录 2026-11-21 五项正式决策，并使后续 3.2.2.0 实施计划可逐项追溯。

### 1.2 明确非目标

- 不实现模型调用、文本/图片 Provider、Skill 编排或自然语言生成。
- 不实现 Gradle、游戏启动器、构建、测试、发布等外部命令执行。
- 不实现多 Agent、长循环、自动重试到成功、Web UI 或 GitHub App。
- 不实现游戏内注入、客户端资源提取、二进制资源改写。
- 不实现跨机器同步、云备份、数据库或向量库。
- 不在本阶段承诺“密码学防篡改”。`decisions.ndjson` 仍是可追责的追加式记录，不是签名账本；拥有项目目录写权限的本地用户可以篡改它。

### 1.3 前提与基线事实

- Phase 3.2.1 已实现：`creation/state.json`、`spec.json`、`plan.json`、`decisions.ndjson`、`locks.json`、`placeholders.json`、`digest.json`、`runs/`；CLI 仅有 `init/status/digest/verify`；`confirm/plan/approve/lock/run` 尚无可用的 CLI 闭环。
- `plan.json` 的 `steps` 只描述“目标/动作/依赖/预算/验收”，**没有 content 字段**；具体写入内容必须由 Operation 承载。
- `decisions.ndjson` 已支持 `event_format=2`、`prev_event_id` 链、`actor ∈ {user, system}`、保留生命周期事件；未知 action 目前可被 `append_decision` 追加，但 `replay` 在 `replay.py:344` 明确忽略非生命周期事件，不改变 expected state。
- 路径安全由 `veyra_pack.paths` 提供：`check_relative_path`、`path_key`、`is_creation_path`、`find_link_component`、`resolve_file`、`normalize_lexical_path`、`is_within`。
- 原子 JSON 写入由 `veyra_pack.contract.write_json_atomic` 提供；它保证“同目录临时文件 + fsync + `os.replace`”，但**不提供跨文件事务、不提供目标存在性谓词、不提供崩溃恢复**。
- 执行器必须继续遵守模块依赖方向：`veyra_creation` 可以导入 `veyra_pack`，反向禁止；不得让 `veyra_pack` 依赖 `veyra_creation`。

---

## 2. 现有架构分析

### 2.1 状态与职责划分

| 组件 | 现有职责 | 与执行器的关系 |
|---|---|---|
| `state.json` | 项目 ID、Spec revision/state、active/approved Plan、last_event_id、last_run_id、history_format | 执行器只通过 `_commit_state` 风格的受控写入更新 `last_event_id` 等摘要；不得让 Operation 直接改 state 的业务字段 |
| `spec.json` | 交付物清单、约束、预算、未知项、验收条件 | Operation 的 target 必须来自 Spec deliverables；Spec 确认后 revision 冻结 |
| `plan.json` | 步骤、依赖、目标、预算、验收 | Plan 是“允许操作哪些路径、按什么顺序”的授权边界；Operation 必须能被 Plan 解释 |
| `decisions.ndjson` | append-only 生命周期与备注事件，`event_format=2`，前一条 event_id 链 | 新增 `operation_batch_started`、`approve_operation`、`operation_result`、`operation_batch_finished` 等非生命周期事件，用于审计与恢复 |
| `locks.json` | 路径 + 锁定哈希 | 锁定路径默认拒绝覆盖；解锁后仍需新的 Plan/Operation/审批 |
| `runs/` | 模型调用记录（3.2.2 不产生） | 执行器不创建模型 run；Operation 批次使用独立 batch_id，避免把文件操作伪装成模型调用 |
| `digest.json` | 只读项目摘要 | 执行器可复用 digest 作为上下文；执行后需要刷新 digest，或标记为 stale |
| `verify` | 状态/哈希/重放一致性检查 | 新增“未完成批次/审批缺失/结果链异常”的检查；不把正常执行中的临时状态误报为损坏 |

### 2.2 模块边界

现有模块：`canonical.py`、`constants.py`、`errors.py`、`spec.py`、`plan.py`、`plan_check.py`、`store.py`、`replay.py`、`digest.py`、`cli.py`。

Phase 3.2.2 建议新增：

```
veyra_creation/
  operations.py      # 纯数据契约、校验、ID/哈希、Operation Plan 解析
  executor.py        # 单批次执行、临时文件、原子替换、结果记录
  approval.py        # 审批请求构造、approve_operation 事件校验（可与 operations 合并）
  recovery.py        # 崩溃/半完成批次的只读诊断与恢复
  paths_mutation.py  # 可选：create 目标/父目录的路径解析（或扩展 veyra_pack.paths）
```

不建议新增完整“事务管理器”“事件总线”“插件系统”。Phase 3.2.2 只需要一个受控的批处理执行器；过度抽象会让崩溃恢复更难验证。

依赖方向：

```
cli.py ──> executor.py ──> operations.py ──> plan.py / plan_check.py / spec.py
  │             │                │
  │             └──> store.py ───┴──> veyra_pack.paths / veyra_pack.contract
  └──> digest.py / replay.py（只读）
```

`operations.py` 不得直接写文件；`executor.py` 不得解析自然语言；`store.py` 不得知道模型或 Provider。

### 2.3 现有执行链路（Phase 3.2.1）

当前没有一个“从 Plan 到文件”的闭环。实际可用链路是：

```
init -> state/spec/locks/placeholders/decisions/project_created
  -> confirm_spec（Python API，尚无 CLI）
  -> save_plan（Python API，经 check_plan）
  -> approve_plan（Python API，尚无 CLI）
  -> [缺失] Operation 提案 / 审批 / 执行 / 结果记录
  -> digest / verify（只读）
```

因此 Phase 3.2.2 不是“改造一个已有执行器”，而是**在既有状态机旁边增加第一个受控写路径**。这有两个直接后果：

1. 必须先把 Operation 与现有 Plan/Spec/Decision 的绑定关系定义清楚，否则执行器会绕过已有的 `check_plan`、`approve_plan` 和锁语义。
2. 必须把“执行结果”作为新的事件类型接入 `decisions.ndjson`，但不能修改 `replay` 对生命周期状态的既有解释；否则 `verify` 的 expected state 会被破坏。

### 2.4 必须继续保持的不变量

1. **路径不越界**：Operation 只能写项目根内、Plan target 中、非 `creation/` 的相对路径；任何 junction/symlink/UNC/设备名/保留名/尾点尾空格都必须 fail closed。
2. **内容可复算**：Operation 的内容必须有稳定的 UTF-8 字节和 SHA-256；执行器不能信任模型给出的哈希，必须自己重算。
3. **前置条件可验证**：create 要求目标不存在，replace/delete 要求目标存在且哈希匹配；不能把“我以为它是这样”当成事实。
4. **锁定优先于审批**：锁定路径即使有 `approve_operation` 也拒绝；必须 unlock 后重新规划。
5. **失败停止**：一个 Operation 失败后，批次停止，后续 Operation 不得继续；已成功的操作保留并记录，不能“回滚到看起来干净”。
6. **记录先行**：操作结果、临时文件、备份、前后哈希必须先持久化，再继续下一步；不能只在内存中维护状态。
7. **不自动重试危险操作**：删除、覆盖、写后校验失败、外部修改冲突，一律停止并等待用户/恢复器处理；不自动重试到成功。
8. **不把推测当事实**：Operation 的 reason、模型解释、用户备注都不能替代哈希、路径、文件存在性这些确定性证据。

### 2.5 当前实现与目标之间的缺口

| 缺口 | 现状 | 3.2.2 需要补什么 |
|---|---|---|
| 无 Operation 数据模型 | 只有 Plan step，无 content/source | `operations.py` 契约与校验 |
| 无执行入口 | CLI 只有 init/status/digest/verify | `run`/`apply` 子命令；先只读 `--dry-run` |
| 无执行前哈希校验 | 只有 lock 时校验 | 每个 op 的 expected_sha256/expected_absent |
| 无写后校验 | `write_json_atomic` 只保证替换 | 回读 after hash；不一致即错误 |
| 无操作记录 | `operation_result` 不存在 | 追加式事件 + batch 日志 |
| 无恢复 | 无临时文件/备份约定 | 临时文件命名、备份目录、恢复器 |
| 无单写者锁 | 无跨进程锁 | `creation/executor.lock` + 进程信息 |
| 无审批执行绑定 | 只有 approve_plan | approve_operation 绑定 op core hash |
| 无 Plan↔Operation 校验 | `check_plan` 只看 Plan | Operation Plan Validator |
| 无 delete 恢复语义 | 未实现 | 备份 + 审批 + 结果记录 |
| 无故障注入测试 | 测试多为静态/单元 | 崩溃点、半完成、外部修改、并发 |

### 2.6 三项 P3 的位置

三项 P3 不是“顺手修一下”的孤立问题，它们分别对应执行器的三个基础假设：

- P3-1（非字符串 ID 校验）：执行器会把外部输入（CLI/JSON/模型）拼进 `plan_id`、`run_id`、`batch_id`；如果底层仍泄漏 `TypeError`，执行器无法稳定分类错误，也无法保证“失败不写文件”。
- P3-2（`approve_plan` 幂等不重算哈希）：执行器信任“approved Plan”作为授权边界；如果 Plan 在审批后被篡改而审批仍返回 approved，执行器的整个前置条件就失去意义。
- P3-3（digest 写入缺独立 link 检查）：执行器会大量写 `creation/` 内部文件；如果内部路径可被替换为链接，备份/日志/临时文件可能写到项目外。P3-3 是同一类纵深防御缺口的最小样本。

因此建议把三项 P3 放在 **3.2.2.1 开工前**完成，而不是拖到 3.2.2.6。

---

## 3. Operation 数据契约

### 3.1 设计原则

1. **Operation 是提案，不是命令。** 它只描述“想把哪个路径从什么状态改成什么状态”，不携带“执行我”的权限。
2. **路径必须是相对路径，且在 Plan target 内。** 不接受绝对路径、盘符、UNC、`..`、反斜杠、ADS、尾点/尾空格、Windows 设备名。
3. **内容必须有稳定字节。** v1 只接受 `inline_text`（UTF-8 文本）；二进制、图片、复制来源放到后续版本，避免 v1 出现“内容来源不可验证”的漏洞。
4. **哈希是事实，不是提示。** `expected_sha256`、`content_sha256` 由执行器重新计算；模型/用户提供的哈希只用于比对，不能直接作为“已校验”的依据。
5. **审批绑定到单个 Operation 的核心内容。** 不是绑定到“批次”“Plan 标题”或“模型说 OK”。
6. **未知字段拒绝，版本显式升级。** v1 对未知字段 fail closed；未来需要新字段时提升 `schema_version`，不要静默忽略。

### 3.2 顶层 Operation Plan

建议文件名：`creation/operations/<batch_id>.json`（内部状态，不导出、不进 digest 的文件清单）。

```json
{
  "schema_version": "1.0.0",
  "plan_id": "plan_ab12cd34ef56",
  "spec_revision": 2,
  "plan_sha256": "6f1c...e9",
  "operations": [
    {
      "op_id": "op_create_mod_entry",
      "action": "create",
      "path": "src/main/java/com/example/mymod/MyMod.java",
      "expected_absent": true,
      "expected_sha256": null,
      "content_sha256": "9a2b...71",
      "size_bytes": 1843,
      "source": {
        "kind": "inline_text",
        "text": "package com.example.mymod;\n\npublic final class MyMod {\n}\n"
      },
      "depends_on": [],
      "create_parents": true,
      "approval_id": "apr_5c0e...9f",
      "reason": "创建 Mod 入口类"
    }
  ]
}
```

字段说明（顶层）：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema_version` | string | 是 | v1 固定 `"1.0.0"`；与 `CREATION_SCHEMA_VERSION` 的 major 兼容策略一致 |
| `plan_id` | string | 是 | 必须等于当前 approved `plan.json` 的 `plan_id` |
| `spec_revision` | integer | 是 | 必须等于当前已确认 Spec 的 `revision`，且与 Plan 一致 |
| `plan_sha256` | string | 是 | 对 `plan.json` 去掉 `state` 后的内容哈希（复用 `_plan_content_sha` 的算法） |
| `operations` | array | 是 | 1–200 个，顺序即默认拓扑顺序；不得有重复 `op_id` 或重复 `path` |

字段说明（单个 Operation）：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `op_id` | string | 是 | `^op_[a-z0-9][a-z0-9._-]{0,63}$`；批次内唯一 |
| `action` | string | 是 | v1 仅 `create`、`replace`、`delete` |
| `path` | string | 是 | 项目根内相对路径，POSIX `/` 分隔；必须与某个 Plan step 的 `target` 完全一致 |
| `expected_absent` | boolean | 是 | `create` 必须为 `true`；`replace`/`delete` 必须为 `false` |
| `expected_sha256` | string/null | 是 | `create` 必须 `null`；`replace`/`delete` 必须是 64 位小写 hex |
| `content_sha256` | string/null | 是 | `create`/`replace` 必须是 64 位小写 hex；`delete` 必须 `null` |
| `size_bytes` | integer/null | 是 | `create`/`replace` 为 UTF-8 字节数；`delete` 为 `null`；执行器会重算并比对 |
| `source` | object/null | 是 | `create`/`replace` 必填，`delete` 必须 `null` |
| `source.kind` | string | 是 | v1 仅 `inline_text`；未来 `artifact`、`copy` 另行升版 |
| `source.text` | string | 是 | 必须是合法 UTF-8 文本；不得包含 NUL；长度受上限约束 |
| `depends_on` | array[string] | 是 | 引用本批次其他 `op_id`；不得自依赖、未知依赖、重复、环 |
| `create_parents` | boolean | 是 | 仅 `create` 可为 `true`；其他动作必须 `false` |
| `approval_id` | string/null | 否 | 若存在必须是 `apr_` + 24 位小写 hex；执行器会重算，不信任该值 |
| `reason` | string/null | 否 | 人类可读说明；不参与授权哈希；不得包含控制字符 |

`path` 与 Plan 的匹配建议使用**完全字符串相等**，而不是仅 `path_key` 相等：在 Windows 大小写不敏感文件系统上，`Foo.java` 与 `foo.java` 可能指向同一文件，若只按 `path_key` 匹配，模型可以用大小写变体绕过“Plan 声明的是哪个文件”的审计。路径去重、锁定比较仍使用 `path_key`。

### 3.3 动作语义

#### create

- 前置：目标路径最终解析后不存在；父目录存在且是真实目录，或 `create_parents=true` 且父路径逐级安全。
- 不允许创建 `creation/`、`.git/`、`.hg/`、`.svn/` 等内部/VCS 路径；`check_relative_path` 与 `is_creation_path` 已覆盖一部分，执行器还需维护显式拒绝清单。
- 写入后目标必须是普通文件，内容哈希等于 `content_sha256`；若目标在执行期间出现，报 `E_TARGET_EXISTS` / `E_CONFLICT`，不覆盖。
- `create` 默认风险等级：低（新文件），但如果目标路径是已存在文件的另一种大小写/短名形式，必须按目标已存在处理并升级为高风险。

#### replace

- 前置：目标存在、是普通文件、不是链接/junction、哈希等于 `expected_sha256`。
- 目标若被锁定：拒绝，错误 `E_LOCKED`；即使有 `approve_operation` 也不得绕过。
- 目标若在 `creation/` 内：拒绝，`E_CREATION_PATH`。
- 写入前再次校验哈希；写入后回读校验 `content_sha256`。
- 风险等级：至少“中”；如果目标在用户可编辑的配置/脚本/资源文件上，标为“高”，需要 `approve_operation`。

#### delete

- 前置：目标存在、是普通文件、不是链接/junction、哈希等于 `expected_sha256`。
- 必须先生成备份（同批次 `creation/backups/<batch_id>/<op_id>.bin`），备份哈希等于 `expected_sha256`；备份失败不得删除。
- 必须存在有效 `approve_operation`；仅 Plan 批准不够。
- 删除后目标必须不存在；无法原子“删除并验证”，因此删除后立即检查；若外部重建同名文件，记录 `E_EXTERNAL_CHANGE_AFTER_DELETE`，不自动再删。
- 风险等级：高，且不可逆（只能靠备份恢复）。

#### 暂不纳入 v1 的动作

- `copy`：Plan 没有源路径字段，Operation 需要 `source.kind=copy` 和源哈希；在 Plan schema 扩展前不实现。
- `generate_text` / `generate_image`：需要 Provider、预算、run_id、内容落盘；放到 3.2.3/3.2.4。
- `check` / `export`：不是文件写操作，不应走 Operation Executor；它们是后续 CLI 的只读/导出流程。
- `mkdir` / `chmod` / `rename` / `symlink`：v1 不做；目录只允许作为 `create` 的父目录副作用。

### 3.4 Operation ID、哈希与审批 ID

- `op_id`：由提案方生成，执行器只校验格式与唯一性；它不承担安全语义。
- `plan_sha256`：`sha256_text(canonical_json(plan_without_state))`，与 `store.py:106-110` 的 `_plan_content_sha` 完全相同；执行器必须用同一函数，避免两套哈希。
- `content_sha256`：对 `source.text.encode("utf-8")` 计算；空文本允许（0 字节），但 create 空文件也必须显式声明。
- `operation_core`：Operation 去掉 `approval_id`、`reason` 后的对象；对其做 `sha256_text(canonical_json(core))`，得到 `operation_core_sha256`。
- `approval_id = "apr_" + operation_core_sha256[:24]`；它绑定 plan_id、spec_revision、plan_sha256、op_id、action、path、expected_absent、expected_sha256、content_sha256、size_bytes、source、depends_on、create_parents。修改其中任何一项，旧 approval 立即失效。
- **不把 `reason` 纳入审批哈希**：reason 只是说明，不应让用户因为改了一句备注而重新审批；但 reason 会进入事件记录，便于审计。
- **不把 `approval_id` 自身纳入哈希**，避免循环定义。
- `batch_id = "opb_" + sha256_text(canonical_json({"plan_id": ..., "spec_revision": ..., "plan_sha256": ..., "operations": [operation_core, ...]}))[:24]`。批次 ID 在审批前即可确定，且不包含 `approval_id`/`reason`，因此补审批不会改变批次 ID；修改任一 op core 会改变批次 ID，旧提案文件不会与旧审批混淆。
- `operations_sha256`：对实际存储的 Operation Plan 顶层对象（含 `operations` 的完整字段，不含任何运行时字段）做 `sha256_text(canonical_json(...))`；用于事件引用与审计，不替代 `batch_id`。执行器不得用 `operations_sha256` 代替逐字段校验。

### 3.5 风险等级

契约中不强制模型填写 `risk_level`，由执行器根据事实推导，避免“模型自称低风险”：

| 条件 | 推导等级 | 是否需要 `approve_operation` |
|---|---|---|
| `create` 且目标不存在、父目录安全 | low | 否（但仍需 approved Plan） |
| `create` 但目标路径大小写/短名变体已存在 | denied | 否（按已存在拒绝；若需覆盖，必须改为 replace 并审批） |
| `replace` 目标存在、哈希匹配、未锁定、非配置/脚本类 | medium | 是（v1 保守策略） |
| `replace` 目标存在、哈希匹配、未锁定、配置/脚本/入口文件 | high | 是 |
| `replace` 目标被锁定 | denied | 否（必须先 unlock） |
| `delete` 任意文件 | high | 是 |
| 任何目标在 `creation/`、项目根外、链接路径 | denied | 否（直接拒绝） |

“配置/脚本/入口文件”的判定属于策略，不应硬编码为唯一真相。v1 建议采用保守规则：**除纯新增文件外，所有 replace/delete 都要求 `approve_operation`**。这样规则简单、可测试，也不会因为“这个文件看起来不重要”而放行。若产品上觉得过严，可在 3.2.2.4 引入 `Spec.deliverables[].role` 或 Plan step 的 `acceptance` 作为白名单，但必须有明确测试。

> **2026-11-21 决策 2（最终）**：不再保留“低风险 replace 自动执行”的选项。表中 medium/high 只用于风险展示与审批提示；**所有 replace 一律要求 `approve_operation`**，扩展名、文件类型、文件大小、是否配置/脚本均不能降低审批要求。上段“若产品上觉得过严”的白名单建议不再采用，保留仅为历史记录。

### 3.6 契约版本与兼容

- v1 的 `schema_version` 固定为 `"1.0.0"`；`SUPPORTED_SCHEMA_MAJOR=1`。
- 新增可选字段必须提升 minor；执行器对未知字段默认拒绝，直到显式支持。
- `operations[]` 的顺序是提案顺序，不是执行顺序；执行顺序由依赖拓扑排序决定。若两个无依赖操作写同一路径，校验直接拒绝（不允许“后写覆盖先写”）。
- 契约文件本身不写入 `decisions.ndjson` 全文；事件只记录 `operations_sha256` 和 `operations_path`，避免事件文件被大段文本撑爆。

---

## 4. 执行器架构

### 4.1 组件与职责

```
CLI run/apply
   │
   ├─ OperationContract (operations.py)      纯校验：schema、路径、哈希、依赖、Plan 绑定
   ├─ ApprovalGate (approval.py)             只读校验：approval 事件与 operation core 一致
   ├─ Preflight (executor.py)                只读检查：Plan approved、Spec 确认、锁、目标状态、预算
   ├─ BatchJournal (store.py/executor.py)    追加事件：batch_started / result / batch_finished
   ├─ SafeWriter (executor.py)               临时文件、fsync、exclusive create / os.replace、回读
   ├─ BackupStore (executor.py)              delete/replace 的备份与哈希
   └─ Recovery (recovery.py)                 崩溃后只读诊断 + 显式恢复
```

关键约束：

- `OperationContract` **不写文件**；它可以读 ProjectStore 做只读校验，但不得调用 `append_decision` 或 `os.replace`。
- `Preflight` 可以读 Plan/Spec/Locks/Decisions/实际文件，但不得写；`--dry-run` 只运行到 Preflight 并输出计划与风险。
- `SafeWriter` 是唯一允许写业务文件的地方；它只能接收已经通过 Preflight 的单个 Operation。
- `BatchJournal` 是唯一允许追加执行事件的地方；它复用 `store.append_decision`，不直接写 `decisions.ndjson`。
- `Recovery` 默认只读；`--recover` 才允许写，且只处理明确记录的批次。

### 4.2 执行流程

```
1. 读取 operations/<batch_id>.json
2. 校验 schema / 路径 / 哈希 / 依赖 / 上限
3. 读取并校验 Plan（approved）、Spec（specified）、Locks、State、Decisions
4. 校验 Operation ↔ Plan ↔ Spec 的绑定
5. 校验高风险操作的 approve_operation 事件
6. 获取项目执行锁（creation/executor.lock）
7. 记录 operation_batch_started
8. 对每个可执行 op（拓扑序）：
   a. 重新读取目标状态（不信任 Preflight 的缓存）
   b. 校验 expected_absent / expected_sha256 / 锁 / 路径
   c. 需要时创建父目录（create_parents）
   d. 需要时写备份（delete）
   e. 写同目录临时文件，fsync
   f. 再次校验目标状态（缩小 TOCTOU 窗口）
   g. exclusive create 或 os.replace
   h. 回读 after hash；失败 -> operation_result=failed / uncertain
   i. 记录 operation_result；成功则继续，失败则停止
9. 记录 operation_batch_finished（succeeded / failed / uncertain）
10. 刷新 digest（或标记 stale）；释放锁
```

每一步的失败都必须产生一个可读的 `operation_result` 事件；如果连事件都写不进去，执行器必须停止并报告“无法记录”，不能继续写业务文件。

### 4.3 建议 Python API

```python
class OperationExecutor:
    def __init__(self, store: ProjectStore, *, clock=None, fault_hook=None): ...

    def load_and_validate(self, batch_path: Path) -> OperationBatch: ...
    def preflight(self, batch: OperationBatch) -> PreflightReport: ...
    def execute(self, batch: OperationBatch, *, dry_run=False) -> ExecutionReport: ...
    def recover(self, batch_id: str) -> RecoveryReport: ...
```

`PreflightReport` 至少包含：

- `ok: bool`
- `errors: list[Issue]`
- `warnings: list[Issue]`
- `operations: list[OperationPreflight]`（每个 op 的解析路径、before hash、风险等级、是否需要审批、是否已批准）
- `batch_sha256`

`ExecutionReport` 至少包含：

- `batch_id`
- `status`: `succeeded` / `failed` / `uncertain`
- `applied`: `list[OperationResult]`
- `failed`: `OperationResult | None`
- `skipped`: `list[str]`
- `first_error`: `Issue | None`

### 4.4 CLI 建议

```text
python -m veyra_creation run <dir> --batch creation/operations/<batch_id>.json --dry-run
python -m veyra_creation run <dir> --batch ... [--json]
python -m veyra_creation recover <dir> --batch <batch_id> [--apply] [--json]
python -m veyra_creation approve-operation <dir> --batch ... --op <op_id> [--json]
```

约束：

- `--dry-run` 必须完全只读：不创建锁、不写临时文件、不追加事件、不刷新 digest。
- 默认 `run` 只执行已 approved Plan 下的批次；没有 `--batch` 时不得猜测“最新文件”，避免误执行。
- `approve-operation` 由用户显式执行；模型不能调用它。CLI 必须打印 op core 摘要（path、action、before/after hash、风险等级），但不得打印整个文件内容到终端（除非用户显式要求）。
- 所有 `--json` 输出为单个对象；成功 exit 0，冲突/校验失败 exit 2，执行失败 exit 1；错误对象复用 `CreationError.as_dict()` 风格。

### 4.5 模型与执行器的分离

Phase 3.2.2 不调用模型。后续阶段模型只能：

1. 读取 Project Digest 与已确认 Spec/Plan；
2. 返回**候选 Operation 列表**（JSON），不返回“执行”指令；
3. 由 Python 校验路径、哈希、Plan 绑定、预算；
4. 由用户批准高风险操作；
5. 由执行器写文件。

执行器不得接受 Provider 返回的任意 shell 命令、绝对路径、URL 下载、动态 import 或“覆盖整个项目”的 content。模型输出即使包含合法 JSON，也必须经过同一套 `OperationContract` 校验；不能因为“是模型生成的”而跳过。

---

## 5. 安全边界与威胁模型

### 5.1 资产与信任边界

| 资产 | 保护目标 | 信任级别 |
|---|---|---|
| 用户已有文件 | 不被未授权覆盖/删除；外部修改不被静默丢弃 | 高 |
| Spec/Plan/approved 状态 | 执行器只执行用户批准的 Plan | 高 |
| `decisions.ndjson` | 追加式、可追溯；执行结果不可悄悄消失 | 中（本地可篡改，非密码学） |
| `creation/operations` / `backups` | 不进入导出/发布；不被路径穿越写出项目 | 中 |
| 临时文件 | 不残留敏感内容；不跨目录 | 中 |
| 模型输出 | 不可信输入 | 低 |
| 本地其他进程 | 可能并发修改文件 | 不可信（尽力检测） |
| 项目根外文件 | 绝对不写 | 最高 |

### 5.2 威胁模型

**T1 模型/提案越权**：模型返回 `path="../../.ssh/authorized_keys"`、绝对路径、`creation/state.json`、`C:\...`、UNC、ADS、尾点空格、保留设备名。

- 防护：`check_relative_path` + `is_creation_path` + Plan target 完全匹配 + `find_link_component` + 最终解析必须在 root 内；未知字段/动作拒绝。

**T2 用户已在提案后修改文件**：模型基于旧哈希提出 replace；用户手动改了目标。

- 防护：执行前重算 before hash；不匹配 `E_CONFLICT`；不覆盖；不自动合并；不把用户修改当成“外部攻击”而删除。

**T3 目标在检查与替换之间被替换为链接/junction**：TOCTOU。

- 防护：同目录临时文件；替换前再次 `find_link_component` / `resolve_file`；Windows 上使用不跟随链接的 lstat；接受残余窗口并在文档/测试中明确，不宣称“完全消除 TOCTOU”。

**T4 并发执行/两个进程同时写**：两个 `run` 同时启动。

- 防护：`creation/executor.lock` 使用 `O_CREAT|O_EXCL`（或 `msvcrt.locking`/`fcntl.flock` 的平台实现），锁内记录 pid/时间/批次；第二个进程拒绝 `E_BUSY`。锁不是安全边界，但能防止最常见的双写。

**T5 崩溃/断电**：临时文件写完但 `os.replace` 前崩溃；替换后事件未写；事件写一半。

- 防护：临时文件命名可识别；结果事件先于下一步；启动时扫描 started 未 finished 的批次；`recover` 只读诊断，用户确认后清理临时文件/回滚或继续。接受“事件可能落后于文件”的窗口，恢复器以实际文件哈希为准。

**T6 删除不可逆**：delete 后无法恢复。

- 防护：备份先行 + 备份哈希校验 + 高风险审批；备份失败拒绝删除；恢复需要用户显式命令。

**T7 内部路径被链接替换**：`creation/operations`、`creation/backups`、`creation/executor.lock` 被替换为 junction。

- 防护：每次写内部文件前 `_assert_no_link`（含父级）；创建内部目录后立即检查；P3-3 同类加固。

**T8 事件伪造/删除**：本地用户手改 `decisions.ndjson`。

- 防护：链校验、event_id 唯一、schema 校验；**不承诺**防篡改。拥有目录写权限的用户可以删除整个 `creation/`；这是本地信任模型的一部分。文档必须明确。

**T9 大内容/磁盘耗尽**：模型返回巨大文本，或备份占满磁盘。

- 防护：单 op 内容上限、批次总字节上限、备份上限、操作数上限；写前检查可用空间（best effort）；磁盘满时保留临时文件并停止，不静默截断。

**T10 日志/事件泄露敏感内容**：Operation 内容可能含密钥/个人信息。

- 防护：`operation_result` 不记录正文，只记录哈希/路径/长度/错误；operations 文件本身仍在 `creation/`，不导出；错误消息截断；文档提醒用户不要提交 `creation/`。

### 5.3 路径安全细则

执行器对每个路径执行以下顺序，任何一步失败即拒绝：

1. 类型检查：必须是 `str`，非空，长度 ≤ 现有 `MAX_PATH_LENGTH`。
2. `check_relative_path(path)`：拒绝绝对路径、盘符、UNC、反斜杠、`:`、`..`、`.`、空段、尾点/尾空格、保留设备名、深度/长度超限。
3. `is_creation_path(path)`：拒绝 `creation/` 及其下所有路径。
4. 与 Plan target 完全相等（字符串级）。
5. 计算 `absolute = store.root / path`；对父目录逐级 `find_link_component`，拒绝任何 symlink/junction。
6. 对已存在的目标：`resolve_file(store.root, path, "operations")`，确认 regular、非链接、最终 `resolve()` 在 root 内。
7. 对不存在的目标（create）：对父目录做 `resolve_file` 风格的逐级检查；父目录必须存在且是真实目录，或 `create_parents=true` 时逐级创建并每次 `_assert_no_link`。
8. 大小写/短名/Unicode 规范化：用 `path_key` 做锁/重复检查；若目标以不同大小写形式存在，按“已存在”处理，不静默创建第二个目录项。
9. 禁止路径：`.git/`、`.hg/`、`.svn/`、`.venv/`、`node_modules/`（如未来需要，显式白名单）；`real_crash.log` 默认拒绝覆盖/删除（与根目录保护一致）。
10. 拒绝在项目根外创建父目录；`create_parents` 不得越过 root。

### 5.4 原子性边界

- `write_json_atomic` 只保证 JSON 文件；Operation 写业务文件需要独立的 `write_bytes_atomic`。
- 临时文件必须与目标同目录（同一文件系统），命名包含 batch/op 与随机后缀；不得写到系统临时目录再跨盘 rename。
- 写入流程：`mkstemp` → `write` 全部字节 → `flush` → `fsync` → 校验临时文件哈希 → 关闭 → 目标谓词检查 → 提交 → 回读目标。
- 提交方式：
  - `replace`：`os.replace(temp, target)`；原子替换。
  - `create`：优先 `os.link(temp, target)`（POSIX，目标存在则失败），Windows 使用 `os.rename(temp, target)`（目标存在则 `FileExistsError`）；成功后删除 temp。若平台不支持硬链接且 `rename` 会覆盖，必须 fail closed，不得用“先检查再 replace”假装原子。
  - `delete`：先备份，再 `os.unlink(target)`；无原子删除，接受“删除后目标可能被外部重建”的窗口，记录 after absent 检查。
- 目录 fsync：POSIX 上 best effort fsync 父目录；Windows 不支持时跳过，文档说明。
- 失败清理：只删除本批次创建且路径匹配的 temp；不得 glob 删除、不得删除未知文件、不得删除备份（除非用户显式清理）。

### 5.5 限额建议

| 限额 | 建议值 | 说明 |
|---|---|---|
| 单批次 operations | 200 | 与 `MAX_PLAN_STEPS` 一致 |
| 单 op inline_text | 2 MiB | 文本 Mod 文件足够；超出走 artifact/分块（后续） |
| 单批次总写入 | 16 MiB | 防止磁盘/Token 滥用 |
| 单批次备份 | 64 MiB | 与 pack 的媒体上限分层，不与二进制资源混淆 |
| `reason` 长度 | 1000 字符 | 事件可读性 |
| 路径长度/深度 | 复用 paths 限额 | 不新增第二套规则 |
| 每 op 依赖数 | 50 | 与 plan 一致 |
| 批次执行墙钟 | v1 不强制 | 文件操作不应长时间运行；如设置，默认 10 分钟并记录 `E_TIMEOUT` |

限额必须是常量并有测试；不要只写在文档里。

### 5.6 设计期间新发现的构造器边界缺口（只读探针，未修复）

> **历史记录**：以下为设计期间（R3 时代）的发现，原文保留。2026-11-21 决策后复核确认当前工作树已由 R4 覆盖该缺口，见 **§5.6.1**；3.2.2.0 只做独立验收与补测，不重复实现。

本轮在只读探针中复核 `ProjectStore` 构造路径时发现，以下 Windows 形态未被构造器统一 fail closed：

- `base\missing\.. \link\project`（`.. ` 带尾空格）：构造器接受，`init` 抛裸 `FileNotFoundError`（WinError 3），未转成 `CreationError`；项目外无 state。
- `base\...\link\project`（三点组件）：构造器接受，`Path.resolve()` 已穿过 junction；`init` 随后抛 `E_LINK_BOUNDARY`，项目外无 state。
- `base\missing\...\link\project` / `....`：构造器接受，`init` 实际在 `base\missing\link\project` 创建 state（Windows 尾点规范化）；声明路径与实际初始化路径不一致。

影响：`init` 的 `_assert_no_link` 兜底阻止了项目外写入（探针未见 outside state），但 `ProjectStore.__init__` 的“每一级组件检查”承诺不成立；执行器若复用同一构造器/解析逻辑，可能把带尾空格/点的路径当成另一个目标，造成 Plan/Operation 绑定与实际路径不一致。

建议：在 3.2.2.0 或 3.2.2.2 前，将 `.. `、`...`、`....`、尾点/尾空格组件统一按 `E_LINK_BOUNDARY`/`E_PATH_UNSAFE` 拒绝，并补测试；这不在三项 P3 原清单内，需用户确认是否纳入。未发现该缺口导致的项目外写入，因此按路径一致性缺陷而非数据泄露处理。

#### 5.6.1 2026-11-21 决策后的只读复核（当前实现已覆盖）

> 上文的“未修复”是**设计期间（R3 时代）的历史记录**。用户决策 1 已明确把本项纳入 3.2.2.0；本轮按决策要求重新核对了当前工作树，结论如下。

当前 `ProjectStore.__init__`（`veyra_creation/store.py:198-268`）已经按“先词法、再逐级检查、resolve 后再检查”的顺序执行：

1. `normalize_lexical_path(Path(root).expanduser())`，在 `ntpath.abspath` 之前先校验原始路径组件；
2. `find_link_component(declared)` 检查声明路径的每一级；
3. `declared.resolve()`；
4. `paths_equivalent(declared, resolved)` 比较声明路径与实际路径；
5. 对 `resolved` 再做一次 `find_link_component(...)`；
6. 任何不一致或链接组件均 fail closed，抛出 `CreationError`（`E_PROJECT_PATH_TRAILING` / `E_LINK_BOUNDARY` / `E_PROJECT_ROOT`）。

`veyra_pack/paths.py` 的对应实现：

- `:181-235` `_validate_windows_root_components`：拒绝 UNC、设备命名空间、驱动器相对路径、ADS、保留设备名，以及**组件尾随点/空格**；`.. `、`...`、`....`、`link.`、`link ` 都属于这一类。
- `:238-263` `normalize_lexical_path`：先验证原始 raw 组件，再调用 `ntpath.abspath` 做词法折叠；因此不会先穿过 junction 再报错。
- `:289-311` `find_link_component`：逐级 `lstat`，对缺失组件继续检查后续组件，不跟随链接；resolve 前后各调用一次。

本轮只读探针使用 `ProjectStore(<base>\<variant>\project)` 逐一构造，以下输入全部被拒绝为 `CreationError E_PROJECT_PATH_TRAILING`，且项目外未创建 `creation/state.json`：

- `base\missing\.. \link\project`（`.. ` 尾空格）
- `base\...\link\project`
- `base\missing\...\link\project`
- `base\....\link\project`
- `base\link.\project`
- `base\link \project`

现有 `tests/test_creation_path_policy.py` 的 18 个测试全部通过（Python 3.15，临时目录，真实 junction），覆盖：

- `test_missing_parent_with_trailing_space_cannot_reach_a_junction`
- `test_missing_parent_with_three_dots_cannot_reach_a_junction`
- `test_three_dot_component_cannot_reach_a_junction`
- `test_junction_spelled_with_trailing_space_is_rejected`
- `test_junction_spelled_with_trailing_dot_is_rejected`
- `test_cli_rejects_a_trailing_dot_bypass`
- `test_all_entrypoints_reject_a_replaced_ancestor`
- `test_ancestor_junction_introduced_after_construction_is_still_rejected`
- `test_r3_missing_parent_junction_is_still_rejected`

**结论**：决策 1 的“必须拒绝”要求在**当前工作树**中已经由 R4 实现。3.2.2.0 的 §5.6 任务应定义为**独立验收 + 负向矩阵补测 + 真实 junction 回归**，而不是重写路径策略：

- 若新增矩阵全部通过，§5.6 在 3.2.2.0 记为“已验证，无源代码改动”；
- 若发现任何未覆盖的规范化变体（例如新的尾随字符组合、CLI 入口绕过、resolve 后二次检查缺口），只允许在 `paths.py`/`ProjectStore.__init__` 做**最小修复**并补测试，不做路径策略重构；
- 必须保留 `.` 组件与正常绝对路径的兼容性，并保留现有 18 个路径策略测试不回归。

这样既满足决策 1 的验收要求，也避免在已经正确的实现上重复修改。

---

## 6. 文件锁与事务策略

### 6.1 为什么需要单写者锁

Phase 3.2.1 已知“无跨进程锁”。Operation Executor 会读-改-写同一批文件，两个进程同时执行会出现：

- 两个进程都通过 before hash；
- A 先 replace，B 再 replace，B 覆盖 A；
- 事件交错，`prev_event_id` 链可能断裂（取决于 append 的原子性）；
- 备份/临时文件互相覆盖。

因此 3.2.2.2 必须引入**项目级执行锁**，至少防止同一项目内两个执行器同时运行。

### 6.2 锁的实现

建议文件：`creation/executor.lock`。

内容（JSON，便于诊断）：

```json
{
  "schema_version": "1.0.0",
  "pid": 12345,
  "started_at": "2026-11-21T08:00:00Z",
  "host": "DESKTOP-...",
  "batch_id": "opb_...",
  "tool_version": "0.1.0"
}
```

获取顺序：

1. `_assert_internal()` 与 `_assert_no_link(creation_dir)`。
2. 以 `O_CREAT | O_EXCL | O_WRONLY` 创建锁文件；已存在则读内容，判断是否 stale。
3. 写入 JSON、flush、fsync。
4. 失败则 `E_BUSY`，输出现有锁的 pid/time/batch；不自动删除锁。
5. 正常结束/异常退出在 `finally` 中删除锁；删除前确认锁内容仍是自己的（避免删掉别人的锁）。
6. 崩溃留下的锁：`recover --force-unlock` 或人工删除；不得由 `run` 自动抢占。若进程已不存在，可提示用户，但仍需显式操作。

平台注意事项：

- Windows 上 `O_EXCL` 对已存在文件可靠；锁文件删除可能受杀毒/索引影响，失败要报告。
- POSIX 上可额外用 `fcntl.flock` 做进程存活检测；不要依赖 `pid` 唯一性。
- 锁文件本身是内部状态，不导出、不进 digest 文件清单。

#### 6.2.1 锁的 owner token（2026-11-21 决策补充）

在 §6.2 的锁内容基础上增加：

```json
{
  "schema_version": "1.0.0",
  "pid": 12345,
  "started_at": "2026-11-21T08:00:00Z",
  "host": "DESKTOP-...",
  "batch_id": "opb_...",
  "owner_token": "random-128-bit-hex",
  "tool_version": "0.1.0"
}
```

规则：

1. `owner_token` 由获取锁的进程用系统 CSPRNG 生成，至少 128 bit，不写入事件正文之外的日志；同一进程重试不得复用旧 token。
2. 释放锁前必须读取锁内容并校验 `owner_token`（同时校验 `batch_id`/`pid` 作为诊断辅助）；不匹配时**不删除锁**，返回 `E_LOCK_OWNER`，避免旧进程/旧批次释放新进程的锁。
3. 崩溃锁不自动抢占；`recover --force-unlock` 必须先确认锁对应的批次已结束或已明确标记为可恢复，并重新检查目标文件状态；抢占动作本身要写事件。
4. 锁文件创建前执行 `_assert_no_link(creation_dir)` 与目标路径检查；锁文件不得是链接/junction。
5. 释放失败（文件被外部删除/替换）要报告，不静默忽略；不能用“删除路径存在”代替 owner 校验。

### 6.3 批次日志（journal）

`decisions.ndjson` 是审计与恢复的主日志，但事件粒度是“操作结果”，不是“事务页”。建议增加一个批次文件：

`creation/operations/<batch_id>.json` 保存提案与状态摘要；`creation/operations/<batch_id>.results.json` 保存执行器自己的结果快照（可选，但便于恢复时不必扫描整个 decisions）。

**单一事实来源优先级**：

1. 实际文件系统（目标是否存在、哈希是多少）；
2. `operation_result` 事件（已记录的结果）；
3. `operations/<batch_id>.json`（提案与预期）；
4. 内存中的执行状态（最不可信，仅当前进程使用）。

恢复器不得只信 `.results.json`；它必须用实际文件哈希与事件记录交叉验证。

### 6.4 事务边界

Phase 3.2.2 **不实现跨文件 ACID 事务**。明确语义：

- 每个 Operation 是**一个文件级原子提交**（replace/create）或**一个带备份的删除**。
- 批次是**顺序执行的集合**，不是原子事务；失败时已成功的操作保留。
- 不承诺“全成功或全回滚”。用户看到的是：哪些已应用、哪些失败、如何恢复。
- 如果产品需要“整批回滚”，应作为后续显式功能（基于备份），而不是在执行器里偷偷回滚；回滚本身也可能失败。

这个取舍的理由：跨文件回滚需要额外的日志、反向操作和失败恢复，且删除/覆盖的回滚并不总是可靠；对 Mod 创作场景，保留已完成的安全写入并明确报告，比假装原子更可信。

### 6.5 备份策略

- 目录：`creation/backups/<batch_id>/<op_id>.bin`。
- 只对 `delete` 强制备份；`replace` 建议也备份（默认开启，可用 `--no-backup` 关闭？v1 建议强制 replace 备份，避免误覆盖无法恢复）。
- 备份写入使用 `write_bytes_atomic`；备份完成后校验哈希等于 `expected_sha256`；不一致则停止。
- 备份文件权限尽量收紧（同用户可读）；不进入 digest 文件清单、不导出、不提交。
- 备份保留：默认保留到批次成功结束后由用户显式清理；不要在成功后自动删除，否则“执行成功但用户后悔”没有退路。磁盘上限/清理策略列入未解决问题。
- 当前根 `.gitignore` 未检索到 `creation/` 规则（仅命中 `# report.md` 与 `real_crash.log` 相关行）；实施阶段应单独获得授权后把 `creation/` 加入忽略，避免 operation/backups 被误提交。本设计阶段不修改 `.gitignore`。
- 恢复：`restore` 只允许把备份写回原路径，仍走 `expected_absent`/`expected_sha256` 校验和原子替换；恢复本身也要记录事件。

#### 6.5.1 delete 备份保留与恢复元数据（2026-11-21 决策补充）

delete 的备份是**默认长期保留**的恢复材料，不是临时文件：

1. 每次 delete 至少生成：
   - `creation/backups/<batch_id>/<op_id>.bin`：原始内容；
   - `creation/backups/<batch_id>/<op_id>.json`：恢复记录。
2. 恢复记录至少包含：
   - `original_path`：相对项目根的目标路径；
   - `original_sha256`：原始内容 SHA-256；
   - `size_bytes`；
   - `op_id`；
   - `batch_id`；
   - `plan_id`、`spec_revision`（便于核对授权边界）；
   - `backup_sha256`：备份文件自身哈希；
   - `created_at`、`tool_version`；
   - `recovery`：恢复所需元数据（目标路径、期望的删除前状态、备份位置）。
3. **不得静默覆盖已有备份**：同一 `<batch_id>/<op_id>` 已存在时，若哈希与内容完全一致可幂等复用；否则报 `E_BACKUP_EXISTS`，要求用户显式清理或更换批次。
4. 备份目录与备份文件必须接受与普通操作目标同等级的路径安全检查：不得位于项目根外、不得进入 `creation/` 以外的业务路径、不得是链接/junction、父目录必须安全；备份写入同样使用同目录临时文件 + 原子替换 + 回读哈希。
5. 备份失败（写入、fsync、回读、哈希不符）时，**不得执行 delete**；已完成的备份不得被后续失败静默删除，除非用户显式清理。
6. 默认保留策略：批次结束、甚至项目验收后都不自动删除备份；只有用户显式执行清理命令（或明确授权）才可删除。清理动作必须记录事件，不能只删文件。
7. 恢复前重新检查：
   - 目标当前是否存在、当前 SHA-256 是否仍等于删除后的预期状态；
   - 若目标已被用户修改或重新创建，拒绝盲目恢复（`E_TARGET_CHANGED`），不得覆盖用户后续修改；
   - 备份自身的 `backup_sha256` 必须与记录一致；
   - 恢复路径仍需通过普通执行器的 Plan/路径/授权校验，不得因为“来自备份”而绕过审批或路径边界；
   - 恢复动作写入事件（`recovery_applied` 或等价事件），并保留原 delete 记录。

### 6.6 执行记录事件

以下事件均为非生命周期事件，`actor="system"`（`approve_operation` 为 `actor="user"`），由 `store.append_decision` 追加。事件字段必须通过白名单校验，未知字段拒绝。

#### `operation_batch_started`

```json
{
  "action": "operation_batch_started",
  "actor": "system",
  "batch_id": "opb_...",
  "plan_id": "plan_...",
  "spec_revision": 2,
  "plan_sha256": "...",
  "operations_sha256": "...",
  "operations_path": "operations/opb_....json",
  "operation_count": 1,
  "approval_ids": ["apr_..."],
  "tool_version": "0.1.0"
}
```

#### `approve_operation`

```json
{
  "action": "approve_operation",
  "actor": "user",
  "approval_id": "apr_...",
  "batch_id": "opb_...",
  "plan_id": "plan_...",
  "spec_revision": 2,
  "op_id": "op_...",
  "operation_core_sha256": "...",
  "path": "src/...",
  "action_type": "replace",
  "expected_sha256": "...",
  "content_sha256": "...",
  "reason": "用户批准的说明"
}
```

规则：

- `approval_id` 必须等于 `apr_` + `operation_core_sha256[:24]`。
- 同一 `approval_id` 只能批准一次；重复批准是幂等还是错误？建议：完全相同则幂等返回，不同内容拒绝 `E_APPROVAL_CONFLICT`。
- `action_type` 用 `replace`/`delete`/`create`，避免与事件 `action` 字段混淆。
- 审批事件不携带 `source.text`；只携带哈希，减少敏感内容落盘。
- 审批必须在 `operation_batch_started` 之前或之后？建议在 `run` 前完成；执行器在 Preflight 读取事件，不要求在批次内。若批次开始后补审批，执行器应拒绝（批次开始后不再接受新授权），避免“边执行边授权”。

#### `operation_result`

```json
{
  "action": "operation_result",
  "actor": "system",
  "batch_id": "opb_...",
  "op_id": "op_...",
  "result": "applied",
  "action_type": "replace",
  "path": "src/...",
  "before_sha256": "...",
  "after_sha256": "...",
  "expected_sha256": "...",
  "content_sha256": "...",
  "error_code": null,
  "error_message": null,
  "temp_path": "operations/opb_....tmp",
  "backup_path": "backups/opb_.../op_....bin",
  "recovery": {
    "kind": "none",
    "target_state": "committed",
    "backup_available": true
  }
}
```

`result` 允许值：

- `applied`：目标已提交，after hash 已验证。
- `skipped`：Preflight 后因依赖失败/批次取消未执行；不得声称已写。
- `failed`：确定未提交，目标保持 before 状态；可重试。
- `recovered_committed`：进程崩溃后恢复器确认替换已发生、事件缺失，补记结果。
- `uncertain`：无法确定是否提交（例如替换后回读失败/磁盘异常）；**禁止自动重试**。

#### `operation_batch_finished`

```json
{
  "action": "operation_batch_finished",
  "actor": "system",
  "batch_id": "opb_...",
  "status": "succeeded",
  "applied": 1,
  "skipped": 0,
  "failed": 0,
  "first_error_code": null,
  "operations_sha256": "..."
}
```

`status`：`succeeded` / `failed` / `uncertain`。只要有一个 `failed` 或 `uncertain`，后续操作必须 skipped，批次不能标记 succeeded。

### 6.7 事件写入顺序与崩溃一致性

每个 op 的顺序：

1. 目标临时文件写完并 fsync。
2. 目标谓词检查。
3. 提交（replace/create）。
4. 回读 after hash。
5. **写 `operation_result` 事件**（append + fsync）。
6. 继续下一个 op。

崩溃点分析：

- 1 之前崩溃：目标未变；临时文件可能不存在或存在；无结果事件。
- 2–3 之间崩溃：目标未变；临时文件存在；无结果事件。
- 3 之后、5 之前崩溃：目标已变，但无结果事件；恢复器通过 before/after 哈希与临时文件判断，补记 `recovered_committed` 或 `uncertain`。
- 5 写入一半：`append_decision` 的先写后 fsync 可能留下半行；`load_decisions` 会报 `E_DECISIONS_CORRUPT`，恢复器必须停止并报告，不能继续写。这是现有机制的已知边界；Phase 3.2.2 不擅自设计“截断修复”，但必须在恢复文档中说明。
- 5 之后崩溃：事件已记录；恢复器检查实际 after hash 与事件一致；若目标又被外部修改，报 `E_EXTERNAL_CHANGE_AFTER_WRITE`。

**关键取舍**：事件写失败时，不能“回滚文件”假装没发生；应标记批次 `uncertain`，停止，并让恢复器基于实际哈希判断。因为回滚本身也可能失败，且会丢失用户可见的已发生事实。

---

## 7. 冲突检测与恢复策略

### 7.1 冲突分类

| 情况 | 错误码 | 是否可自动恢复 | 用户动作 |
|---|---|---|---|
| 目标在 create 时已存在 | `E_TARGET_EXISTS` | 否 | 重新规划/改名/显式 replace+审批 |
| replace/delete 的 before hash 不匹配 | `E_CONFLICT` / `E_EXTERNAL_CHANGE` | 否 | 检查用户修改，重新生成 Operation |
| 目标被锁定 | `E_LOCKED` | 否 | unlock 后重新规划 |
| 目标在 `creation/` 或根外 | `E_PATH_UNSAFE` / `E_CREATION_PATH` | 否 | 修正提案 |
| 目标为 symlink/junction | `E_LINK_BOUNDARY` | 否 | 移除链接/换路径 |
| Plan 未 approved / 哈希不符 | `E_PLAN_NOT_APPROVED` / `E_PLAN_TAMPERED` | 否 | 重新审批 Plan |
| Spec revision 不符 | `E_SPEC_CHANGED` | 否 | 重新确认 Spec/Plan |
| 高风险 op 无审批 | `E_APPROVAL_REQUIRED` | 否 | 用户 `approve-operation` |
| 审批与 op core 不符 | `E_APPROVAL_INVALID` | 否 | 重新审批 |
| 批次正在执行 | `E_BUSY` | 否 | 等待/检查锁 |
| 写入后 after hash 不符 | `E_VERIFY_AFTER_WRITE` | 否 | 停止，检查外部修改/磁盘 |
| 磁盘写失败 | `E_WRITE_FAILED` | 视情况 | 释放空间后重试（重新预检） |
| 事件写入失败 | `E_JOURNAL_FAILED` | 否 | 停止，人工检查 decisions |
| 内容超限 | `E_OPERATION_TOO_LARGE` | 否 | 拆分/改用 artifact（后续） |

### 7.2 恢复器设计

`recover` 是只读优先的诊断工具：

1. 读取 `decisions.ndjson`，找到所有 `operation_batch_started`，按 batch_id 分组；匹配 `operation_batch_finished`。
2. 对每个未完成批次，读取 `operations/<batch_id>.json`（若存在），并逐个 op 检查：
   - 目标路径是否安全；
   - 目标是否存在，当前 hash 是多少；
   - 是否有对应 `operation_result`；
   - 是否有临时文件/备份。
3. 输出 `RecoveryReport`：
   - `batch_id`、`status`（incomplete/consistent/conflicted/uncertain）
   - 每个 op 的 `observed_state`（absent/expected_hash/other_hash/link/unsafe）
   - 建议动作（补记结果、清理临时文件、从备份恢复、重新规划、人工检查）
4. `--apply` 才执行有限动作：
   - 仅当实际状态明确匹配“已提交且无结果事件”时，补记 `recovered_committed`。
   - 仅当实际状态明确匹配“未提交且临时文件属于本批次”时，删除临时文件。
   - 不做自动回滚、不自动重试、不自动删除备份。
5. 恢复动作本身也写事件（`operation_result` 或 `recovery_applied`），保持链。

**2026-11-21 决策补充的恢复前置条件**：

- 恢复前必须确认批次存在、锁的 `owner_token` 归属（或锁已按 §6.2.1 显式释放）、目标当前状态与事件记录一致；不能只凭“备份文件存在”就恢复。
- 恢复前重新计算目标当前 SHA-256；如果目标已被用户修改或重新创建，拒绝盲目恢复（`E_TARGET_CHANGED`），保留用户修改并报告差异。
- 备份必须通过 §6.5.1 的元数据校验（`backup_sha256`、`original_sha256`、`op_id`、`batch_id`）；缺字段或哈希不符按 `E_BACKUP_INVALID` 处理，不得“尽力恢复”。
- 恢复不得绕过普通执行器的 Plan/路径/授权校验：仍要求 Plan approved、路径在 Plan target 内、无链接/`creation/` 越界；delete 的恢复同样不能绕过路径检查。
- 备份默认保留；恢复成功后也不自动删除备份。清理必须由用户显式授权，并写清理事件。
- 恢复失败时不得删除备份、不得改写原 `operation_result`；只追加新的恢复记录，保留完整审计链。

### 7.3 部分执行后的状态一致性

- 批次失败后，`state.json` 的业务字段（Spec/Plan）不变；`last_event_id` 随事件前进。
- `digest` 可能包含已应用文件的新哈希；执行后应刷新 digest，否则 `verify` 会报 digest stale（这是正确的，不要忽略）。
- 已应用但后续失败的操作，其文件保留；用户可选择：
  - 保留（部分完成）；
  - 用备份恢复（若 replace 备份）；
  - 重新生成 Plan/Operation 继续。
- 不自动回滚已完成操作；不把“部分执行”标成“成功”。
- 如果用户在失败后手动修改了已应用文件，恢复器以当前哈希为准；事件中的 after hash 只代表执行时，不代表现在。

### 7.4 幂等与重复执行

- 同一 `batch_id` 不允许重复执行；已存在 `operation_batch_started` 的批次，再次 `run` 必须拒绝 `E_BATCH_ALREADY_STARTED`，除非 `recover`.
- 已记录 `applied` 的 op 再次出现：执行器应跳过并提示 `E_ALREADY_APPLIED`，不得重复写。
- `approve_operation` 重复：完全相同幂等；不同 core hash 拒绝。
- `create` 重复：目标已存在即失败，不“幂等成功”；因为无法证明现有文件就是上次写的内容。
- `replace` 重复：before hash 已变成 content hash，第二次执行会因 expected 不匹配失败；这是有意的，不做“幂等覆盖”。

### 7.5 故障注入矩阵（执行器必须测试）

| 故障点 | 注入方式 | 期望 |
|---|---|---|
| 临时文件写完前 | 写 `os.write` 抛 `OSError` | 目标不变；temp 清理或保留并记录；无成功事件 |
| 临时文件 fsync 失败 | patch `os.fsync` | 目标不变；报 `E_WRITE_FAILED` |
| 目标谓词检查后、replace 前 | fault hook | 目标不变/替换按实际；不静默覆盖 |
| replace 后、回读前 | fault hook / subprocess crash | 下次恢复识别已提交；不重复写 |
| 回读后、事件前 | fault hook | 恢复补记 `recovered_committed` 或 `uncertain` |
| 事件写一半 | 截断 decisions 文件 | `load_decisions` 报 `E_DECISIONS_CORRUPT`；恢复停止 |
| 磁盘满 | mock write/fsync | 目标不变；temp 保留；报告 |
| 外部修改 | 测试中改目标 | `E_CONFLICT`；不覆盖 |
| 链接替换 | 测试中把父目录换 junction | `E_LINK_BOUNDARY`；项目外无文件 |
| 并发 | 两个子进程同时 run | 一个 `E_BUSY`；无交错写 |
| delete 备份失败 | patch 备份写 | 目标不删；报错 |
| 删除后外部重建 | 测试中重建 | 记录 after absent 失败/外部变化；不自动再删 |

真实崩溃测试建议用子进程 + 环境变量 `VEYRA_FAULT_POINT`，在指定点调用 `os._exit(17)`；父进程检查临时文件、目标、事件。这比只 mock 更接近断电/崩溃。测试全部使用临时目录，不碰仓库根。

### 7.6 `verify` 对未完成批次的分级语义（2026-11-21 决策）

`verify` 必须区分两类情况，不能把所有“没有 `operation_batch_finished`”都当成损坏：

| 情况 | 级别 | 建议代码 | 含义与处理 |
|---|---|---|---|
| 批次合法中断：事件链完整、审批与 op core 一致、路径安全、已有结果与目标实际状态一致，但没有 `batch_finished` | warning | `W_OPERATION_INCOMPLETE` | 可恢复状态；提示用户运行 `recover --batch`，不改变项目健康结论 |
| 批次部分成功：部分 op `applied`，其余 `skipped`/`failed`，记录与目标一致 | warning | `W_OPERATION_PARTIAL` | 保留已完成写入；恢复器只做显式处理，不自动回滚 |
| `uncertain` 结果但事件完整、路径/审批正常 | warning | `W_OPERATION_UNCERTAIN` | 需要人工确认；禁止自动重试 |
| 事件记录损坏：JSON 半行、链断、重复 event_id、非法字段 | error | `E_DECISIONS_CORRUPT` / `E_EVENT_CHAIN` | 完整性错误；停止恢复，交人工/后续工具 |
| Plan 内容哈希与审批事件不一致 | error | `E_PLAN_TAMPERED` | 审批失效；不得执行 |
| `replace`/`delete` 缺少必要审批，或审批与 op core 不一致 | error | `E_APPROVAL_REQUIRED` / `E_APPROVAL_INVALID` | 授权链错误；不得执行/恢复 |
| 路径越界、进入 `creation/`、链接/junction | error | `E_PATH_UNSAFE` / `E_LINK_BOUNDARY` / `E_CREATION_PATH` | 安全边界错误；不得执行/恢复 |
| 已完成 op 记录 `applied`，但目标缺失、当前哈希 ≠ `after_sha256`，或与记录的 before/after 关系矛盾 | error | `E_OPERATION_RESULT_MISMATCH` | 已完成操作与事实不一致；不得用“补记成功”掩盖 |
| 同一 op 出现互相冲突的结果，或 `batch_finished` 后仍有 op 无结果 | error | `E_OPERATION_EVENT_INCONSISTENT` | 事件语义错误；需人工审计 |
| 备份记录与备份文件哈希不一致，或恢复记录缺失关键字段 | error | `E_BACKUP_INVALID` | 恢复材料不可信；delete 视为未安全完成 |

原则：

- **warning 表示“可恢复的未完成状态”**，不改变 `verify` 的退出码为失败；CLI 仍以 `0` 返回，但输出明确的恢复提示。
- **error 表示“完整性/授权/路径/事实不一致”**，CLI 退出 `1`；不得因为“可以 recover”而降级为 warning。
- 只有事件链、审批、路径、哈希、结果记录全部自洽时，才允许把未完成批次标为 warning。
- `verify` 不得为了让批次“看起来完成”而补写事件；补记只能由 `recover --apply` 在重新核对目标后执行。

测试至少覆盖：合法中断 warning、部分成功 warning、`uncertain` warning、decisions 截断 error、Plan 篡改 error、审批缺失/不符 error、路径链接 error、`applied` 但目标被改 error、`batch_finished` 缺结果 error。

---

## 8. 用户授权机制

### 8.1 五个阶段，职责不可混淆

| 阶段 | 主体 | 产物 | 不得做什么 |
|---|---|---|---|
| 1. 提案 | 模型/用户 | `operations/<batch_id>.json`，候选 Operation | 不得写业务文件、不得决定审批结果 |
| 2. 校验 | Python | `PreflightReport`，错误/风险/审批需求 | 不得调用模型、不得写文件 |
| 3. 批准 | 用户 | `approve_operation` 事件（所有 replace/delete） | 不得由模型代签；不得复用其他 op 的 approval |
| 4. 执行 | Python 执行器 | 文件提交 + `operation_result` 事件 | 不得越过 Plan/锁/哈希；不得自动重试危险操作 |
| 5. 验证 | Python + 用户 | after hash、digest、verify、人工检查 | 不得把“命令退出 0”当成“内容正确” |

### 8.2 授权矩阵（v1 建议）

| 操作 | Plan approved | approve_operation | 额外条件 |
|---|---|---|---|
| create 新文件（父目录安全、目标不存在） | 必须 | 否 | 路径在 Plan target |
| replace 已存在、未锁定、哈希匹配 | 必须 | **必须**（保守策略） | 非 `creation/`、非链接 |
| replace 锁定文件 | 必须 | 即使有也拒绝 | 必须先 unlock + 新 Plan |
| delete 任意文件 | 必须 | **必须** | 备份成功、恢复信息完整 |
| 写项目根外 | 拒绝 | 拒绝 | 无 |
| 写 `creation/` | 拒绝 | 拒绝 | 无 |
| 批量修改（>1 文件） | 必须 | 每个高风险 op 单独审批 | 不允许一个 approval 覆盖多个 op |
| 自动执行 | 仅 create 低风险 | 否 | 仍受预算/上限/锁约束 |

“create 自动执行”是相对的：它仍需要 approved Plan 和 Preflight 通过；不是“模型说创建就创建”。

> **2026-11-21 决策 2**：上表中所有 `replace`（无论 medium 还是 high、无论扩展名/文件类型/大小）都必须有 `approve_operation`；矩阵里的“高风险 op 单独审批”应理解为“每个 replace/delete 单独审批”。不存在“低风险 replace 自动执行”的白名单。审批绑定 operation core hash；修改内容、`path`、`expected_sha256`、`content_sha256`、依赖/父目录策略或 Plan 内容/revision 后，旧审批不得继续有效；`batch_id` 只是批次标识，不能替代单 op 审批。

### 8.3 两阶段确认

1. **提案阶段**：模型/用户生成 operation core 与 `approval_id` 候选；系统显示 path、action、before/after hash、风险等级、diff 摘要。
2. **批准阶段**：用户执行 `approve-operation`；系统重新计算 operation core hash，校验与提案一致，再写 `approve_operation` 事件。
3. **执行阶段**：执行器再次计算 core hash，与事件中的 `operation_core_sha256` 比对；不一致 `E_APPROVAL_INVALID`。
4. **执行后**：用户看到实际 after hash；若与审批时展示的 content hash 不一致，说明执行内容被替换，必须报警。

审批事件绑定的是**内容**，不是“批次”或“Plan 标题”；因此：

- 修改 `path` → core hash 变 → 旧 approval 失效；
- 修改 `expected_sha256` → 失效；
- 修改 `content_sha256` 或 source text → 失效；
- 修改 `depends_on`/`create_parents` → 失效；
- 只改 `reason` → 不失效；`reason` 不进入 core，审批事件记录自己的 reason，执行器不要求提案 reason 与审批 reason 相等。

### 8.4 审批的展示与不可抵赖

- CLI 展示必须包含：`op_id`、`action`、`path`、`expected_sha256`、`content_sha256`、`size_bytes`、风险等级、依赖、备份路径（delete）。
- 不把整个文件正文默认打印；大文件只打印摘要/前 N 行，避免终端注入和敏感内容泄露。
- `approve-operation` 需要显式 `--yes` 或交互确认，不能仅凭文件名自动批准。
- 事件中记录 `actor="user"`，但不声称“法律意义上的不可抵赖”；本地用户仍可改库。
- 如果用户拒绝，记录 `reject` 事件（现有设计允许），并保留提案供审计；不删除操作文件。

### 8.5 什么可以自动执行

v1 只建议自动执行：

- Plan 内、目标不存在、父目录安全、内容哈希一致的 `create`；
- 不涉及锁定文件、不涉及删除、不覆盖已存在文件。

其他一律需要用户显式批准。这个保守策略的理由：Mod 项目里“看起来是新建”的文件可能已经存在（大小写/短名/生成物），自动覆盖的代价远高于多一次确认。

---

## 9. 三项 P3 加固方案

### 9.1 P3-1：非字符串 ID 泄漏 TypeError

**现状**

`store.py:305-316` 对 `project_id`/`spec_id` 使用 `PROJECT_ID_RE.match(...)`/`SPEC_ID_RE.match(...)`；`store.py:1070-1073` 对 `run_id` 使用 `RUN_ID_RE.match(...)`。如果直接 Python API 传入 `int`/`None`，`re.match` 抛 `TypeError: expected string or bytes-like object, got 'int'`，而不是稳定的 `CreationError`。CLI argparse 不触发，因为参数是字符串。

**实际风险**

- 3.2.2 执行器会从 JSON/CLI/模型接收 `plan_id`、`batch_id`、`op_id`、`run_id`；如果底层仍泄漏 `TypeError`，CLI 的 `main` 只捕 `CreationError`/`OSError`，可能打印 traceback，破坏 JSON 错误契约。
- 更严重的是错误分类不稳定：执行器可能把“输入类型错误”当成内部 bug，而不是“提案无效”，从而在写文件前没有统一拒绝。
- 当前仅 Python API 可达，CLI 不可达；因此 P3 不是 P1，但执行器开工前应修。

**修复方案**

在 `ProjectStore.init` 和 `start_run` 的入口先做类型/空值检查，再调用正则：

```python
def _require_id(value: object, pattern: re.Pattern[str], code: str, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise CreationError(code, f"{label} must be a non-empty string", "project")
    if not pattern.match(value):
        raise CreationError(code, f"invalid {label}: {value!r}", "project")
    return value
```

对 `project_id`、`spec_id`、`run_id` 统一使用；不要只修一处。`pack_id` 已有校验的路径也顺便审查。

**测试要求**

- `init(project_id=123)`、`init(spec_id=None)`、`start_run(run_id=123)` 均抛 `CreationError`，`code` 稳定，且 `creation/` 未产生新文件。
- 合法字符串仍通过；边界格式（空串、超长、非法字符、大小写）保持原行为。
- CLI 不受影响（argparse 已保证字符串）。

**与执行器的关系**

执行器的所有外部 ID 必须先经过同一类 `_require_id` 风格的校验；Operation 契约中的 `op_id`/`batch_id` 也要在 `operations.py` 做类型检查，不能依赖底层正则的隐式 `TypeError`。

**建议阶段**：3.2.2.0（本轮已正式纳入）。

#### 9.1.1 决策后的最小实现口径

上文示例中的 `_require_id` 只是示意，不应为了三个 ID 引入一次公共重构。3.2.2.0 采用最小补丁：

- `store.py:311`：`if not isinstance(project_id, str) or not PROJECT_ID_RE.match(project_id):`
- `store.py:315`：`if not isinstance(spec_id, str) or not SPEC_ID_RE.match(spec_id):`
- `store.py:1072`：`if not isinstance(run_id, str) or not RUN_ID_RE.match(run_id):`
- **保留 `None` 的默认生成语义**：`project_id=None`/`spec_id=None`/`run_id=None` 仍表示自动生成，不应当作类型错误；只拒绝“非 `None` 且非 `str`”的输入。
- `pack_id` 已有 `isinstance`，本任务不改；`version` 不属于 ID，不纳入本任务，避免扩大范围。
- 错误码与 location 保持现有语义：`E_PROJECT_ID`/`E_SPEC_ID` 在 `project`，`E_RUN_ID` 在 `runs`；不得把非字符串输入泄漏成 `TypeError`。

对应负向测试应使用 `123`/`object()` 等非字符串值，而不是 `None`；正向测试覆盖 `None` 仍自动生成。

### 9.2 P3-2：`approve_plan` 幂等分支不重算内容哈希

**现状**

`store.py:711-712`：

```python
if plan.get("state") == "approved" and state.get("approved_plan_id") == plan_id:
    return plan
```

该分支直接返回，不重新计算 `_plan_content_sha(plan)`，也不重新 `check_plan`。如果用户在审批后篡改 `plan.json`，再次调用 `approve_plan` 仍返回 `state="approved"`；随后 `verify` 会因 Plan content sha 不匹配报 `E_PLAN_TAMPERED`，但 `approve_plan` 本身已经给出了错误的“成功”信号。

**实际风险**

- 执行器把 approved Plan 当作授权边界；如果审批 API 在篡改后仍返回 approved，执行器可能基于被篡改的 Plan 继续（取决于它是否自己重算）。这是授权链的漏洞。
- 现有 `verify` 能发现，但发现时机太晚；用户可能已经在 `run` 中执行了错误步骤。
- 仅影响 Python API 的幂等调用；CLI 尚无 approve 命令，因此不是当前用户路径的 P1，但 3.2.2 会把它变成关键路径。

**修复方案**

把幂等分支改为“先重算、再比较”：

```python
if plan.get("state") == "approved" and state.get("approved_plan_id") == plan_id:
    issues = check_plan(plan, spec)
    if issues:
        raise CreationError("E_PLAN_TAMPERED", issues[0].format(), "plan")
    actual = _plan_content_sha(plan)
    if actual != plan.get("content_sha256") ...:
        raise CreationError("E_PLAN_TAMPERED", "approved Plan content changed since approval", "plan")
    return plan
```

注意：当前 `plan.json` 是否持久化 `content_sha256` 需要核对（`_plan_content_sha` 是计算值，approve 事件里有 sha；Plan 本身可能没有该字段）。如果 Plan 没有持久化哈希，应以 `decisions` 中最近一次 `approve_plan` 事件的 `sha256` 为准，或新增 `plan.approved_sha256`（需要 schema 变更）。**设计建议**：优先从 decisions 读取审批事件的 sha，避免给 Plan 增加可被单独篡改的字段；若读取失败，报 `E_PLAN_TAMPERED`。

**测试要求**

- 正常 approve → 再次 approve 幂等，返回 approved。
- approve 后篡改 `plan.json`（改 step/target/budget）→ 再次 approve 抛 `E_PLAN_TAMPERED`，不返回 approved。
- 篡改后 `verify` 仍报 `E_PLAN_TAMPERED`（回归）。
- 篡改 `decisions` 中 approve 事件 → 链/哈希校验失败（现有 replay 已有）。
- 幂等调用不得重复追加 approve_plan 事件。

**与执行器的关系**

执行器 Preflight 必须自己重算 Plan content hash，并与 approved 事件比对，不能只信 `plan.state=="approved"`。P3-2 修复后，两处校验形成双保险。

**建议阶段**：3.2.2.0；执行器 Preflight 实现时再加一次独立校验。

#### 9.2.1 决策后的确定实现（不新增 Plan schema 字段）

上文示例中的 `plan.get("content_sha256")` 在现有 Plan 中并不存在；3.2.2.0 **不新增 `plan.approved_sha256`**，直接以 `approve_plan` 事件中的 `sha256` 为准：

```python
if plan.get("state") == "approved" and state.get("approved_plan_id") == plan_id:
    actual_sha = _plan_content_sha(plan)
    for event in reversed(self.load_decisions()):
        if event.get("action") == "approve_plan" and event.get("plan_id") == plan_id:
            if event.get("sha256") != actual_sha:
                raise CreationError(
                    "E_PLAN_TAMPERED",
                    "approved Plan content changed since its approval event",
                    "plan",
                )
            return plan
    raise CreationError(
        "E_PLAN_TAMPERED",
        "approved Plan has no matching approval event",
        "plan",
    )
```

要点：

- 幂等分支只做“重算 + 对照审批事件”，**不重复追加事件**，不改 `state`，不改 `plan.json`。
- 正常审批分支已有 `check_plan` 与哈希计算，不需要重复改；只在 `:711-712` 的短路分支加校验。
- 找不到审批事件时同样拒绝，不能因为 `state.approved_plan_id` 存在就返回成功。
- 不新增 schema 字段、不迁移历史数据；旧项目已有 `approve_plan` 事件含 `sha256`，可直接兼容。
- 负向测试：审批后修改 `plan.json` 任一参与哈希的字段（如 step `prompt`/`target`/`budget`），再次 `approve_plan` 必须抛 `E_PLAN_TAMPERED`；正向测试：未篡改的重复审批仍幂等，事件数不增加。

### 9.3 P3-3：`build_digest(write=True)` 缺少独立 link 检查

**现状**

`digest.py` 在 `write=True` 时直接 `write_json_atomic(store.digest_path, payload)`。`write_json_atomic` 会在 `os.replace` 时替换 `digest.json` 本身；如果 `digest.json` 是符号链接，`os.replace` 替换的是链接目录项，而不是跟随目标（这一点需要按平台确认）。前置的 `load_state/load_spec/...` 会检查各自路径，`creation/` 祖先被替换为 junction 时通常会 fail closed；但没有在写 `digest.json` 前单独 `_assert_no_link(digest_path)`，属于纵深防御缺口。

**实际风险**

- 如果 `digest.json` 被替换为指向项目外的链接，且平台/实现导致跟随，可能写项目外；即使当前 `os.replace` 不跟随，也缺少显式断言，未来重构容易退化。
- 执行器会大量写 `creation/` 内部文件；P3-3 是“内部路径写入前必须独立检查 link”的最小样本，修复它能为 operations/backups/temp 的同类检查定下模式。
- 不是已证实的项目外写入；是防御深度问题，P3。

**修复方案**

在 `build_digest` 写之前：

```python
store._assert_no_link(store.digest_path, "digest")
store._write_json(store.digest_path, payload)
```

或使用 `_write_json`（它已经先 `_assert_no_link` 再 atomic write）。注意 `_assert_no_link` 检查的是路径组件，包括目标自身和祖先；`creation/` 已由 `_assert_internal` 检查，仍应保留目标自身检查。

对执行器的推广规则：

- 写 `operations/<batch>.json` 前：`_assert_no_link(operations_dir)` + `_assert_no_link(target)`.
- 写 `backups/...` 前：逐级检查 `backups_dir`、batch 子目录、目标。
- 写临时文件前：检查父目录；`mkstemp` 创建后确认是 regular、非链接。
- 删除临时文件/备份前：确认路径仍在本批次目录内，不跟随链接。

**测试要求**

- 把 `creation/digest.json` 替换为 symlink（Windows 需权限；可用 junction 或跳过并记录）→ `build_digest(write=True)` 抛 `E_LINK_BOUNDARY`，项目外无文件。
- `creation/` 被替换为 junction → 写 digest 拒绝。
- 正常写 digest 仍成功；`verify` 正常。
- Windows 无 symlink 权限时测试 skip，不伪造通过；用 junction/mock 覆盖 `os.replace` 的调用前检查。

**与执行器的关系**

执行器的内部写入路径比 digest 更多；P3-3 的修复模式应作为 3.2.2.2 的“内部写入检查清单”的一部分，而不是只修 digest 一处。

**建议阶段**：3.2.2.0；3.2.2.2 扩展为通用内部写入检查。

#### 9.3.1 决策后的最小实现

在 `digest.py` 的 `write=True` 分支、调用 `write_json_atomic(store.digest_path, payload)` 之前加一行：

```python
store._assert_no_link(store.digest_path, "digest")
```

不修改 `write_json_atomic`，不新增路径策略，不重构 digest。`_assert_no_link` 已检查目标自身及祖先；`creation/` 的祖先检查由现有 `_assert_internal`/load 路径继续保持。

测试要求：

- 正常 `build_digest(write=True)` 仍成功，`verify` 无新增 error；
- `creation/digest.json` 为文件 symlink（有权限时）或目录 junction（Windows 可用）时，`build_digest(write=True)` 抛 `E_LINK_BOUNDARY`，项目外无写入；
- 无 symlink 权限时用 junction 覆盖；不能 skip 后宣称通过；
- `creation/` 祖先被替换为 junction 时，写入仍拒绝（回归）。

### 9.4 三项 P3 的优先级与验收

| 项 | 优先级 | 是否阻断 3.2.2.1 | 验收 |
|---|---|---|---|
| P3-1 类型校验 | 中 | 否，但建议同批 | 稳定 CreationError + 无副作用 |
| P3-2 approve 幂等重算 | 高（执行器授权边界） | 建议阻断 3.2.2.2 | 篡改后 approve 失败 |
| P3-3 digest link 检查 | 中 | 否，但模式重要 | 项目外无写入 + 正常路径不回归 |

三项都不应改变现有公开行为（合法输入），只应把“未定义错误”变成“明确拒绝”。

§5.6 的构造器边界缺口不在三项 P3 内，是否纳入 3.2.2.0 由用户确认；若纳入，应与 P3-1 同批补 `isinstance`/尾点尾空格测试，避免执行器复用同一构造器时出现“声明路径与实际路径不一致”。

> **2026-11-21 决策后的状态**：
> - P3-1、P3-2、P3-3 与 §5.6 **全部纳入 3.2.2.0**，不再“可选”。
> - P3-2 仍为最高优先级，必须阻断 3.2.2.2 的 `replace` 执行；3.2.2.0 未完成前不得进入 3.2.2.1 实施。
> - §5.6 在当前工作树已由 R4 覆盖（见 §5.6.1），3.2.2.0 以独立复核/补测为主；若测试无缺口，记录“无源代码改动”，不重复实现。
> - P3-1/P3-3 的修改只应把未定义 `TypeError`/缺失 link 检查变成明确拒绝，不改变合法输入的公开行为。

---

## 10. 测试设计

### 10.1 测试分层

| 层 | 目标 | 是否写文件 | 运行时间 |
|---|---|---|---|
| 契约单元 | schema、字段、ID、哈希、路径、依赖 | 否（纯内存） | 秒级 |
| 执行器单元 | 单 op create/replace/delete、临时文件、回滚 | 临时目录 | 秒级 |
| 集成 | Store + Plan + Spec + Decisions + Digest | 临时目录 | 秒级 |
| 故障注入 | 崩溃点、磁盘失败、半事件 | 临时目录/子进程 | 十秒级 |
| 安全 | 路径穿越、链接、并发、伪造 | 临时目录 | 秒级 |
| CLI | dry-run/run/recover/approve 的 JSON/退出码 | 临时目录 | 秒级 |

### 10.2 必测用例（建议编号）

**契约**

1. 合法 create/replace/delete 通过。
2. 未知顶层/op 字段拒绝。
3. 错误 `schema_version` 拒绝。
4. `op_id` 非法/重复、`path` 重复、`depends_on` 自依赖/未知/环拒绝。
5. `create` 的 `expected_absent=false` 拒绝；`replace/delete` 的 `expected_absent=true` 拒绝。
6. `content_sha256` 与 `source.text` 不一致拒绝。
7. 非 `inline_text` source 拒绝（v1）。
8. 路径：绝对、UNC、`..`、反斜杠、ADS、尾点、尾空格、保留名、`creation/`、超长/超深全部拒绝。
9. Plan target 不匹配拒绝；Spec revision/plan_id/plan_sha256 不匹配拒绝。
10. 上限：201 ops、单 op 2MiB+1、批次总量超限拒绝。

**执行器**

11. create 成功：目标出现、内容/哈希正确、结果事件 applied。
12. create 目标已存在：失败、不覆盖、无成功事件。
13. replace 成功：before 匹配、after 正确、备份存在（若强制）。
14. replace before 不匹配：`E_CONFLICT`，目标不变。
15. replace 目标被锁定：`E_LOCKED`，即使有 approval。
16. delete 成功：备份哈希正确、目标消失、结果事件。
17. delete 备份失败：目标不删。
18. delete 无 approval：`E_APPROVAL_REQUIRED`。
19. 多 op 依赖顺序：按拓扑执行；依赖失败则下游 skipped。
20. 批次失败：已成功保留、后续 skipped、batch_finished=failed。

**安全**

21. 父目录 junction：`E_LINK_BOUNDARY`，项目外无文件。
22. 目标 symlink：拒绝；删除/覆盖不跟随。
23. `creation/` 路径：拒绝。
24. 两个进程并发：一个 `E_BUSY`，无交错事件。
25. 篡改 Plan 后 Preflight：`E_PLAN_TAMPERED`。
26. 伪造 approval：core hash 不匹配 `E_APPROVAL_INVALID`。
27. 重放 approval 到不同 path/hash：拒绝。

**故障/恢复**

28. 临时文件写失败：目标不变，temp 清理/记录。
29. replace 后崩溃：恢复识别已提交，不重复写。
30. 事件写一半：load_decisions 报 corrupt，恢复停止。
31. 删除后外部重建：记录 external change，不自动再删。
32. `--dry-run` 完全只读（文件列表/哈希/decisions 字节不变）。
33. 未完成批次 `verify` 报 `E_OPERATION_INCOMPLETE`（或不报 error 但 warning；需定稿）。
34. 恢复补记 `recovered_committed` 后，事件链/状态一致。

**CLI**

35. `run --dry-run --json` 输出单对象、exit 0。
36. 冲突 exit 2，错误含 `E_CONFLICT`；执行失败 exit 1。
37. `recover` 只读默认不写；`--apply` 才写。
38. `approve-operation` 缺 `--yes` 不写事件。

### 10.3 测试运行方式

```powershell
C:\Users\lenovo\AppData\Local\Programs\Python\Python315\python.exe -m unittest discover -s tests -p "test_creation_*.py"
C:\Users\lenovo\AppData\Local\Programs\Python\Python315\python.exe -m unittest discover -s tests
```

- 全部使用 `tempfile.TemporaryDirectory()`；不得写仓库根 `report.md`/`real_crash.log`/真实 `creation/`。
- Windows symlink 权限不足时用 junction 或 `unittest.skipUnless`；skip 不得计为通过，报告必须写明。
- 故障注入测试用子进程，避免测试进程崩溃；父进程收集退出码/文件状态。
- 不使用 pytest、hypothesis、mock 之外的第三方；标准库 `unittest.mock` 可用。

### 10.4 验收数字（建议）

- 3.2.2.1 契约 + P3 修复：≥ 45 个新测试。
- 3.2.2.2 执行器：≥ 35 个新测试。
- 3.2.2.3 恢复：≥ 20 个新测试。
- 3.2.2.4 校验器：≥ 20 个新测试。
- 3.2.2.5 集成：≥ 15 个新测试。
- 3.2.2.6 端到端：≥ 6 个场景（含真实崩溃/并发/路径攻击）。

不追求数字本身；每个阶段必须至少覆盖“正常、冲突、失败、恢复、安全边界”五类。

---

## 11. 阶段拆分与依赖关系

### 11.1 推荐拆分

| 子阶段 | 范围 | 主要产物 | 退出条件 |
|---|---|---|---|
| 3.2.2.0 | 开工前加固（建议） | P3-1/2/3 修复 +（若用户确认）§5.6 构造器边界修复 + 回归 | 三项 P3 测试通过；若纳入 §5.6，则尾点/尾空格/三点组件测试通过；现有 124/305 不回退 |
| 3.2.2.1 | Operation Contract | `operations.py`、schema 校验、ID/哈希、Plan 绑定、上限 | 纯内存测试全绿；契约冻结；不写业务文件 |
| 3.2.2.2 | Safe Executor + 锁 | `executor.py`、`write_bytes_atomic`、exclusive create、executor.lock、batch_started/result/finished | create/replace 成功/冲突/锁；无部分目标；并发拒绝 |
| 3.2.2.3 | Conflict + Recovery | `recovery.py`、备份、delete、故障注入、recover CLI | 崩溃/半执行可诊断；不自动危险重试；delete 有备份 |
| 3.2.2.4 | Operation Plan Validator | Plan↔Operation↔Spec 绑定、审批检查、风险等级 | 篡改/越权/缺失审批全部拒绝 |
| 3.2.2.5 | Records + State/Digest/Verify | 事件扩展、digest 刷新/stale、verify 检查、status 摘要 | `verify` 对未完成批次/审批异常有明确结果；事件链一致 |
| 3.2.2.6 | End-to-End Safety Acceptance | 场景测试、故障注入、独立验收报告 | 无 P0/P1；P2 有处置；文档与实现一致 |

**2026-11-21 决策后的正式口径**：

- 上表 3.2.2.0 不再是“建议/若用户确认”，而是**强制开工门槛**；范围固定为 P3-1、P3-2、P3-3、§5.6 四项。
- 3.2.2.0 的退出条件至少包括：四项各自的正向/负向测试通过、现有 124 creation / 305 total 不回退、§5.6 的 18 个路径策略测试通过、`report.md`/`real_crash.log` 未改变。
- 3.2.2.1 的 Contract 冻结必须以 3.2.2.0 的验收结果为输入；3.2.2.2 不得在 P3-2 未修复时执行 `replace`。
- 具体任务卡、最小修改、测试名称与验收证据见 `docs/phase-3.2.2.0-preflight-plan.md`。

### 11.2 依赖关系

```
3.2.2.0 (P3 修复)
   └──> 3.2.2.1 (Contract) ──> 3.2.2.2 (Executor + lock)
                                   ├──> 3.2.2.3 (Recovery + delete)
                                   └──> 3.2.2.4 (Validator)
                                             └──> 3.2.2.5 (Records/Verify)
                                                       └──> 3.2.2.6 (Acceptance)
```

- 3.2.2.1 必须先冻结；否则 executor/recovery 字段会漂移。
- 3.2.2.2 可以先不做 delete，但必须把 `create`/`replace` 的崩溃语义做对。
- 3.2.2.3 依赖 3.2.2.2 的 temp/backup 命名；不要在 3.2.2.3 重新发明。
- 3.2.2.4 的审批校验可以晚于执行器，但执行器不能在没有审批校验的情况下处理高风险 op；建议高风险 op 在 3.2.2.2 就拒绝，直到 3.2.2.4 完成。
- 3.2.2.5 的 verify 扩展必须与事件 schema 同批；不要先写事件、后补校验。
- 3.2.2.6 独立验收，不复用实现者的“自测通过”作为结论。

### 11.3 为什么不建议合并

- 把 Contract + Executor 合并：执行器会被实现细节反向污染契约；测试无法只验证数据。
- 把 Recovery 合并进 Executor：恢复逻辑会依赖执行器的内存状态，无法独立测试崩溃。
- 把 Approval 合并进 Contract：审批是用户行为，不是 JSON 校验；混在一起会让人误以为“字段存在就是已批准”。
- 把 State/Digest 集成合并进 Executor：执行器会越权改 state/digest，破坏现有 verify 语义。

### 11.4 每个阶段的完成定义

- 代码 + 测试 + 文档 + 实施报告四件套齐全。
- 新增测试在 Python 3.15 上通过；Windows symlink skip 如实记录。
- 不回归现有 124/305（或当时基线）。
- 不修改 `report.md`、`real_crash.log`；不提交真实玩家日志。
- 不做 Git add/commit/push（除非用户另行授权）。

---

## 12. Token 与成本控制

### 12.1 Phase 3.2.2 本身不消耗模型 Token

执行器是无模型的确定性组件；它不调用 LLM、不生成文本、不访问网络。所有测试也应在离线状态通过。因此 3.2.2 的成本主要是开发/审查时间，不是 API 费用。

### 12.2 为后续 Provider 阶段设定的原则

1. **模型只输出增量 Operation**，不输出整个项目；已存在且未变化的文件不进入上下文。
2. **上下文用 Project Digest**：Spec 摘要、Plan、目标文件哈希/大小、锁、最近决策；不把全部文件正文塞进 prompt。
3. **大文件用差异/分块**：v1 Operation 只支持 inline_text；超过 2 MiB 的文件必须走后续 artifact/分块，不允许模型一次返回巨大文本。
4. **确定性校验交给 Python**：路径、哈希、Plan 绑定、预算、锁全部本地计算，不消耗 Token。
5. **Provider 与执行器分离**：Provider 返回候选；执行器不依赖特定模型、不把 API key 写入 Operation/事件。
6. **硬停止**：Spec `constraints.budget` 的 `max_usd`/`max_input_tokens`/`max_output_tokens`/`max_images`/`max_wall_clock_seconds` 仍是上限；执行器在写文件前检查预算快照，超限 `E_BUDGET_EXCEEDED`，不写。
7. **最大 Operation 数量**：200/批；超过要求用户拆分 Plan，不自动截断（截断会改变语义）。
8. **最大重试**：v1 不自动重试危险操作；Provider 重试在 3.2.3 单独设计，且必须计入预算。

### 12.3 成本可见性

- 事件记录 `operations_sha256`、`operation_count`、`size_bytes` 总和；不记录正文。
- 若后续引入 Provider，`runs/<run_id>.json` 记录 usage/cost；Operation 事件记录 `run_id` 引用，不重复计费。
- 报告/CLI 显示“本批次写入字节数/文件数/备份数”，帮助用户理解成本与风险。

---

## 13. 未解决问题与技术取舍

### 13.1 已定取舍

1. **不做跨文件事务**：文件级原子 + 批次顺序 + 明确部分完成；理由见 §6.4。
2. **不做自动回滚**：回滚本身可能失败，且会掩盖已发生事实；用备份 + 显式恢复。
3. **v1 仅 inline_text**：避免内容来源不可验证；二进制/图片后续升版。
4. **replace 保守审批**：所有 replace 都需 `approve_operation`；先安全，再优化体验。
5. **create 的原子性用 exclusive link/rename**：不用“先检查再 `os.replace`”假装原子。
6. **不把 `reason` 纳入审批哈希**：减少无意义重新审批；但 reason 仍记录。
7. **不自动修 decisions 半行**：现有 `load_decisions` 会报 corrupt；恢复器停止，交人工/后续工具。
8. **不为执行器新增模型 run**：batch_id 独立，避免语义混淆。

### 13.2 开放问题（需产品/架构确认）

> **2026-11-21 决策状态**：第 1、2、4 项已由五项正式决策关闭；第 5 项按“所有 replace 必须审批”收紧；其余仍开放。原文保留如下，便于追溯当时的问题。

1. **replace 是否全部需要审批？** ~~本文建议是；如果产品要求“低风险 replace 自动执行”，需要定义可验证的白名单（文件类型/角色/大小），并加测试，不能由模型自评。~~ **已决定：所有 replace 必须审批，无白名单。**
2. **备份保留多久？** ~~默认“成功后保留，用户显式清理”；是否需要自动保留 N 天/容量上限？需要产品决定。~~ **已决定：默认保留，只有用户显式授权才能清理。**
3. **delete 的恢复是否要在 v1 提供 CLI？** 本文建议 v1 至少能 `recover` 查看备份路径，`restore` 可放 3.2.2.6 或后续。
4. **未完成批次 `verify` 是 error 还是 warning？** ~~本文倾向 error（`E_OPERATION_INCOMPLETE`），因为“执行到一半”不是健康状态；但会影响 CI/脚本，需要确认。~~ **已决定：合法中断/部分成功/等待恢复为 warning；完整性错误为 error，见 §7.6。**
5. **`operation_batch_started` 是否要求先有 approval 事件？** 建议高风险 op 必须先有；低风险 create 可以没有。若批次中混合，Preflight 逐 op 判断。**补充：所有 replace/delete 均属于必须先有 approval 的集合；“高风险”不再只是部分 replace。**
6. **是否允许用户在批次开始后补审批？** 本文建议不允许；批次开始后只执行已批准的集合，补审批需要新批次。这样避免“边执行边授权”的竞态。
7. **Windows 上 `os.link` 的可用性**：NTFS 支持硬链接，但跨卷/权限/文件系统可能失败；需要实测，失败时 fail closed。若产品要求 FAT/exFAT，需另设计“exclusive create + 直接写”的降级方案，但会牺牲原子性，必须明确标注。
8. **Operation 文件是否要加密/权限收紧？** 内容可能含用户代码；`creation/` 默认不导出，但本地权限是否要 0600？Windows ACL 复杂，建议先文档提醒，后续再做。
9. **是否支持 `copy`/`rename`？** v1 不做；`copy` 需要 Plan 源路径字段和源哈希；`rename` 需要原子性与跨目录语义，容易引入新漏洞。
10. **是否需要操作级预算？** 文件写入本身不花钱，但后续 generate 操作需要；v1 只做大小限制，不做 USD 预算。

### 13.3 被拒绝的方案

| 方案 | 拒绝理由 |
|---|---|
| 模型直接写文件/执行 shell | 无法保证路径/内容/审批；违反项目原则 |
| 先写目标再校验 | 失败会留下部分文件；违反“不把用户文件当草稿” |
| 用一个 `os.replace` 覆盖所有动作 | create 会覆盖竞态出现的目标；delete 无法回滚 |
| 用 `git` 做事务/回滚 | 项目禁止擅自 Git 写；且用户工作区可能已有未提交修改 |
| 用数据库做操作日志 | 引入依赖/迁移/并发复杂度；现有 ndjson 足够 |
| 自动重试到成功 | 会放大冲突/覆盖风险；危险操作必须人确认 |
| 用文件锁替代哈希校验 | 锁不能证明内容；外部修改仍可发生在锁外 |
| 用模型判断风险等级 | 不可验证；风险必须由路径/动作/锁/哈希事实推导 |

### 13.4 残余风险

- TOCTOU 无法完全消除；只能缩小窗口并在文档/测试中明确。
- 本地用户可篡改 `decisions`/`creation`；不是密码学账本。
- Windows 文件系统/杀毒软件可能导致 `os.replace`/`unlink` 失败；执行器必须报错而不是假装成功。
- 网络盘/同步盘上的原子性不保证；建议文档明确“仅本地磁盘”。
- 备份可能包含敏感内容；不导出、不提交、权限收紧是必要但不充分的。
- 事件日志可能因磁盘满写不进去；执行器必须停止，不能继续写业务文件。

---

## 14. 与现有文档/验收的关系

- 本设计不改变 Phase 3.2.1 已验收的 State/Spec/Plan/Replay/Digest/路径安全语义；新增的 Operation 事件是非生命周期事件，`replay` 目前忽略它们（`replay.py:344`），因此不会改变 `expected_state`。
- 3.2.2.5 若要 `verify` 检查未完成批次/审批，需要显式扩展 `digest.verify_project`，并增加测试；不能靠“事件链没断”推断执行完整。
- `docs/phase-3.2-ai-creation-workflow-design.md` §10 是本设计的直接上游；本文把其中的 `operations.py` 从“计划中的模块”细化为可实施契约与执行语义。
- `docs/phase-3.2.1-final-acceptance.md` 的三项 P3 在本文 §9 给出修复方案；本轮不修改代码，因此 P3 仍未修复。§5.6 是例外：2026-11-21 复核确认当前工作树已由 R4 覆盖，见 §5.6.1；3.2.2.0 只做独立验收与补测，不重复实现。
- 本文不替代 Phase 3.2.2 的实施报告/验收报告；实施后应另写 `docs/phase-3.2.2-...-report.md`，并区分已验证/验证不足/未完成。

---

## 15. 设计验收条件（供审阅）

本设计文档至少应满足：

1. 能回答“模型提出的 Operation 如何被限制在 Plan target 内”；
2. 能回答“用户手动修改文件后如何检测、如何不覆盖”；
3. 能回答“写到一半崩溃后，下一次启动如何判断目标状态”；
4. 能回答“哪些操作必须用户批准，审批绑定到什么”；
5. 能回答“delete 的恢复材料是什么，备份失败怎么办”；
6. 能回答“两个进程同时执行会怎样”；
7. 能回答“三项 P3 与执行器的关系，先修哪项”；
8. 能回答“v1 不做什么，为什么”；
9. 能给出可测试的阶段拆分与验收数字；
10. 不引入新第三方依赖、不扩大产品范围、不改变 Phase 3.2.1 已验收语义；
11. 完整记录 2026-11-21 五项正式决策，并明确它们对 replace、verify、备份、锁、路径和阶段顺序的约束；
12. 给出 3.2.2.0 四项任务的可执行计划，且不把四项合并成一次路径/存储大重构。

若审阅认为上述任何一项不成立，应先修订本文，再进入 3.2.2.1 实施。设计通过后，先执行 3.2.2.0，不得跳过。

---

## 附录 A：错误码建议

| 错误码 | 含义 |
|---|---|
| `E_OPERATION_SCHEMA` | Operation 文件 schema/字段错误 |
| `E_OPERATION_ID` | op_id 格式/重复 |
| `E_OPERATION_PATH` | 路径不安全/不在 Plan target |
| `E_OPERATION_HASH` | 内容哈希/大小不匹配 |
| `E_OPERATION_DEPENDENCY` | 依赖未知/环/自依赖 |
| `E_OPERATION_LIMIT` | 操作数/大小超限 |
| `E_PLAN_NOT_APPROVED` | Plan 未 approved |
| `E_PLAN_TAMPERED` | Plan 内容哈希与审批不符 |
| `E_SPEC_CHANGED` | Spec revision 不符 |
| `E_TARGET_EXISTS` | create 目标已存在 |
| `E_CONFLICT` | before hash 不匹配/外部修改 |
| `E_LOCKED` | 目标被锁定 |
| `E_LINK_BOUNDARY` | 路径包含链接/junction |
| `E_CREATION_PATH` | 目标在 creation/ 内 |
| `E_APPROVAL_REQUIRED` | replace/delete 缺少审批 |
| `E_APPROVAL_INVALID` | 审批与 op core 不符 |
| `E_LOCK_OWNER` | 释放锁时 owner_token 不匹配 |
| `E_BUSY` | 已有执行器锁 |
| `E_BATCH_ALREADY_STARTED` | 批次重复执行 |
| `E_WRITE_FAILED` | 临时文件/提交失败 |
| `E_VERIFY_AFTER_WRITE` | 写后哈希不符 |
| `E_EXTERNAL_CHANGE_AFTER_WRITE` | 写后目标被外部修改 |
| `E_JOURNAL_FAILED` | 结果事件写入失败 |
| `E_OPERATION_RESULT_MISMATCH` | `applied` 记录与目标实际状态不一致（verify error） |
| `E_OPERATION_EVENT_INCONSISTENT` | 同 op 结果冲突或 finished 后缺结果（verify error） |
| `E_BACKUP_INVALID` | 备份/恢复元数据缺失或哈希不符 |
| `W_OPERATION_INCOMPLETE` | 有 started 无 finished、其余记录自洽（verify warning） |
| `W_OPERATION_PARTIAL` | 批次部分成功、记录自洽（verify warning） |
| `W_OPERATION_UNCERTAIN` | 结果 uncertain、事件完整（verify warning） |

错误对象复用 `CreationError(code, message, location, details)`；CLI JSON 输出应包含 `code/location/message`，不输出 traceback（除非 `--debug`）。

## 附录 B：Operation 状态机

```
proposed
  ├─ preflight_failed ──> rejected (不写文件)
  ├─ approval_required ──> awaiting_approval
  │                         ├─ approved ──> ready
  │                         └─ rejected ──> rejected
  └─ ready ──> applying
                ├─ applied ──> done
                ├─ failed ──> failed (可重试，需重新 preflight)
                └─ uncertain ──> needs_recovery (禁止自动重试)
```

批次状态：`started` → `succeeded` / `failed` / `uncertain`；`started` 没有对应 finished 时，`verify` 报 `E_OPERATION_INCOMPLETE`。

## 附录 C：最小示例（create）

```json
{
  "schema_version": "1.0.0",
  "plan_id": "plan_demo000001",
  "spec_revision": 1,
  "plan_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "operations": [
    {
      "op_id": "op_create_readme",
      "action": "create",
      "path": "docs/hello.md",
      "expected_absent": true,
      "expected_sha256": null,
      "content_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      "size_bytes": 12,
      "source": {"kind": "inline_text", "text": "hello veyra\n"},
      "depends_on": [],
      "create_parents": true,
      "approval_id": null,
      "reason": "示例"
    }
  ]
}
```

执行器必须重新计算 `content_sha256` 与 `size_bytes`；示例中的哈希只是占位，实际测试应使用真实值。

## 附录 D：审阅清单

- [ ] Operation 契约是否覆盖 create/replace/delete 的安全语义
- [ ] 是否明确“Plan 是路径边界、Operation 是内容提案、approval 是单次授权”
- [ ] 是否明确 before/after hash、exclusive create、原子替换
- [ ] 是否明确失败停止、部分执行、不自动回滚
- [ ] 是否明确单写者锁与 TOCTOU 残余风险
- [ ] 是否明确 delete 备份与恢复
- [ ] 是否明确三项 P3 的修复方案与测试
- [ ] 是否明确 v1 不做的动作与理由
- [ ] 是否明确阶段拆分、依赖与验收条件
- [ ] 是否明确 Token/成本边界（3.2.2 不调用模型）
- [ ] 是否保留 Phase 3.2.1 已验收语义
- [ ] 是否没有修改任何既有代码/报告，没有 Git 写操作

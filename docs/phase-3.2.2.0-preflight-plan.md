# Veyra Phase 3.2.2.0 — Preflight Hardening 实施计划

- 文档状态：计划稿，等待用户与 ChatGPT 审阅
- 适用基线：Phase 3.2.1 已验收基线（`main` / `0fa1bbfff9c28e1b12c94b334f23b732d059926e`）
- 关联设计：`docs/phase-3.2.2-operation-executor-design.md`（2026-11-21 五项决策更新版）
- 本轮范围：只做只读核对与规划；**不修改源代码、不实施 3.2.2.0、不执行 Git 写操作**
- 计划性质：3.2.2.0 是 3.2.2.1 Contract 冻结前的强制开工门槛

---

## 0. 结论先行

Phase 3.2.2.0 只包含四项加固，且四项必须分别实施、分别测试、分别验收，**不得合并成一次路径/存储大重构**：

| 任务 | 目标文件 | 当前缺陷 | 最小改动 | 是否预期产生源代码改动 |
|---|---|---|---|---|
| P3-2 | `veyra_creation/store.py` | `approve_plan` 幂等分支不重算 Plan 内容哈希 | 在 `:711-712` 短路分支增加“重算 + 对照审批事件” | 是，约 10 行 |
| P3-1 | `veyra_creation/store.py` | `project_id`/`spec_id`/`run_id` 非字符串输入泄漏 `TypeError` | 在三处正则前增加 `isinstance` | 是，3 行 |
| P3-3 | `veyra_creation/digest.py` | `build_digest(write=True)` 未单独检查 `digest_path` 链接 | 写前调用 `store._assert_no_link(store.digest_path, "digest")` | 是，1 行 |
| §5.6 | `veyra_pack/paths.py` / `veyra_creation/store.py` | 设计时存在构造器路径规范化缺口 | **当前工作树已由 R4 覆盖**；本任务以复核、补测、真实 junction 回归为主 | 预计否；仅在发现新缺口时做最小修复 |

> **重要复核结论**：设计文档 §5.6 描述的“构造器接受 `.. `、`...`、`....` 并可能声明/实际路径不一致”是设计期间（R3 时代）的历史发现。2026-11-21 对当前工作树的只读探针和 `tests/test_creation_path_policy.py` 的 18 个测试均表明，当前实现已经拒绝这些输入；详见 §4.4 与设计文档 §5.6.1。因此 3.2.2.0 的 §5.6 任务应写成“独立验收 + 补测”，而不是重复实现。**这一点会影响 3.2.2.0 是否产生 §5.6 的源代码 diff，请审阅时确认接受该口径。**

本轮不执行上表中的任何代码修改；本文件只定义实施边界、测试与验收证据。

---

## 1. 目标、非目标与开工条件

### 1.1 目标

1. 在 3.2.2.1 冻结 Operation Contract 之前，消除会污染执行器授权链的已知加固缺口。
2. 把 P3-2 从“`verify` 事后发现”提升为“审批 API 当场拒绝”，避免执行器把已篡改 Plan 当作 approved。
3. 把 P3-1 的未定义 `TypeError` 转为稳定 `CreationError`，保证未来 CLI/JSON 错误契约不被 traceback 破坏。
4. 为 P3-3 的内部写入建立“写前独立 link 检查”的最小样板，供 3.2.2.2 的 executor/backups/temp 复用。
5. 对 §5.6 的路径边界做一次**可重复的独立验收**：确认原始组件检查、resolve 前后检查、声明/实际路径一致性、真实 junction 拒绝均成立；有缺口才最小修复。
6. 保持 Phase 3.2.1 的既有 API、历史数据和已验收语义不变。

### 1.2 非目标

- 不实现 Operation Contract、执行器、锁、备份、恢复、审批 CLI；这些属于 3.2.2.1 之后。
- 不实现 `generate_*`、Provider、预算、Skill 编排。
- 不重构 `veyra_pack.paths` 的路径策略，不引入新的路径抽象层。
- 不修改 `report.md`、`real_crash.log`、既有验收报告或历史设计原文。
- 不执行 `git add`、`git commit`、`git push`、`git reset`、`git checkout`、`git clean`、`git rebase` 或历史重写。
- 不新增第三方依赖；不把测试写到项目根目录或真实用户文件上。

### 1.3 开工条件

在获得用户明确授权前，不开始任何代码修改。授权后开工前还必须满足：

1. 当前分支、HEAD、`git status` 与本计划基线一致；若有用户新增改动，先只读核对，不覆盖。
2. `report.md` 只核验 SHA-256，不读取内容；`real_crash.log` 不读取、不修改、不删除。
3. 使用临时目录运行所有新增测试；不得把临时文件、junction、备份留在仓库根。
4. 使用项目既有测试解释器（当前为 Python 3.15.0）运行；Windows symlink 权限不足时，用真实 junction 覆盖，不能 skip 后宣称通过。
5. 每项任务独立形成可审查的 diff 与测试结果；不得把四项合并为一次提交。

### 1.4 基线数字

- 最近已验收基线：**124 个 creation 测试通过（skipped 1）/ 305 个全量测试通过（skipped 2）**。
- 2026-11-21 本轮只读复核：`tests/test_creation_path_policy.py` **18 个测试全部通过**（真实 junction，临时目录）。
- 本轮不修改代码，因此不改变上述基线；实施 3.2.2.0 后，新增测试计入新数字，既有 124/305 不得回退。

---

## 2. 共同实施约束

### 2.1 修改边界

- 只允许改任务卡列出的目标文件；不得顺手改 `version` 校验、event_id 校验、路径策略其他部分或无关格式化。
- 每项任务只解决一个缺陷；P3-1 不引入公共 `_require_id` 重构，P3-2 不新增 Plan schema 字段，P3-3 不修改 `write_json_atomic`。
- 不改变合法输入的公开行为；只把未定义异常/缺失检查变成明确拒绝。
- 不改变现有 JSON schema、事件格式、历史数据布局；不迁移、不重写 `decisions.ndjson`。

### 2.2 测试约束

- 所有测试使用 `tempfile.TemporaryDirectory()` 或等价临时目录；cleanup 必须删除测试创建的 junction/文件。
- 负向测试必须断言：
  - 稳定 `CreationError.code`；
  - 无业务文件/状态副作用；
  - 不泄漏 `TypeError` 或 traceback。
- 正向测试必须覆盖原有合法路径，尤其是 `None` 默认值、正常 digest 写入、`.` 组件与普通绝对路径。
- 测试命名建议使用 `test_<behavior>_<condition>`；同类测试放入现有测试类，不新建平行大文件。

### 2.3 验证命令

在 `D:\Projects_vesper\veyra` 下使用 Python 3.15：

```powershell
& 'C:\Users\lenovo\AppData\Local\Programs\Python\Python315\python.exe' -m unittest discover -s tests -v
```

针对性运行：

```powershell
& 'C:\Users\lenovo\AppData\Local\Programs\Python\Python315\python.exe' -m unittest discover -s tests -p 'test_creation_store.py' -v
& 'C:\Users\lenovo\AppData\Local\Programs\Python\Python315\python.exe' -m unittest discover -s tests -p 'test_creation_digest.py' -v
& 'C:\Users\lenovo\AppData\Local\Programs\Python\Python315\python.exe' -m unittest discover -s tests -p 'test_creation_path_policy.py' -v
```

验收时必须记录完整命令、通过/跳过数量、失败详情；不能只写“测试通过”。

---

## 3. 任务依赖与执行顺序

建议顺序（每步可独立回滚）：

1. **3.2.2.0.1 P3-2**：最高优先级，先切断“篡改 Plan 仍返回 approved”的授权漏洞。
2. **3.2.2.0.2 P3-1**：低风险类型加固，为后续 CLI/JSON 错误契约铺路。
3. **3.2.2.0.3 P3-3**：内部写入 link 检查样板。
4. **3.2.2.0.4 §5.6**：独立验收路径边界；若前一步发现路径辅助函数问题，在此一并确认，但不得借机重构。

依赖关系：

- P3-2 不依赖其他三项；但 3.2.2.2 的 `replace` 执行必须以 P3-2 完成为前提。
- P3-1 与 P3-3 相互独立；可与 P3-2 同批审查，但必须分开测试与记录。
- §5.6 的复核结果可作为 3.2.2.1 Contract 路径校验的输入；若发现真实缺口，必须先修复再冻结 Contract。

每步完成后设置检查点：代码 diff 审查、目标测试通过、全量回归通过、保护文件哈希未变；四项全部完成后才形成 3.2.2.0 验收报告。

---

## 4. 任务卡

### 4.1 P3-2：`approve_plan` 幂等分支重新校验 Plan 内容哈希

#### 目标文件

- 源码：`veyra_creation/store.py`
- 测试：`tests/test_creation_store.py`（可复用 `creation_helpers.make_store`、`confirmed_with_deliverables`、`plan_payload`）

#### 当前缺陷

`store.py:693-741` 的 `approve_plan` 在幂等分支：

```python
if plan.get("state") == "approved" and state.get("approved_plan_id") == plan_id:
    return plan
```

该分支（`:711-712`）直接返回，不重新计算 `_plan_content_sha(plan)`，也不与 `approve_plan` 事件中的 `sha256` 比较。审批后篡改 `creation/plan.json`，再次调用仍返回 `state="approved"`；虽然 `verify` 会报 `E_PLAN_TAMPERED`，但审批 API 已经给出了错误的成功信号。

#### 最小修改范围

只改 `approve_plan` 的短路分支，正常审批路径（`:713-741`）不动：

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

约束：

- **不新增** `plan.approved_sha256` 字段，不改 schema，不迁移历史数据。
- 以 `approve_plan` 事件的 `sha256` 为唯一审批凭证；`_plan_content_sha` 已排除 `state`，与正常审批分支计算口径一致。
- 找不到审批事件时拒绝，不能因 `state.approved_plan_id` 存在就返回成功。
- 幂等成功时不追加事件、不更新 state、不重写 plan。
- 不在此任务中重复 `check_plan`；正常审批已经做过结构校验，这里的目标是校验“内容是否仍是审批时的内容”。

#### 负向测试

新增到 `tests/test_creation_store.py` 的 `PlanTests` 或独立 `PlanApprovalTamperTests`：

1. `test_repeated_approval_is_idempotent_without_new_event`
   - 创建项目 → 确认 Spec → `save_plan` → `approve_plan`；
   - 记录事件数；再次 `approve_plan`；
   - 断言仍返回 `state="approved"`，事件数不变，`state.approved_plan_id` 不变，`verify_project` 无 error。
2. `test_approval_rejects_plan_tampered_after_approval`
   - approve 后读取 `plan.json`，修改一个参与内容哈希且仍保持 schema 合法的字段（建议 `steps[0]["prompt"]`，不要改 `state`）；
   - 直接写回 `plan.json`（测试内使用 `json.dumps`/`write_text`，不经过 `save_plan`，避免被其校验拦截）；
   - 再次 `approve_plan`；
   - 断言 `CreationError.code == "E_PLAN_TAMPERED"`，且没有追加新事件；`verify_project` 仍报 `E_PLAN_TAMPERED`。
3. `test_approval_rejects_plan_without_matching_event`（可选，若构造成本低）
   - 若能在临时 fixture 中移除/替换审批事件而不破坏链，断言无匹配事件时拒绝；
   - 若构造会破坏 `load_decisions` 链，则不做该测试，避免用非法 fixture 伪造结论。

#### 正向回归

- 现有 `test_plan_approval_is_recorded_and_blocks_replacement` 必须继续通过。
- 现有 `test_revising_spec_supersedes_the_approved_plan` 必须继续通过。
- 合法重复审批不产生新事件、不改 `plan.json`。
- `verify_project` 对合法项目仍无 error。

#### 验收条件

- 篡改后 `approve_plan` 稳定抛 `E_PLAN_TAMPERED`，不返回 approved。
- 未篡改的幂等调用行为不变，事件数不增加。
- 既有 Plan/审批/verify 测试全部通过；全量 305 不回退。
- 不修改 `plan.py`、`digest.py`、事件 schema 或历史数据。

#### API / 历史数据影响

- 公开 API 签名不变。
- 合法调用行为不变；只有“审批后篡改再审批”这一非法路径从“错误成功”变为“明确拒绝”。
- 不改变 `plan.json`/`decisions.ndjson` 格式；旧项目已有审批事件含 `sha256`，可直接兼容。

#### 风险与回滚

- 风险：`load_decisions()` 读取失败会向上抛现有决策错误；这是正确的 fail closed，不应吞掉。
- 风险：事件字段名若与历史数据不一致，需先只读核对；当前正常分支写入的是 `sha256`，与方案一致。
- 回滚：仅还原 `approve_plan` 短路分支与新增测试，不影响正常审批路径。

---

### 4.2 P3-1：`ProjectStore.init` / `start_run` 的非字符串 ID 校验

#### 目标文件

- 源码：`veyra_creation/store.py`
- 测试：`tests/test_creation_store.py`

#### 当前缺陷

- `store.py:309-312`：`project_id` 在 `None` 时生成默认值，随后直接 `PROJECT_ID_RE.match(project_id)`；传 `123`/`object()` 会抛 `TypeError: expected string or bytes-like object, got 'int'`。
- `store.py:313-316`：`spec_id` 同理，location 为 `"spec"`；传非字符串会泄漏 `TypeError`。
- `store.py:1070-1073` 的 `start_run`：`run_id` 非 `None` 时直接 `RUN_ID_RE.match(run_id)`；传 `123` 会泄漏 `TypeError`。
- `pack_id` 在 `:305` 已有 `isinstance`，不是本任务范围；`version` 在 `:307` 的类型问题也不在本任务范围，避免扩大。

#### 最小修改范围

只加三处类型判断，不加公共 helper、不改默认生成语义：

```python
# store.py:311
if not isinstance(project_id, str) or not PROJECT_ID_RE.match(project_id):
    raise CreationError("E_PROJECT_ID", f"invalid project_id: {project_id!r}", "project")

# store.py:315
if not isinstance(spec_id, str) or not SPEC_ID_RE.match(spec_id):
    raise CreationError("E_SPEC_ID", f"invalid spec_id: {spec_id!r}", "spec")

# store.py:1072
if not isinstance(run_id, str) or not RUN_ID_RE.match(run_id):
    raise CreationError("E_RUN_ID", f"invalid run_id: {run_id!r}", "runs")
```

关键语义：

- `project_id=None` / `spec_id=None` / `run_id=None` 仍表示**自动生成默认值**，不得拒绝。
- 只拒绝“非 `None` 且非 `str`”的值；空字符串仍由原正则拒绝（`E_PROJECT_ID`/`E_SPEC_ID`/`E_RUN_ID`）。
- 校验必须在任何目录/文件写入之前，确保失败无副作用。
- `_run_path` 可保持现状；`start_run` 是公开入口，先校验即可，避免无谓改动。

#### 负向测试

新增到 `tests/test_creation_store.py`：

1. `test_init_rejects_non_string_project_id_without_writing`
   - `ProjectStore(root).init(name="Demo", pack_type="mod", project_id=123)`；
   - 断言 `E_PROJECT_ID`，location 为 `"project"`；
   - 断言 `creation/state.json` 不存在，且未创建业务文件。
2. `test_init_rejects_non_string_spec_id_without_writing`
   - `spec_id=object()`（或 `123`）；
   - 断言 `E_SPEC_ID`，location 为 `"spec"`；
   - 断言无 `state.json`。
3. `test_start_run_rejects_non_string_run_id`
   - 初始化项目 → `start_run("task", run_id=123)`；
   - 断言 `E_RUN_ID`，location 为 `"runs"`；
   - 断言 `runs/123.json` 不存在，`state.last_run_id` 未改变。
4. `test_non_string_ids_do_not_leak_typeerror`
   - 可用 `assertRaises(CreationError)` 并检查 `exception.code`，确保不是裸 `TypeError`；
   - 可与 1–3 合并，不重复构造。

#### 正向回归

- `init(project_id=None, spec_id=None)` 仍生成合法 ID，`state` 可正常加载。
- `start_run("task")` 仍自动生成 `run_...`，`last_run_id` 正确更新。
- 合法字符串 ID 的格式校验行为不变（非法格式仍报原错误）。
- 现有 124 creation 测试全部通过。

#### 验收条件

- 三种非字符串输入均抛稳定 `CreationError`，code/location 正确，无 `TypeError`。
- `None` 默认生成语义不变。
- 失败路径无 `creation/`/`runs/` 副作用。
- 无 schema/历史数据/API 签名变化。

#### API / 历史数据影响

- 不改变任何合法调用；只把未定义异常变为明确拒绝。
- 不改变 `state.json`/`run` JSON 结构；不迁移历史 run。
- `version`/`event_id` 的其他类型缺口不在本任务处理，留作后续独立审查。

#### 风险与回滚

- 风险：误把 `None` 当非法值；测试必须覆盖 `None` 正向路径。
- 风险：`project_id=0` 等假值会被 `if project_id is None` 保留，随后由 `isinstance` 拒绝；这是期望行为。
- 回滚：还原三处条件与新增测试，不影响其他逻辑。

---

### 4.3 P3-3：`build_digest(write=True)` 写前独立检查 `digest_path` 链接

#### 目标文件

- 源码：`veyra_creation/digest.py`
- 测试：`tests/test_creation_digest.py`；若需要 junction helper，可复用 `tests/test_creation_integrity.py` 的 `_make_junction` 或在测试内用 `cmd /c mklink /J` 创建临时 junction。

#### 当前缺陷

`digest.py:236-240` 的 `build_digest` 在 `write=True` 时直接：

```python
write_json_atomic(store.digest_path, payload)
```

没有在写入前单独调用 `store._assert_no_link(store.digest_path, "digest")`。前置的 `load_state/load_spec/...` 会检查各自路径，`creation/` 祖先被替换为 junction 时通常 fail closed；但 `digest.json` 自身若是链接/junction，缺少显式断言，属于纵深防御缺口，也会给执行器内部写入树立不好的样板。

#### 最小修改范围

在 `write=True` 分支、`write_json_atomic` 之前加一行：

```python
store._assert_no_link(store.digest_path, "digest")
write_json_atomic(store.digest_path, payload)
```

不修改 `write_json_atomic`，不新增路径策略，不改 digest payload/字段/排序。

#### 负向测试

新增到 `tests/test_creation_digest.py`（或 integrity 测试）：

1. `test_build_digest_write_rejects_digest_symlink`
   - 在项目外创建目标文件；
   - 尝试把 `creation/digest.json` 建为指向该文件的 symlink；
   - 若 Windows 无 symlink 权限，**不要 skip 后宣称通过**；改用 junction 用例或显式记录环境限制并让验收项保持未通过。
   - 断言 `build_digest(write=True)` 抛 `E_LINK_BOUNDARY`，项目外文件内容/哈希不变。
2. `test_build_digest_write_rejects_digest_junction`
   - 使用真实 junction：`creation/digest.json` 指向项目外目录；
   - 断言 `E_LINK_BOUNDARY`，项目外目录无新增文件。
3. `test_build_digest_write_rejects_replaced_creation_ancestor`
   - 将 `creation/` 替换为指向项目外的 junction（需先移走真实 creation，测试内完成并清理）；
   - 断言写入拒绝，项目外无写入；这是现有祖先检查的回归，不要求新代码。

#### 正向回归

- `test_digest_writes_and_reloads` 等现有测试继续通过。
- 正常 `build_digest(write=True)` 后 `digest.json` 可解析、`digest_id` 稳定、`verify_project` 无新增 error。
- 无链接时行为、字段、排序完全不变。

#### 验收条件

- `digest.json` 为 symlink 或 junction 时，写入前拒绝，错误稳定，项目外无写入。
- 正常 digest 写入/读取/verify 全部通过。
- 不修改 `write_json_atomic`、不改变 digest schema 或历史数据。
- 测试如因 Windows 权限无法创建 symlink，必须用 junction 覆盖；不得以“平台限制”直接判定通过。

#### API / 历史数据影响

- 公开 API 不变；正常调用行为不变。
- 仅对“digest_path 是链接”的异常场景新增拒绝；不迁移/不改写已有 digest。
- 该检查模式将作为 3.2.2.2 内部写入检查清单的最小样板。

#### 风险与回滚

- 风险：测试清理 junction 时误删目标；cleanup 必须先删链接目录项，不跟随目标。
- 风险：`_assert_no_link` 对不存在路径的行为需确认；现有实现应允许不存在目标，仅拒绝链接组件。
- 回滚：删除新增一行与测试，不影响正常 digest。

---

### 4.4 §5.6：构造器路径组件边界复核与补测

#### 目标文件

- 主实现（仅在有缺口时）：`veyra_pack/paths.py`、`veyra_creation/store.py`
- 测试：`tests/test_creation_path_policy.py`；必要时补 `tests/test_creation_store.py`
- **当前预期：无源代码改动**，以独立验收和补测为主。

#### 当前状态（2026-11-21 只读复核）

设计文档 §5.6 的原始记录是 R3 时代发现：`base\missing\.. \link\project`、`base\...\link\project`、`base\missing\...\link\project`、`base\....\link\project` 等形态曾未被统一拒绝。当前工作树已包含 R4 修复：

- `ProjectStore.__init__`（`store.py:198-268`）先 `normalize_lexical_path`，再 `find_link_component(declared)`、`resolve()`、`paths_equivalent`、再次 `find_link_component(resolved)`。
- `paths.py:181-235` 的 `_validate_windows_root_components` 在词法规范化前拒绝组件尾随点/空格、ADS、设备/UNC/驱动器相对/保留名。
- `paths.py:238-263` 的 `normalize_lexical_path` 先检查原始组件，再 `ntpath.abspath`。
- `paths.py:289-311` 的 `find_link_component` 逐级 `lstat`，不跟随链接。

本轮只读探针的 6 种输入全部被拒绝为 `CreationError E_PROJECT_PATH_TRAILING`，项目外未生成 `creation/state.json`：

| 输入形态 | 当前结果 | 项目外写入 |
|---|---|---|
| `base\missing\.. \link\project`（`.. ` 尾空格） | `E_PROJECT_PATH_TRAILING` | 无 |
| `base\...\link\project` | `E_PROJECT_PATH_TRAILING` | 无 |
| `base\missing\...\link\project` | `E_PROJECT_PATH_TRAILING` | 无 |
| `base\....\link\project` | `E_PROJECT_PATH_TRAILING` | 无 |
| `base\link.\project` | `E_PROJECT_PATH_TRAILING` | 无 |
| `base\link \project` | `E_PROJECT_PATH_TRAILING` | 无 |

现有 `tests/test_creation_path_policy.py` 18 个测试全部通过，覆盖真实 junction、missing parent、三点组件、尾随空格、CLI bypass、late ancestor junction。

#### 决策 1 要求与当前实现的对应

| 决策要求 | 当前实现/测试 | 3.2.2.0 动作 |
|---|---|---|
| 词法规范化之前检查原始路径组件 | `normalize_lexical_path` 先验 raw 再 `abspath`；`_validate_windows_root_components` 拒绝尾随点/空格 | 复核并保留 |
| resolve 前后都保持边界验证 | 构造器声明路径检查 + `resolve()` + resolved 再检查；`paths_equivalent` | 复核并补测 |
| 不能先穿过 junction 再报告异常 | 原始组件校验先拒绝；`find_link_component` 逐级不跟随 | 复核真实 junction 用例 |
| 不允许声明路径与实际路径不一致 | `paths_equivalent(declared, resolved)` 比较 | 复核并补测 |
| 增加负向测试和真实 junction 测试 | 现有 18 个测试已覆盖主要矩阵 | 补缺失变体（若有） |
| 保留 `.` 和正常路径兼容 | 正常绝对路径测试已过；`.` 组件需显式正向测试 | **新增正向测试** |
| 不扩大为不必要的路径策略重构 | 当前实现已满足；不重写 | 只做最小修复（若缺口） |

#### 最小修改范围（仅当复核发现缺口）

如果新增矩阵中任一输入未被拒绝，或出现声明/实际路径不一致：

1. 只允许修改 `veyra_pack/paths.py` 的 `_validate_windows_root_components` / `normalize_lexical_path`，或 `ProjectStore.__init__` 的检查顺序；
2. 修复必须是**单点、可解释、带回归测试**的，不得重写 `find_link_component`/`resolve_file` 或引入新路径抽象；
3. 修复后必须保持正常路径、`.` 组件、现有 18 个测试全部通过；
4. 若未发现缺口，记录“无源代码改动”，不为了产生 diff 而改代码。

#### 负向测试矩阵

现有测试已覆盖的用例不重复造；若计划新增，优先 table-driven：

```text
base\missing\.. \link\project
base\missing\...\link\project
base\missing\....\link\project
base\link.\project
base\link \project
base\...\link\project
base\....\link\project
base\.\link\project      # '.' 正常，但 link 仍须拒绝
```

每个用例断言：

- `CreationError.code` 为 `E_PROJECT_PATH_TRAILING` 或 `E_LINK_BOUNDARY`（不得裸 `FileNotFoundError`/`TypeError`）；
- 声明路径与实际创建路径一致（若允许创建）；
- 项目外目标目录没有新增 `creation/state.json` 或业务文件；
- 测试结束清理 junction 与临时目录。

#### 正向回归

- `ProjectStore(base\project).init(...)` 正常创建并加载。
- 带 `.` 组件的正常路径（如 `base\.\project`）被接受，且解析到同一项目。
- 正常绝对路径、已存在的普通目录、非链接父目录行为不变。
- 现有 18 个路径策略测试全部通过。

#### 验收条件

- 6 种探针输入全部稳定拒绝，且项目外无写入。
- 现有 18 个路径策略测试通过；新增 `.` 正向测试通过。
- 声明路径/实际路径一致性检查有效；resolve 后二次检查有效。
- 无路径策略重构；若无需修复，源代码 diff 为空，并记录复核证据。
- 全量 305 不回退。

#### API / 历史数据影响

- 预计无 API/数据变化；当前实现已拒绝这些输入。
- 若发现缺口并最小修复，只影响原本就应被拒绝的异常路径；正常路径保持兼容。
- 不改变 `ProjectStore` 的公开构造签名、不改变 `creation/` 布局。

#### 风险与回滚

- 风险：为了“修 §5.6”而重写路径策略，破坏正常 `.`/相对路径；本计划明确禁止。
- 风险：测试 junction 清理不彻底；必须使用临时目录并验证 cleanup。
- 风险：把历史 R3 现象当成当前缺陷，产生无意义 diff；以本轮探针和测试为准。
- 回滚：若做了最小修复，只还原对应条件与新增测试；不影响其他任务。

---

## 5. 测试计划汇总

### 5.1 新增测试清单（建议）

| 任务 | 测试文件 | 测试名（建议） |
|---|---|---|
| P3-2 | `tests/test_creation_store.py` | `test_repeated_approval_is_idempotent_without_new_event` |
| P3-2 | `tests/test_creation_store.py` | `test_approval_rejects_plan_tampered_after_approval` |
| P3-1 | `tests/test_creation_store.py` | `test_init_rejects_non_string_project_id_without_writing` |
| P3-1 | `tests/test_creation_store.py` | `test_init_rejects_non_string_spec_id_without_writing` |
| P3-1 | `tests/test_creation_store.py` | `test_start_run_rejects_non_string_run_id` |
| P3-1 | `tests/test_creation_store.py` | `test_none_ids_still_generate_defaults` |
| P3-3 | `tests/test_creation_digest.py` | `test_build_digest_write_rejects_digest_junction` |
| P3-3 | `tests/test_creation_digest.py` | `test_build_digest_write_rejects_digest_symlink`（有权限时） |
| P3-3 | `tests/test_creation_digest.py` | `test_build_digest_write_still_writes_normal_digest` |
| §5.6 | `tests/test_creation_path_policy.py` | `test_dot_component_in_root_is_accepted` |
| §5.6 | `tests/test_creation_path_policy.py` | 现有 18 个用例全部保留；仅补缺口变体 |

表内名称是计划建议，实施时可微调，但必须保持“一个行为一个测试”的可读性。

### 5.2 回归测试

必须运行：

- `test_creation_store.py`：Plan 审批、Spec 确认、runs、decisions。
- `test_creation_digest.py`：digest 写入、verify、symlink skip 行为。
- `test_creation_path_policy.py`：全部 18 个路径策略用例。
- 全量 `unittest discover`：既有 124/305 不回退。

### 5.3 测试数据与清理

- 所有测试在 `tempfile.TemporaryDirectory()` 下创建项目根、outside 目录、junction。
- 不读取/写入仓库根的 `report.md`、`real_crash.log`、真实玩家日志。
- cleanup 顺序：先删除 junction 目录项（`rmdir`/`unlink`，不跟随），再删除临时根；若 Windows 占用导致失败，报告并保留临时目录路径，不静默忽略。

### 5.4 测试证据

验收报告至少包含：

- 运行命令与 Python 版本；
- 每类测试的 `Ran N tests`、`OK`/`FAILED`、skipped 原因；
- 新增负向测试的异常 code 与“无副作用”断言；
- §5.6 探针输入/输出表；
- `git status --porcelain` 与 `report.md` SHA-256 的只读核验结果。

---

## 6. 验收标准（3.2.2.0 整体）

3.2.2.0 只有同时满足以下条件才算完成：

1. **P3-2**：篡改 Plan 后再次 `approve_plan` 稳定抛 `E_PLAN_TAMPERED`；合法幂等调用不增加事件；`verify` 回归正常。
2. **P3-1**：`project_id`/`spec_id`/`run_id` 的非字符串非 `None` 输入均稳定抛对应 `CreationError`，无 `TypeError`、无副作用；`None` 默认生成不变。
3. **P3-3**：`digest_path` 为 symlink/junction 时 `build_digest(write=True)` 拒绝；正常 digest 写入/verify 不回归；项目外无写入。
4. **§5.6**：6 种探针输入全部拒绝；18 个现有路径策略测试 + `.` 正向测试通过；声明/实际路径一致；无路径策略重构；若无缺口，记录“无源代码改动”。
5. **回归**：既有 124 creation / 305 total 不回退；新增测试全部通过；skipped 如实记录。
6. **保护文件**：`report.md` SHA-256 仍为 `A11D09D49217CA3C6CD7C269268C148558EA488067E8D1399BCAFD7ABBC2D68D`；`real_crash.log` 未读、未改、未删。
7. **Git**：未执行任何写操作；`git status` 只出现预期的源代码/测试修改（获得授权后），以及既有未跟踪目录。
8. **文档**：设计文档决策记录与本计划一致；不删除历史设计原文；实施报告区分已验证/验证不足/未完成。

任何一项不满足，3.2.2.0 不得标记 PASS，不得进入 3.2.2.1 实施。

---

## 7. 风险、回滚与停止条件

### 7.1 主要风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| 把 §5.6 历史缺口当成当前缺陷，重复修改已正确的路径逻辑 | 引入回归、扩大 diff | 先跑 18 个现有测试与 6 种探针；无缺口则不改代码 |
| P3-2 误伤合法幂等调用 | 审批 API 回归 | 以 `_plan_content_sha` + 审批事件 `sha256` 同口径比较；覆盖正常重复审批 |
| P3-1 误把 `None` 当非法 | 默认 ID 生成回归 | 明确保留 `None` 语义；正向测试覆盖 |
| P3-3 测试清理 junction 时误删目标 | 测试污染/数据损坏 | 只删链接目录项，不跟随；临时目录隔离 |
| 四项合并导致无法定位回归 | 审查/回滚困难 | 每项独立 diff、独立测试、独立检查点 |

### 7.2 回滚策略

- 每项修改保持最小、局部；回滚只还原该任务的条件/分支与新增测试。
- 不通过 Git 历史重写回滚；由用户授权后使用普通补丁/提交回退。
- 若某项测试暴露既有实现问题，先停止该项，记录证据，不擅自扩大修复范围。

### 7.3 停止条件

本计划完成后立即停止，等待用户与 ChatGPT 审阅：

- 不开始 P3-2/P3-1/P3-3/§5.6 的代码实施；
- 不进入 3.2.2.1 Contract；
- 不做 Git 写操作；
- 不修改 `report.md`/`real_crash.log`；
- 不把本计划标记为“已实施”。

获得明确授权后，才按 §3 的顺序开始 3.2.2.0.1。

---

## 8. 审阅检查表

请审阅时确认：

- [ ] 接受“§5.6 当前已由 R4 覆盖，3.2.2.0 以复核/补测为主，预计无源代码 diff”的结论；若要求必须产生路径代码改动，请明确指定要改的行为与验收测试。
- [ ] 接受 P3-2 不新增 `plan.approved_sha256`，以审批事件 `sha256` 为准。
- [ ] 接受 P3-1 保留 `None` 默认生成语义，只拒绝非 `None` 的非字符串。
- [ ] 接受 P3-3 只加一行 `_assert_no_link`，不改 `write_json_atomic`。
- [ ] 接受四项分别实施、分别测试，不合并为一次大重构。
- [ ] 确认 3.2.2.0 完成并通过验收后，才进入 3.2.2.1。
- [ ] 确认本轮不授权代码实施与 Git 写操作。

---

## 9. 交付物与下一步

本轮交付：

1. 更新后的 `docs/phase-3.2.2-operation-executor-design.md`（新增 §0.1 决策记录、§5.6.1 复核、§6.2.1 锁 owner、§6.5.1 备份、§7.6 verify 分级、§9 最小实现口径）。
2. 本计划文件 `docs/phase-3.2.2.0-preflight-plan.md`。

下一步（需用户明确授权）：

- 按 3.2.2.0.1 → 0.2 → 0.3 → 0.4 实施；
- 每步提交独立实施记录与测试证据；
- 四项全部通过后，撰写 3.2.2.0 验收报告，再决定是否进入 3.2.2.1。

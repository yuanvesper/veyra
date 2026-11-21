# Veyra Phase 3.2.1 最终验收报告

- 验收日期：2026-11-21
- 验收对象：Phase 3.2.1 状态存储、Spec/Plan 生命周期、Decision Replay、Project Digest、路径安全与四个 CLI 子命令
- 验收角色：独立最终验收（只读核查 + 独立运行测试；除本报告外不修改仓库）
- 结论：**PASS（限定 Phase 3.2.1 范围）**

> 本报告只把实际存在、实际执行或实际检查到的能力记为通过。未实现能力见 §8；未解决边界见 §7；本报告不等于对 Phase 3.2.2 的验收。

## 1. 最终结论

**PASS。**

Phase 3.2.1 的既定范围已经实现，核心状态与安全契约在本次独立检查中成立：

- `state.json`、`spec.json`、`plan.json`、`decisions.ndjson`、`locks.json`、`placeholders.json`、`runs/` 的读写、校验和关闭重开恢复均通过。
- Spec revision、确认、Plan 提议/批准/`superseded` 生命周期、Decision Replay 与 `verify` 的一致性检查均通过。
- Digest 可从权威状态与真实文件确定性重建；`creation/`、VCS、缓存、`real_crash.log`、symlink/junction 的排除逻辑有测试或代码证据。
- R1–R4.1 的路径与完整性修复完整保留；原始字符串空路径在构造器与 CLI 入口均 fail closed。
- 当前测试：creation **124 tests OK（skipped=1）**，全量 **305 tests OK（skipped=2）**；跳过项是 Windows symlink 权限，未计为通过。
- 未发现 P0/P1/P2 级阻断缺陷，未发现测试失败或可复现的状态一致性错误。

本次验收另记录 3 项 **P3 级、非阻断**的 API/幂等观察（§7.1）。它们不影响 3.2.1 的 CLI、持久化契约或本轮 PASS 结论，但建议纳入 3.2.2 开工前的 API 加固清单。

**是否建议进入 Phase 3.2.2：** 建议在项目发起人确认本报告后进入 3.2.2 的规划与实施；本轮不启动 3.2.2，不做任何 Git 写操作。

## 2. 验收范围

本次验收覆盖：

1. **Project State**：目录结构、`state.json` 读写与版本、Spec revision、Plan 生命周期、decisions 事件、locks、runs、placeholders。
2. **Spec 与 Plan**：初始化、读取、确认、revision 变化；未确认 Spec 不得批准 Plan；Plan 提议、独立校验、批准与 `superseded`；Plan 与当前 Spec revision 一致性。
3. **Decision Replay 与完整性**：事件链、`event_format`、`prev_event_id`、state 与 replay 比较、Spec/Plan 哈希与审批链、空文件/空白行/缺末尾换行。
4. **Project Digest**：确定性、排除范围、过期/损坏/重建、写入路径安全。
5. **路径安全**：R1–R4.1 的 junction/symlink、词法规范化、resolve 前后检查、Windows 路径策略、ADS/保留名/尾随点空格、空字符串边界。
6. **CLI**：`init`、`status`、`digest`、`verify` 的退出码、JSON 模式、错误码和路径入口。
7. **历史整合**：R1、R2、R3、R4、R4.1 的问题、修复、测试数字与独立审核结论。

## 3. 架构与文件清单

### 3.1 核心模块

| 文件 | 职责 |
| --- | --- |
| [`veyra_creation/canonical.py`](../veyra_creation/canonical.py) | 规范化 JSON、UTC 时间、文本/文件 SHA-256。 |
| [`veyra_creation/constants.py`](../veyra_creation/constants.py) | `creation/` 布局、schema/工具版本、排除目录、文件名常量。 |
| [`veyra_creation/errors.py`](../veyra_creation/errors.py) | `CreationError(code, message, location, details)`。 |
| [`veyra_creation/spec.py`](../veyra_creation/spec.py) | Spec schema 1.0.0 校验、初始 Spec、加载与错误分类。 |
| [`veyra_creation/plan.py`](../veyra_creation/plan.py) | Plan schema 1.0.0、步骤/依赖图/环/深度/路径校验、加载。 |
| [`veyra_creation/plan_check.py`](../veyra_creation/plan_check.py) | 独立语义校验：target 属于 confirmed deliverable、acceptance 引用、预算不超 Spec。 |
| [`veyra_creation/replay.py`](../veyra_creation/replay.py) | 从 `decisions.ndjson` 重建期望 state、Spec/Plan/lock 摘要，供 `verify` 发现删除审批与内容篡改。 |
| [`veyra_creation/store.py`](../veyra_creation/store.py) | `ProjectStore`：init、state、Spec、Plan、decisions、locks、runs、placeholders、status。 |
| [`veyra_creation/digest.py`](../veyra_creation/digest.py) | 从权威状态与真实文件构建确定性 Digest；`verify_project()` 只读核验。 |
| [`veyra_creation/cli.py`](../veyra_creation/cli.py) | `python -m veyra_creation init/status/digest/verify`，JSON 信封与退出码。 |
| [`veyra_creation/__init__.py`](../veyra_creation/__init__.py)、[`__main__.py`](../veyra_creation/__main__.py) | 公共导出与模块入口。 |

### 3.2 测试与文档

| 文件 | 覆盖 |
| --- | --- |
| [`tests/test_creation_store.py`](../tests/test_creation_store.py) | 初始化、Spec revision/确认、Plan、decisions、locks、runs、status。 |
| [`tests/test_creation_digest.py`](../tests/test_creation_digest.py) | Digest 确定性、排除、交付物、写入/重建、关闭重开、verify 的锁/状态/损坏/陈旧检查。 |
| [`tests/test_creation_integrity.py`](../tests/test_creation_integrity.py) | 删除审批、空决策、篡改 Spec/Plan、junction、ADS、大小写、run、placeholders、空白 decisions。 |
| [`tests/test_creation_cli.py`](../tests/test_creation_cli.py) | 四个子命令、JSON、退出码、help/version、重复 init、坏锁。 |
| [`tests/test_creation_path_policy.py`](../tests/test_creation_path_policy.py) | R4 Windows 根路径策略与真实 junction 入口。 |
| [`tests/test_creation_empty_path.py`](../tests/test_creation_empty_path.py) | R4.1 原始空字符串入口与 `.`/`Path(".")`/绝对/相对路径回归。 |
| [`tests/creation_helpers.py`](../tests/creation_helpers.py) | 临时项目、固定时钟、Spec/Plan/CLI 辅助。 |
| [`docs/phase-3.2.1-state-store-report.md`](phase-3.2.1-state-store-report.md) | 3.2.1 实现、契约、测试和未完成项。 |
| [`docs/phase-3.2.1-r1-integrity-report.md`](phase-3.2.1-r1-integrity-report.md) | R1/R2/R3/R4/R4.1 整合记录、威胁模型与边界。 |
| [`docs/phase-3.2.1-r4-path-policy-report.md`](phase-3.2.1-r4-path-policy-report.md) | R4 Windows 路径策略；§13 为 R4.1 空路径补丁。 |

### 3.3 状态目录

```text
<project>/
  creation/
    state.json          # 权威：ID、版本、Spec revision/状态、确认、活动/批准 Plan、最后 run/事件
    spec.json           # 权威：创作规格
    plan.json           # 权威：当前 Plan；新 Spec revision 后旧 Plan 标记 superseded
    decisions.ndjson    # 权威、只追加：事件、actor、action、链式 event_id
    locks.json          # 权威：路径 + SHA-256
    placeholders.json   # 预留登记；导出阻断在 3.2.2
    digest.json         # 派生缓存，可删除
    runs/run_*.json     # 单次执行记录
```

单文件写入使用 `write_json_atomic()`（同目录临时文件 + fsync + `os.replace`）。`state.json` 的 `project_version` 与 `decisions.ndjson` 的事件计数不是同一个计数。

## 4. 核心契约验收表

### 4.1 Project State

| 契约 | 证据 | 结果 |
| --- | --- | --- |
| `init` 创建版本化 state 与全部基础文件，拒绝覆盖已有 state/非空 `creation/` | `test_init_creates_versioned_state_and_files`、`test_init_refuses_an_existing_state`、`test_init_refuses_a_non_empty_creation_directory` | 通过 |
| 关闭并重新构造 `ProjectStore(root)` 后 state/spec/plan/locks/runs 恢复 | `test_state_survives_reopening_the_project` | 通过 |
| 每次状态提交递增 `project_version`；decisions 追加不混淆两个计数 | `test_decisions_are_append_only_and_bump_project_version`、`test_save_spec_advances_revision_and_version` | 通过 |
| `status` 汇总 Spec/Plan/锁/run，不把派生缓存当事实 | `test_status_summarises_the_project`、独立 CLI smoke | 通过 |

### 4.2 Spec 与 Plan

| 契约 | 证据 | 结果 |
| --- | --- | --- |
| 新 Spec 必须使用 `revision+1` 且从 `draft` 开始 | `test_save_spec_requires_the_next_revision`、`test_save_spec_advances_revision_and_version` | 通过 |
| 确认冻结当前 revision；新 draft revision 清除确认 | `test_confirm_spec_freezes_the_current_revision`、`test_a_new_draft_revision_clears_confirmation` | 通过 |
| 旧 revision 不能确认；确认后重复确认不会追加事件 | `test_confirm_rejects_a_stale_revision`、`test_confirm_spec_is_idempotent_for_an_untampered_spec` | 通过 |
| 未确认 Spec 不能保存/批准 Plan | `test_plan_rejects_a_draft_spec`、`test_approve_requires_an_active_plan` | 通过 |
| Plan 必须匹配当前 Spec revision，target 必须属于 confirmed deliverable | `test_plan_must_target_the_current_spec_revision`、`test_plan_rejects_a_target_outside_the_spec` | 通过 |
| 提议 → 批准；批准后不可被新 Plan 直接替换；Spec 修订使旧 Plan `superseded` | `test_plan_approval_is_recorded_and_blocks_replacement`、`test_revising_spec_supersedes_the_approved_plan`、`test_approved_plan_cannot_be_replaced_by_a_raw_plan_proposed` | 通过 |
| 独立 `check_plan` 校验 deliverable/acceptance/budget/generator/依赖 | `test_proposed_plan_is_checked_for_deliverables_and_acceptance`、`test_proposed_plan_budget_over_spec_is_detected`、`test_proposed_plan_with_bad_generator_is_rejected`、`test_proposed_plan_with_unknown_dependency_is_rejected` | 通过 |

### 4.3 Decision Replay 与完整性

| 契约 | 证据 | 结果 |
| --- | --- | --- |
| 第一条事件必须是 `project_created`；Spec revision 必须 +1；确认/批准状态机受限 | `replay.py` 生命周期校验；`test_duplicate_confirm_is_rejected_even_with_valid_chain` | 通过 |
| 删除审批、清空决策、篡改已确认 Spec/已批准 Plan 被 `verify` 发现 | `test_deleted_approval_event_is_detected`、`test_empty_decision_log_is_detected`、`test_edited_confirmed_spec_is_detected`、`test_edited_approved_plan_is_detected` | 通过 |
| `event_id` 唯一、`event_format=2`、`prev_event_id` 连续；legacy 不被静默接受 | `load_decisions` 链校验；`test_legacy_history_is_rejected_and_reportable` | 通过 |
| 非空 decisions 缺末尾换行、空白行、仅空白文件被拒绝，且 append 不写入 | `test_missing_trailing_newline_is_rejected_without_appending`、`test_blank_only_log_is_rejected_without_appending`、`test_trailing_blank_line_is_rejected_without_appending` | 通过 |
| 真正零字节文件允许首次追加；连续追加产生独立 JSON 行 | `test_empty_log_allows_a_first_append`、`test_consecutive_appends_are_independent_json_lines` | 通过 |
| `verify` 比较 replay 期望 state、Spec/Plan 内容 SHA-256、锁集合、`last_run_id`、digest 缓存 | `test_verify_detects_state_desync`、`test_verify_detects_a_broken_lock`、`test_verify_detects_a_stale_digest` 等 | 通过 |

**边界：** 这是本地一致性校验，不是密码学防篡改；拥有目录写权限者可同步重写事件链与哈希。详见 §7。

### 4.4 Digest

| 契约 | 证据 | 结果 |
| --- | --- | --- |
| 同内容重建 `digest_id` 稳定，生成时间不影响 ID | `test_digest_is_deterministic`、`test_digest_id_ignores_the_generation_time` | 通过 |
| 排除 `creation/`、`.git`、`real_crash.log` 等受保护/派生内容 | `test_digest_excludes_state_and_protected_files` | 通过 |
| 必需 deliverable 缺失只警告；可选缺失不警告；文件变化改变 ID | `test_missing_required_deliverable_warns`、`test_optional_missing_deliverable_does_not_warn`、`test_digest_changes_when_a_file_changes` | 通过 |
| 写入可 round-trip，无残留 `.tmp`；删除缓存后可重建相同 ID | `test_written_digest_round_trips_and_has_no_stale_temp_files`、`test_digest_is_rebuilt_after_deleting_the_cache` | 通过 |
| Digest 记录 history/runs；不跟随 junction | `test_digest_records_history_and_runs`、`test_digest_does_not_follow_a_project_junction` | 通过 |
| symlink 文件/目录被跳过 | 代码 `_iter_project_files`/`resolve_file` 检查；`test_digest_skips_symlinks` 因 Windows 权限跳过 | 代码审查通过，**动态未验证** |

### 4.5 路径安全

| 契约 | 证据 | 结果 |
| --- | --- | --- |
| 悬空 root junction、祖先 junction、构造后 root 被替换均拒绝 | `test_init_refuses_a_dangling_root_junction`、`test_root_below_an_ancestor_junction_is_rejected`、`test_init_rechecks_root_before_writing` | 通过 |
| `missing/..` 词法路径不能隐藏 junction；CLI 同样拒绝 | `test_lexical_parent_component_cannot_hide_a_junction`、`test_multiple_missing_components_do_not_hide_a_junction`、`test_cli_init_rejects_a_lexical_parent_bypass` | 通过 |
| `spec.json`/`plan.json` 被 junction 替换时拒绝 | `test_spec_file_replaced_by_a_junction_is_rejected`、`test_plan_file_replaced_by_a_junction_is_rejected` | 通过 |
| Windows 尾随点/空格、设备/扩展命名空间、UNC、盘符相对、root-relative 路径拒绝；正常盘符接受 | `tests/test_creation_path_policy.py` 18 tests（真实 junction 入口） | 通过 |
| ADS、保留名、大小写内部路径、`creation/` 注入被拒绝 | `test_ads_paths_are_rejected`、`test_case_variant_creation_path_is_rejected`、`test_lock_identity_is_case_insensitive` | 通过 |
| 原始字符串 `""` 在 `ProjectStore` 与 CLI 入口 fail closed；`.`/`Path(".")` 正常 | [`store.py:198-209`](../veyra_creation/store.py:198)、[`cli.py:61-71`](../veyra_creation/cli.py:61)、`test_creation_empty_path` 9 tests | 通过（R4.1 独立审核 PASS） |

### 4.6 CLI

| 契约 | 证据 | 结果 |
| --- | --- | --- |
| `init` 正常退出 0，JSON 包含 project/spec 信息；重复 init 退出 2 | `test_init_json_reports_the_new_project`、`test_init_twice_fails_with_exit_code_two` | 通过 |
| `status` JSON；缺失项目退出 2 并带 `E_STATE_MISSING` | `test_status_json`、`test_status_on_a_missing_project_fails` | 通过 |
| `digest` 输出 JSON；`--write` 写 `creation/digest.json`，ID 一致 | `test_digest_prints_json_and_optionally_writes_it` | 通过 |
| `verify` 干净 0；错误 1；参数/状态读取失败 2 | `test_verify_returns_zero_for_a_clean_project`、`test_verify_returns_one_for_a_broken_lock` | 通过 |
| `--help`/`--version` 可用 | `test_version_and_help_are_available` | 通过 |
| 空路径四个子命令均 exit 2、stderr 含 `E_PROJECT_PATH_INVALID`、不创建 `creation/` | R4.1 独立烟测 + 本次独立 CLI smoke | 通过 |

## 5. R1–R4.1 历史修复记录

| 轮次 | 主要问题 | 修复/结果 | 当轮测试 |
| --- | --- | --- | --- |
| R1 | P0-1 删除/篡改决策不被发现；P0-2 junction 逃逸；P1-1 审批状态机；P1-2 ADS/大小写/内部路径；P1-3 run；P1-4 placeholders；P1-5 无独立 Plan 校验器；P2 若干 | replay + `verify`、link 检查、独立 `plan_check.py`、严格 run/placeholders、大小写与 ADS 处理 | 当前报告未保留 R1 独立计数；其回归包含在 R3 的 97/278 中 |
| R2 | 悬空 root junction；重复 `confirm_spec`；approved Plan 被新 `plan_proposed` 替换；decisions 缺末尾换行；proposed Plan 漏检 | 构造前/后 link 检查、replay 生命周期收紧、幂等确认校验 SHA、append 拒绝坏文件、`check_plan` 加深 | 当前报告未保留 R2 独立计数；回归包含在 R3 的 97/278 中 |
| R3 | `missing/..` 词法路径隐藏 junction；空白 decision 行被接受 | 词法规范化后再查 link；只允许真正零字节空日志 | **97 tests OK（skipped=1）；全量 278（skipped=2）**；因 Windows 尾随点/设备命名空间仍开放而 CONDITIONAL PASS |
| R4 | `...`/`.. ` 尾随点空格、`\\?\GLOBALROOT` 等设备命名空间、声明/实际路径偏差、`init` 直接写 decisions 前检查 | Windows 根路径策略、构造器 normalize→link→resolve→等价性→link、18 个新测试 | **115 tests OK（skipped=1）；全量 296（skipped=2）**；独立审核发现 `ProjectStore("")`/CLI 空参 P2，故当时 CONDITIONAL PASS |
| R4.1 | 空字符串被 `Path("")` 变成当前目录 | 构造器与 `_store` 在 `Path()` 前拒绝原始空串；9 个新测试 | **124 tests OK（skipped=1）；全量 305（skipped=2）**；独立只读审核 **PASS（限定 R4.1）** |

历史数字只按上一轮报告保留，不把 97/278、115/296、124/305 混为同一次结果。R4 报告 §13 是 R4.1 的详细补丁记录。

## 6. 当前测试结果

测试解释器：

```text
C:\Users\lenovo\AppData\Local\Programs\Python\Python315\python.exe
Python 3.15.0
```

本次独立执行：

```powershell
& $py -m unittest discover -s tests -p "test_creation_*.py"
& $py -m unittest discover -s tests
```

| 范围 | 结果 | 退出码 |
| --- | --- | --- |
| creation（`test_creation_*.py`） | **Ran 124 tests，OK（skipped=1），4.633s** | 0 |
| 全量 `tests/` | **Ran 305 tests，OK（skipped=2），6.859s** | 0 |
| `tests/test_creation_path_policy.py` | **Ran 18 tests，OK，0.274s** | 0 |
| `tests/test_creation_empty_path.py` | **Ran 9 tests，OK，0.482s** | 0 |

跳过项：

1. `test_creation_digest.VerifyTests.test_digest_skips_symlinks` — `WinError 1314`，当前 Windows 账户无创建 symlink 的权限。
2. `test_pack_validation.PathSafetyTests.test_symlink_is_rejected` — 同一权限原因。

**跳过不等于通过。** Junction 测试在本机实际执行并通过，包括悬空 root、祖先 junction、`missing/..` 和 R4 尾随点/空格入口；不能把 junction 结论外推为 symlink 已动态验证。

环境说明：直接 `python` 在部分 shell 会解析到 msys2 Python 3.14.7，全量测试会因子进程中文输出的 locale 解码产生 7 failures/3 errors；这些不是 3.2.1 代码回归，本次最终结果以 Python 3.15 为准。该解释器差异本身是验证环境限制，不是产品缺陷。

## 7. 未解决风险与影响

以下风险均已在 R1/R4/R4.1 报告中披露；本节再次确认它们**仍然存在**，不声称已解决。

| 风险 | 影响 | 是否阻断 3.2.1 | 后续 |
| --- | --- | --- | --- |
| `find_link_component` 检查与 `mkdir`/`write_text` 之间存在 TOCTOU 窗口 | 并发攻击者可在检查后替换路径组件；单进程正常使用不受影响 | 否 | 3.2.2 之后引入锁/句柄级校验；保留测试边界 |
| 缺少跨进程锁 | 两个进程同时写同一项目时，单文件原子但多文件可能部分更新 | 否 | 单写者设计；后续加锁 |
| 缺少跨文件事务 | `save_spec`/`save_plan`/`approve_plan` 等跨 state/decisions/plan/locks 的更新可能在崩溃时留下中间态 | 否 | `verify` 可发现；后续事务/恢复日志 |
| Windows symlink 分支未动态验证 | 仅代码审查与跳过测试；junction 已实测 | 否 | 在支持 symlink 的 Windows/CI 账户执行 |
| `Path("")` 与 `Path(".")` 不可区分 | 只拦截原始字符串入口；调用方先转成 `Path("")` 时无法恢复空串语义 | 否 | 已在构造器注释与 R4 §13 记录；API 调用方应传原始字符串 |
| UNC、`\\?\` 扩展长度、设备命名空间不支持 | 显式 fail closed，不会静默降级；需要这些路径的调用方被拒绝 | 否 | 当前 3.2.1 本地项目不支持；后续单独设计 |
| 8.3 短名 fail closed | 依赖短名的旧路径不会被接受 | 否 | 作为 Windows 路径策略的明确限制记录 |

补充：`verify` 对缺失的必需 deliverable 只给 `W_DELIVERABLE_MISSING` 警告，不作为错误退出；正式 `check/export` 阻断属于 3.2.2。

### 7.1 非阻断观察（P3）

以下为本次独立探针复现的 API 健壮性观察。均不改变 CLI 正常路径，不构成 3.2.1 验收阻断；本轮不修改代码。

1. **非字符串标识符会泄漏 `TypeError`。**
   - 位置：[`store.py:305-316`](../veyra_creation/store.py:305) 的 `project_id`/`spec_id` 正则匹配，[`store.py:1070-1073`](../veyra_creation/store.py:1070) 的 `run_id` 匹配。
   - 复现：`ProjectStore(temp).init(name="x", pack_type="mod", project_id=123)` 或 `start_run("t", run_id=123)`。
   - 实际：`TypeError: expected string or bytes-like object, got 'int'`；不是稳定的 `CreationError`。
   - 影响：仅影响直接调用 Python API 的调用方；CLI 的 argparse 参数为字符串，不触发。
   - 建议：下一轮在正则匹配前做 `isinstance(..., str)`，统一返回 `E_PROJECT_ID`/`E_SPEC_ID`/`E_RUN_ID`。

2. **`approve_plan` 的幂等分支不重新校验 Plan 内容哈希。**
   - 位置：[`store.py:711-712`](../veyra_creation/store.py:711)。
   - 复现：批准 Plan 后修改 `plan.json` 的 step 内容，再调用 `approve_plan(same_id)`。
   - 实际：仍返回 `state="approved"`；随后 `verify` 能报 `E_PLAN_TAMPERED`。
   - 影响：幂等调用方若不执行 `verify`，可能把已篡改 Plan 当作批准结果；3.2.1 没有 `approve` CLI，正常 CLI 路径不受影响。
   - 建议：与 `confirm_spec` 的幂等分支对齐，在返回前核对 approve 事件的 SHA-256；或将幂等返回限定为“调用方已运行 verify”。

3. **`build_digest(write=True)` 未在写 `digest.json` 前单独调用 `_assert_no_link(digest_path)`。**
   - 位置：[`digest.py`](../veyra_creation/digest.py) 的写缓存路径；前置 `load_state/spec/plan/locks/decisions` 已检查 `creation/`，`write_json_atomic` 的 `os.replace` 替换链接本身而非跟随目标。
   - 影响：当前未发现项目外写入；属于纵深防御缺口，已有 TOCTOU/路径边界披露覆盖。
   - 建议：3.2.2 若强化 digest 写入，补显式路径检查或复用 `store._write_json` 的同等检查。

## 8. 未实现功能清单

以下功能**不属于** Phase 3.2.1 已完成范围；不得在 3.2.1 验收中记为已实现：

- Operations 执行器、审批执行绑定、原子应用与恢复记录（3.2.2）。
- `check/export` CLI、占位素材导出阻断、ZIP 重新校验（3.2.2）。
- 文本/图像 Provider、模型调用、成本/预算硬停止（3.2.3/3.2.4）。
- Style Profile、Game Adapter 的实际加载与游戏规则（3.2.3+）。
- Digest 的按需截断、上下文切片、`budget` 段；当前只记录 run 的 `cost_usd`，不计算已用预算。
- 3.2.1 没有 `confirm/plan/approve/lock/run` CLI；这些是 `ProjectStore` API，由测试直接覆盖。
- 真实游戏素材、真实 `pack.json`、游戏导入管线验证；本轮仍以合成临时项目为主。

## 9. `report.md` 核验

- 路径：`D:\Projects_vesper\veyra\report.md`
- 大小：1570 字节
- SHA-256：`A11D09D49217CA3C6CD7C269268C148558EA488067E8D1399BCAFD7ABBC2D68D`
- 验收开始与结束时均与预期一致。
- **未读取内容、未修改、未覆盖、未恢复、未删除。**

## 10. `real_crash.log` 保护情况

- 路径：`D:\Projects_vesper\veyra\real_crash.log`
- 大小：21999 字节
- Git 忽略规则：`.gitignore:29:real_crash.log`
- **未读取内容、未修改、未删除、未恢复、未重新跟踪、未纳入测试。**
- 该文件不进入 Digest 的 `files[]`；其排除由 `real_crash.log` 文件名规则覆盖。

## 11. Git 状态和 HEAD

- 分支：`main`
- HEAD：`0fa1bbfff9c28e1b12c94b334f23b732d059926e`
- 验收期间未执行 `git add/commit/push/pull/fetch/reset/checkout/clean/rebase/stash`，未切换分支，未修改历史。
- 验收前工作区包含既有修改与未跟踪文件；本报告是本次唯一新增文件。
- 既有修改包括 `README.md`、`README_zh.md`、`report.md`；未跟踪包括 `veyra_creation/`、`veyra_pack/`、`tests/test_creation_*.py`、`tests/creation_helpers.py`、既有 Phase 3 文档与本报告。
- 验收结束的 `git status --porcelain` 仍为：`M README.md`、`M README_zh.md`、`M report.md`；`??` 既有 `docs/`、`tests/`、`veyra_creation/`、`veyra_pack/` 以及本报告；无其他改动。
- 本报告未提交、未推送。

## 12. 是否建议进入 Phase 3.2.2

**建议：在项目发起人确认本报告后，进入 Phase 3.2.2 的规划。**

理由：

- 3.2.1 的既定状态/安全契约已通过当前代码、测试和独立 CLI 烟测。
- 未发现 P0/P1/P2 阻断缺陷；剩余风险已明确披露。
- §7.1 的 3 项 P3 观察应作为 3.2.2 开工前的 API 加固项，不需要回退 3.2.1。

**停止条件：** 本报告提交后不执行 Git 写操作，不自动提交/推送，不启动 3.2.2，不继续修复额外问题；等待项目发起人下一步决定。

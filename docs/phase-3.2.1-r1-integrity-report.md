# Phase 3.2.1-R1/R2/R3/R4 完整性与可信度修复报告

- 日期：2026-11-21
- 范围：R1 修复 3.2.1 审核发现的 P0/P1/P2 问题；R2 关闭第二轮只读审核确认的 3 个 P1 残余、decisions 末尾换行缺陷与 proposed Plan 漏检；R3 修复 R2 独立验收发现的 root 路径词法规范化绕过与 decisions 空白行接受问题；R4 关闭 R3 验收发现的 Windows 尾随点/空格、设备命名空间与声明/实际路径偏差；R4.1 修复 R4 独立审核发现的构造器/CLI 空字符串 fail-open。只涉及 `veyra_creation/`、`veyra_pack/paths.py`、creation 测试与文档。
- 结论：R1 审核提出的 P0-1、P0-2、P1-1 至 P1-5 均已修复并有负向回归测试；R2 修复了 root 悬空 junction、重复 `confirm_spec`、approved Plan 替换、decisions 追加换行和 proposed Plan 校验缺口；R3 增加了词法规范化后再检查 link 的防护，并拒绝任何空白 decision 行；R4 增加 Windows 路径策略、构造器声明/实际一致性检查与直接 decisions 写入前检查；R4.1 在原始字符串入口拒绝空项目路径，并补充构造器/CLI 回归测试。剩余边界与威胁模型限制在 §7、§10 明确列出。R4 详细报告见 [phase-3.2.1-r4-path-policy-report.md](phase-3.2.1-r4-path-policy-report.md)，空路径补丁见其 §13。
- 未进入 3.2.2，未调用模型，未联网，未执行 Git 写操作，未触碰 `report.md`。
- 设计依据：[phase-3.2-ai-creation-workflow-design.md](phase-3.2-ai-creation-workflow-design.md) §4、§11、§19.3.2.1。

## 1. 修复总览

| 审核问题 | 修复 | 回归测试 |
| --- | --- | --- |
| P0-1 删除/篡改决策不被发现 | `replay.py` 从 `decisions.ndjson` 重建期望状态；`verify` 比较 state、Spec 哈希、Plan 内容哈希与锁集合 | `test_deleted_approval_event_is_detected`、`test_empty_decision_log_is_detected`、`test_edited_confirmed_spec_is_detected`、`test_edited_approved_plan_is_detected` |
| P0-2 junction 逃逸 | `ProjectStore` 初始化/读写前检查 link 组件；Digest 跳过 symlink/junction；`load_decisions`、`load_spec`、`load_plan` 也检查 | `test_init_refuses_a_creation_junction`、`test_digest_does_not_follow_a_project_junction`、`test_spec_file_replaced_by_a_junction_is_rejected`、`test_plan_file_replaced_by_a_junction_is_rejected` |
| P1-1 审批状态机可绕过 | `save_plan`/`approve_plan` 要求 Spec 已确认、active Plan 一致、重新执行 `check_plan`；旧测试改为先 confirm | `test_plan_rejects_a_draft_spec`、`test_plan_rejects_a_target_outside_the_spec`、`test_approve_requires_an_active_plan` |
| P1-2 ADS/大小写/内部路径 | `check_relative_path` 拒绝 `:`（先识别盘符）；`is_creation_path` 大小写无关；locks/digest 用 `path_key` | `test_ads_paths_are_rejected`、`test_case_variant_creation_path_is_rejected`、`test_lock_identity_is_case_insensitive`、`test_deliverable_matching_is_case_insensitive_and_hashes_the_real_file` |
| P1-3 run 字段/删除 run | `_validate_run` 校验状态与时间、usage/cost/outputs/metadata；`verify` 核对 `last_run_id`；`list_runs` 限制数量并拒绝 link | `test_run_with_inconsistent_fields_is_detected`、`test_deleted_run_referenced_by_state_is_detected` |
| P1-4 placeholders 静默吞错 | `load_placeholders` 严格校验 schema/路径/id/时间/重复；`verify` 不再静默 | `test_corrupt_placeholders_are_detected` |
| P1-5 无独立 Plan 校验器 | 新增 `veyra_creation/plan_check.py`，校验 target 属于 deliverable、预算不超 Spec、generator/acceptance、依赖与深度 | 既有 Plan/CLI 测试 + 新增负向测试 |
| P2 大小写内部目录、drive/ADS 顺序、根 `creation` 排除、locks 排序、legacy history | 已修复；`digest` 仅排除根 `creation/`，嵌套同名目录保留 | `test_legacy_history_is_rejected_and_reportable`；pack 既有 drive 测试 |
| R2 P1 悬空 root junction | `ProjectStore` 在 `resolve()` 前用 `find_link_component` 检查声明路径的每一级组件；悬空 junction 不再被 `Path.exists() == False` 绕过；构造后 root 被替换时 `init` 的 `_assert_no_link` 再次拒绝 | `test_init_refuses_a_dangling_root_junction`、`test_init_rechecks_root_before_writing`、`test_root_below_an_ancestor_junction_is_rejected`、`test_normal_directory_still_initializes` |
| R2 P1 重复确认 Spec | replay 只接受 `spec_state == "draft"` 且 `confirmed_spec_revision is None` 的 `confirm_spec`；API 幂等返回前核对确认事件的 SHA-256，篡改时抛 `E_SPEC_TAMPERED` 且不追加事件 | `test_duplicate_confirm_is_rejected_even_with_valid_chain`、`test_confirm_spec_is_idempotent_for_an_untampered_spec` |
| R2 P1 approved Plan 被替换 | replay 拒绝已有 approved Plan 后的 `plan_proposed`；`verify` 对所有非 superseded Plan 调用 `check_plan` | `test_approved_plan_cannot_be_replaced_by_a_raw_plan_proposed`、`test_proposed_plan_is_checked_for_deliverables_and_acceptance`、`test_proposed_plan_budget_over_spec_is_detected`、`test_proposed_plan_with_bad_generator_is_rejected`、`test_proposed_plan_with_unknown_dependency_is_rejected`、`test_legal_spec_revision_allows_a_new_plan` |
| R2 decisions 末尾换行 | 非空 `decisions.ndjson` 缺末尾换行直接报 `E_DECISIONS_CORRUPT`，解析与 append 都拒绝，不掩盖损坏 | `test_missing_trailing_newline_is_rejected_without_appending`、`test_empty_log_allows_a_first_append`、`test_consecutive_appends_are_independent_json_lines` |
| R3 P1 路径词法规范化绕过 | `ProjectStore` 先调用 `normalize_lexical_path` 做不访问文件系统的 `os.path.abspath` 规范化，折叠 `.`/`..`，再逐级 `find_link_component`；`missing/..` 不能再让 junction 藏在后续组件后面。`find_link_component` 遇到缺失组件继续检查后续组件；`_assert_no_link` 拒绝路径上任意位置的 link，不再只拒绝 root 以下；`resolve()` 失败转 `E_PROJECT_ROOT` | `test_lexical_parent_component_cannot_hide_a_junction`、`test_multiple_missing_components_do_not_hide_a_junction`、`test_cli_init_rejects_a_lexical_parent_bypass`、`test_ancestor_junction_introduced_after_construction_is_rejected`、既有悬空/祖先/构造后 root 测试 |
| R3 P2 decisions 空白行 | 只允许真正的零字节空日志；非空文件中的任何空白行（包括合法事件后的尾部空行）均报 `E_DECISIONS_CORRUPT`；append 先执行同一解析，损坏时不写入文件 | `test_blank_only_log_is_rejected_without_appending`、`test_trailing_blank_line_is_rejected_without_appending`、`test_empty_log_allows_a_first_append`、`test_consecutive_appends_are_independent_json_lines` |
| R4 Windows 路径边界 | `normalize_lexical_path` 在 Windows 上先按组件拒绝尾随点/空格、设备/扩展命名空间、UNC、盘符相对与 root-relative 路径，再 `ntpath.abspath`；构造器比较声明路径与 `resolve()` 结果，并在 resolve 前后各做一次 link 检查 | `test_creation_path_policy` 的 8 个策略测试与 10 个真实 junction/入口测试 |

## 2. 权威历史与 `verify`

`decisions.ndjson` 现在是审批与内容哈希的权威记录：

- 每条事件必须有 `event_id`、`ts`、`actor` 和非空 `action`；当前实现不对 `action` 做白名单枚举，未知 action 被当作非生命周期记录（如 `note`），只更新 `last_event_id`。新格式还要求 `event_format=2` 与 `prev_event_id`，链断裂报 `E_DECISIONS_CORRUPT`。
- `project_created` 必须是第一条且只能一次，记录初始 Spec revision 与 SHA-256。
- `spec_revised` 的 revision 必须恰好 +1；`confirm_spec` 只允许 `draft` 且未有确认记录的 Spec，并必须指向当前 revision；`plan_proposed` 前 Spec 必须已确认，且 `spec_revision` 等于当前值；已有 approved Plan 时拒绝新的 `plan_proposed`，除非先经过合法的 Spec revision 变更使旧 Plan superseded；`approve_plan` 必须匹配 Plan ID 与内容 SHA-256。
- legacy（缺 `event_format`）事件不会被静默接受：`verify` 报 `E_DECISIONS_LEGACY`，追加新事件也会被拒绝。
- `verify` 用 replay 的期望值逐字段比较 `state.json`，再比较 `spec.json` 的实际 SHA-256、`plan.json` 去掉 `state` 后的内容 SHA-256、锁路径/哈希和 `last_run_id`。在“文件被改但事件记录未同步”的检测模型下，删除审批、改 Spec/Plan 内容、清空决策日志会报错；同步重写整条事件历史不在当前检测能力内，见 §10。

错误码包括 `E_STATE_DESYNC`、`E_SPEC_TAMPERED`、`E_PLAN_TAMPERED`、`E_PLAN_UNEXPECTED`、`E_PLAN_MISSING`、`E_LOCKS_DESYNC`、`E_RUN_MISSING`、`E_DIGEST_STALE`、`E_DECISIONS_LEGACY`。

## 3. 路径与 link 边界

- R2 在 `resolve()` 之前检查声明路径的每个组件，但当时的 `find_link_component` 遇到第一个不存在的组件就停止；`missing/..\link\project` 因此跳过 `link` 检查，随后 `resolve()` 跟随 junction 落到项目外。R2 的测试覆盖了悬空 root、祖先 junction 和构造后替换 root，**没有覆盖 `missing/..` 这类词法规范化路径**。
- R3 在 `ProjectStore.__init__` 中先调用 `normalize_lexical_path`：使用 `os.path.abspath` 只做路径字符串规范化，折叠 `.`/`..`，不访问文件系统、不解析 link；然后才逐级调用 `find_link_component`，最后 `resolve()`。因此 `missing/..\link\project` 会先折叠为 `link\project`，现存 junction 被检查并报 `E_LINK_BOUNDARY`；`resolve()` 的 `OSError`/`RuntimeError`/`ValueError` 转为 `E_PROJECT_ROOT`。
- `find_link_component` 遇到缺失组件后继续检查后续组件，不再因 `FileNotFoundError` 提前 `break`；只有实际存在的 link 才会被返回。缺失组件后若没有 `..`，其后的真实子项无法被访问，后续写入仍由 `init`、JSON 读写和 run/decisions 的 link 检查约束。
- R3 实际覆盖：Windows junction 的 `missing/..`、多个缺失组件加 `..`、通过正式 `python -m veyra_creation init` 的同类路径、悬空 root、祖先 junction、构造后替换 root、构造后新引入祖先 junction，以及普通目录正常初始化。Windows symlink 分支仍因当前账户无权限而未实测，不能把 junction 的结论外推为 symlink 已动态验证。
- R4 在 `normalize_lexical_path` 的 `ntpath.abspath` 之前按组件拒绝 Windows 尾随点/空格（含 `...`、`.. `、`. `、`... `、`link.`、`link `）、设备/扩展命名空间、UNC、盘符相对与 root-relative 路径，错误码分别为 `E_PROJECT_PATH_TRAILING`、`E_PROJECT_PATH_DEVICE`、`E_PROJECT_PATH_UNC`、`E_PROJECT_PATH_DRIVE_RELATIVE`、`E_PROJECT_PATH_INVALID`。`ProjectStore.__init__` 在 resolve 后用 `paths_equivalent` 比较声明路径与实际路径，并对解析结果再做一次 link 检查；`init` 在创建 `runs/` 和直接写 `decisions.ndjson` 前补 `_assert_no_link`。R4 的真实 junction 测试断言构造器本身拒绝、项目外无 `creation/state.json`；`init/load_state/load_spec/load_decisions/append_decision/lock/build_digest` 在祖先替换后均抛 `E_LINK_BOUNDARY`，`verify` 返回含该错误的 issue。详见 [phase-3.2.1-r4-path-policy-report.md](phase-3.2.1-r4-path-policy-report.md)。
- 检查与写入之间替换路径组件的 TOCTOU 竞争不在本轮测试覆盖内；这是 §7、§10 明确保留的边界，不是“绝对安全”保证。
- `resolve_file` 拒绝 symlink 与 Windows junction（`os.path.isjunction`，带 `getattr` 兼容）；`is_link_like` 同时覆盖两者。
- Digest 遍历目录时跳过 link 目录，遍历文件时跳过 link 文件；只排除项目根下的 `creation/`，不再排除任意层级同名目录。
- 所有用户路径先过 `check_relative_path`：空/NUL/绝对路径/盘符/UNC/反斜杠/`..`/点尾/保留设备名/ADS（`:`）/超长/超深。
- `path_key` 使用 NFC + `casefold`，使 Windows 上 `Assets/Avatar.PNG` 与 `assets/avatar.png` 指向同一身份；Digest 的 present 状态与哈希也按该 key 绑定真实文件。
- `report.md`、`real_crash.log` 的保护逻辑未改变；`real_crash.log` 继续被 Digest 排除。

## 4. 审批与 Plan

- 新 Plan 只能在 Spec `specified` 且 `confirmed_spec_revision == spec.revision` 时保存；`plan.spec_revision` 必须等于确认 revision。
- `save_plan` 和 `approve_plan` 都调用独立 `check_plan`；`approve_plan` 还要求 `state.active_plan_id` 与 Plan 一致、没有其他 approved Plan，并重新校验 Spec 确认与 Plan 内容。
- 所有 step 的 `target` 必须是当前 Spec 的 deliverable 或确定性派生路径（`check`/`export` 除外）；`generate_image` 必须有 `generator`，step 预算不得超过 Spec 预算，`depends_on` 必须存在、无自依赖、无环，依赖深度有上限。
- Plan 内容哈希排除 `state` 字段，因此批准动作本身不会让内容哈希失配；批准后再改 step target、generator、预算或依赖时，只要事件记录未同步重写，`verify` 就会发现；同步重写事件哈希与链的限制见 §10。

## 5. runs 与 placeholders

- `_validate_run` 校验 schema、`run_id` 与文件名、task、时间、状态、`finished_at` 与状态一致性、usage/cost 类型、outputs 列表、metadata 对象；`list_runs` 对坏记录报 `E_RUN_CORRUPT`，数量上限 1000。
- `verify` 要求 `state.last_run_id` 指向存在的 run；删除 run 文件会报 `E_RUN_MISSING`。
- `load_placeholders` 对路径、ID、reason、created_at、重复项做严格校验；损坏文件报 `E_PLACEHOLDERS_CORRUPT`，不再返回空列表。非空 placeholders 在 verify 中给 `W_PLACEHOLDER_ASSET`，正式导出阻断仍属于 3.2.2。

## 6. 测试结果

Python：`C:\Users\lenovo\AppData\Local\Programs\Python\Python315\python.exe`（3.15.0）。直接 `python` 可能解析到 msys2 Python 3.14，控制台编码不同，不作为本阶段回归依据。

```powershell
& $py -m unittest discover -s tests -p "test_creation_*.py"
& $py -m unittest discover -s tests
```

| 范围 | 结果 |
| --- | --- |
| `test_creation_*` | **124 tests，OK（skipped=1）**，4.563s（R4.1 后） |
| 全量 `tests/` | **305 tests，OK（skipped=2）**，6.703s（R4.1 后） |
| 既有非 creation 测试 | 全部保留并通过 |

R4.1 新增 `tests/test_creation_empty_path.py` 的 9 个测试；上表数字已包含。R4 审核当时的 115/296 保留在 R4 报告中作为历史记录。

跳过的两项仍是当前 Windows 环境无法创建 symlink：`test_creation_digest.VerifyTests.test_digest_skips_symlinks` 与 `test_pack_validation.PathSafetyTests.test_symlink_is_rejected`。Junction 测试在本机实际执行，不是跳过，包括悬空 root junction、祖先 junction、R3 的 `missing/..` 路径和正式 CLI `init`。

`tests/test_creation_integrity.py` 覆盖：删除审批、空决策、篡改确认 Spec、篡改批准 Plan、active Plan 缺失、legacy history、ADS、大小写内部路径、大小写锁身份、大小写 deliverable、creation junction、项目内 junction、`spec.json`/`plan.json` 被 junction 替换、无效 run、删除 run、坏 placeholders；R2 新增悬空 root junction、构造后 root 替换为 junction、祖先 junction、普通目录初始化、重复 confirm（含未篡改幂等）、approved 后 plan_proposed、proposed Plan 的 deliverable/acceptance/budget/generator/dependency、合法 Spec revision 后新 Plan，以及 decisions 末尾换行、空日志首次追加和连续追加；R3 新增 `missing/..` 与多个缺失组件加 `..` 的构造器测试、正式 CLI `init` 拒绝测试、构造后新引入祖先 junction 的 `init` 拒绝测试、仅空白 decisions 与事件后尾部空行的 load/append 拒绝测试。

R4 新增 `tests/test_creation_path_policy.py`（18 tests）：8 个 Windows 路径策略测试覆盖正常盘符、UNC/扩展 UNC、`\\?\GLOBALROOT`、`\\.\C:`、`\\?\C:`、`\??\C:`、`C:foo`、`C:`、`\project`、`...`/`.. `/`. `/`... `/`link.`/`link `、ADS、保留名、非法字符与空路径；10 个真实 junction/入口测试断言构造器自身拒绝 `...`、`.. `、`missing\.. `、`missing\...`、`link.`、`link `，R3 `missing\..\link` 仍报 `E_LINK_BOUNDARY`，构造后祖先替换时指定入口全部 fail closed。R3 的 97 个测试全部保留在 R4 的 115 个 creation 测试中。

R4.1 新增 `tests/test_creation_empty_path.py`（9 tests）：直接覆盖 `ProjectStore("")`、CLI `init ""`、错误码、空路径不创建 `creation/`，以及 `.`/`Path(".")`/CLI `init .`/绝对路径/相对路径的正向回归。R4 的 18 个路径测试与 R3 的回归全部保留，creation 总数变为 124。


## 7. 未完成与剩余边界

- **多进程并发**：没有跨进程文件锁；同一项目被两个进程同时写时，单文件仍原子，但跨 state/decisions/locks/plan 的多文件操作可能留下部分状态。`verify` 能发现不一致，但本阶段不自动修复。
- **崩溃原子性**：`decisions.ndjson` 追加使用 fsync，但没有事务；崩溃后可能停在“文件已写、state 未更新”的中间态，需要人工按 `verify` 提示处理。
- **symlink 与路径覆盖**：Windows 当前账户无 symlink 权限，symlink 分支只有代码审查与既有跳过测试；junction 已实测，包括 `spec.json`/`plan.json` 被 junction 替换。R3 已动态验证 `missing/..`、多个缺失组件加 `..` 的构造器/CLI 路径，以及构造后新引入祖先 junction 时 `init` 拒绝；R4 已动态验证 `...`、`.. `、`missing\.. `、`missing\...`、`link.`、`link `、设备命名空间、UNC 和盘符相对路径在构造器阶段被拒绝。但 `find_link_component` 检查与 `mkdir`/`write_text` 之间替换组件的 TOCTOU 竞争没有并发对抗测试；`init` 直接 `decisions_path.write_text("")` 在检查后写，也不是原子“检查 + 创建”。不能宣称绝对安全。
- **路径策略取舍**：R4 明确拒绝 UNC、所有 `\\?\` 扩展长度/设备命名空间、盘符相对与 root-relative 路径；需要网络共享或超长路径的调用方会被拒绝，不会静默降级。
- **必需 deliverable 缺失**：`verify` 仍返回 `W_DELIVERABLE_MISSING` 警告而非错误；正式 `check/export` 闸门属于 3.2.2。
- **Plan 执行**：`plan_check` 是静态校验器；Operation 执行、审批绑定、原子应用与恢复记录尚未实现，属于 3.2.2。
- **模型与真实素材**：未调用模型，未用真实游戏素材或游戏导入管线验证。
- **init 失败清理**：失败时只清理本次创建的路径；没有跨进程互斥，极端并发下仍可能与其他初始化者竞争。
- **digest 规模**：未做超大项目的性能测试。

## 8. 保护文件与 Git 状态

- `report.md`：未修改、未覆盖、未恢复；SHA-256 仍为 `A11D09D49217CA3C6CD7C269268C148558EA488067E8D1399BCAFD7ABBC2D68D`，1570 字节。
- `real_crash.log`：未读取、未恢复、未改动、未纳入测试。
- 未执行 `git add/commit/push/pull/fetch/reset/checkout/clean/rebase/stash` 或历史改写。
- 新增/修改仅限 `veyra_creation/`、`veyra_pack/paths.py`、`tests/creation_helpers.py`、`tests/test_creation_*.py` 与本报告及相关设计/阶段报告文字。
- 所有测试使用 `tempfile.TemporaryDirectory()`。

## 9. 验收对照

| 闸门 | 结果 |
| --- | --- |
| 删除 `approve_plan` 事件后 `verify` 报错 | 通过（`E_STATE_DESYNC` + `E_PLAN_TAMPERED`） |
| 确认后篡改 Spec 内容被 `verify` 发现 | 通过（`E_SPEC_TAMPERED`） |
| 批准后篡改 Plan 内容被 `verify` 发现 | 通过（`E_PLAN_TAMPERED`） |
| `creation/` 预置 junction 时 `init` 不向项目外写 state | 通过（`E_LINK_BOUNDARY`） |
| Digest 不读取项目内 junction 指向的外部文件 | 通过 |
| draft Spec 上保存/批准 Plan 被拒绝 | 通过 |
| ADS、大小写 `Creation/` 路径被拒绝 | 通过 |
| 无效 run、删除 run、坏 placeholders 被 `verify` 发现 | 通过 |
| 关闭重开状态、digest 重建、锁定漂移 | 通过（既有测试保留） |
| 悬空 root junction 与祖先 junction 被拒绝，目标目录未创建；构造后 root 被替换为 junction 时 `init` 重新检查并拒绝；构造后新引入祖先 junction 时 `init` 也拒绝 | 通过（`E_LINK_BOUNDARY`） |
| 重复 `confirm_spec`（含重算 sha256/event_id/prev） | 通过（`E_DECISIONS_CORRUPT`；API `E_SPEC_TAMPERED`） |
| approved Plan 后被新 `plan_proposed` 替换 | 通过（`E_DECISIONS_CORRUPT` + `E_STEP_TARGET_NOT_DELIVERABLE`） |
| proposed Plan 的 target/acceptance/budget/generator/dependency 被检查 | 通过（`check_plan` 或 `E_PLAN_INVALID`） |
| decisions 缺末尾换行时 load/append 均拒绝且文件不变 | 通过（`E_DECISIONS_CORRUPT`） |
| `missing/..\link\project`（含多个缺失组件）被构造器拒绝；正式 CLI `init` 返回 2；项目外目标无 `creation/state.json` | 通过（`E_LINK_BOUNDARY`） |
| 仅有空白行、或合法事件后多一个空行的 decisions，load/append 均拒绝且文件字节不变；真正零字节仍可首次 append | 通过（`E_DECISIONS_CORRUPT`） |
| 合法 Spec revision 后创建并批准新 Plan | 通过（verify 无 error） |
| R4 `base\...\link\project`、`base\.. \link\project`、`base\missing\.. \link\project`、`base\missing\...\link\project`、`link.`、`link ` 由构造器拒绝 | 通过（`E_PROJECT_PATH_TRAILING`；真实 junction；outside 无 state） |
| R4 正常盘符路径接受；UNC、`\\?\GLOBALROOT`、`\\.\C:`、`\\?\C:`、`\??\C:`、`C:foo`、`C:`、`\project` 被拒绝 | 通过（`E_PROJECT_PATH_UNC`/`E_PROJECT_PATH_DEVICE`/`E_PROJECT_PATH_DRIVE_RELATIVE`/`E_PROJECT_PATH_INVALID`） |
| R4 构造后祖先替换为 junction 时指定入口全部 fail closed | 通过（`init/load_state/load_spec/load_decisions/append_decision/lock/build_digest` 抛 `E_LINK_BOUNDARY`；`verify` 返回该 issue） |
| R4 CLI：正常 init/status/digest/verify exit 0；设备/UNC/尾随点路径 exit 2 | 通过 |
| R4.1 `ProjectStore("")` 与 CLI `init ""` 被拒绝；空路径未创建 `creation/`；`"."`/`Path(".")` 正常 | 通过（`E_PROJECT_PATH_INVALID`，`test_creation_empty_path`） |
| 全量测试 | 305 tests OK（skipped=2；creation 124） |

## 10. 安全威胁模型（R4 修订）

当前机制是**一致性校验**，不是密码学防篡改，也不是外部不可变审计：

- **可以检测**：Spec/Plan 文件被单独修改而事件记录未同步；decisions 事件被删除、重复、断链或包含空白行；`state.json` 与 replay 结果不一致；重复确认、approved 后替换 Plan 等非法生命周期转换；锁文件与目标文件哈希不一致；proposed Plan 不满足 deliverable、acceptance、generator、budget 或 dependency 约束；声明路径中的 `missing/..` 规范化绕过，以及现存 symlink/junction 组件；Windows 尾随点/空格组件、设备/扩展命名空间、UNC、盘符相对与 root-relative 路径；声明路径与实际 `resolve()` 路径不一致；原始字符串空项目路径在 `ProjectStore` 与 CLI 入口被拒绝。调用方若已把空字符串转换为 `Path("")`，它与故意的 `Path(".")` 不可区分，属于明确的 API 边界。
- **不能保证**：拥有项目目录写权限的攻击者同步重写整条 decisions 历史、所有内容 SHA-256、event_id 与 prev_event_id 后，仍能被发现；没有数字签名、可信时间戳或外部不可变审计记录；没有跨进程锁或跨文件事务，崩溃/并发可能留下需人工处理的中间态；`lstat` 检查与 `mkdir`/`write_text` 之间的 TOCTOU 路径替换未加并发锁；Windows symlink 分支未在本机动态实测；普通 UNC 和所有 `\\?\` 扩展长度路径被策略拒绝而非安全支持。
- 因此，文档中的“删除审批/改内容/清空日志/路径绕过会报错”仅指上述已记录、已测试的检查模型；不应解读为对恶意写权限者或并发攻击者的安全保证。R2 曾把“检查声明路径”表述得比实际覆盖更强，R3 已把范围收紧为“词法规范化后的现存组件检查”，R4 进一步把 Windows 命名空间和声明/实际一致性纳入构造阶段，并保留未覆盖边界。未来若需要强防篡改，应引入签名或外部审计存储，而不是继续堆叠本地哈希。

**阶段结论：** Phase 3.2.1-R4 已关闭 R3 独立验收确认的 Windows 尾随点/空格、设备命名空间、UNC/盘符相对拒绝与声明/实际路径偏差问题；R4.1 已关闭 R4 独立审核发现的构造器/CLI 空字符串 fail-open。R4.1 后 creation 124 个测试、全量 305 个测试（skipped=2）和 CLI smoke 均通过；R4 的 115/296 保留为历史数字。当前结果可提交独立只读复核，复核确认前不开始 3.2.2。

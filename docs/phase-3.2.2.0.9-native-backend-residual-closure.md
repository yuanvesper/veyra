# Phase 3.2.2.0.9 — Native Backend Residual Closure

日期：2026-11-21
状态：原型验证完成，未进入 3.2.2.1，未修改生产代码
原型目录：`C:\Users\lenovo\AppData\Local\Temp\veyra-0.9-native-20261121213135-e4abbfc4`

## 1. 结论摘要

本轮只处理 0.8 审计遗留的 P1 与组合故障语义，不扩展功能面：

- 未发现 P0。
- 0.8 审计的 P1-1…P1-8 均已在原型中修复，并有对应回归用例。
- 第一次 0.9 审计发现 1 个 P1 残留：目标已恢复但 `RESTORE_DONE` 未落盘时，重试永久 `REFUSED` 且报 `durable=True`。已修复为“内容相同则采纳、先持久化 `RESTORE_DONE`、不重写目标”，失败返回 `PARTIAL/durable=False`；`R1-17` 覆盖。
- 第二次独立只读复核未发现新的 P0/P1，确认该残留闭合。
- 0.9 实验套件：50 例，47 PASS、2 BLOCKED、1 LIMITATION、0 FAIL；Python 3.15.0 与 3.14.7 结果一致。
- 0.8 回归：40 例，36 PASS、4 BLOCKED、0 FAIL；两个 Python 版本一致。
- 2 个 BLOCKED 都是当前环境没有创建符号链接的特权（`WinError 1314`），不是产品断言失败。
- 1 个 LIMITATION 是完成标记没有 MAC，项目内写者可以伪造自洽的 metadata+payload+marker；已在报告和用例证据中明示。

因此：**R1/R2/R4 的核心失败语义与幂等恢复已达到本轮退出条件；R3 的符号链接分支未验证；不能把本轮描述为“所有平台/所有重解析点均封闭”。**

## 2. 范围与不变量

本轮只验证 Windows NTFS 本地后端原型，不涉及 CLI、Skill、README、CI 或生产目录。

保留的不变量：

1. 目标被本操作修改后，任何失败都不得报告 `COMPLETED` 或 `FAILED`；必须是 `RECOVERY_REQUIRED` 或可解释的 `PARTIAL`。
2. `COMPLETED` 只能在目标新内容校验、备份标记完成、隔离副本删除都成功之后出现。
3. 恢复不得覆盖第三方写入；只有目标 identity/content 与本操作新内容或原始内容相符时才可动作。
4. 恢复必须幂等；重复调用返回 `ALREADY_RECOVERED`/`ALREADY_COMPLETED`，不得重复写状态或重复改目标。
5. 证据与残留只报告，不自动删除；完整备份、marker-only、payload-only、`.veyra-new-*`、`.veyra-old-*` 都保留。
6. 状态日志是追加式 JSONL；每条转换 flush 后才允许继续；尾部撕裂行可修复，完整坏行视为损坏。

## 3. 原型结构

| 文件 | 作用 |
|---|---|
| `winapi.py` | NT 句柄、相对打开、身份查询、独占锁、截断、重解析点标志 |
| `security.py` | 可信根、锚定目录、原子替换、备份、marker schema 2、清理 |
| `state.py` | 操作状态机、追加日志、跨进程操作锁、撕裂尾修复 |
| `durable.py` | 带备份的替换执行器与失败状态语义 |
| `recovery.py` | 残留盘点、恢复、隔离副本校验 |
| `snapshot.py` | 项目外副作用快照与轮询监测 |
| `experiments09.py` | 0.9 实验套件（50 例） |
| `child_kill.py` / `child_recover.py` | 真实进程终止与双进程恢复子进程 |

关键入口：

- `state.py:89` `_OperationLock`
- `state.py:138` `operation_lock`
- `state.py:227` `append_event`
- `state.py:271` `StateStore`
- `durable.py:92` `DurableOps.replace_existing`
- `recovery.py:255` `recover`
- `recovery.py:276` `_recover`
- `recovery.py:666` `_finish_restore_done`
- `recovery.py:739` `_finish_without_restore`
- `security.py:810` `BackupManager.verify`
- `security.py:830` marker `schema_version`/精确键集校验

## 4. P1 关闭情况

| 编号 | 问题 | 处理 | 证据 |
|---|---|---|---|
| P1-1 | 目标已恢复但 q 删除或 RECOVERED 追加失败，留下“非原始也非操作新内容”的目标，重试永久 REFUSED | 恢复前先持久化 `RESTORE_DONE`；失败返回 `PARTIAL/durable=False`；重试识别 `RESTORE_DONE` 后只收尾 | `R1-13`、`R1-14`、`R1-17` |
| P1-1a | 目标已写回但 `RESTORE_DONE` 追加前崩溃/失败，重试误判第三方修改 | 无 `RESTORE_DONE` 且目标 hash 等于原始 hash、identity 不同时，采纳该内容，先追加 `RESTORE_DONE`，不重写；追加失败 `PARTIAL` | `recovery.py:507-544`、`R1-17` |
| P1-2 | 无 q 且目标已是原始内容时直接 `ALREADY_RECOVERED`，不追加 RECOVERED、不清理可识别备份 | `_finish_without_restore` 统一追加 RECOVERED、清理已识别 q；失败 `PARTIAL` | `R1-15` |
| P1-3 | 状态追加无跨进程写互斥，可能重复 sequence 并永久 `StateCorrupt` | 每个操作 ID 一把 `LockFileEx` 独占锁，进程内 RLock；追加前重读并校验 sequence | `R2-12` 双进程 |
| P1-4 | 备份结构未与 state 绑定，foreign op ID 可能被接受 | 校验 backup 的 operation ID、original_path、TARGET_VERIFIED sha、BACKUP_CREATED sha/identity；不一致 `INCONSISTENT`/`REFUSED` | `R2-06`、`R2-13`、`R2-14` |
| P1-5 | marker 写了 `schema_version` 但未校验；限制描述过窄 | `verify` 强制 schema_version==2 且键集精确匹配；缺键、多键、版本错误都 `E_BACKUP_INCOMPLETE` | `R4-01`、`R4-02`、`R4-10`；限制见 `R4-05` |
| P1-6 | `.veyra-new-*`、`.veyra-old-*`、恢复后备份未盘点/未清理 | 新增 `inventory_transients`/`inventory_backups`/`inventory_quarantines`，只报告不删除 | `R2-01`、`R2-07`、`R4-11` |
| P1-7 | 没有通过的测试断言 `E_REPARSE_REFUSED`；R3-03 证据夸大 | 用 `cmd /c mklink /J` 在目标名建 junction，断言替换与恢复均拒绝、项目外内容不变；R3-03 改为“快照检测到穿过 junction 的外部写” | `R4-12`、`R3-03` |
| P1-8 | state 中的 quarantine 名未校验，`ValueError` 逃逸 | 恢复前 `validate_component`，非法名 `INCONSISTENT` | `R2-14` |

第一次 0.9 审计指出的残留（上表 P1-1a）已由 `R1-17` 关闭；第二次复核确认无新 P0/P1。

## 5. 测试结果

命令（两个解释器分别执行）：

```text
C:\Users\lenovo\AppData\Local\Programs\Python\Python315\python.exe experiments09.py
C:\msys64\ucrt64\bin\python.exe experiments09.py
```

结果文件：`results09-315.json`、`results09-314.json`。

```json
{"PASS":47,"BLOCKED":2,"LIMITATION":1}
```

共 50 例，0 FAIL。分组：R1 17、R2 14、R3 6、R4 12、C 1。

覆盖的关键场景：

- R1-01 正常替换的 12 个状态事件顺序；目标新内容；无 q；备份可验证。
- R1-02 marker 失败 → `RECOVERY_REQUIRED`、`E_MARKER_FAILED`，恢复旧内容。
- R1-03 q 删除失败 → `E_QUARANTINE_DELETE_FAILED`，恢复时清理 q。
- R1-04 首次写入前状态失败 → `FAILED`、目标未变、无 q。
- R1-05 目标已改后状态失败 → `RECOVERY_REQUIRED`，可恢复旧内容。
- R1-06 备份创建失败 → `FAILED`、目标未变。
- R1-07 最终 COMPLETED 状态失败 → 不得报告成功，恢复旧内容。
- R1-08 重复 operation ID → `E_OPERATION_EXISTS`，目标不变。
- R1-09 未知 operation → `NOT_FOUND`，目标不变。
- R1-10/11/12 真实子进程 `os._exit(99)`：隔离后、提交后、marker 后；父进程确认退出码 99，恢复旧内容。
- R1-13 q 清理失败 → `PARTIAL/durable=False`，重试 `RECOVERED`。
- R1-14 RECOVERED 追加失败 → `PARTIAL`，重试只追加状态、不重写目标，identity 不变。
- R1-15 无 q 且目标已是原始内容 → 追加 RECOVERED，不伪造“已完成”。
- R1-16 已 FAILED → 明确 REFUSED，不自动恢复。
- R1-17 `RESTORE_DONE` 落盘失败 → `PARTIAL/durable=False`；重试采纳相同内容，追加 RESTORE_DONE/RECOVERED，identity 不变。
- R2-01 完整/不完整/marker-only/payload-only 分类，不删除任何一项。
- R2-02 隔离后目标缺失 → 从 q/备份恢复。
- R2-03 结果已写但未完成 → 恢复旧内容。
- R2-04 崩溃后用户改写目标 → REFUSED，用户内容与 q 均保留。
- R2-05 q 内容被改 → REFUSED，q 保留。
- R2-06 同 operation 两份完整备份 → 写入目标前 `INCONSISTENT`。
- R2-07 marker-only/payload-only 残留保留。
- R2-08 尾部撕裂行被忽略/修复；完整坏行 REFUSED。
- R2-09 二次恢复 → `ALREADY_RECOVERED`，内容不变。
- R2-12 两个进程同时恢复 → 一个 `RECOVERED`、一个 `ALREADY_RECOVERED`，sequence 连续。
- R2-13 外来 Operation ID → `INCONSISTENT`。
- R2-14 非法 q 名 `../escape` → `INCONSISTENT`。
- R3-01 快照检测新增/删除/修改文件与目录。
- R3-02 替换后项目外文件/目录哨兵字节与身份不变。
- R3-03 通过真实 junction 的外部写被快照检测。
- R3-04 同名项目外文件不被触碰。
- R3-05 只读项目外哨兵不被触碰。
- R4-01 marker 内容篡改 → `E_BACKUP_INCOMPLETE`。
- R4-02 跨备份配对 marker → 拒绝。
- R4-03 metadata `[]` → `E_BACKUP_CORRUPT`。
- R4-04 metadata 同字节/新 identity → `E_BACKUP_INCOMPLETE`。
- R4-05 marker 仅替换 identity → 仍可通过（无 MAC 限制）。
- R4-06 清理前 payload 改变 → `E_BACKUP_CORRUPT`，payload 保留。
- R4-07 重复清理 → 报告缺失，不跟随路径。
- R4-10 schema_version 99、多键、缺非版本键 → 均拒绝。
- R4-11 `.veyra-new-*`/`.veyra-old-*` 盘点但不删除。
- C-01 marker 失败 + TARGET_MUTATED 状态失败组合 → 恢复旧内容，残留备份保留。

0.8 回归：

```json
{"total":40,"pass":36,"fail":0,"blocked":4}
```

BLOCKED：G3n/G4n/G6n（嵌套 live rename `WinError 5`）与 G5（符号链接特权 `WinError 1314`）。

## 6. 未关闭与未验证

### 6.1 BLOCKED（环境限制，非通过）

- `R3-06`：无法创建符号链接，`os.symlink` 返回 `WinError 1314`，因此“快照记录符号链接重解析元数据且不跟随”未执行。
- `R4-08`：无法用符号链接构造目标，`E_REPARSE_REFUSED` 的符号链接分支未执行；junction 分支由 `R4-12` 覆盖。

### 6.2 LIMITATION（设计限制）

- `R4-05`：marker schema 2 没有 MAC。项目内写者可以同时伪造 metadata、payload 和 marker，使三者自洽；`verify` 只能发现不自洽或 identity 变化，不能证明来源。
- `RESTORE_DONE` 的“内容采纳”分支只凭 SHA-256 相等，信任边界从 identity+content 降为 content。因内容与原始备份完全相同、不覆盖用户内容，故无数据丢失；但同字节新文件也会被认领。
- 状态日志不是防篡改日志；项目内写者可以改历史事件。结构校验能发现部分不一致，不能提供密码学完整性。
- `inventory_backups` 使用 `os.walk`，`list_operation_ids` 使用 `os.listdir`；两者在重解析点/并发替换下是宿主路径语义，不是全程锚定句柄语义。

### 6.3 未实现/未覆盖

- `DurableOps.delete_with_backup` 仍为 `E_NOT_IMPLEMENTED`。
- 残留清理只盘点，不自动删除；这是刻意保留证据，不是遗漏。
- 未测试非 NTFS、跨卷、长路径、权限拒绝变体、电源掉电、磁盘满、杀软拦截。
- 真实进程终止只覆盖 3 个 hook（隔离后、提交后、marker 后）；其他转换点靠异常注入，不是逐点进程 kill。
- 并发只做双进程恢复；未做高并发/锁等待超时/进程挂起测试。
- 没有 schema 1 → 2 的迁移；旧 marker 会被拒绝。
- 恢复只支持项目根下的单个文件名目标；子目录目标未纳入本轮。

## 7. 独立审计

- 第一次只读审计：无 P0；指出 P1-1 残留（`RESTORE_DONE` 落盘失败导致永久 REFUSED）。
- 修复后第二次只读复核（`audit09b`）：未发现新 P0/P1；确认 `recovery.py:515-544` + `R1-17` 正确闭合 P1-1a；确认 `TARGET_VERIFIED`/`BACKUP_CREATED` 必填比对、`state.py` 锁竞态修复、`durable.py` 错误码保留均有效。
- 复核提出的 P3 项（`restored_now`/`q_ident` 死参数、RESTORE_DONE details 重复、content_match 成功消息措辞）不影响正确性，未在本轮清理。
- 复核特别指出：content_match 路径绕过了 identity 守卫，仅凭内容相等采纳；已在本报告 6.2 明示。

## 8. 复现

在原型目录执行：

```powershell
& 'C:\Users\lenovo\AppData\Local\Programs\Python\Python315\python.exe' experiments09.py
& 'C:\msys64\ucrt64\bin\python.exe' experiments09.py
& 'C:\Users\lenovo\AppData\Local\Programs\Python\Python315\python.exe' experiments_08.py
& 'C:\msys64\ucrt64\bin\python.exe' experiments_08.py
```

套件只使用 `%TEMP%` 下独立目录；通过/限制用例结束会清理，失败用例保留现场。未写入 `D:\Projects_vesper\veyra` 的任何生产文件。

## 9. 保护文件与 Git

本报告写入前只读核对：

- 分支 `main`，HEAD `0fa1bbfff9c28e1b12c94b334f23b732d059926e`。
- `report.md`：1570 字节，mtime `2026-11-11 22:07:06`，SHA256 `A11D09D49217CA3C6CD7C269268C148558EA488067E8D1399BCAFD7ABBC2D68D`。
- `real_crash.log`：21999 字节，mtime `2026-11-05 18:40:14`，SHA256 `CC1418B7690284787B55D3417383F23DD6DEAA47619BC41702F9F33895DCD54D`。
- 工作区此前的 `README.md`、`README_zh.md`、`report.md` 修改和既有未跟踪文档/测试目录均未触碰。
- 本轮唯一新增生产文件是本报告；未执行 `git add/commit/push/reset/checkout/clean/rebase`。

## 10. 最终判断

- 可以认定：0.8 审计的 P1 与 0.9 首次审计的 P1 残留均已在原型中闭合，并有可重复用例。
- 不可以认定：符号链接分支已通过；marker 具备来源认证；所有崩溃点都做过真实进程 kill；所有平台/文件系统都适配。
- 建议下一步（若用户授权 3.2.2.1）先处理：符号链接测试环境、marker MAC/来源认证设计、逐转换点真实 kill、残留保留策略与清理命令的产品化；不要在本轮基础上直接宣称生产可用。

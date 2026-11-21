# Phase 3.2.2.0.8 — Windows 原生后端安全原型报告

- 日期：2026-11-21
- 范围：Phase 3.2.2.0.8（独立临时目录原型，不进入 3.2.2.1）
- 原型目录：`C:\Users\lenovo\AppData\Local\Temp\veyra-0.8-native-20261121180144-3387d01a`
- 生产仓库：`D:\Projects_vesper\veyra`（main，HEAD `0fa1bbfff9c28e1b12c94b334f23b732d059926e`）
- 生产代码、测试、README、CI、`report.md`、真实日志均未修改；未执行任何 Git 写操作。

## 1. 结论摘要

本轮目标是把 0.7 报告中的三个硬阻断（G 祖先 junction、H 按名字替换调包、I 备份路径 junction）在独立原型中关闭，并给出可复现证据。

结论：

1. **G：祖先 junction 未锚定 —— 已关闭。**
   `open_trusted_root` 从卷根开始，逐级以句柄相对方式打开，每级都带 `FILE_OPEN_REPARSE_POINT`，并校验 reparse 标签、文件 identity 和卷号。静态多级 junction（G2/G6s）被拒绝；项目根被 rename 或被 junction 替换后，已打开的根句柄仍绑定原对象（G3/G4）。

2. **H：替换目标被调包 —— 已关闭。**
   `replace_existing` 不再按名字 `replace=True` 提交。它先打开并核对目标 identity，按句柄把目标 rename 到随机 quarantine，再用 `FILE_CREATE` 式 `replace=False` 提交；提交冲突即失败，不覆盖新对象。H5 中攻击者在提交窗口插入同名文件，提交返回 `E_COMMIT_FAILED`，攻击者内容保留，quarantine 中仍是已验证的旧对象。H9 进一步验证：目标句柄以 deny-write 共享模式打开后，提交前原地写目标会得到 `PermissionError`。

3. **I：备份路径越界 —— 在 junction 威胁模型下已关闭；符号链接路径仍未实测。**
   备份根和中间目录全部从项目根句柄逐级打开并拒绝 reparse；payload、元数据、完成标记不再用可预测名字直接 `FILE_CREATE`，而是“随机临时名创建 + `replace=False` 提交”。I10 在最终备份名预置 junction，提交返回 `E_ALREADY_EXISTS`，outside 无文件。审计指出的正常“替换后恢复”也已修复：`restore` 可显式接收当前 replacement 的 identity 和内容 hash，I1 先真实替换再恢复；目标已删除时 I11 从备份独占重建。

4. **测试结果：40 例，36 PASS，0 FAIL，4 BLOCKED。**
   两个 Python 版本状态一致：
   - Python 3.15.0（MSVC）：40 total / 36 pass / 0 fail / 4 blocked
   - Python 3.14.7（MinGW UCRT）：40 total / 36 pass / 0 fail / 4 blocked

   BLOCKED 为 G3n、G4n、G6n（Windows 拒绝持子句柄时祖先 rename，`ERROR_ACCESS_DENIED(5)`，fail-closed）和 G5（`SeCreateSymbolicLinkPrivilege` 不可用，WinError 1314）。

5. **总体判断：G/H 在 junction 威胁模型下基本关闭；I 的写入边界关闭，但“符号链接 + 备份完整性/故障恢复”仍有未验证项。**
   本轮不宣称“36 项全语义通过”，也不宣称可以无条件进入 3.2.2.1。建议把 §10 的残留项作为进入 3.2.2.1 前的门禁。

## 2. 范围、威胁模型与不变量

### 2.1 范围

- 只改临时目录中的 `winapi.py`、`security.py`、`experiments.py` 和结果 JSON。
- 不修改生产代码、`tests/`、`README*`、CI。
- 不执行 `git add/commit/push/reset/checkout/clean/rebase`。
- 每例使用独立 `%TEMP%\veyra-0.8-exp-*`，结束后清理；不读取或覆盖真实玩家日志。

### 2.2 威胁模型

攻击者：

- 对项目目录有写权限；
- 能创建 junction；
- 能在项目内创建/删除/改名文件；
- 能在检查与使用之间制造竞争。

本轮环境不能创建符号链接（WinError 1314），所以符号链接只是设计上考虑、未执行验证。攻击者不具备管理员权限、不能改内核/文件系统驱动、不能改卷根。

### 2.3 不变量

1. 所有项目内路径从卷根开始逐级句柄相对打开，任何一级 reparse 都拒绝。
2. 只替换“已打开并核对 identity”的对象；不按名字覆盖未知对象。
3. 任何备份/临时文件都不写到项目边界之外。
4. 冲突、检查失败、提交失败必须 fail-closed；不能把攻击者内容当作用户内容。
5. 备份恢复必须能在“目标已被替换”后工作，不能只支持 no-op 重写。

## 3. 原型结构

### 3.1 低层：`winapi.py`

- `query_identity`：`GetFileInformationByHandleEx(FileIdInfo)`，返回卷号 + 128-bit file id。
- `open_volume_root`：`CreateFileW("C:\\", FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT)`。
- `open_dir_at` / `open_file_at`：`NtCreateFile`，`RootDirectory` 为父句柄，`FILE_OPEN_REPARSE_POINT`；最终组件是 reparse 时打开 reparse 本身，由策略层拒绝。
- `create_file_at`：`FILE_CREATE`；增加 `FILE_OPEN_REPARSE_POINT`，避免创建时跟随最终组件的悬空链接。
- `rename_handle`：`FileRenameInformation`（class 10），`ReplaceIfExists=0` 时绝不覆盖已有名字。
- `delete_handle`：`FileDispositionInformationEx`（class 64），flags `FILE_DISPOSITION_DELETE | FILE_DISPOSITION_POSIX_SEMANTICS`。
- `WinHandle` 跟踪所有打开的句柄；`open_handle_count()` 用于每例泄漏检查。

### 3.2 策略层：`security.py`

- `AnchoredDir`：目录句柄 + 每一级的 `Level(name, identity)`；`open_subdir` 逐级校验 reparse/卷号。
- `create_file`：写前用 `open_file(name)` 探测；已存在或 reparse 直接拒绝。写后 rewind 重读并校验 SHA256。
- `create_exclusive_rename`：随机临时名创建，再按句柄 `rename_handle(..., replace=False)` 提交到最终名；最终名已有任何对象（含 junction/symlink）时失败，绝不跟随。
- `SafeOps.replace_existing`：
  1. 打开目标，校验 identity（可选内容 hash）；
  2. 可选写备份；
  3. 创建临时新文件；
  4. 按句柄把目标 rename 到随机 `.veyra-old-*`；
  5. `rename_handle(..., replace=False)` 提交临时文件；
  6. 校验最终名字 identity，删除 quarantine。
  目标句柄使用 `FILE_SHARE_READ | FILE_SHARE_DELETE`（deny write），但仍允许 quarantine/delete。
- `BackupManager`：
  - 备份根锚定在项目内 `.veyra-backups`；
  - payload 和 metadata 用 `create_exclusive_rename`；
  - `mark_complete` 写 `.complete`；
  - `verify` 校验 metadata、payload identity、size、SHA256、marker operation_id；
  - `restore` 默认要求目标仍等于 original；替换后恢复必须显式给当前 identity+hash；目标缺失时从备份 `create_exclusive` 重建；
  - `cleanup` 对 metadata/payload 校验 identity，路径必须位于 backup root 内。
- `BackupManager` 的临时 `bdir` 句柄和子句柄在正常/异常路径显式关闭；`handle_delta` 全部为 0。

### 3.3 实验层：`experiments.py`

- 40 个用例，覆盖 G1–G7、H1–H10、I1–I11、GH/GI/HI/GHI 组合。
- 每例独立临时根，含 `project` 和 `outside`；`outside` 前后快照；句柄增量检查；异常分类 PASS/FAIL/BLOCKED。
- 用例失败也会清理；残留目录单独核验。

## 4. G 类结果：祖先 junction 与根绑定

| 用例 | 场景 | 结果 | 证据 |
|---|---|---|---|
| G1 | 多级普通目录锚定打开、创建/读回 | PASS | `levels` 从 `C:\` 到 `c`；`handle_delta=0` |
| G2 | `ancestor -> outside` junction，项目在 outside 下，重复 3 次 | PASS | `open_trusted_root` 均 `E_REPARSE_REFUSED`；outside 快照不变 |
| G3 | 打开根后 rename 项目根，重复 3 次 | PASS | 旧句柄仍读写 moved；outside 无文件 |
| G3n | 子目录句柄打开时 rename 祖先 | BLOCKED | Windows `ERROR_ACCESS_DENIED(5)`；未执行，不能写成“已验证 moved” |
| G4 | 根路径被 junction 替换 | PASS | 根句柄仍写 moved；outside 无文件 |
| G4n | 子目录句柄存活时替换祖先为 junction | BLOCKED | 同上，OS 拒绝祖先 rename |
| G5 | 静态 symlink 拒绝 | BLOCKED | `SeCreateSymbolicLinkPrivilege` 不可用（1314） |
| G6s | 多级静态 junction | PASS | 逐级拒绝；outside 不变 |
| G6n | 多级 live 替换 | BLOCKED | OS 拒绝祖先 rename |
| G7 | 删除/同名重建后的 identity | PASS | 同名新对象 identity 与旧对象不同 |

**G 的结论**：静态祖先 junction 和多级路径已由“卷根逐级 + OPEN_REPARSE_POINT + identity/卷号校验”关闭；根 rename/replace 后的绑定由已打开句柄保证。三个 nested live 用例是 OS fail-closed，不是“绑定 moved 已验证”；报告不把它们算作通过。

## 5. H 类结果：替换目标调包与原地写

| 用例 | 场景 | 结果 | 证据 |
|---|---|---|---|
| H1 | 正常替换 | PASS | 新内容生效；backup `original_sha256 == sha256("old")`；verify 通过；quarantine 已删 |
| H2 | 检查后 rename 目标 | PASS | `E_TARGET_CHANGED`；目标/攻击者内容不被覆盖 |
| H3 | 检查后同名新文件 | PASS | `E_TARGET_CHANGED`；不覆盖新文件 |
| H4 | 目标换成 junction | PASS | `E_REPARSE_REFUSED`；outside 无文件 |
| H5 | 提交窗口插入同名文件 | PASS | `E_COMMIT_FAILED`；攻击者内容保留；quarantine 内容确认为 `b"old"` |
| H6 | 删除/重建目标 | PASS | `E_TARGET_CHANGED`；攻击者内容保留 |
| H7 | 备份后、隔离前注入失败 | PASS | 目标仍 `old`；备份目录无残留 |
| H8 | 传入错误 identity | PASS | `E_IDENTITY_MISMATCH`；目标不变 |
| H9 | 提交前原地写目标 | PASS | 目标句柄 deny-write，`PermissionError`；替换仍成功 |
| H10 | quarantine rename 注入失败 | PASS | `E_QUARANTINE_FAILED`；目标仍 `old`；无 `.veyra-old-*`；备份已清理 |

**H 的结论**：按名字“替换新对象”的路径被关闭。H5 是 0.7 H 探针的直接回归：攻击者插入的同名文件没有被覆盖，quarantine 保存的是已核对对象。H9 证明在目标句柄存活期间普通写被共享模式拒绝；H10 证明 quarantine 失败时不会留下无引用备份。

## 6. I 类结果：备份边界与恢复

| 用例 | 场景 | 结果 | 证据 |
|---|---|---|---|
| I1 | 先真实替换，再恢复 original | PASS | replacement → restore；内容回到 `original`；`old_identity` 为 replacement |
| I2 | 备份根是 junction | PASS | `E_REPARSE_REFUSED`；outside 不变 |
| I3 | 备份中间目录是 junction | PASS | 拒绝；outside 不变 |
| I4 | 备份 payload 被替换 | PASS | `E_BACKUP_IDENTITY_MISMATCH` |
| I5 | 备份 payload 原地修改 | PASS | `E_BACKUP_CORRUPT`（size/hash） |
| I6 | 目标原地修改 | PASS | `E_TARGET_MODIFIED` |
| I7 | 备份缺失 | PASS | `E_NOT_FOUND` / fail-closed |
| I8 | restore 提交前插入目标 | PASS | `E_COMMIT_FAILED`；攻击者内容保留 |
| I9 | cleanup 前 backup identity 改变 | PASS | `E_BACKUP_IDENTITY_MISMATCH`；metadata 未被删 |
| I10 | 最终备份名预置 junction | PASS | `E_ALREADY_EXISTS`；junction 未被跟随；outside 无文件 |
| I11 | 备份后删除目标，再恢复 | PASS | `delete_with_backup` 成功；目标缺失时从备份 `create_exclusive` 重建 |

**I 的结论**：

- 备份根、中间目录、最终名三层都不再跟随 junction；最终名写入改为“随机临时名 + 独占 rename”。
- 审计指出的“restore 只支持 no-op”已修复：I1 是真实替换后的恢复，不再把相同内容重写当成恢复。
- 删除后的恢复也已支持：I11 目标缺失时从已验证备份重建，返回 `old_identity=None`。
- 仍未验证符号链接；见 §10。

## 7. 独立审计意见的闭环

独立只读审计（auditor，未运行实验、只读源码/JSON 并校验 ctypes 布局）提出两个严重问题和若干中等问题。本轮的处理如下：

| 审计意见 | 处理 | 证据/状态 |
|---|---|---|
| 严重 1：备份 payload/meta/marker 用可预测名字直接 `FILE_CREATE`，悬空 symlink 可写到项目外 | payload/meta/marker 改走 `create_exclusive_rename`：随机临时名 + `replace=False` 提交；`create_file` 增加写前 reparse/存在性探测，`create_file_at` 加 `FILE_OPEN_REPARSE_POINT` | I10（最终名 junction）PASS；symlink 未实测，G5 BLOCKED |
| 严重 2：restore 只接受 original，正常替换后恢复必失败；I1 假通过 | `restore` 增加 `expected_current_identity`/`expected_current_sha256`；目标缺失时从备份独占重建；I1 改为真实替换后恢复 | I1、I11、GHI2 PASS |
| quarantine rename 失败时备份不清理 | 候选名只在 rename 成功后赋给 `quarantined_name`；失败时走备份 cleanup | H10 PASS |
| cleanup 按名删、无 identity | `_delete_backup_member` 对 metadata/payload 校验 identity，并校验 backup root 前缀 | I9 PASS |
| bdir/子句柄依赖 GC | `BackupManager` 各方法、`open_subdir`/`open_trusted_root` 异常路径显式 close | 40 例 `handle_delta` 全 0 |
| delete_with_backup 无测试 | 新增 I11：删除 + 备份 + 缺失目标恢复 | I11 PASS |
| H1/H5 断言弱 | H1 校验 backup 的 original hash；H5 校验 quarantine 内容仍为 `b"old"` | 均 PASS |
| 成功路径 mark_complete/隔离删除失败只记 notes | 未修，保留为残余风险 | §10 第 3 项 |
| metadata 类型错误、marker 未认证、快照忽略目录 | 未修，保留为残余风险 | §10 第 4/5/6 项 |

审计确认的 ctypes 布局（`OBJECT_ATTRIBUTES` 48、`IO_STATUS_BLOCK` 16、`FILE_RENAME_INFORMATION` 24/offset 20、`FILE_DISPOSITION_INFORMATION_EX` 4、`FILE_ID_INFO` 24、`FILE_ATTRIBUTE_TAG_INFO` 8）与本轮实现一致；类号 10/13/64、flags 3 正确。

## 8. 组合场景

| 用例 | 场景 | 结果 |
|---|---|---|
| GH1 | 根 rename + 替换 | PASS；写 moved，outside 无文件 |
| GH2 | 根 rename + 原子创建 | PASS；写 moved |
| GI1 | 根 rename + 备份 | PASS；备份仍在原根对象内 |
| GI2 | 备份目录 junction + restore | PASS；拒绝，outside 不变 |
| HI1 | 目标 identity 改变 + restore | PASS；拒绝错误目标 |
| HI2 | 替换失败 + 备份清理 | PASS；目标完整，备份无残留 |
| GHI1 | 目标 junction + restore | PASS；拒绝 |
| GHI2 | 中断替换 + 根 rename + restore | PASS；目标缺失时从备份重建 original |
| GHI3 | 提交冲突 + restore | PASS；攻击者内容不被覆盖，restore 拒绝 |

## 9. 关键设计说明

### 9.1 为什么“句柄 rename 到 quarantine”能关闭 H

0.7 的 H 攻击是：

1. 打开 `r.txt`，检查它是 original；
2. 攻击者把 `r.txt` rename 走，新建 `r.txt = attacker`；
3. 旧实现用 `rename(temp, "r.txt", replace=True)` 提交，按名字覆盖了 attacker。

0.8 不再按名字覆盖：

- 目标句柄在检查后一直存活；
- quarantine 是“把这个句柄指向的对象改名”，不是“把当前名字上的对象改名”；
- 新文件提交用 `replace=False`，名字已被占用就失败；
- 失败时 attacker 文件保留，quarantine 里是原对象，不会把 attacker 当用户内容备份或删除。

### 9.2 为什么 deny-write 共享模式是必要的

仅校验 identity 不能阻止同一 identity 的原地写：内容变了，identity 不变。H9 打开目标时使用：

```text
FILE_SHARE_READ | FILE_SHARE_DELETE
```

不共享写。普通 `open(path, "wb")` 会得到 `PermissionError`；quarantine rename 和 delete 仍被允许。这样从“打开目标”到“备份/quarantine”之间，内容不会被第三方普通写改掉。

### 9.3 符号链接与 junction 的区别

本轮能用 `mklink /J` 创建 junction，不能用 `os.symlink`（1314）。因此：

- 静态 junction、最终名 junction（I10）有实测；
- 符号链接的 `FILE_CREATE`/`OPEN_REPARSE_POINT` 行为没有在本机执行通过项；
- G5 只能标 BLOCKED，不能写成“symlink 已关闭”。

设计上，`create_exclusive_rename` 对最终名不区分 reparse 类型：只要最终名已有对象，`replace=False` 就失败；`create_file` 也加了预探测和 `FILE_OPEN_REPARSE_POINT`。但“设计上覆盖”不等于“已实测”，报告按后者表述。

## 10. 未关闭、未验证与残余风险

以下不是“已通过”，不能省略：

1. **符号链接最终组件未实测**（G5 BLOCKED）。需要在启用 Developer Mode 或有 `SeCreateSymbolicLinkPrivilege` 的环境重跑；重点验证悬空 symlink 在 `create_file` 预探测与 `create_exclusive_rename` 提交两个窗口的行为。
2. **三个 nested live 用例未执行**（G3n/G4n/G6n BLOCKED）。Windows 拒绝持子句柄时祖先 rename；这是 fail-closed，但不能替代“绑定 moved 已验证”的结论。
3. **成功路径的故障处理仍是 notes 语义**：`mark_complete` 失败（`security.py:469`）和 quarantine 对象删除失败（`security.py:478`）只追加 notes，函数仍返回成功。可能留下缺 `.complete` 的备份或 `.veyra-old-*` 对象；没有注入测试。
4. **metadata 完整性和错误类型**：`_read_meta` 对类型错误可能抛 `ValueError`/`KeyError` 而非 `SecurityError`；marker 只比对 operation_id，`meta_identity` 未参与校验；在“攻击者可写备份目录”模型下 metadata 可被替换。当前路径校验把影响限制在备份根内，但会削弱恢复可信度。
5. **cleanup 的 marker 仍按路径删除**，没有 identity；metadata/payload 已校验 identity。marker 不是数据源，但清理中途失败可能留下不可 verify 的备份。
6. **快照忽略目录**：`experiments.py:48` 的 `snapshot` 只记录文件和链接；如果攻击只创建/删除目录，`outside_unchanged` 不会发现。I10 用针对性断言补了最终名 junction，但这是结构性漏检。
7. **未覆盖**：并发双实例、崩溃恢复、只读属性/AV 锁、非 NTFS、长路径、非 ASCII、OneDrive/云 reparse、硬链接、删除失败路径、`create_exclusive` 直接冲突、metadata 类型错误。
8. **恢复语义的边界**：`restore` 目标缺失时从备份重建；如果攻击者在“缺失判断”和 `create_exclusive` 之间插入同名对象，提交会 `E_ALREADY_EXISTS`，不会覆盖。若目标仍存在但 identity 不同，必须显式传 expected identity/hash，否则拒绝；这是有意的 fail-closed。
9. **权限过宽**：`open_trusted_root` 每级用 `DIR_ACCESS`（含 DELETE/FILE_ADD_FILE 等）打开，只读目录可能无法构造；原型可接受，生产化需收窄。

## 11. 对 3.2.2.1 的建议

不建议把本报告解释为“I 已完全关闭”。建议：

- **G/H：可以认为在 junction 威胁模型下达到进入下一阶段的门槛**；nested live 仍应保留为未验证项。
- **I：写入边界达到门槛，恢复能力达到“替换后/删除后”可用；符号链接和故障路径未达到门槛。**
- 进入 3.2.2.1 前至少补：
  1. 有符号链接权限的环境重跑 G5，并加悬空 symlink 的 final-name 用例；
  2. `mark_complete` 失败、quarantine 删除失败、metadata 类型错误的注入测试；
  3. 明确“成功但备份不可 verify”是回滚失败还是可接受降级；
  4. 重新评估快照是否应记录目录，或在每个用例中显式检查 outside 目录集合。
- 若不愿承担符号链接/故障路径风险，3.2.2.1 只能把 I 的承诺降级为“junction 威胁模型下的 best-effort”，不能对外承诺“零外部写入”。

## 12. 复现与产物

### 12.1 运行命令

```text
cd C:\Users\lenovo\AppData\Local\Temp\veyra-0.8-native-20261121180144-3387d01a

C:\Users\lenovo\AppData\Local\Programs\Python\Python315\python.exe -B experiments.py
C:\msys64\ucrt64\bin\python.exe -B experiments.py
```

每次运行写 `results.json`；本轮另存：

- `results-py315.json`
- `results-py314.json`

两个版本 summary 均为：

```json
{"total": 40, "pass": 36, "fail": 0, "blocked": 4}
```

BLOCKED：G3n、G4n、G5、G6n。两版本 case 状态一致；由于临时目录名、file id、随机 quarantine 名不同，JSON 字节不相同，不能表述为“字节一致”。

### 12.2 用例清单

G1、G2、G3、G3n、G4、G4n、G5、G6s、G6n、G7；H1–H10；I1–I11；GH1、GH2、GI1、GI2、HI1、HI2、GHI1、GHI2、GHI3。

### 12.3 本轮新增/修改文件（仅临时目录）

- `winapi.py`
- `security.py`
- `experiments.py`
- `results.json`、`results-py314.json`、`results-py315.json`

生产仓库只新增本报告：

- `docs/phase-3.2.2.0.8-windows-native-backend-security.md`

### 12.4 保护文件与 Git 状态

本轮开始/结束均只读核验：

```text
report.md
  bytes  = 1570
  SHA256 = A11D09D49217CA3C6CD7C269268C148558EA488067E8D1399BCAFD7ABBC2D68D

real_crash.log
  bytes  = 21999
  SHA256 = CC1418B7690284787B55D3417383F23DD6DEAA47619BC41702F9F33895DCD54D
```

两者均未修改。Git：branch `main`，HEAD `0fa1bbfff9c28e1b12c94b334f23b732d059926e`；未 `add/commit/push/reset/checkout/clean/rebase`。

## 13. 最终判断

- **G：关闭**（静态/根级证据充分；nested live 仍 BLOCKED）。
- **H：关闭**（名字调包与原地写均有回归；H10 覆盖 quarantine 失败清理）。
- **I：写入边界关闭；恢复在“替换后”和“删除后”可用；符号链接与故障路径未关闭。**
- **总体：不应把 40 例写成“全部语义通过”。准确表述是 36 PASS、0 FAIL、4 BLOCKED，且 §10 仍有未验证项。**

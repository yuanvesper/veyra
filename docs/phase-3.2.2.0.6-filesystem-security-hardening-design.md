# Veyra Phase 3.2.2.0.6 — 文件系统写入边界安全加固设计

- 文档状态：设计稿，等待用户与 ChatGPT 审阅
- 适用基线：`main` / `0fa1bbfff9c28e1b12c94b334f23b732d059926e`
- 关联报告：`docs/phase-3.2.2.0.5-toctou-security-assessment.md`
- 关联设计：`docs/phase-3.2.2-operation-executor-design.md`
- 本轮范围：**只读代码审查与设计，不修改生产代码、不修改测试代码、不进入 3.2.2.1**
- 当前结论：**3.2.2.1 的所有文件写操作继续保持阻断；在本文 §11 的准入条件完成前，不得开始 Operation Executor 的写实现。**

---

## 0. 结论摘要

### 0.1 现状

当前 Veyra 的生产写入路径全部是“路径字符串检查 + 随后调用路径 API”：

1. `ProjectStore.init()` 使用 `mkdir(parents=True, exist_ok=True)`，并在创建后检查链接。
2. `ProjectStore` 的 JSON 状态写入统一经过 `write_json_atomic()`，而该函数内部又执行 `path.parent.mkdir(parents=True, exist_ok=True)`。
3. `decisions.ndjson` 通过 `open("a")` 追加，检查与打开之间没有对象身份绑定。
4. 清理逻辑使用 `created_root` / `created` 布尔和路径列表，`unlink()` / `rmdir()` 不再核对“这是不是我创建的那个对象”。
5. `veyra_pack.init_pack()`、`veyra_pack.export_pack()` 和 `triage_agent.write_report_text()` 各自重复了“`mkdir` → `mkstemp` → `os.replace` → 失败清理”的模式，且都没有统一边界。
6. 当前实现没有统一的写入层；`write_json_atomic()` 只是 JSON 便捷函数，不是安全边界。

因此，当前代码能拒绝**检查时已经存在**的 symlink/junction，但不能保证**检查与使用之间**仍然是同一个文件系统对象。

### 0.2 关键判断

- 纯 Python 的逐级检查、重复检查、`lstat`、`resolve()` 都不能彻底消除 TOCTOU。
- Windows 上仅使用 `CreateFileW` / `os.open` / `Path` 也不能把“后续创建或替换”绑定到已验证的父目录对象；Win32 的创建 API 没有“相对目录句柄”的参数。
- 要满足任务书 §8 的“项目外路径不能因 junction 替换而产生写入”，需要 **句柄相对的创建/提交原语**（Windows 上是 `NtCreateFile` + `NtSetInformationFile`，POSIX 上是 `openat`/`renameat`/`unlinkat`），或者明确降低该准入条件。
- 清理不能依赖“创建时路径曾经合法”，必须在删除前核对文件身份；默认应 **fail-closed**，宁可留下空目录，也不删除身份不明的目录。
- `write_json_atomic()` 不应再隐式创建父目录；父目录必须由调用者通过显式的、逐级的、可审计的目录创建步骤准备好。

### 0.3 推荐方案

推荐 **方案 D+：统一 Python 策略层 + Windows/POSIX 句柄相对提交后端**。

- 策略层负责：路径政策、目标快照、身份、配额、审计、失败语义。
- 提交层负责：在已验证的父目录句柄下创建临时文件、提交创建/替换、删除、备份和恢复。
- Windows 严格模式使用 `CreateFileW` 打开目录句柄、`GetFileInformationByHandleEx` 读取身份、`NtCreateFile` 相对父句柄创建、`NtSetInformationFile` 相对父句柄重命名/删除。
- POSIX 使用 `openat` / `renameat` / `unlinkat` 与 `O_NOFOLLOW`；不支持这些能力的平台，写操作 **fail closed**，不静默降级。
- 如果产品不接受 `ntdll` 的非公开 ABI 风险，则只能选择方案 D 的兼容模式（路径 + 身份快照），并把任务书 §8 第 2 条降级为“不能保证零外部写入”；不能在文档中假装兼容模式满足严格门槛。

**这是本报告最重要的取舍：安全保证不是靠多写几次 `exists()` 得到的。要么采用句柄相对提交，要么明确承认残余窗口。**

---

## 1. 目标、非目标与安全假设

### 1.1 目标

1. 盘点当前所有可能产生文件系统副作用的代码路径，而不只是搜索 `mkdir`。
2. 说明每个写入点的路径来源、前置检查、实际 API、对象身份保证、失败副作用和清理行为。
3. 评估 Windows 目录句柄、reparse point、文件身份和句柄相对操作能提供什么保证，不能提供什么保证。
4. 比较四种加固方案，给出推荐架构和安全保证边界。
5. 设计清理、原子写入、备份、恢复和测试矩阵。
6. 给出 3.2.2.1 的明确准入条件；未满足前保持写操作阻断。
7. 保持与 Veyra 最终目标（AI Game Mod Creation Platform）一致，不引入不必要的平台化或第三方依赖。

### 1.2 非目标

- 不实现 `SafeWriter`、`OperationExecutor`、`recovery`、备份 CLI 或新的 Operation 契约。
- 不修改 `ProjectStore`、`veyra_pack`、`triage_agent.py` 或任何测试。
- 不重新设计 Phase 3.2.1 的 State/Spec/Plan/Replay/Digest 语义。
- 不把 `report.md`、`real_crash.log` 当作测试目标；不读取、不覆盖、不删除、不恢复。
- 不执行 Git 写操作、不 pull、不提交、不推送。
- 不引入第三方依赖；不引入数据库、守护进程或系统级文件系统过滤驱动。

### 1.3 保护资产

| 资产 | 保护目标 | 失败时的默认行为 |
|---|---|---|
| 项目根之外的任意文件/目录 | 不创建、不覆盖、不删除、不写入 | 拒绝操作，保留现场 |
| 用户已有项目文件 | 不被未授权覆盖/删除；外部修改不被静默丢弃 | 冲突即停止，不自动合并 |
| `creation/state.json`、Spec、Plan、Locks | 不被半写、不被链接重定向 | 原子替换或保持旧内容 |
| `creation/decisions.ndjson` | 追加式、可追溯、不写到项目外 | 写失败即停止，不继续业务写 |
| `creation/digest.json` | 派生视图，可重建 | 失败不谎报成功 |
| `creation/backups/` | 恢复材料；不被静默覆盖或清理 | 备份失败则不删除目标 |
| 临时文件 | 不跨目录、不残留敏感内容、不覆盖未知文件 | 只清理身份匹配的自建临时文件 |
| `report.md` / `real_crash.log` | 用户保护文件 | 本轮只核验，永不写 |

### 1.4 信任模型与假设

本设计假设：

1. 运行 Veyra 的用户就是项目目录的所有者；攻击者是与用户同机的本地进程，可能并发创建/替换 junction、symlink、目录项。
2. 攻击者**不**拥有内核调试、文件系统过滤驱动、备份/还原特权或管理员级 `SeBackupPrivilege`；这些能力可以绕过大部分用户态共享锁和身份检查，不在本设计的保证范围内。
3. 只支持本地 NTFS/ReFS 等具备稳定文件 ID 的卷。网络盘、同步盘、FAT/exFAT、压缩/加密过滤驱动上的原子性和身份语义不保证；严格模式应拒绝或明确标记为不支持。
4. 不假设进程崩溃后目录项元数据一定持久化；Windows 上没有可移植的目录 `fsync` 等价物。文件内容通过 `FlushFileBuffers`/`os.fsync` 保证“已写入”，目录项持久性只做 best effort。
5. 本地用户可以直接修改 `creation/`、`decisions.ndjson` 和 `state.json`；Veyra 不提供密码学账本，也不承诺防篡改。身份检查只用于“不要把外部对象误当成自己的对象”，不是访问控制。
6. 3.2.2.1 的模型输出仍是不可信输入；模型不能提供绝对路径、shell 命令或任意文件内容而不经过契约校验。

### 1.5 术语

| 术语 | 含义 |
|---|---|
| 路径身份 | 词法规范化后的字符串路径；受大小写、短名、Unicode、尾点/尾空格影响 |
| 对象身份 | 卷 + 文件 ID（Windows `FileIdInfo`/`BY_HANDLE_FILE_INFORMATION`；POSIX `st_dev` + `st_ino`） |
| 快照 | 某一时刻对路径的 `lstat` 结果：存在性、类型、reparse 标记、对象身份、大小、哈希 |
| 目录租约（DirLease） | 一次显式目录创建/打开的结果；记录路径、对象身份、是否由本次操作创建、父租约 |
| 文件租约（FileLease） | 临时文件或已存在文件的打开句柄/身份记录；用于提交与清理 |
| 严格模式 | 使用句柄相对提交原语的模式；不能提供时拒绝写 |
| 兼容模式 | 仅路径 + 快照检查；**不作为 3.2.2.1 的写模式** |
| fail-closed | 无法确认对象身份、父目录或提交结果时，停止并保留现场，不猜测、不重试 |

---

## 2. 全写入链清单

### 2.1 盘点方法

本轮只读检查了以下生产文件，并对全仓库 `.py` 搜索了 `mkdir`、`open(..., "w"/"a")`、`write_text`、`write_bytes`、`os.replace`、`os.rename`、`unlink`、`rmdir`、`tempfile`、`zipfile`、`shutil` 等模式：

- `veyra_creation/store.py`（1340 行）
- `veyra_creation/digest.py`（442 行）
- `veyra_creation/cli.py`（188 行）
- `veyra_pack/contract.py`（195 行）
- `veyra_pack/paths.py`（372 行）
- `veyra_pack/exporter.py`（185 行）
- `veyra_pack/init_project.py`（110 行）
- `triage_agent.py`（1651 行，重点 1108–1262）

结论：生产代码中实际写入点集中在 9 个位置；`veyra_pack/validation.py`、`imaging.py`、`canonical.py`、`plan.py` 等只有读或只定义计划动作，没有直接写入。测试辅助代码和测试用例会写临时目录，但不属于生产写入链。

补充盘点边界：

- 当前生产代码**没有** backup、restore、recover、delete-file、delete-directory 的执行实现；`plan.py` 中的 `delete`/`copy` 只是计划动作字符串，`operation-executor-design.md` 中的备份/恢复是未来设计。因此本清单没有“现有 backup/recover 写入点”可列，但 §9 仍为它们预留同一边界。
- 当前生产代码**没有**跨文件事务、日志重放写入、数据库写入或网络文件传输；唯一追加日志是 `decisions.ndjson`。
- 当前生产代码**没有** `shutil.rmtree` / `shutil.copy` / `os.rename`；不要因为搜索不到这些 API 就认为风险只来自 `mkdir`。

### 2.2 写入点 W1：`ProjectStore.init()` 目录创建

| 项目 | 内容 |
|---|---|
| 函数 | `ProjectStore.init()`，`veyra_creation/store.py:274-395` |
| 操作类型 | 递归创建根目录；创建 `creation/`；创建 `creation/runs/` |
| 路径来源 | CLI 用户输入的项目根；`root` 由 `ProjectStore.__init__` 规范化并 resolve |
| 前置检查 | `:287 _assert_internal()`；`:288-301` 检查 root 类型、state 存在、creation 非空；`_assert_internal` 在 `:442` 对 `creation_dir` 做整链 `find_link_component` |
| 实际写入 API | `self.root.mkdir(parents=True, exist_ok=True)`（`:343`）；`self.creation_dir.mkdir(parents=True, exist_ok=True)`（`:345`）；`self.runs_dir.mkdir(parents=True, exist_ok=True)`（`:347`） |
| 路径身份保证 | 无。每次 `mkdir` 后调用 `_assert_no_link`（`:344/346/348`），但创建已经发生 |
| TOCTOU | 存在。`_assert_internal()` 之后到 `:343` 之间，父目录可被替换为 junction；受控实验 B 已确认可在项目外创建 `outside/project` |
| 失败行为 | 可能已在项目外创建目录；异常向上抛 `E_LINK_BOUNDARY` 或 `OSError` |
| 清理行为 | 见 W2；用布尔 `created_root` 和路径列表清理，可能残留或误删 |

补充：

- `created_root = not self.root.exists()`（`:340`）是在 `mkdir` 之前计算的，之后如果路径被替换，该布尔值不再代表“当前路径上的对象是我创建的”。
- `created: list[Path]`（`:341`）只记录路径，不记录对象身份。
- 即使 `exist_ok=True`，当 `root` 已经存在但其实是 junction 时，`mkdir` 可能成功或失败，取决于 Windows 语义；检查发生在之后，不能作为安全边界。

### 2.3 写入点 W2：`ProjectStore.init()` 失败清理

| 项目 | 内容 |
|---|---|
| 函数 | `ProjectStore.init()` 的 `except BaseException` 分支，`store.py:374-394` |
| 操作类型 | 删除已创建文件；删除 `runs/`、`creation/`、项目根 |
| 路径来源 | 内存中的 `created` 列表和固定的 `self.runs_dir` / `self.creation_dir` / `self.root` |
| 前置检查 | 无身份检查；仅捕获 `OSError` 后忽略 |
| 实际写入 API | `path.unlink()`（`:377`）、`self.runs_dir.rmdir()`（`:382`）、`self.creation_dir.rmdir()`（`:386`）、`self.root.rmdir()`（`:391`） |
| 路径身份保证 | 无。路径在失败后可能已经被替换 |
| TOCTOU | 存在。`unlink`/`rmdir` 可能在替换后的 junction 上执行 |
| 失败行为 | 非空目录导致 `rmdir` 失败，留下项目外文件（受控实验 C） |
| 清理行为 | 若项目根被替换为指向项目外的 junction，且目标为空目录，`root.rmdir()` 可能删除项目外既有空目录（受控实验 E） |

这是当前最危险的写入点，因为它同时具备“外部创建”和“外部删除”两种副作用。

### 2.4 写入点 W3：`write_json_atomic()` 及其调用链

| 项目 | 内容 |
|---|---|
| 函数 | `write_json_atomic(path: Path, payload: dict[str, Any])`，`veyra_pack/contract.py:162-190` |
| 操作类型 | 创建父目录；创建临时文件；写入；`os.replace`；失败清理临时文件 |
| 路径来源 | 所有 `ProjectStore` 内部路径（state/spec/plan/locks/placeholders/digest/run）；`init_pack` 的 manifest；以及间接调用者 |
| 前置检查 | 调用者可能先做 `_assert_no_link(path)`，但 `write_json_atomic` 自身不检查路径，只做 `path.parent.mkdir(...)` |
| 实际写入 API | `path.parent.mkdir(parents=True, exist_ok=True)`（`:174`）；`tempfile.mkstemp(...)`（`:176`）；`os.fdopen` 写入、`flush`、`os.fsync`（`:180-183`）；`os.replace(tmp_name, path)`（`:184`）；异常时 `os.unlink(tmp_name)`（`:187`） |
| 路径身份保证 | 无。临时文件父目录和最终目标父目录都按路径解析 |
| TOCTOU | 存在。调用者检查 `path` 后，`write_json_atomic` 内部的 `mkdir` 和 `replace` 之间可发生父目录替换 |
| 失败行为 | 可能把临时文件创建到项目外；`os.replace` 可能覆盖项目外同名文件；失败清理可能删除项目外临时路径 |
| 清理行为 | 只清理 `tmp_name` 路径，不核对临时文件身份 |

调用者：

- `ProjectStore._write_json()`（`store.py:444-446`）：先 `self._assert_no_link(path)`，再调用 `write_json_atomic`。
- `ProjectStore._commit_state()`（`:410-416`）。
- `save_spec()`、`confirm_spec()`、`save_plan()`、`approve_plan()`、`lock()`、`unlock()`、`start_run()`、`finish_run()` 等。
- `build_digest(write=True)`（`digest.py:236-240`）：先检查 `digest_path`，再直接调用 `write_json_atomic`。
- `init_pack()`（`init_project.py:109`）。

`write_json_atomic` 是当前所有 JSON 状态写入的唯一底层实现，但它不是安全边界：

```text
ProjectStore._write_json(path)
  -> _assert_no_link(path)                 # 时间点 T1
  -> write_json_atomic(path, payload)
       -> path.parent.mkdir(parents=True)  # 时间点 T2，可能跟随 junction
       -> mkstemp(dir=path.parent)
       -> os.replace(tmp_name, path)       # 时间点 T3，可能覆盖外部同名文件
```

`_assert_no_link` 只检查 T1；T2 和 T3 都没有再次绑定父目录对象。

### 2.5 写入点 W4：`decisions.ndjson` 追加

| 项目 | 内容 |
|---|---|
| 函数 | `ProjectStore._append_decision_raw()`，`store.py:883-921`；公开入口 `append_decision()`（`:864-881`） |
| 操作类型 | 打开并追加一行 JSON；flush + fsync |
| 路径来源 | 固定的 `self.decisions_path = creation/decisions.ndjson` |
| 前置检查 | `:913 self._assert_no_link(self.decisions_path, "decisions")`；没有父目录身份检查 |
| 实际写入 API | `self.decisions_path.open("a", encoding="utf-8", newline="\n")`（`:915`）；`handle.write(line + "\n")`；`flush`；`os.fsync`（`:917-918`） |
| 路径身份保证 | 无。打开的是路径，不是已验证的对象 |
| TOCTOU | 存在。检查与 `open` 之间 `creation/` 或 `decisions.ndjson` 可被替换为 junction |
| 失败行为 | 可能把一行事件追加到项目外文件；可能留下半行；可能创建意外的 `decisions.ndjson` |
| 清理行为 | 不清理；失败即 `E_DECISIONS_WRITE`，但不回滚已写入的字节 |

此外，`init()` 在 `:359` 用 `self.decisions_path.write_text("", encoding="utf-8")` 截断/创建日志：

- 没有 `open(..., "x")` 的排他创建；
- 没有对象身份检查；
- 若此时 `decisions.ndjson` 是外部文件的 junction，可能截断外部文件；
- 如果 `write_text("")` 已创建/截断文件、随后抛错，该路径尚未加入 `created` 列表（`:360` 才 append），失败清理不会处理它；
- 该写入与后续 `_append_decision_raw` 不是同一个安全层。

### 2.6 写入点 W5：`start_run()` 的 `runs/` 创建

| 项目 | 内容 |
|---|---|
| 函数 | `ProjectStore.start_run()`，`store.py:1076-1120` |
| 操作类型 | 递归创建 `creation/runs/`；随后写 run JSON |
| 路径来源 | 固定内部路径 |
| 前置检查 | 无目录链接检查；`runs_dir.mkdir(parents=True, exist_ok=True)` 后才 `_assert_no_link`（`:1097-1098`） |
| 实际写入 API | `self.runs_dir.mkdir(parents=True, exist_ok=True)`（`:1097`）；`_write_json`（`:1118`） |
| 路径身份保证 | 无 |
| TOCTOU | 存在。与 W1 同类；`runs/` 可在 `creation/` 被替换后创建到项目外 |
| 失败行为 | 可能创建外部 `runs/`；随后 `E_LINK_BOUNDARY` |
| 清理行为 | 无；失败不清理已创建的 `runs/` |

### 2.7 写入点 W6：`build_digest(write=True)`

| 项目 | 内容 |
|---|---|
| 函数 | `build_digest(store, *, write=False)`，`digest.py:83-241` |
| 操作类型 | 写 `creation/digest.json` |
| 路径来源 | 固定 `store.digest_path` |
| 前置检查 | `:237 store._assert_no_link(store.digest_path, "digest")` |
| 实际写入 API | `write_json_atomic(store.digest_path, payload)`（`:240`） |
| 路径身份保证 | 无；检查后仍可能被替换 |
| TOCTOU | 存在；与 W3 相同 |
| 失败行为 | 可能写外部 `digest.json`；`build_digest` 仍已计算完内存数据 |
| 清理行为 | 由 `write_json_atomic` 清理临时文件，无身份核对 |

### 2.8 写入点 W7：`veyra_pack.init_pack()`

| 项目 | 内容 |
|---|---|
| 函数 | `init_pack(destination, ...)`，`init_project.py:64-110` |
| 操作类型 | 创建目标目录；创建 `assets/`、`files/`；写 `pack.json` |
| 路径来源 | CLI 用户指定的 `destination` |
| 前置检查 | `:85-91` 仅检查存在性、是否目录、是否为空；**没有路径政策、链接检查或身份检查** |
| 实际写入 API | `destination.mkdir(parents=True, exist_ok=True)`（`:103`）；`(destination / "assets").mkdir(exist_ok=True)`（`:104`）；`(destination / "files").mkdir(exist_ok=True)`（`:105`）；`write_json_atomic(pack_path, manifest)`（`:109`） |
| 路径身份保证 | 无 |
| TOCTOU | 存在。目标父目录或目标本身可被替换；`pack_path.exists()`（`:107`）到 `write_json_atomic` 之间也可被替换 |
| 失败行为 | 可能创建项目外目录/文件；失败后留下半成品 skeleton，无回滚 |
| 清理行为 | 无 |

### 2.9 写入点 W8：`veyra_pack.export_pack()`

| 项目 | 内容 |
|---|---|
| 函数 | `export_pack(pack_root, output=None, *, overwrite=False, limits=None)`，`exporter.py:48-123` |
| 操作类型 | 创建输出父目录；创建临时 ZIP；写入 ZIP；校验；`os.replace`；失败清理临时文件 |
| 路径来源 | `pack_root` 与 CLI `-o/--output`；默认输出为当前目录的 `<pack_id>-<version>.zip` |
| 前置检查 | `:78 destination.resolve()`；`:79 is_within(destination, root)`；`:84-94` 若存在且未 `overwrite` 则拒绝；`_verify_archive` 在替换前校验 ZIP |
| 实际写入 API | `destination.parent.mkdir(parents=True, exist_ok=True)`（`:96`）；`tempfile.mkstemp(... dir=destination.parent)`（`:97-100`）；`zipfile.ZipFile(temp_path, "w")`（`:103-109`）；`os.replace(temp_path, destination)`（`:111`）；失败 `temp_path.unlink()`（`:112-117`） |
| 路径身份保证 | 无。`destination.resolve()` 会跟随链接；`temp_path` 和 `destination` 都是路径 |
| TOCTOU | 存在。`exists()` 检查后到 `os.replace` 之间，输出文件可被创建/替换；父目录可被替换为 junction；`os.replace` 可能覆盖项目外文件 |
| 失败行为 | 可能写出项目外 ZIP；临时文件清理可能删到外部同名路径；输出父目录可能被创建到外部 |
| 清理行为 | 只按 `temp_path` 路径 `unlink`，不核对身份 |

说明：导出目标本来就可以在项目根之外（用户显式选择输出位置），所以“项目根边界”不适用于输出文件本身；适用的是“用户选择的输出父目录 + 目标对象身份”。即使如此，junction 替换仍可使 `destination` 指向用户未选择的目录。

### 2.10 写入点 W9：`triage_agent.write_report_text()`

| 项目 | 内容 |
|---|---|
| 函数 | `write_report_text(path, text)`，`triage_agent.py:1120-1157`；`write_contract_json()` 在 `:1254-1262` 复用它 |
| 操作类型 | 创建输出父目录；创建临时文件；写入文本/JSON；`os.replace`；失败清理 |
| 路径来源 | CLI `-o/--output` 或默认 `REPORT_PATH` |
| 前置检查 | `_paths_refer_to_same_file()`（`:1108-1117`）拒绝输入输出同文件；无链接/身份检查 |
| 实际写入 API | `parent.mkdir(parents=True, exist_ok=True)`（`:1129`）；`tempfile.mkstemp(prefix=".veyra-report-", ... dir=parent)`（`:1137-1139`）；`fdopen` 写入、`flush`、`fsync`（`:1141-1144`）；`os.replace(tmp_path, target)`（`:1146`）；失败 `unlink`（`:1152-1157`） |
| 路径身份保证 | 无。`_paths_refer_to_same_file` 只在两个路径都存在时使用 `samefile`，否则使用 `realpath`；它是“是否同一文件”的启发式，不是写入身份绑定 |
| TOCTOU | 存在。输出父目录可被替换；目标可在检查后被替换为链接或另一个文件 |
| 失败行为 | 可能写项目外文件（输出路径由用户选择，但可能不是用户以为的目录）；可能覆盖外部同名文件 |
| 清理行为 | 只按 `tmp_path` 路径 `unlink`，不核对身份 |

说明：`triage_agent.py` 是独立 CLI，输出路径本来就可以在任意位置。加固目标不是“限制在项目根内”，而是“用户指定的输出目录/文件在提交时仍是同一个对象”，并且不因父目录 junction 被重定向到另一个位置。

### 2.11 写入点汇总

| 编号 | 位置 | 操作 | 主要风险 | 是否统一层 |
|---|---|---|---|---|
| W1 | `store.py:343-348` | `mkdir(parents=True)` ×3 | 外部目录创建 | 否 |
| W2 | `store.py:374-394` | `unlink` / `rmdir` | 外部删除、残留 | 否 |
| W3 | `contract.py:162-190` | 隐式父目录、临时文件、`os.replace` | 外部写入/覆盖 | 否 |
| W4 | `store.py:359, 913-918` | `write_text("")`、`open("a")` | 外部截断/追加 | 否 |
| W5 | `store.py:1097` | `mkdir(parents=True)` | 外部目录创建 | 否 |
| W6 | `digest.py:237-240` | JSON 原子写 | 与 W3 相同 | 否 |
| W7 | `init_project.py:103-109` | 目录 + manifest | 无路径检查、半成品 | 否 |
| W8 | `exporter.py:96-117` | 目录、ZIP、`replace` | 外部覆盖、输出对象替换 | 否 |
| W9 | `triage_agent.py:1129-1157` | 目录、文本、`replace` | 外部覆盖、输出对象替换 | 否 |

---

## 3. 当前风险和调用链

### 3.1 `init()` 的竞态窗口

已由 Phase 3.2.2.0.5 的受控实验确认：

```text
ProjectStore(root)                         # 构造时检查声明路径和 resolve 路径
  -> init()
     -> _assert_internal()                 # store.py:287，检查 creation_dir
     -> root.exists() / state.exists() ... # store.py:288-301
     -> creation_existed / created_root    # store.py:339-341
     -> root.mkdir(parents=True)           # store.py:343  <-- 父目录可已被替换
     -> _assert_no_link(root)              # store.py:344  <-- 太晚
```

时间窗口在 `_assert_internal()` 返回之后到 `mkdir` 之间。受控实验 B 使用真实 junction，在检查后、创建前替换父目录，`mkdir(parents=True)` 在项目外创建了 `outside/project`，随后才由 `:344` 报 `E_LINK_BOUNDARY`。

这不是“理论上的极小概率”问题，而是“检查与使用之间没有对象绑定”的结构性问题。重复检查只能缩小窗口，不能证明窗口不存在。

### 3.2 清理逻辑的二次副作用

失败清理假设：

```python
created_root = not self.root.exists()   # 创建前
created = []                            # 只记录路径
...
if created_root:
    self.root.rmdir()                   # 创建失败后按路径删除
```

它没有记录：

- 我创建的根目录的对象身份；
- 当前 `self.root` 是否还是那个对象；
- `self.root` 是否已被替换为 junction；
- 目标目录是否为空、是否包含别人创建的文件。

因此可能出现：

1. 我创建了根目录，但后来父目录被替换为 junction；`root.rmdir()` 删除项目外既有空目录（实验 E）。
2. 项目外有并发文件，`root.rmdir()` 失败，留下外部文件（实验 C）。
3. 清理 `unlink` 时，`created` 中的文件名在外部同名路径上存在，可能删除外部文件。
4. `created_root=False` 时，清理不会删除根目录，看似安全；但其他 `unlink` / `rmdir` 仍可能越界。

**结论：清理必须基于对象身份，而不是布尔值或路径列表。默认不应自动删除目录。**

### 3.3 `write_json_atomic()` 的隐式父目录

`write_json_atomic()` 同时承担三件事：

1. 创建父目录；
2. JSON 序列化；
3. 原子替换。

这三件事的安全前提不同，混在一起导致：

- 调用者无法区分“父目录不存在”是可恢复的输入错误，还是应该自动创建；
- 低层函数隐式扩大了写入范围；
- 调用者的 `_assert_no_link` 检查发生在函数外，函数内部又产生新的路径使用；
- 测试无法只验证“原子替换”而不触发目录创建。

**建议：`write_json_atomic()` 只负责“父目录已存在且已验证”的原子写；父目录创建必须由显式的 `ensure_parent()` 完成。**

### 3.4 `decisions.ndjson` 追加的边界

追加日志不是普通 JSON 文件：

- 不能先写临时文件再 `os.replace`，否则会破坏“追加”语义；
- 必须使用追加句柄，并保证“打开的那个文件就是日志文件”；
- 必须保证一行事件要么完整写入，要么被识别为损坏，不能静默截断；
- 如果日志文件被替换为 junction，`open("a")` 会跟随到外部。

当前实现只做了 `_assert_no_link`，没有句柄身份核对。即使使用严格句柄相对打开，仍需要：

1. 以 `FILE_APPEND_DATA` / `O_APPEND` 打开；
2. 记录打开前后的对象身份；
3. 写入并 `fsync`/`FlushFileBuffers`；
4. 再次核对目录项身份；
5. 若目录项已变，标记 `E_DECISIONS_RACE`，停止后续写，不尝试删除已写内容。

### 3.5 `init_pack()` 的无边界创建

`init_pack()` 的输入是 CLI 用户路径，没有经过 `ProjectStore` 的路径政策。它只检查：

- 目标存在时必须是目录；
- 目标存在时必须为空。

然后直接 `mkdir(parents=True)` 并写 manifest。风险包括：

- 目标父目录是 junction 时，`assets/`、`files/`、`pack.json` 可能创建到外部；
- 目标存在但为空时，快照与创建之间可被替换；
- `pack_path.exists()` 检查后到 `write_json_atomic` 之间可被替换；
- 写失败时留下 `assets/`、`files/` 或半 initialized 目录，没有回滚；
- `destination` 可由用户选择，但仍不应被 junction 重定向到另一个位置。

### 3.6 `export_pack()` 的输出替换

`export_pack()` 的 `destination.resolve()` 会跟随链接：

```python
destination = Path(output).resolve()
if is_within(destination, root): ...
if destination.exists() and not overwrite: ...
destination.parent.mkdir(parents=True, exist_ok=True)
...
os.replace(temp_path, destination)
```

风险：

- `resolve()` 可能在检查前就把 junction 目标展开；用户以为写的是 `out/kit.zip`，实际可能写的是 `other/kit.zip`；
- `destination.parent.mkdir(parents=True)` 可能沿 junction 创建外部目录；
- `exists()` 检查与 `os.replace` 之间，目标可被创建；`overwrite=False` 的拒绝可能被绕过；
- `os.replace` 没有核对目标身份；如果目标已被替换为另一个文件，可能覆盖用户文件；
- 失败清理按 `temp_path` 路径 `unlink`，如果临时路径被替换，可能删除外部文件。

### 3.7 `write_report_text()` 的输出覆盖

`triage_agent.py` 的输出路径是用户显式选择的，通常允许覆盖 `report.md`。风险不是“允许覆盖”，而是：

- `parent.mkdir(parents=True)` 可能沿 junction 创建目录；
- 目标已存在时，`os.replace` 无条件覆盖；
- 输入输出同文件检查使用 `samefile`/`realpath`，不是写入时的对象绑定；
- 输出父目录在检查后被替换，`os.replace` 可能把报告写到另一个目录；
- 失败清理按路径删除临时文件，可能误删外部同名文件。

### 3.8 并发与锁

当前 `ProjectStore` 没有跨进程写锁：

- 两个 `init` 可以同时通过 `state_path.exists()`，然后交错写；
- 两个 `append_decision` 可以同时读旧日志、分配 event_id、追加；
- 两个 `build_digest(write=True)` 可以同时替换 `digest.json`；
- 清理逻辑可以在另一个进程写入时删除目录。

未来设计的 `creation/executor.lock` 只覆盖 executor，不覆盖现有 `init` / `save_spec` / `append_decision` / `digest`。文件安全层不能替代并发控制；但至少应保证“不把另一个进程刚创建的文件当成自己的临时文件清理”。

### 3.9 Python/Windows 兼容性缺口

当前 `is_link_like()`（`paths.py:86-105`）先调用 `path.is_symlink()`，再检测 `os.path.isjunction`：

```python
isjunction = getattr(os.path, "isjunction", None)
if isjunction is not None:
    ...
```

问题：

- `os.path.isjunction` 是 Python 3.12 新增的；在 Python 3.10/3.11 上不存在；
- 如果运行在 3.10/3.11，`isjunction` 为 `None`，junction **不会被检测**，除非 `path.is_symlink()` 对 junction 返回真（Windows 上通常不返回）；
- 这与项目声称的 Python 3.10+ 兼容不一致，是一个需要优先修复的兼容性缺口；
- 同一缺口也影响 `digest._iter_project_files()` 的读取/遍历：旧 Python 上 junction 可能被当成普通目录遍历或普通文件读取，导致 digest 包含项目外内容；这不是写入，但会污染证据/摘要，H0 必须一起修复。

建议的兼容检测：

1. `lstat`，读取 `st_file_attributes`；
2. 若含 `stat.FILE_ATTRIBUTE_REPARSE_POINT`，再读取 `st_reparse_tag`；
3. 已知 junction 标签 `IO_REPARSE_TAG_MOUNT_POINT`（0xA0000003）和 symlink 标签 `IO_REPARSE_TAG_SYMLINK`（0xA000000C）都拒绝；
4. 未知 reparse tag 一律拒绝（fail-closed）；
5. 若解释器缺少 `st_reparse_tag` 或 `st_file_attributes`，无法确认时拒绝，而不是当成普通目录；
6. 不能只依赖 `Path.is_symlink()`。

本轮能力探针（Windows / Python 3.15.0）：

```text
O_NOFOLLOW = None
os.supports_dir_fd = []
os.supports_follow_symlinks = {'chmod', 'stat'}
os.path.isjunction = <built-in function _path_isjunction>
stat.IO_REPARSE_TAG_MOUNT_POINT = 2684354563
stat.FILE_ATTRIBUTE_REPARSE_POINT = 1024
```

这些结果表明：

- Windows Python 标准库没有 `dir_fd` 相对操作；
- 没有 `O_NOFOLLOW`；
- 不能指望用 `os.open` 的 `O_NOFOLLOW` 解决最终组件链接；
- 必须自己做 reparse point 检测，或使用原生句柄 API。

---

## 4. Windows 文件系统安全能力分析

### 4.1 路径 API 能保证什么

`Path.mkdir()`、`Path.open()`、`os.replace()`、`shutil`、`zipfile` 等最终都通过路径解析：

1. 从卷根开始逐段解析；
2. 遇到 reparse point 时，除非显式要求打开 reparse point 本身，否则跟随；
3. 在解析过程中，其他进程可以重命名、删除、替换目录项；
4. API 返回后，调用者只知道“这个路径当时指向某个对象”，不知道下一次路径解析会指向谁。

因此：

- `exists()` / `is_dir()` / `is_symlink()` / `resolve()` 都是**时间点观察**；
- 重复调用只能缩短窗口；
- 路径检查不能作为“对象身份”的证明；
- `Path.resolve()` 不是安全函数；它会跟随链接，且可能把不存在/悬空链接折叠成意外路径。

### 4.2 reparse point 与 junction

Windows 的 junction 和 symlink 都是 reparse point：

| 类型 | reparse tag | 典型创建方式 |
|---|---|---|
| Junction（mount point） | `IO_REPARSE_TAG_MOUNT_POINT` = 0xA0000003 | `cmd /c mklink /J`，无需管理员 |
| Symbolic link | `IO_REPARSE_TAG_SYMLINK` = 0xA000000C | `mklink` / `os.symlink`，通常需要权限或开发者模式 |
| 其他 reparse point | 其他 tag | 云同步、去重、WIM、过滤驱动等 |

安全规则：

- 任何 reparse point 都不应被当作普通目录/文件；
- 不能只拒绝“symlink”；junction 是更常见、更容易创建的绕过方式；
- 未知 tag 也应拒绝，因为其语义不可验证；
- 检测必须在**实际打开的对象**上做，而不是只在路径字符串上做；
- `GetFileInformationByHandleEx(FileAttributeTagInfo)` 可以读取已打开句柄的 attributes 和 reparse tag；
- `FILE_FLAG_OPEN_REPARSE_POINT` 让 `CreateFileW` 打开 reparse point 本身，而不是目标；这是检查链接对象的关键。

### 4.3 `CreateFileW` 与目录句柄

打开目录：

```text
CreateFileW(
  path,
  FILE_READ_ATTRIBUTES | FILE_LIST_DIRECTORY | SYNCHRONIZE,
  FILE_SHARE_READ | FILE_SHARE_WRITE,        // 不共享删除
  NULL,
  OPEN_EXISTING,
  FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
  NULL
)
```

要点：

- `FILE_FLAG_BACKUP_SEMANTICS` 是打开目录句柄的必要标志；
- `FILE_FLAG_OPEN_REPARSE_POINT` 只保证**最终组件**按 reparse point 本身打开；它不会阻止祖先目录在解析过程中被替换。打开项目根/父目录时，必须从卷根或受信锚点逐级相对打开，或明确要求父目录由用户预先创建并验证；否则祖先 junction 仍可把“根”重定向到别处；
- `FILE_SHARE_DELETE` 不设置时，其他进程通常不能在本进程持有句柄期间重命名/删除该目录；这能提供“钉住对象”的效果，但不是绝对安全边界（已有句柄、备份语义、管理员/过滤驱动可绕过）；
- 句柄本身不是路径；`GetFileInformationByHandleEx(FileIdInfo)` 可以读取卷序列号和 128 位文件 ID；
- `GetFinalPathNameByHandleW` 可以读出句柄对应的最终路径，但它只用于诊断/比对，不是“未来路径解析”的保证；
- 持有目录句柄并不能让 `CreateFileW("parent\\child")` 相对该句柄解析；Win32 没有 `RootDirectory` 参数。

### 4.4 句柄相对操作：Win32 与 NT API 的边界

这是本设计最关键的技术判断。

**Win32 公开 API：**

| API | 是否支持相对目录句柄 | 结论 |
|---|---|---|
| `CreateFileW` | 否 | 总是按完整路径解析；不能绑定已验证父目录 |
| `CreateFile2` | 否 | 同上，仅增加扩展参数 |
| `ReplaceFileW` | 否 | 路径替换；不提供句柄相对语义，且不适用于创建 |
| `MoveFileExW` | 否 | 路径重命名；可被父目录替换影响 |
| `CopyFile2` | 否 | 路径复制；不解决目标身份 |
| `SetFileInformationByHandle` | 部分 | 可对**已打开句柄**做 rename/delete；`FILE_RENAME_INFO` 结构包含 `RootDirectory` 字段，但其相对语义必须在目标 Windows 版本实测确认；且没有“相对目录句柄创建新文件”的公开 Win32 API |
| `GetFileInformationByHandleEx` | 是（查询） | 可读取句柄身份、attributes、reparse tag |

因此：

- 只使用公开 Win32 API 时，**无法**在“创建临时文件”这一步绑定父目录对象；
- `SetFileInformationByHandle` 可以把“移动临时文件到目标”绑定到父句柄，但临时文件本身已经按路径创建；
- 这不足以满足“项目外零写入”的严格门槛。

**NT 原生 API：**

- `NtCreateFile` 的 `OBJECT_ATTRIBUTES` 有 `RootDirectory` 字段；
- `ObjectName` 可以是相对该目录句柄的 `UNICODE_STRING`；必须显式设置 `OBJ_CASE_INSENSITIVE`，否则会偏离 Windows 的常规大小写不敏感语义，产生同名不同对象；
- `CreateOptions` 可带 `FILE_OPEN_REPARSE_POINT`，检查/打开 reparse point 本身；
- `CreateDisposition` 可区分 `FILE_CREATE`（目标存在则失败）、`FILE_OPEN`、`FILE_OVERWRITE_IF` 等；
- `NtSetInformationFile` 的 `FileRenameInformation` 支持 `RootDirectory` + 相对名，`FileDispositionInformation` 支持按句柄删除；
- 这是目前可用的、能在用户态把创建/重命名/删除绑定到已验证目录句柄的**原生原语组合**；不排除其他未文档化的 NT 路径，但不应依赖未验证的替代品。

代价：

- `ntdll` 不是稳定的公开应用 API；结构体布局、状态码、信息类编号在不同 Windows 版本/架构上需要验证；
- 需要 `ctypes` 定义 `UNICODE_STRING`、`OBJECT_ATTRIBUTES`、`IO_STATUS_BLOCK`、`FILE_RENAME_INFORMATION` 等结构；
- `UNICODE_STRING.Length` 是字节数，不是字符数；32/64 位对齐和 `WCHAR` 缓冲区生命周期容易出错；
- NTSTATUS 需要 `RtlNtStatusToDosError` 映射；未知状态必须 fail-closed；
- 杀毒/EDR/文件系统过滤驱动可能改变行为；
- 需要为 POSIX 提供不同的 `openat` 后端，否则跨平台不可用；
- 测试必须覆盖真实 junction、真实句柄、失败注入和崩溃，不是只 mock 路径。

**结论：**

- 如果任务书 §8 的“项目外路径不能因 junction 替换而产生写入”是硬门槛，则必须接受 `NtCreateFile`/`NtSetInformationFile` 的 ABI 风险，或者把写操作限制在一个由用户预先创建并受 ACL 保护的目录中（仍不能证明零外部写入）。
- 如果产品不接受 `ntdll`，则应明确把门槛降级为“不能静默/未检测地写到项目外；强制竞态下可能写出，但必须报告并留下可审计证据”。这需要用户书面确认，不能由实现者自行决定。

### 4.5 文件身份

Windows 可用身份：

| 来源 | 内容 | 适用 |
|---|---|---|
| `GetFileInformationByHandle` | `dwVolumeSerialNumber` + `nFileIndexHigh/Low`（64 位） | 传统、兼容性好 |
| `GetFileInformationByHandleEx(FileIdInfo)` | 卷序列号 + 128 位 file ID | 推荐；ReFS 等 |
| `os.stat` / `os.lstat` | `st_dev` + `st_ino`；`st_file_attributes`；`st_reparse_tag` | Python 便捷路径；需运行时检测 |
| `Path.samefile` | 底层文件 ID 比较 | 只用于已存在文件；不能用于尚不存在的目标 |

限制：

- FAT/exFAT 可能没有稳定 file ID；`st_ino` 可能为 0；
- 网络共享/同步盘可能复用或伪造 ID；
- 文件 ID 只在卷内唯一；必须同时比较卷序列号；
- 文件 ID 证明“同一个对象”，不证明“内容未变”；内容仍需 SHA-256；
- 目录项被删除后重建，即使同名，file ID 也不同；这应被视为“不同对象”；
- 句柄身份比路径 `stat` 更强，因为句柄在打开期间钉住对象；但仍需防止句柄被复制/继承。

### 4.6 Python 标准库能力（Windows）

| 能力 | 当前 Python 3.15 | 3.10/3.11 注意事项 |
|---|---|---|
| `os.open(..., dir_fd=...)` | 不支持（`supports_dir_fd=[]`） | 不能依赖 |
| `O_NOFOLLOW` | 不存在 | 不能依赖 |
| `os.path.isjunction` | 存在 | 3.12 新增；3.10/3.11 不能假设存在 |
| `st_file_attributes` | 存在 | 需检测 |
| `st_reparse_tag` | 存在 | 需检测；缺失时 fail-closed |
| `os.replace` | 存在 | 路径 API，受父目录替换影响 |
| `os.link` | NTFS 上可用 | FAT/exFAT 可能失败；失败必须 fail-closed |
| `ctypes` | 标准库 | 可用，但需要自行承担 ABI 风险 |

因此，严格模式不能建立在“Python 标准库已经提供安全相对操作”的假设上；它必须有一个显式的原生后端，且在没有后端时拒绝写。

### 4.7 保证矩阵

| 机制 | 防止检查时已存在的链接 | 防止检查后被替换 | 防止清理误删 | 证明目标内容 | 跨平台 |
|---|---:|---:|---:|---:|---:|
| `Path.exists()` / `is_symlink()` | 部分 | 否 | 否 | 否 | 是 |
| 逐级 `lstat` + 重复检查 | 是 | 缩小窗口，不能证明 | 否 | 否 | 是 |
| `Path.resolve()` + `is_within` | 部分 | 否 | 否 | 否 | 是 |
| `CreateFileW` 目录句柄 + 身份 | 是 | 钉住已打开对象，但路径创建仍可能被祖先替换 | 可用于删除前核对 | 否 | Windows |
| `NtCreateFile` 相对父句柄 | 是 | 是（在句柄链内） | 是（按句柄） | 否 | Windows |
| POSIX `openat`/`renameat` | 是 | 是（在 dirfd 内） | 是（按 dirfd） | 否 | POSIX |
| SHA-256 | 否 | 否 | 否 | 是 | 是 |

**没有任何单一机制同时解决路径、身份、内容和并发；必须分层。**

---

## 5. 四种方案比较

### 5.1 方案 A：纯 Python 逐级检查与创建

做法：

- 所有路径先 `check_relative_path` + `is_creation_path`；
- 从根开始逐段 `lstat`，拒绝任何 reparse point；
- 每级 `mkdir()` 一次，不用 `parents=True`；
- 每次创建后重新 `lstat`，核对父目录未变；
- 临时文件同目录；
- 提交前重新检查目标；
- 清理前重新检查路径和类型。

能防住：

- 词法穿越、绝对路径、盘符、UNC、ADS、尾点/尾空格、保留名；
- 检查时已经存在的 symlink/junction；
- `parents=True` 导致的隐式深层创建；
- 明显的“先检查后创建”错误（通过减少窗口）；
- 清理时误删明显的非空/类型不符对象（若加上身份检查）。

不能防住：

- 检查与 `mkdir`/`open`/`replace` 之间的父目录替换；
- 祖先目录被替换后，路径解析到另一个真实目录；
- 强制交错下的外部写入；
- 清理前路径被替换（仅靠路径检查）；
- 网络/FAT 语义。

TOCTOU：仍存在，只能缩小。

Python 3.10+ 兼容：是。

Windows 版本兼容：是。

依赖：无。

复杂度：低到中。

测试难度：中；需要真实 junction 和故障注入，但不需要原生 API。

对现有 API 的影响：中；`write_json_atomic` 要移除隐式父目录，调用者要显式创建。

对未来 executor 的影响：可作为策略层，但不能单独满足严格门槛。

### 5.2 方案 B：Python + Windows 原生目录句柄

做法：

- `CreateFileW` 打开项目根/父目录句柄（`FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT`，不共享删除）；
- 用 `GetFileInformationByHandleEx` 核对身份和 reparse tag；
- 用 `NtCreateFile` 相对父句柄创建临时文件/子目录；
- 用 `NtSetInformationFile` 相对父句柄重命名/删除；
- 所有清理按句柄对象身份，不按路径；
- POSIX 用 `openat` 族实现同一接口。

能防住：

- 检查后父目录被替换为 junction/symlink；
- 临时文件被创建到项目外；
- 提交时目标被替换为另一个对象（通过句柄/身份/目标谓词）；
- 清理误删外部目录（按句柄/身份）；
- 大部分路径 TOCTOU。

不能防住：

- 管理员/备份特权/内核过滤驱动/恶意文件系统；
- 网络盘/同步盘语义；
- 进程崩溃后的目录项持久性；
- 已打开的旧句柄被复制/继承；
- `ntdll` 行为差异/未验证的 Windows 版本；
- 内容正确性（仍需 SHA-256）。

TOCTOU：在句柄链内基本消除路径替换；仍存在“打开根之前的路径替换”和“外部进程持有句柄”的窗口，需要根身份/锁/审计。

Python 3.10+ 兼容：Windows 上可用 `ctypes`；POSIX 需 `openat`，但 Windows 标准库无 `dir_fd`。

Windows 版本兼容：Windows 10/11 原则可用；必须实测目标版本，未知行为 fail-closed。

依赖：标准库 `ctypes`；无第三方。

复杂度：高。

测试难度：高；需要真实 junction、句柄、故障注入、子进程崩溃；需要 32/64 位和不同 Windows 版本。

对现有 API 的影响：高；需要新的 `SafeWriter`/后端接口。

对未来 executor 的影响：可直接作为严格提交层。

### 5.3 方案 C：限制父目录必须预先存在

做法：

- 所有写操作要求目标父目录已经存在且已验证；
- 禁止任何递归创建（包括 `init` 的 `parents=True`）；
- 用户必须预先创建项目根/输出目录；
- 写入只做文件级 create/replace/delete；
- 清理默认不删除目录；
- 配合 A 的逐级检查和身份快照。

能防住：

- 大部分隐式父目录创建；
- 通过缺失父目录触发 junction 的路径；
- 清理误删目录（因为不删目录）；
- 明显的外部目录创建。

不能防住：

- 已存在父目录在检查后被替换；
- 最终文件的父目录被替换；
- `os.replace` 覆盖外部文件；
- 外部并发修改。

TOCTOU：仍存在；只是缩小了攻击面。

Python 3.10+ 兼容：是。

Windows 版本兼容：是。

依赖：无。

复杂度：低。

测试难度：低到中。

对现有 API 的影响：中到高；`init` 行为变化（需要父目录存在），可能影响用户习惯。

对未来 executor 的影响：可作为最低门槛，但不能满足严格 gate 2。

### 5.4 方案 D：混合方案

做法：

- 策略层：A 的路径检查 + C 的“默认不递归创建” + 对象身份快照；
- 提交层：在 Windows 上使用 B 的句柄相对操作；POSIX 使用 `openat` 族；
- 如果没有严格后端：写操作 fail-closed，不静默退回 A；
- 清理：身份匹配 + 默认不删目录；
- 备份/恢复：复用同一提交层；
- 审计：每个操作记录父/目标身份、临时文件身份、提交结果。

能防住：

- A/C 能防住的所有问题；
- 句柄链内的父目录替换；
- 临时文件/备份/恢复的越界；
- 清理误删；
- 目标对象替换（配合身份 + 哈希）。

不能防住：

- 特权攻击者/过滤驱动/网络盘；
- 打开根之前或根对象本身的替换（需要根身份/ACL/锁）；
- 进程崩溃后的目录项持久性；
- `ntdll` ABI 差异；
- 外部进程在操作后修改文件（只能检测）。

TOCTOU：在严格后端内基本消除；根打开前仍有窗口；必须明确说明。

Python 3.10+ 兼容：Windows 严格后端 + POSIX `openat`；无后端则拒绝写。

Windows 版本兼容：需要实测；不支持时 fail-closed。

依赖：标准库 `ctypes`；无第三方。

复杂度：中到高。

测试难度：高。

对现有 API 的影响：高，但可分阶段适配。

对未来 executor 的影响：最佳；`SafeWriter` 直接复用。

### 5.5 比较总表

| 维度 | A 纯 Python | B 原生句柄 | C 父目录预存在 | D 混合 |
|---|---|---|---|---|
| 防已存在链接 | 是 | 是 | 是 | 是 |
| 防检查后父目录替换 | 否（仅缩小） | 是（句柄内） | 否 | 是（严格后端） |
| 防临时文件越界 | 否 | 是 | 否 | 是 |
| 防清理误删 | 需额外身份 | 是 | 默认不删目录 | 是 |
| 满足 gate 2 | 否 | 是 | 否 | 是（严格后端） |
| TOCTOU 声明 | 残余 | 句柄内基本消除，根打开前仍有 | 残余 | 句柄内基本消除，根打开前仍有 |
| Python 3.10+ | 是 | Windows 是 | 是 | 是（无后端则拒绝） |
| 第三方依赖 | 无 | 无（ctypes） | 无 | 无（ctypes） |
| 复杂度 | 低 | 高 | 低 | 中高 |
| 测试难度 | 中 | 高 | 低中 | 高 |
| 现有 API 影响 | 中 | 高 | 中高 | 高 |
| executor 适用 | 策略层 | 提交层 | 最低门槛 | 推荐 |

### 5.6 推荐

**推荐方案 D，且严格模式必须包含方案 B 的句柄相对提交层。**

理由：

1. A/C 不能诚实地满足“项目外路径不能因 junction 替换而产生写入”的准入条件；重复路径检查不是证明。
2. B 单独缺少策略、配额、审计、备份和恢复；D 把 B 限制在提交层，避免原生代码扩散。
3. D 可以在没有严格后端的平台上 fail-closed，而不是悄悄退回不安全的 A。
4. 现有代码可以逐步迁移：先统一 `write_json_atomic` 和 `init`，再迁移 `init_pack`/`export_pack`/`triage_agent`，最后接入 executor。
5. `ctypes` 是标准库，不违反“不引入不必要第三方依赖”；但 `ntdll` 的 ABI 风险必须由项目明确接受，不能隐式引入。

如果用户/架构审阅拒绝 `ntdll`，则只能选择 **D 的兼容模式**，并把任务书 §8 gate 2 明确降级；此时不得把 3.2.2.1 标记为“满足严格写边界”。

---

## 6. 推荐架构

### 6.1 分层

```text
调用者（CLI / ProjectStore / init_pack / exporter / triage_agent / future executor）
   │
   ├─ 1. 策略层（纯 Python）
   │     - check_relative_path / is_creation_path / Plan 绑定
   │     - 目标快照、配额、风险等级、审批
   │     - 不直接写文件
   │
   ├─ 2. 边界层（SafeFs / WriteBoundary）
   │     - 根身份、父目录租约、路径快照
   │     - 选择严格/兼容后端；严格不可用则拒绝
   │     - 生成操作计划（temp、backup、commit）
   │
   ├─ 3. 提交层（严格后端 / 兼容后端）
   │     - Windows: CreateFileW + NtCreateFile + NtSetInformationFile
   │     - POSIX: openat / renameat / unlinkat
   │     - 兼容后端：仅用于只读/诊断，不用于 3.2.2.1 写
   │
   └─ 4. 审计层
         - decisions.ndjson 追加
         - 操作结果、清理结果、残余风险
```

### 6.2 模块建议

建议新增一个仅依赖标准库的共享模块，例如 `veyra_safeio.py`（放在仓库根，供 `triage_agent.py`、`veyra_creation`、`veyra_pack` 共同导入）：

- 为什么不放在 `veyra_creation`：`triage_agent.py` 是独立 CLI，不应依赖创建工具；
- 为什么不放在 `veyra_pack`：`triage_agent.py` 目前不依赖 pack，强行引入会扩大耦合；
- 为什么不用第三方：无必要；`ctypes` 已足够；
- 避免循环导入：模块只依赖 `pathlib`、`os`、`stat`、`ctypes`、`tempfile`、`hashlib`，不导入 `veyra_creation` / `veyra_pack`。

模块内部建议拆分（可以是同一文件内的类）：

| 组件 | 职责 |
|---|---|
| `NodeKind` | 文件/目录/其他/reparse/未知 |
| `FileIdentity` | 卷 ID + 文件 ID + kind + reparse tag |
| `PathSnapshot` | 路径、lstat、身份、存在性、大小、哈希（按需） |
| `DirLease` | 父路径、身份、句柄（严格模式）、是否本次创建、父租约 |
| `FileLease` | 打开的文件句柄/身份/临时文件标记 |
| `WriteBoundary` | 根、后端、快照、ensure_dir、atomic_write、delete、append |
| `CommitBackend` | 严格/兼容后端的接口 |
| `SafeFsError` | 稳定错误码，不泄漏平台异常 |

### 6.3 建议 API（设计签名，不是本轮实现）

```python
class WriteBoundary:
    def __init__(self, root: Path, *, backend: str = "strict"): ...

    def snapshot(self, relative: str) -> PathSnapshot: ...
    def open_parent(self, relative_parent: str) -> DirLease: ...
    def ensure_dir(self, relative: str, *, create: bool = False) -> DirLease: ...

    def atomic_write(
        self,
        relative: str,
        data: bytes,
        *,
        mode: str,                         # "create" | "replace"
        expected_absent: bool = False,
        expected_sha256: str | None = None,
        create_parents: bool = False,      # 仅显式，且逐级
    ) -> WriteReceipt: ...

    def delete(
        self,
        relative: str,
        *,
        expected_sha256: str,
        backup: BackupSink | None = None,
    ) -> DeleteReceipt: ...

    def append_line(self, relative: str, line: bytes) -> AppendReceipt: ...
    def cleanup_temp(self, lease: FileLease) -> CleanupReceipt: ...
```

约束：

- `mode="create"` 必须使用排他创建；目标存在即失败；
- `mode="replace"` 必须提供 `expected_sha256`（或明确的“用户已确认覆盖”记录）；
- `create_parents=True` 只能由显式调用者设置，且必须逐级创建、逐级核对；不能从 `write_json_atomic` 隐式传入；
- `atomic_write` 不接受目录作为目标；
- `delete` 默认必须先备份；没有备份实现时拒绝 delete；
- `append_line` 只用于 append-only 日志，不能用 `replace` 模拟；
- 所有返回值包含 `receipt`（身份、哈希、临时文件、提交结果），便于审计和恢复；
- 所有异常使用稳定 `SafeFsError.code`，不把 `WinError` 原样泄漏给模型。

### 6.4 路径创建

**原则：默认不递归创建；每次只创建一级；每级都从已验证父租约出发。**

`ensure_dir(relative, create=False)`：

1. `check_relative_path` + `is_creation_path`（如果调用者要求）；
2. 从根租约开始，按组件逐级：
   - 严格模式：`NtCreateFile`/`CreateFileW` 打开子目录，`FILE_OPEN_REPARSE_POINT`，核对不是 reparse；
   - 兼容模式：`lstat` 每个组件；
3. 若不存在且 `create=True`：
   - 严格模式：相对父句柄 `NtCreateFile(FILE_CREATE, FILE_DIRECTORY_FILE)`；
   - 兼容模式：`Path.mkdir()` 只创建一级，再 `lstat` 核对；
4. 创建成功后记录 `DirLease(created=True, identity=...)`；
5. 任一步失败：停止；已创建目录默认不自动删除（见 §7）。

`create_parents=False` 是默认值。`ProjectStore.init` 的根目录创建：

- 要求根的**直接父目录**已经存在且是真实目录；
- 只创建根这一级；不自动创建缺失的祖先；
- `creation/`、`runs/` 各创建一级；
- 如果产品需要“一键创建多层父目录”，必须作为显式 `--create-parents`，并逐级审计，不能默认。

### 6.5 原子写入

`atomic_write` 的步骤：

1. **快照目标**：`PathSnapshot`（存在性、kind、identity、reparse、大小、哈希）；
2. **打开父租约**：必须是已验证目录；严格模式持有句柄；
3. **创建临时文件**：在父租约内，使用排他创建（`FILE_CREATE` / `O_CREAT|O_EXCL`）；名称包含随机 nonce，禁止可预测；
4. **验证临时文件**：`lstat`/句柄核对是普通文件、不是 reparse、身份与打开句柄一致；
5. **写入**：`write` 全部字节；`flush`；`fsync` / `FlushFileBuffers`；
6. **回读/哈希**：从临时文件句柄读回或重算哈希，确认内容；
7. **预提交谓词与目标钉住**：
   - create：目标必须仍不存在；
   - replace/delete：先以“不共享删除”的方式打开目标并持有句柄（至少需要读取 attributes/identity 与删除/重命名所需访问权），核对身份与哈希；提交期间保持句柄；如果无法打开、访问权不足或平台不支持，拒绝，不退回“检查后 replace”；
   - replace：目标身份与快照一致、哈希等于 `expected_sha256`；
   - 父租约身份必须未变；
8. **提交**：
   - create：相对父句柄 `NtSetInformationFile(FileRenameInformation, ReplaceIfExists=False)` 或 `os.link` + unlink；
   - replace：相对父句柄 rename with replace，或 `os.replace`（仅兼容后端）；
   - 严格模式下不使用“先检查再 `os.replace`”冒充排他创建；
9. **回读目标**：核对身份和哈希；失败标记 `uncertain`，不自动重试；
10. **清理临时文件**：只在身份匹配且未提交时删除；已提交的临时路径不应存在。

失败语义：

- 写入前失败：目标不变；临时文件可能不存在；
- 写入后、提交前失败：目标不变；临时文件保留或按身份清理；记录；
- 提交后、回读前失败：目标可能已变；标记 `uncertain`，由恢复器按实际哈希判断；
- 回读后失败：标记 `uncertain`；不自动回滚；
- 任何情况下都不删除目标来“重试”。

### 6.6 JSON 写入迁移

`write_json_atomic` 改为：

```python
def write_json_atomic(
    path: Path,
    payload: dict[str, Any],
    *,
    boundary: WriteBoundary,
    mode: str = "replace",
    expected_sha256: str | None = None,
) -> None:
    ...
```

或者保留同名函数但移除 `path.parent.mkdir`：

- 父目录不存在时抛 `E_FS_PARENT_MISSING`；
- 调用者必须先 `ensure_dir(..., create=False/True)`；
- 序列化逻辑保持不变（`ensure_ascii=False, indent=2`，末尾换行）；
- `ProjectStore._write_json` 改为 `boundary.atomic_write`；
- `build_digest(write=True)` 改为 `boundary.atomic_write`；
- 不建议保留一个“自动创建父目录”的兼容开关；它会重新引入隐式边界。

### 6.7 追加日志

`decisions.ndjson` 使用 `append_line`：

1. 打开 `creation/` 租约；
2. 以“打开已存在、只追加”模式打开 `decisions.ndjson`；严格模式使用 `FILE_APPEND_DATA` / `O_APPEND`；
3. 检查打开对象的身份、类型、reparse；
4. 写入一行（UTF-8、无换行注入；`json.dumps` 已去除换行）；
5. `flush` + `fsync`；
6. 重新核对目录项身份；若变化，返回 `E_DECISIONS_RACE`，停止后续业务写；
7. 不尝试删除已写内容，不自动截断半行。

`init()` 创建空日志时使用排他创建（`FILE_CREATE` / `O_CREAT|O_EXCL`），不能用 `write_text("")` 覆盖已有文件。

### 6.8 初始化流程（目标形态）

```text
init(project):
  1. 策略检查（路径、类型、名称、pack_id、version）
  2. 打开项目根的父目录租约（必须已存在）
  3. 创建项目根（一级，排他或已存在且为空真目录）
  4. 创建 creation/（一级）
  5. 创建 creation/runs/（一级）
  6. 原子写 spec.json / locks.json / placeholders.json
  7. 排他创建 decisions.ndjson
  8. 追加 project_created 事件
  9. 原子写 state.json
  10. 失败：只删除本次创建且身份匹配的文件；不自动删除目录；报告残留
```

与当前实现的差异：

- 不再使用 `created_root` 布尔；
- 不再自动 `root.rmdir()`；
- 不再 `parents=True`；
- 每一步都有 `DirLease` / `FileLease`；
- 失败后保留空目录是允许的，但必须在错误/日志中说明。

### 6.9 `init_pack` / `export_pack` / `triage_agent` 适配

**`init_pack()`：**

- 目标父目录必须存在且已验证；默认不递归创建；
- 目标不存在：创建一级；目标存在：必须是空真目录；
- `assets/`、`files/` 各创建一级；
- `pack.json` 使用原子 create（目标必须不存在）；
- 失败时只删除本次创建的文件；目录默认保留；
- 不覆盖已存在的 `pack.json`。

**`export_pack()`：**

- 输出父目录必须存在且已验证，或显式 `--create-parents` 逐级创建；
- `destination` 不能是 reparse point；`destination.resolve()` 只用于显示/`is_within` 辅助判断，不作为安全边界；
- `overwrite=False` 时使用排他 create；`overwrite=True` 时记录目标快照/哈希，提交前再核对；
- 临时 ZIP 在输出父租约内创建；`os.replace` 仅兼容后端；严格模式相对父句柄 rename；
- 失败清理只删除身份匹配的临时文件；
- 输出在项目根外是允许的，但必须在用户选择的父目录内。

**`triage_agent.write_report_text()`：**

- 保持“用户指定输出路径”的产品语义；
- 输出父目录默认必须存在；如果用户要求自动创建，逐级创建并审计；
- 输入输出同文件检查保留，但不能替代对象身份；
- 目标已存在时，保持当前覆盖语义；是否增加“拒绝覆盖”属于产品决策，不在本设计擅自改变；
- 严格后端下，提交前核对目标身份；如果目标被替换为用户未选择的文件，拒绝；
- 失败清理只删除自建临时文件。

### 6.10 错误码建议

| 错误码 | 含义 |
|---|---|
| `E_FS_PLATFORM_UNSUPPORTED` | 当前平台没有严格提交后端，写操作被拒绝 |
| `E_FS_PARENT_MISSING` | 父目录不存在且未显式允许创建 |
| `E_FS_PARENT_UNSAFE` | 父目录是 reparse/非目录/身份不符 |
| `E_FS_TARGET_UNSAFE` | 目标是 reparse/目录/类型不符 |
| `E_FS_TARGET_EXISTS` | create 目标已存在 |
| `E_FS_TARGET_CHANGED` | 目标快照/哈希在提交前变化 |
| `E_FS_IDENTITY_CHANGED` | 父/目标/临时文件身份变化 |
| `E_FS_TEMP_RACE` | 临时文件创建/替换被干扰 |
| `E_FS_CLEANUP_REFUSED` | 清理时身份不符，拒绝删除 |
| `E_FS_BACKUP_REQUIRED` | delete 缺少备份 |
| `E_FS_BACKUP_FAILED` | 备份写入/校验失败 |
| `E_FS_COMMIT_UNCERTAIN` | 提交后无法确认目标状态 |
| `E_DECISIONS_RACE` | 日志目录项在追加期间变化 |

错误码需要稳定、可测试；不得把 `WinError` 原文直接当公开 code。

---

## 7. 清理与文件身份设计

### 7.1 基本规则

1. **不按布尔值删除。** `created_root`、`created` 不再作为删除依据。
2. **不按路径删除。** 删除前必须核对对象身份。
3. **默认不删除目录。** 失败清理只删除本次创建的文件；目录留给用户/显式清理。
4. **无法确认身份时拒绝删除。** `E_FS_CLEANUP_REFUSED`，保留现场。
5. **不递归删除。** 不使用 `shutil.rmtree`，不 glob 删除。
6. **不删除备份。** 备份默认长期保留；清理需用户显式授权并记录事件。
7. **不删除未知临时文件。** 只删除本次操作记录且身份匹配的临时文件；未知残留只报告。

### 7.2 `DirLease` 与 `FileLease`

```text
DirLease:
  path            # 仅用于显示/审计
  identity        # 卷 + file ID
  handle          # 严格模式；兼容模式为 None
  created_by_us   # 仅当本次调用创建成功
  parent          # 父租约
  snapshot        # 创建/打开时的 lstat 摘要

FileLease:
  path
  identity
  handle
  temporary       # 是否是本次创建的临时文件
  expected_sha256
  parent          # 父租约
```

规则：

- 租约只能由 `WriteBoundary` 创建，不能由调用者伪造；
- 租约在操作结束后关闭句柄；
- 任何跨函数传递都必须携带身份，不能只传 `Path`；
- 清理函数只接受租约，不接受裸路径（或裸路径必须重新建立租约）。

### 7.3 清理决策表

| 情况 | 行为 |
|---|---|
| 临时文件身份与记录一致，未提交 | 删除；记录 `cleanup_removed` |
| 临时文件身份不一致 | 不删除；`E_FS_CLEANUP_REFUSED`；记录 |
| 目标已被外部修改 | 不删除；不覆盖；报告 `E_FS_TARGET_CHANGED` |
| 目录是本次创建、身份一致、为空、严格后端支持按句柄删除 | 可删除；记录 |
| 目录是本次创建、身份一致，但兼容后端 | 不删除（默认）；报告 `cleanup_left` |
| 目录身份不一致/是 reparse/非空 | 不删除；报告 |
| 项目根 | **永不自动删除**；即使本次创建、为空，也保留并报告 |

项目根永不自动删除的理由：

- 实验 E 已证明 `root.rmdir()` 可删除项目外既有空目录；
- 即使有身份检查，路径解析仍可能在 `rmdir` 时被替换；
- 留下一个空项目根是低代价、可恢复的失败状态；
- 用户可手动删除，或由未来的显式 `cleanup` 命令在严格后端下处理。

### 7.4 并发修改

- 删除前重新读取身份和哈希；
- 如果目标在检查后变化，停止；不“尽力删除”；
- 如果目标被删除后重建，视为新对象，不删除；
- 如果目录非空，不删除；
- 不通过“递归删除内容”来让 `rmdir` 成功；
- 清理失败不改变原始异常；原始错误和清理错误都要记录。

### 7.5 与实验 C/E 的对应

- 实验 C：项目外并发文件导致 `root.rmdir()` 失败并残留。新设计不再自动 `root.rmdir()`，因此不会尝试删除；残留由用户处理。
- 实验 E：项目外既有空目录被 `root.rmdir()` 删除。新设计永不自动删除项目根，因此不会发生。
- 若未来需要删除 `creation/` 或 `runs/`，只能在严格后端下按句柄/身份删除；兼容后端不删除目录。

---

## 8. 原子写入设计

### 8.1 create

目标：目标不存在时创建一个完整文件；目标存在时失败，不覆盖。

步骤：

1. 快照目标：必须不存在；
2. 父租约必须有效；
3. 临时文件排他创建并写入/fsync；
4. 再次确认目标不存在；
5. 提交：
   - 严格：相对父句柄 rename，`ReplaceIfExists=False`；
   - POSIX：`os.link(temp, target)` + `unlink(temp)`；`link` 目标存在即失败；
   - Windows 兼容：`os.rename(temp, target)`，目标存在即失败；但父目录替换风险仍在，不能用于严格门槛；
6. 回读目标：存在、普通文件、哈希正确；
7. 清理临时文件（身份匹配）。

禁止：

- 先 `exists()` 再 `os.replace`；
- 用 `os.replace` 覆盖 create 目标；
- 在目标存在时“幂等成功”；
- 跨目录/跨卷 rename。

### 8.2 replace

目标：只有目标仍是审批时那个对象/内容时才替换。

步骤：

1. 快照目标：存在、普通文件、非 reparse、identity、SHA-256；
2. 以不共享删除的方式打开目标并持有句柄，确认打开对象就是快照对象；
3. 校验 `expected_sha256`；
4. 父租约有效；
5. 临时文件写入/fsync/回读；
6. 再次核对目标 identity + hash + 父租约；目标句柄仍在；
7. 提交：
   - 严格：相对父句柄 rename with replace，同时持有目标句柄；
   - 兼容：`os.replace`（明确标记残余风险）；
8. 回读目标：identity 变化、hash 等于新内容；
9. 清理临时文件。

失败：

- 目标被替换/修改：`E_FS_TARGET_CHANGED`，不覆盖；
- 提交结果不确定：`E_FS_COMMIT_UNCERTAIN`，不自动重试；
- 回读失败：标记 `uncertain`，保留临时文件/证据。

### 8.3 delete

目标：先备份，再删除；删除前确认对象身份。

步骤：

1. 快照目标：存在、普通文件、非 reparse、identity、SHA-256；
2. 以不共享删除的方式打开目标并持有句柄，确认打开对象就是快照对象；访问权不足则拒绝；
3. 校验 `expected_sha256`；
4. 备份到 `creation/backups/<batch>/<op>.bin`：
   - 使用同一 `WriteBoundary`；
   - 备份 create 必须排他；
   - 备份内容哈希必须等于目标哈希；
   - 备份元数据写入 `<op>.json`；
5. 删除：
   - 严格：按句柄 `FileDispositionInformation`；
   - 兼容：`os.unlink`，提交前再次核对 identity/hash；
6. 回读：目标不存在；若被外部重建，记录 `E_FS_TARGET_CHANGED`，不再次删除；
7. 记录 `DeleteReceipt`。

禁止：

- 没有备份就 delete；
- 备份失败后继续 delete；
- 删除目录（目录删除单独设计，默认禁止）；
- 删除 reparse point 的目标内容（应先拒绝 reparse）。

### 8.4 fsync 与目录持久性

- 文件内容：`flush` + `fsync`/`FlushFileBuffers`；
- 临时文件关闭后再提交；
- Windows 目录句柄的 `FlushFileBuffers` 可能失败；不把“目录 fsync 成功”作为承诺；
- POSIX 上 best effort `fsync` 父目录；
- 文档必须说明：崩溃后目录项可能回退/丢失，恢复器以实际哈希为准。

### 8.5 失败注入点

| 注入点 | 期望 |
|---|---|
| 临时文件创建前 | 目标不变；无临时文件；错误 |
| 写临时文件中 | 目标不变；临时文件可识别；错误 |
| fsync 前/后 | 目标不变；临时文件处理明确 |
| 临时文件创建后、目标检查前替换父目录 | 严格模式：提交失败/拒绝；外部无目标文件 |
| 目标检查后、提交前替换目标 | 严格模式：身份不符，拒绝；目标不被覆盖 |
| 提交后、回读前崩溃 | 恢复器按实际哈希判断；不重复写 |
| 回读后、事件前崩溃 | 事件缺失；恢复器补记/标记 |
| 清理时替换临时路径 | 身份不符，拒绝删除；外部文件保留 |
| 删除时替换父目录 | 严格模式按句柄删除；兼容模式拒绝/可能失败，必须报告 |

---

## 9. 备份与恢复设计

### 9.1 备份布局

```text
creation/
  backups/
    <batch_id>/
      <op_id>.bin        # 原始内容
      <op_id>.json       # 恢复元数据
```

元数据至少包含：

- `original_path`（项目相对路径）；
- `original_sha256`；
- `size_bytes`；
- `op_id`、`batch_id`；
- `plan_id`、`spec_revision`；
- `backup_sha256`；
- `created_at`、`tool_version`；
- `recovery`：恢复所需目标状态/备份位置。

### 9.2 备份写入

- 备份目录必须位于已验证的 `creation/backups/` 下；
- 默认不递归创建；逐级 `ensure_dir`；
- `<op_id>.bin` 排他创建；已存在时：
  - 内容/哈希完全一致 → 可幂等复用；
  - 否则 `E_FS_BACKUP_EXISTS`，不覆盖；
- 写入后 fsync、回读哈希；
- 备份失败 → 不执行 delete/replace；
- 备份不自动删除；清理需用户显式授权并写事件。

### 9.3 恢复

恢复不是“把备份写回目标”这么简单：

1. 校验备份元数据：`backup_sha256`、`original_sha256`、`op_id`、`batch_id`；
2. 校验目标当前状态：
   - 不存在，或；
   - 仍是删除后的预期状态；
   - 如果用户已修改/重建，拒绝 `E_FS_TARGET_CHANGED`；
3. 校验 Plan/Spec/审批边界：恢复不得绕过普通执行器的路径/授权检查；
4. 使用 `atomic_write` 的 replace/create，目标父目录必须已验证；
5. 恢复本身写事件（`recovery_applied` 或等价事件）；
6. 恢复失败不删除备份、不改写原 `operation_result`；追加新记录；
7. 恢复成功后也不自动删除备份。

### 9.4 备份与边界

- 备份文件也是业务写入，必须走同一 `WriteBoundary`；
- 备份路径不能是 reparse point；
- 备份文件不能进入导出/digest 文件清单；
- 备份可能含敏感内容；权限收紧、不提交、不导出；
- 备份清理是独立的高风险操作，必须有身份检查、事件和用户确认。

---

## 10. 测试矩阵

### 10.1 测试原则

1. 所有测试使用 `tempfile.TemporaryDirectory()`；不得写仓库根、`report.md`、`real_crash.log`、真实 `creation/`。
2. Windows 链接测试优先使用真实 junction（`cmd /c mklink /J`）；symlink 权限不足时 `skipTest`，skip 不得计为通过。
3. 每个安全用例必须同时断言：
   - 最终目标状态；
   - 项目外没有新增/修改/删除；
   - 原有文件和目录未被误删；
   - 错误码/回执稳定；
   - 临时文件/目录的残留符合预期。
4. 受控交错测试必须用真实文件系统 + 注入钩子/子进程，不只 mock 路径。
5. 失败注入测试用子进程 + 环境变量（如 `VEYRA_FAULT_POINT`）在指定点 `os._exit`；父进程检查现场。
6. 测试必须区分“最终状态正确”与“过程没有越界副作用”；不能只看最后文件是否存在。
7. Windows 10/11、Python 3.10/3.11/3.15 的未验证行为必须显式标记；不能假设。

### 10.2 必测矩阵

| 编号 | 场景 | 设置 | 期望 |
|---|---|---|---|
| T01 | 正常 init | 普通临时目录 | 成功；无外部写入；无残留 |
| T02 | 正常 JSON create/replace | 已验证父目录 | 成功；identity/hash 正确 |
| T03 | 正常 delete + backup | 有备份 | 目标消失；备份哈希正确；事件记录 |
| T04 | 正常 restore | 备份 + 目标状态匹配 | 内容恢复；事件记录；备份保留 |
| T05 | 父目录缺失 | `atomic_write(create_parents=False)` | `E_FS_PARENT_MISSING`；不创建目录 |
| T06 | 父目录是 junction | 真实 junction 指向外部 | `E_FS_PARENT_UNSAFE`；外部无文件 |
| T07 | 目标最终组件是 junction | 真实 junction | 拒绝；不跟随；外部不变 |
| T08 | 目标最终组件是 symlink | 权限允许时 | 拒绝；skip 时不计通过 |
| T09 | init 父目录在检查后替换 | 注入真实 junction | 严格模式：外部无 `project`；拒绝 |
| T10 | init 在 mkdir 后替换父目录 | 注入 | 严格模式：不删除外部目录；项目根保留/报告 |
| T11 | 清理时项目外有并发文件 | 外部文件 | 不删除外部文件；不尝试递归删除 |
| T12 | 清理时项目外有既有空目录 | 外部空目录 | 永不自动删除项目根；外部目录保留 |
| T13 | 临时文件创建期间替换父目录 | 注入 | 严格模式：临时文件不在外部；拒绝 |
| T14 | 临时文件路径被替换 | 注入 | 清理拒绝删除；外部文件保留 |
| T15 | 目标在检查后、replace 前替换 | 注入 | `E_FS_TARGET_CHANGED`；不覆盖 |
| T16 | create 目标在检查后出现 | 注入 | 排他 create 失败；不覆盖 |
| T17 | delete 期间目标被替换 | 注入 | 拒绝/不确定；不删除新对象 |
| T18 | 备份写入失败 | fault hook | 目标不删；`E_FS_BACKUP_FAILED` |
| T19 | 备份元数据哈希不符 | 篡改 | 恢复拒绝；`E_FS_BACKUP_INVALID` |
| T20 | 恢复时目标被用户修改 | 修改目标 | `E_FS_TARGET_CHANGED`；不覆盖 |
| T21 | decisions 日志父目录被替换 | junction | 严格模式拒绝；外部日志不变 |
| T22 | decisions 追加期间目录项变化 | 注入 | `E_DECISIONS_RACE`；停止后续写 |
| T23 | 两个进程同时 init | 子进程 | 一个成功/一个 `E_FS_*`；无交错状态 |
| T24 | 两个进程同时写同一 JSON | 子进程 | 一个提交；另一个冲突/不确定；不静默覆盖 |
| T25 | 进程在提交后、事件前崩溃 | 子进程 `os._exit` | 恢复按实际哈希判断；不重复写 |
| T26 | 进程在写临时文件中崩溃 | 子进程 | 目标不变；临时文件可识别/不误删 |
| T27 | 磁盘满/fsync 失败 | mock/故障注入 | 目标不变；错误明确；不谎报 |
| T28 | 路径为 UNC/设备/ADS/尾点/保留名 | 纯输入 | 拒绝；不触盘 |
| T29 | `creation/` 路径 | 纯输入 | 拒绝；不写 |
| T30 | Python 3.10 junction 检测 | 兼容性测试 | 不能因 `isjunction` 缺失而放行；拒绝/fail-closed |
| T31 | `init_pack` 目标父目录 junction | 真实 junction | 外部无 `assets/files/pack.json` |
| T32 | `export_pack` 输出父目录 junction | 真实 junction | 外部无 ZIP；拒绝 |
| T33 | `triage` 输出父目录 junction | 真实 junction | 外部无报告；拒绝 |
| T34 | `write_json_atomic` 不再隐式建父目录 | 缺失父目录 | `E_FS_PARENT_MISSING`；目录未创建 |
| T35 | 正常 CLI init/status/digest/verify | 临时项目 | 现有行为不回归；错误码/JSON 不变 |

### 10.3 测试分层

| 层 | 内容 | 是否写文件 |
|---|---|---|
| 单元 | `FileIdentity`、`PathSnapshot`、reparse 检测、错误码 | 否/临时 |
| 边界集成 | `WriteBoundary` + 临时目录 + 真实 junction | 是（临时） |
| Store 集成 | init/save_spec/plan/digest/decisions/runs | 是（临时） |
| Pack 集成 | init_pack/export_pack | 是（临时） |
| Triage 集成 | report/JSON 输出 | 是（临时） |
| 故障注入 | 子进程崩溃、fsync/写失败、路径替换 | 是（临时） |
| 并发 | 两个子进程 | 是（临时） |

### 10.4 验收口径

- 不以“测试数量”代替覆盖；每个 P0 场景必须有独立测试；
- 安全用例失败即阻断，不允许 skip 后宣称通过；
- Windows symlink skip 必须记录；junction 优先；
- 测试结束后检查临时目录无残留 junction；
- 测试不得依赖管理员权限；
- 测试不能把项目根的真实文件当 fixture。

---

## 11. 3.2.2.1 准入条件

### 11.1 硬性条件

在以下条件全部满足前，**不允许进入 3.2.2.1 的写实现，也不允许把 3.2.2.1 标记为可开工**：

1. **统一策略覆盖所有写入点**：W1–W9 全部接入 `WriteBoundary`；生产代码中不再有裸 `mkdir(parents=True)`、裸 `open("a")`、裸 `os.replace` 或独立的 `tempfile` 写入逻辑。
2. **项目外零写入**：在强制 junction 替换测试中，项目外没有新增/修改文件或目录；严格模式必须使用句柄相对创建/提交。纯路径检查不能作为验收证据。
3. **清理身份化**：清理只删除本次创建且身份匹配的文件；默认不删除目录；项目根永不自动删除；无法确认身份时 fail-closed。
4. **临时文件/备份/恢复同一边界**：临时文件、备份、恢复、导出、报告输出全部走同一 `WriteBoundary`，不得各自实现。
5. **真实文件系统测试**：T05–T35 中所有 P0 用例在 Windows 真实 junction/子进程/故障注入下通过；symlink skip 如实记录。
6. **无法验证的 Windows 行为已标记**：`ntdll` ABI、目录 fsync、网络/FAT、Windows 版本差异必须有明确的支持矩阵和 fail-closed 行为；不能把“未验证”写成“已保证”。
7. **独立只读审计通过**：由未参与实现的审查者复核代码、测试、错误码和残余风险；审计报告必须列出未覆盖项。

### 11.2 建议的完成顺序

不新增产品阶段，只作为 3.2.2.0 的后续最小加固批次（编号由用户确认）：

1. **H0 兼容性与身份原语**：修复 Python 3.10/3.11 junction 检测；实现 `FileIdentity`/`PathSnapshot`/reparse 分类；只读测试。
2. **H1 目录与原子写**：`WriteBoundary`、`ensure_dir`、`atomic_write`；迁移 `write_json_atomic`；移除隐式父目录；测试 T05/T06/T13/T14/T34。
3. **H2 init 与清理**：`ProjectStore.init` 改为逐级创建 + 租约；移除 `created_root` 目录清理；测试 T09–T12。
4. **H3 日志与 digest**：`append_line`、`digest` 接入；测试 T21/T22。
5. **H4 pack/triage 适配**：`init_pack`、`export_pack`、`write_report_text` 接入；测试 T31–T33。
6. **H5 严格后端**：Windows `NtCreateFile`/`NtSetInformationFile`；POSIX `openat`；无后端 fail-closed；测试 T09/T13/T15/T17/T25。
7. **H6 审计与验收**：独立只读审计，形成 3.2.2.0.6 实施/验收报告。

### 11.3 明确禁止

在上述条件完成前：

- 不实现 `OperationExecutor` 的 create/replace/delete；
- 不实现 `SafeWriter` 的临时文件/备份写；
- 不在现有 `ProjectStore` 上增加新的写 API；
- 不把 `write_json_atomic` 的隐式父目录行为复制到新代码；
- 不以“已经有 `_assert_no_link`”为由跳过身份绑定；
- 不以“测试通过”代替强制竞态测试。

### 11.4 审阅决策

本设计需要用户/ChatGPT 明确以下选择：

1. 是否接受 `ntdll` 严格后端？如果不接受，gate 2 必须降级，3.2.2.1 不能声称零外部写入。
2. 是否接受“失败时保留空目录、项目根永不自动删除”？推荐接受。
3. 是否接受“默认不递归创建父目录，需要显式 `--create-parents`”？推荐接受。
4. 是否要求 `triage_agent` 也接入统一层，还是仅先覆盖 `veyra_creation`/`veyra_pack`？推荐全部接入。
5. 是否要求 3.2.2.1 在 H1–H5 完成前完全不开工？本文建议：**是；至少不得写任何业务文件。**

---

## 12. 未解决问题与残余风险

### 12.1 未解决问题

1. **`ntdll` 的 ABI 风险**：结构体布局、状态码、Windows 版本差异需要实测；是否值得承担，需要产品/架构决策。
2. **Python 3.10/3.11 junction 检测**：当前 `is_link_like` 在缺少 `os.path.isjunction` 时可能漏检；必须在 H0 修复并测试。
3. **项目根身份**：严格模式打开根之前仍有路径替换窗口；是否需要在 `state.json` 记录根 identity，或要求根目录 ACL/用户预创建，尚未定。
4. **目录 fsync**：Windows 没有可移植的目录持久化保证；崩溃后的目录项状态需要恢复器按实际哈希处理。
5. **并发锁**：现有 `ProjectStore` 没有写锁；`executor.lock` 只覆盖未来 executor。是否为现有写操作增加 `creation/write.lock`，需要单独设计。
6. **FAT/exFAT/网络盘**：没有稳定 file ID/硬链接；严格模式应拒绝，兼容模式需要明确标记。
7. **`triage_agent` 的覆盖语义**：当前允许覆盖 `report.md`；是否增加“拒绝覆盖”或 `--force` 是产品决策，不在本轮改变。
8. **备份上限/清理策略**：备份默认长期保留；磁盘上限、用户显式清理命令和事件格式尚未定稿。
9. **测试环境**：Windows symlink 权限、不同 Windows 版本、32 位 Python、ReFS 需要覆盖；本轮未运行测试。
10. **性能**：每个写入都打开句柄/查询身份会增加开销；需要基准，但不能以性能为由跳过身份检查。

### 12.2 残余风险

- **特权攻击者**：管理员、备份特权、内核过滤驱动可以绕过用户态共享锁/身份检查；本设计不承诺防御。
- **根打开前窗口**：严格模式在打开项目根之前仍有路径替换窗口；根 identity/ACL 只能缩小，不能彻底消除。
- **网络/同步盘**：文件 ID、rename 原子性、共享语义不可靠；必须拒绝或标记。
- **进程崩溃**：提交后、事件前崩溃仍可能产生“文件已变、日志缺失”；恢复器只能按实际哈希推断，不能保证完全一致。
- **目录项持久性**：Windows 上目录元数据持久性无保证；断电后可能出现目标存在但事件缺失，或反之。
- **TOCTOU 不能宣称彻底消除**：即使在句柄链内，仍有根打开前、外部已持有共享删除句柄、特权操作等窗口；目标钉住失败时必须 fail-closed/标记 uncertain，不能退回路径检查。
- **目标钉住的 Windows 语义**：以“不共享删除”打开目标只能阻止后续获得删除共享的打开；如果外部进程已经持有允许删除的句柄，替换仍可能发生。实现必须在目标版本上验证 `NtSetInformationFile` + 目标句柄的行为，未知即拒绝。
- **备份敏感性**：备份可能包含用户代码/密钥；不导出、不提交、权限收紧是必要但不充分。
- **`ntdll` 兼容性**：未来 Windows 更新可能改变行为；必须有 fail-closed 和版本测试。
- **日志半行**：追加写入仍可能因磁盘满/崩溃产生半行；恢复器必须停止并报告，不能自动截断。
- **测试覆盖不足**：本轮只做设计，未运行新增测试；所有保证在实现和独立验收前都只是设计目标。

### 12.3 与 0.5 结论的关系

- 0.5 已确认 `init()` 的窄窗口、清理残留/误删、`write_json_atomic` 的同类窗口。
- 0.6 不重复实验，而是在 0.5 的事实上设计统一边界。
- 0.5 的建议是“最小加固 + 阻断 3.2.2.1”；0.6 进一步指出：**仅靠最小路径加固不能满足 gate 2，必须决定是否采用句柄相对提交。**
- 如果只做 0.5 的最小加固而不做 0.6 的严格后端，3.2.2.1 仍应阻断，或明确降级安全声明。

---

## 13. 与 Veyra 最终目标的一致性

Veyra 的最终目标是面向游戏 Mod 社区的 AI Game Mod Creation Platform。当前阶段仍是“AI Skill + 垂直工具”，不应因为安全加固而扩张为平台工程。本文的设计与最终目标一致：

1. **证据可信度优先**：文件写入边界是“证据/产物不被伪造或误覆盖”的基础；比让 AI 输出看起来完整更重要。
2. **Python 做确定性操作**：路径、身份、哈希、提交、清理由 Python 完成；模型不直接写文件。
3. **跨游戏适配**：安全层与游戏无关，不引入游戏特定逻辑；未来 executor 可复用。
4. **不引入不必要依赖**：严格后端只用标准库 `ctypes`；不引入第三方库、数据库或守护进程。
5. **可验证**：每个保证都有测试矩阵和 fail-closed 语义；不把“重复检查”宣传成“消除 TOCTOU”。
6. **渐进式**：先收紧现有写点，再接入 executor；不推翻 Phase 3.2.1 的 State/Spec/Plan/Digest 语义。
7. **平台化留白**：`WriteBoundary` 是内部基础能力，不暴露为 Web 平台/GitHub App；未来需要时再上层封装。
8. **用户保护**：`report.md`、`real_crash.log` 和用户文件不被自动删除/覆盖；失败保留现场，便于人工判断。

---

## 附录 A：代码路径索引

| 路径 | 作用 |
|---|---|
| `veyra_creation/store.py:274-395` | `ProjectStore.init()` |
| `veyra_creation/store.py:410-446` | `_commit_state` / `_write_json` / `_read_json` |
| `veyra_creation/store.py:864-921` | `append_decision` / `_append_decision_raw` |
| `veyra_creation/store.py:1076-1156` | `start_run` / `finish_run` |
| `veyra_pack/contract.py:162-190` | `write_json_atomic()` |
| `veyra_creation/digest.py:83-241` | `build_digest(write=True)` |
| `veyra_pack/init_project.py:64-110` | `init_pack()` |
| `veyra_pack/exporter.py:48-123` | `export_pack()` |
| `triage_agent.py:1120-1157` | `write_report_text()` |
| `triage_agent.py:1254-1262` | `write_contract_json()` |
| `veyra_pack/paths.py:86-105` | `is_link_like()` |
| `veyra_pack/paths.py:289-311` | `find_link_component()` |
| `veyra_pack/paths.py:314-322` | `is_within()` |
| `veyra_pack/paths.py:325-372` | `resolve_file()` |

## 附录 B：本轮验证边界

- 已做：静态代码审查；全仓库写入 API 搜索；Python 3.15/Windows 能力探针；对 0.5 报告的交叉引用。
- 未做：修改代码；运行测试套件；实现原生句柄后端；真实 `NtCreateFile` 实验；不同 Windows 版本/32 位/ReFS/FAT 实验；并发压力测试。
- 因此，本文中所有 Windows 原生 API 结论是设计分析，不是实现验收；实现前必须在目标 Windows 版本上做最小探针，并以失败注入测试验证。
- 保护文件：`report.md` 只核验 SHA-256；`real_crash.log` 只核验元数据/SHA-256；本轮未读取内容、未修改、未删除。
- Git：本轮只新增本文件；不执行 `git add`/`commit`/`push`/`reset`/`checkout`/`clean`/`rebase`。

---

**最终建议：先审阅本文 §5.6、§11.4 的两个决策（是否接受 `ntdll` 严格后端、是否接受“默认不删目录/不递归创建”），再决定 3.2.2.1 是否具备开工条件。在决策前，所有写操作继续保持阻断。**

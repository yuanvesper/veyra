# Phase 3.2.2.0.7 — Windows 严格后端可行性原型报告

- 日期：2026-11-21
- 状态：原型完成，等待用户/ChatGPT 审阅；**未进入 Phase 3.2.2.1**
- 范围：只在独立临时目录验证 Windows 原生句柄相对文件操作，不修改 Veyra 生产代码、现有测试或数据
- 结论一句话：**句柄相对原语本身可行，但当前原型还不是“项目外零写入”的完整边界；建议有条件接受 `ntdll`，先关闭根锚点、替换提交和备份路径三个缺口，再谈生产化。**

---

## 结论摘要

本报告回答 0.7 的任务问题：在 Python 3.10+ 上，是否能用一个隔离原型验证 Windows 严格写入后端，并据此决定是否接受 `ntdll`。

已验证：

1. 通过 `CreateFileW` 固定项目根目录句柄，再用 `NtCreateFile` 的 `OBJECT_ATTRIBUTES.RootDirectory` 做相对创建/打开，能在根路径被 rename + junction 替换后继续操作**原来的根对象**（实验 A、C1）。
2. 目录子句柄在父目录 rename 后仍指向原对象，`open_child_at` 仍能读到原对象内容（实验 B）。
3. 原子创建（临时文件 + 不替换 rename + 身份/哈希复验）和 POSIX 语义替换在原型中可用（实验 C1、C2）。
4. 删除可以用句柄身份绑定；旧身份不匹配会拒绝，junction 目标会拒绝（实验 D1、D2）。
5. 临时文件清理会比较“当前名字对应的对象身份”和“临时句柄身份”，不会误删同名的外部文件（实验 E）。
6. 同一卷内 `VolumeSerialNumber` 稳定；同一个对象 rename 后 `FileId` 保持稳定，同名路径重新创建后新对象会得到新的 `FileId`，可作为对象身份证据（实验 F）。
7. 原型自带 16 个标准库 `unittest`，在 Python 3.14.7 和 3.15.0 上均通过；A–F 共 8 个受控实验全部通过。

但同时发现了三类**不能忽略的边界缺口**：

1. **根路径的祖先 junction 未被锚定**：`WinWriteBoundary(root_path)` 仍用 `CreateFileW` 按路径打开根；如果根的某个祖先目录被 junction 替换，根句柄会落在项目外。补充实验 G 已真实复现：`create_file_atomic` 把文件写到了项目外的 `outside/project`。
2. **替换没有绑定“被检查的那个目标对象”**：`replace_file` 打开目标并检查，但 POSIX rename 只按“根句柄 + 名字”提交；在打开检查之后把目标改名、再放一个新文件，提交会替换新文件。补充实验 H 已复现：最终 `r.txt` 不是最初检查的对象。
3. **备份路径不在写入边界内**：`delete_file` 的 `backup_path` 由调用方传入，原型只检查父目录存在和名字不冲突，没有检查父目录是否为 junction/symlink。补充实验 I 已复现：把备份父目录做成 junction 后，备份文件被写到项目外。

因此：

- **不能**把本原型直接当作 3.2.2.1 的“零项目外写入”验收证据。
- **可以**把本原型作为 H5 严格后端的可行性证据：句柄相对创建/打开/删除、身份校验、POSIX 语义这些原语在 Windows 上确实可用。
- 推荐：**有条件接受 `ntdll` 作为 Windows 严格后端的内部实现**；但必须把“根锚点”“替换提交绑定”“备份/临时/恢复同一边界”作为 H0/H5 的硬条件，并由独立只读审计复核。若不接受 `ntdll`，则只能降级为“路径检查的最佳努力”，不能宣称零外部写入。

---

## 1. API 语义与来源

本节只记录原型实际使用的 API，以及官方文档能支持的最小语义。`ntdll` 的 `Nt*` 接口属于 Windows 原生/Direct Kernel 文档，不是稳定的 Win32 合同；这一点本身就是风险，不应在文档里写成“官方保证”。

### 1.1 `NtCreateFile` 与相对打开

原型使用的核心事实：

- `OBJECT_ATTRIBUTES.RootDirectory` 非 NULL 时，`ObjectName` 是相对于该目录句柄的名字。
- 相对名必须是简单子名，不能带路径分隔符；原型在 `_nt_create_relative` 和 `_simple_name` 两处都做了拒绝。
- `FILE_OPEN_REPARSE_POINT`（`0x00200000`）让最终组件作为 reparse point 本身打开，而不是跟随它。
- `FILE_CREATE`（`2`）要求目标不存在；`FILE_OPEN`（`1`）要求目标存在。
- 目录必须使用 `FILE_DIRECTORY_FILE`（`0x1`），文件使用 `FILE_NON_DIRECTORY_FILE`（`0x40`）。
- `FILE_SYNCHRONOUS_IO_NONALERT`（`0x20`）需要 `SYNCHRONIZE` 访问。
- 打开目录时，访问位应使用 `FILE_LIST_DIRECTORY`/`FILE_TRAVERSE`，不应使用文件语义的 `FILE_READ_DATA`/`FILE_WRITE_DATA`/`FILE_APPEND_DATA`/`FILE_EXECUTE`。

来源：

- [NtCreateFile](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntifs/nf-ntifs-ntcreatefile)
- [OBJECT_ATTRIBUTES](https://learn.microsoft.com/en-us/windows/win32/api/ntdef/ns-ntdef-_object_attributes)

### 1.2 `NtSetInformationFile`：rename 与 delete

原型使用两类信息：

- `FileRenameInformation`（类 `10`）：`FILE_RENAME_INFORMATION`，非 POSIX 语义；`ReplaceIfExists` 为 `BOOLEAN`。
- `FileRenameInformationEx`（类 `65`）：`FILE_RENAME_INFORMATION_EX` 的 `Flags`，原型使用：
  - `FILE_RENAME_REPLACE_IF_EXISTS = 0x1`
  - `FILE_RENAME_POSIX_SEMANTICS = 0x2`
- `FileDispositionInformation`（类 `13`）：`FILE_DISPOSITION_INFORMATION { BOOLEAN DeleteFile; }`。
- `FileDispositionInformationEx`（类 `64`）：`FILE_DISPOSITION_INFORMATION_EX { ULONG Flags; }`，原型使用：
  - `FILE_DISPOSITION_DELETE = 0x1`
  - `FILE_DISPOSITION_POSIX_SEMANTICS = 0x2`

关键约束：

- rename 的源句柄需要 `DELETE` 访问。
- delete 的句柄需要 `DELETE` 访问；disposition 设置后应尽快关闭句柄。
- 非 POSIX rename 在目标有打开句柄时可能失败；POSIX 语义允许替换已有句柄的目标，已有句柄仍然有效。
- **POSIX rename 只按“根目录句柄 + 名字”定位目标，不接收“目标对象句柄”。** 这是 H 实验缺口的根因。

来源：

- [NtSetInformationFile](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntifs/nf-ntifs-ntsetinformationfile)
- [FILE_RENAME_INFORMATION](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntifs/ns-ntifs-_file_rename_information)
- [FILE_RENAME_INFO](https://learn.microsoft.com/en-us/windows/win32/api/winbase/ns-winbase-file_rename_info)
- [FILE_DISPOSITION_INFORMATION](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntddk/ns-ntddk-_file_disposition_information)
- [FILE_DISPOSITION_INFORMATION_EX](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntddk/ns-ntddk-_file_disposition_information_ex)

### 1.3 身份与文件信息

原型使用：

- `GetFileInformationByHandleEx(FileIdInfo=18)` → `FILE_ID_INFO { ULONGLONG VolumeSerialNumber; FILE_ID_128 FileId; }`。
- `GetFileInformationByHandle` 的 `dwVolumeSerialNumber + nFileIndexHigh/Low` 作为旧版回退。
- `FileAttributeTagInfo=9` → 属性 + reparse tag。
- `FileStandardInfo=1` → `EndOfFile`、`NumberOfLinks`、`DeletePending`、`Directory`。

来源：

- [GetFileInformationByHandleEx](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-getfileinformationbyhandleex)
- [FILE_ID_INFO](https://learn.microsoft.com/en-us/windows/win32/api/winbase/ns-winbase-file_id_info)

### 1.4 根目录打开

原型用 `CreateFileW` 打开根：

```text
access   = FILE_LIST_DIRECTORY | FILE_TRAVERSE | FILE_READ_ATTRIBUTES | SYNCHRONIZE
share    = FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE
flags    = FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT
```

`FILE_FLAG_BACKUP_SEMANTICS` 是打开目录所必需的；`FILE_FLAG_OPEN_REPARSE_POINT` 只作用于**最终组件**，不会阻止祖先目录被 junction 跟随。来源：[CreateFileW](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew)。

### 1.5 `OBJ_DONT_REPARSE` 的实测语义

补充探针 `probe_dont.py`：

- 对 junction `link` 做“跟随打开”（不带 `FILE_OPEN_REPARSE_POINT`），`dont_reparse=True`：
  - `NtCreateFile` 返回 `STATUS_REPARSE_POINT_ENCOUNTERED (0xC000050B)`。
- 同一打开，`dont_reparse=False`：
  - 成功打开 junction 目标（`outside`），`is_reparse=False`。
- 原型 `open_child` 默认带 `FILE_OPEN_REPARSE_POINT`：
  - `dont_reparse=True/False` 都成功打开 junction 本身，得到 `is_reparse=True`、tag `0xA0000003`。

结论：`OBJ_DONT_REPARSE` 对**跟随打开**的最终组件有效；但原型为了检查最终组件是否 reparse，使用了 `FILE_OPEN_REPARSE_POINT`，所以不能把 `OBJ_DONT_REPARSE` 当作“不会跟随祖先 junction”的保证。祖先路径仍必须在根锚点处解决。

---

## 2. 原型结构

原型位于独立临时目录，未进入仓库：

```text
C:\Users\lenovo\AppData\Local\Temp\veyra-0.7-native-20261121093741-aab90b8c\
  errors.py                 108 行  错误分类、NTSTATUS/Win32 映射
  identity.py               202 行  FileIdentity / FileInfo / 句柄信息查询
  native_backend.py         907 行  根句柄、相对打开/创建/rename/delete
  experiments.py            705 行  A–F 八个受控实验
  experiments.json          624 行  实验结果
  test_native_backend.py    252 行  16 个 unittest
  python_compat.py          278 行  纯 Python 路径检查对照 P1–P4
  python_compat.json         70 行  对照结果
  probe_native.py            44 行  junction / 名字检查探针
  probe_dont.py              30 行  OBJ_DONT_REPARSE 探针
  probe_ancestor.py          20 行  祖先 junction 探针（G）
  probe_replace_swap.py      24 行  替换目标调包探针（H）
  probe_backup_link.py       18 行  备份路径 junction 探针（I）
  probe_rename.py            27 行  根目录 rename 探针
  smoke.py                   21 行  最小 smoke
```

职责边界：

- `errors.py` 只负责错误对象和状态码名称，不尝试模仿 `OSError`。
- `identity.py` 只从**已打开句柄**查询身份/属性，不按路径猜身份。
- `native_backend.py` 是唯一执行文件操作的原型模块；`experiments.py` 和测试只调用它。
- `experiments.py` 每个用例使用独立 `%TEMP%\veyra-0.7-experiments-*` 根，内含 `project` 和 `outside`，真实 `cmd /c mklink /J` 创建 junction。
- `python_compat.py` 是“路径检查对照”，不是生产候选。

---

## 3. 句柄与身份模型

### 3.1 根句柄

```python
with WinWriteBoundary(root_path) as boundary:
    boundary.create_file_atomic("report.md", data)
```

`WinWriteBoundary.__init__`：

1. `os.path.abspath(root)`。
2. `CreateFileW` 打开目录，得到 `root_handle`。
3. 从该句柄查询 `FileInfo`，拒绝“不是目录”和“最终组件是 reparse”。
4. 之后所有子操作都使用 `root` 句柄作为 `RootDirectory`，不再用字符串拼接路径。

**限制**：第 2 步仍按路径打开；如果 `root_path` 的祖先中有 junction，`CreateFileW` 会跟随祖先。`FILE_FLAG_OPEN_REPARSE_POINT` 只保护最终组件。实验 G 已证明。

### 3.2 子句柄与相对操作

`NativeHandle` 保存：

- 原始 `HANDLE`；
- 标签（用于错误信息和临时清理）；
- 打开时从同一句柄查询的 `FileInfo`。

`FileInfo`：

```text
FileIdentity(volume_serial, file_id, source)
attributes, reparse_tag, size, number_of_links,
is_directory, delete_pending, is_reparse
```

`FileIdentity` 的来源字段是 `FileIdInfo` 或 `FileIndex`，让证据能说明“这个 ID 是从哪个 API 来的”，而不是把两种来源混成一个不可解释的值。

### 3.3 简单名规则

`_simple_name` 拒绝：

- 非字符串、空、`.`、`..`；
- 前后空白；
- `/`、`\`、`:`、NUL；
- 结尾 `.` 或空格；
- 长度 > 255；
- DOS 保留名（`CON`、`PRN`、`AUX`、`NUL`、`COM1..9`、`LPT1..9`，按第一个点前的基础名大写判断）。

`_nt_create_relative` 再次拒绝 `/`、`\`、`:`、NUL，避免绕过 `_simple_name` 直接调用底层函数。

未覆盖：Unicode 规范化/大小写折叠、8.3 短名、尾点/空格在 NTFS 上的特殊解析、`\\?\` 长路径、UNC、卷挂载点。原型依靠 `OBJ_CASE_INSENSITIVE` 处理常规大小写，但没有做规范化测试。

### 3.4 原子创建

`create_file_atomic(name, data)`：

1. 生成 `.veyra-tmp-<24 hex>`。
2. `create_exclusive` 以 `FILE_CREATE` 创建，写数据，flush。
3. `rename(temp, name, replace=False, posix=False)`：目标已存在则失败，不覆盖。
4. `_verify_named(name, temp.info.identity, temp.sha256())`：按名字重新打开，验证身份和哈希。
5. 失败且尚未 rename 时，`_cleanup_temp` 只删除身份匹配的临时对象。

关键点：创建路径**不替换已有目标**；因此“目标在检查后被创建”会以 `STATUS_OBJECT_NAME_COLLISION` 失败，而不是覆盖。

### 3.5 替换

`replace_file(name, data, *, pin_target=True, posix=True)`：

1. 创建临时文件。
2. `open_child(name, delete=True, share_delete=True)` 打开目标。
3. 拒绝目录、拒绝 reparse；目标不存在时退化为创建。
4. `pin_target=True` 时保持目标句柄打开。
5. 用 POSIX rename（`FileRenameInformationEx`）替换同名目标。
6. 验证新名字的身份和哈希。

这里有一个必须写清的取舍：

- `delete_file` 用 `share_delete=False` 钉住目标，外部不能 rename/delete 它；但这也意味着自己也不能在目标打开时替换它。
- `replace_file` 为了 POSIX 替换，目标句柄使用 `share_delete=True`；外部可以在检查后把目标改名并放入新对象。POSIX rename 只按名字提交，于是会替换**新对象**。
- 因此，“目标句柄钉住”不等于“提交绑定到该目标对象”。实验 H 已复现。

### 3.6 删除

`delete_file(name, *, backup_path, expected_identity, posix=True)`：

1. 要求非空 `backup_path`、非空 `expected_identity`。
2. `os.path.exists(backup_path)` 为真则 `E_BACKUP_EXISTS`。
3. 备份父目录不存在则 `E_BACKUP_PARENT_MISSING`。
4. `open_child(name, delete=True, share_delete=False)`：拒绝目录/reparse；比较 `expected_identity`。
5. 读取全部内容，计算 SHA-256，写备份（`open("xb")` + `flush` + `fsync`）。
6. `delete_handle(posix=True)`。
7. 检查名字是否仍存在；仍存在则 `E_DELETE_NOT_EFFECTIVE`。

**限制**：

- `backup_path` 没有做“是否在边界内”“父目录是否 reparse”“目标是否已存在”的句柄化校验。实验 I 已证明 junction 备份父目录会把备份写到项目外。
- 备份写成功但 delete 失败时，备份会留下；没有回滚。
- 目录删除被显式禁用；没有递归删除。
- 删除成功后如果名字被重新创建，最后的名字存在性检查会报 `E_DELETE_NOT_EFFECTIVE`，但无法撤销已完成的删除。

### 3.7 错误分类

`BackendError` 基类；三类子类：

- `NativeError`：保留 `operation`、NTSTATUS、状态名。
- `Win32Error`：保留 `operation`、`winerror`、`FormatError` 文本。
- `PolicyError`：在触碰文件系统前拒绝，带稳定 `code`。

原型使用的策略错误码：

```text
E_ROOT_NOT_DIRECTORY
E_REPARSE_ROOT
E_BAD_NAME
E_REPARSE_REFUSED
E_DIRECTORY_REPLACE_REFUSED
E_DIRECTORY_DELETE_REFUSED
E_IDENTITY_REQUIRED
E_IDENTITY_MISMATCH
E_CONTENT_MISMATCH
E_BACKUP_REQUIRED
E_BACKUP_EXISTS
E_BACKUP_PARENT_MISSING
E_DELETE_NOT_EFFECTIVE
```

这些码是原型内部合同，不是生产 API 合同；生产化时需要在 `docs/pack-contract.md` 或新的错误合同里重新定稿。

---

## 4. 创建、替换、删除实验

运行命令：

```text
cd C:\Users\lenovo\AppData\Local\Temp\veyra-0.7-native-20261121093741-aab90b8c
python experiments.py
```

结果：**8/8 pass**，退出码 0，写入 `experiments.json`。

### 4.1 A — 根路径被 rename + junction 替换

步骤：

1. 打开 `project` 根句柄。
2. `os.rename(project, project-moved)`。
3. `mklink /J project -> outside`。
4. 用根句柄 `create_file_atomic("a.txt")`。

结果：

```text
moved/a.txt           = true
outside/a.txt         = false
project_is_reparse    = true
project_reparse_tag   = 0xA0000003
```

解释：根句柄仍指向原对象；路径 `project` 已变成 junction，但相对创建不受影响。**这证明“句柄钉住根对象”在根已经被打开之后有效**；它不证明“根打开之前祖先路径安全”（见 G）。

### 4.2 B — 子目录 rename 后句柄仍指向原对象

步骤：

1. `child` 是 `project` 下的普通目录，写入 `note.txt = "B"`。
2. 打开 `child` 句柄。
3. `rename(child, child-moved)`。
4. `open_child_at(child_handle, "note.txt")` 读取。
5. 按路径 `project/child` 打开，观察 reparse 状态。

结果：

```text
path_child_is_reparse = true
pinned_child_read     = "B"
outside/note.txt      = false
```

解释：钉住的 child 句柄仍指向 moved；路径 `child` 已经是 junction。句柄相对读取稳定，路径检查会被替换绕过。

### 4.3 C1 — 根替换下的原子创建

步骤：

1. 创建临时文件并写入（此时先关闭临时句柄）。
2. `os.rename(project, moved)`。
3. 建立 junction `project -> outside`。
4. 用钉住的根句柄按名字重新打开临时文件，提交 rename 到 `c1.txt`。

结果：

```text
moved/c1.txt  = true
outside/c1.txt = false
```

第一次尝试时，临时句柄保持打开导致 `os.rename(project, moved)` 报 `PermissionError [WinError 5]`。这不是“绕过成功”，而是 Windows 的真实约束：**打开的子文件句柄会阻止父目录 rename**。修正后的流程先关闭临时句柄，再用根句柄按名字重开，仍然能完成原子创建。

### 4.4 C2 — 目标被 junction 替换后的替换

步骤：

1. 目标 `target.txt` 被删除，建立 junction `target.txt -> outside`。
2. `replace_file("target.txt", ...)`。

结果：

```text
status              = PolicyError
code                = E_REPARSE_REFUSED
outside/new         = false
temp_leftovers      = []
```

解释：静态 junction 目标会被拒绝；但这只覆盖“打开时已经是 junction”的情况，不覆盖“打开后调包”（实验 H）。

### 4.5 D1 — 删除时身份不匹配

步骤：

1. 创建 `target.txt`，记录身份。
2. 删除并重新创建同名文件（新身份）。
3. 用旧身份调用 `delete_file`。

结果：

```text
status        = PolicyError
code          = E_IDENTITY_MISMATCH
new_content   = "new"
backup_created = false
```

解释：身份不匹配时拒绝删除，不创建备份。这是“无法确认身份就 fail-closed”的直接证据。

### 4.6 D2 — 删除 junction 目标

步骤：

1. 目标被 junction 替换。
2. `delete_file` 用预期身份调用。

结果：

```text
status        = PolicyError
code          = E_REPARSE_REFUSED
outside/new   = false
backup_created = false
```

解释：删除路径也拒绝 reparse，不会沿 junction 删除外部对象。

### 4.7 E — 清理时同名外部文件

步骤：

1. 创建 `.tmp-e`，记录身份。
2. 关闭临时句柄，rename 根目录，建立 junction。
3. 用根句柄重新打开 moved 中的 `.tmp-e`。
4. 在 `outside` 创建同名 `.tmp-e = "outside"`。
5. 调用 `_cleanup_temp(temp_reopened)`。

结果：

```text
moved_temp_exists    = false
outside_temp_exists  = true
outside_temp_content = "outside"
```

解释：清理只删除临时句柄身份匹配的对象；同名的外部文件不是该对象，必须保留。清理逻辑没有按路径盲删。

### 4.8 F — 身份在 rename/recreate/junction 后的变化

结果：

```text
同卷 VolumeSerialNumber 不变
同一个文件 rename 后 FileId 不变：first_file.identity == old_file.identity
同名路径重新创建后 FileId 变化：
  first_directory != second_directory
  first_file      != second_file
junction 打开后 is_reparse=true, tag=0xA0000003
```

解释：`VolumeSerial + FileId` 可以作为对象身份；FileId 对“同一个对象”在 rename 后保持稳定，但同名路径重新创建后会给新对象新的 FileId。不能只看路径、大小或时间戳。FileId 不是跨卷/跨机器的永久标识，但对“当前对象是否还是我打开的那一个”足够。

### 4.9 实验结论

- 句柄相对操作在根/子目录被路径替换后仍稳定。
- 原子创建、身份绑定删除、临时清理都有可工作的原型。
- 替换的“目标绑定”不完整。
- 根打开之前的祖先路径不在保护范围内。
- 备份路径不在保护范围内。

---

## 5. junction / reparse 实验

### 5.1 创建方式

所有 junction 都用：

```text
cmd /c mklink /J <link> <target>
```

不使用管理员权限，不使用符号链接（symlink 在当前环境需要额外权限，未作为通过项）。

### 5.2 静态检测

- `CreateFileW` 打开根时带 `FILE_FLAG_OPEN_REPARSE_POINT`；如果最终组件是 junction，根句柄会打开 junction 本身，`root.info.is_reparse` 为真。
- `open_child` 默认带 `FILE_OPEN_REPARSE_POINT`；最终组件是 junction 时会打开 junction 本身，`is_reparse` 为真。
- 替换/删除在打开目标后检查 `is_directory`/`is_reparse`，拒绝目录和 reparse。
- 目录形状的目标如果以 `STATUS_FILE_IS_A_DIRECTORY` 打开，`_classify_directory_target` 再用目录方式打开一次，区分“真实目录”和“目录 junction”。

### 5.3 G — 祖先 junction（关键缺口）

补充探针 `probe_ancestor.py`：

```text
base\outside\project        真实项目目录
base\ancestor -> outside    junction
root = base\ancestor\project
```

结果：

```text
WinWriteBoundary(root) 构造成功
root.info.is_reparse = false
create_file_atomic("ancestor.txt") 成功
outside\project\ancestor.txt = true
```

解释：最终组件 `project` 是普通目录，所以 `FILE_FLAG_OPEN_REPARSE_POINT` 不会阻止 `CreateFileW` 跟随祖先 `ancestor`。之后的相对操作都“忠实地”写到了 outside/project。**这是当前原型最严重的边界缺口。**

修复方向（未实现）：

- 从卷根或受信锚点开始，逐级用句柄相对打开，每级检查 `is_reparse`；或
- 打开根后，从根句柄反查完整路径并与预期路径比较；或
- 要求根路径来自一个已经由上层安全创建/验证的句柄，而不是任意字符串。

### 5.4 H — 替换目标调包（关键缺口）

补充探针 `probe_replace_swap.py`：

1. 创建 `r.txt = "original"`，打开目标句柄（`delete=True, share_delete=True`）。
2. 把目标 rename 为 `r-old.txt`。
3. 在 `r.txt` 创建 `"attacker"`。
4. 创建临时文件 `"authorized"`。
5. 用 `rename(temp, "r.txt", replace=True, posix=True)` 提交。

结果：

```text
commit OK
r.txt content = "authorized"
r.txt is the originally checked object? = False
```

解释：提交按名字替换了“新的 r.txt”，没有验证它是不是第 1 步打开的对象。**这不等于写到了项目外**（名字仍在根内），但它违反了“只替换已验证目标”的策略，也说明 `replace_file` 的目标钉住不能单独作为安全保证。

可能的修复方向：

- `replace_file` 接收 `expected_identity`，在提交前重新按名字打开并比较；但这仍有“比较后、rename 前”的窗口。
- 用目标句柄的父目录句柄 + 目标名字做 rename，仍无法把“名字”绑定到“对象”。
- 若要求严格绑定，可能需要“先删除已验证目标，再创建新名字”的两步策略，并接受失败时目标缺失/需恢复的语义；或者使用不共享删除的 target handle 阻止调包，但这会阻止 POSIX 替换自身，需要重新设计提交流程。
- 无论哪种，必须把“替换目标身份绑定”作为 H5 的未决设计点，而不是默认已解决。

### 5.5 I — 备份路径 junction（关键缺口）

补充探针 `probe_backup_link.py`：

```text
base\backup-link -> outside   junction
delete_file("t.txt", backup_path=base\backup-link\t.bak, expected_identity=...)
```

结果：

```text
delete OK
outside\t.bak exists = true
outside\t.bak content = b"DATA"
```

解释：`backup_path` 的父目录是 junction，`open("xb")` 跟随并写到 outside。删除本身在根内完成，但备份写到了项目外。**备份/恢复必须和临时文件、目标写入使用同一边界。**

### 5.6 `OBJ_DONT_REPARSE` 补充

见 §1.5。结论：

- 跟随打开时，`OBJ_DONT_REPARSE` 能让 `NtCreateFile` 返回 `STATUS_REPARSE_POINT_ENCOUNTERED`。
- 带 `FILE_OPEN_REPARSE_POINT` 时，它不会阻止你打开 reparse 本身；原型依赖 `is_reparse` 元数据做拒绝。
- 它不能替代祖先路径锚定。

---

## 6. 过程副作用与清理

### 6.1 副作用范围

- 原型目录只在 `%TEMP%\veyra-0.7-native-20261121093741-aab90b8c`。
- 测试和实验使用独立 `%TEMP%\veyra-0.7-*` 根；每个用例的 `project`/`outside` 都在该临时根内。
- 没有修改 `D:\Projects_vesper\veyra` 的生产代码、现有测试、`skills/`、`demo/` 或日志。
- 没有执行 `git add`、`git commit`、`git push`、`git reset`、`git checkout`、`git clean`、`git rebase`。
- 没有读取或修改真实玩家日志；没有触碰 `real_crash.log` 内容。

### 6.2 保护文件

本轮前后核验（只读，不读取内容）：

```text
report.md
  bytes  = 1570
  SHA256 = A11D09D49217CA3C6CD7C269268C148558EA488067E8D1399BCAFD7ABBC2D68D

real_crash.log
  bytes  = 21999
  SHA256 = CC1418B7690284787B55D3417383F23DD6DEAA47619BC41702F9F33895DCD54D
```

两者均未修改。

### 6.3 临时文件清理

- `%TEMP%\veyra-0.7-experiments-*`：无残留。
- `%TEMP%` 下的 `.tmp-*` / `.veyra-tmp-*`：无残留。
- 原型目录内的 `__pycache__`：最终清理删除。
- 原型目录本身：本次交付报告后删除；报告保留全部可复现命令和结果。
- 探针产生的 `veyra-probe-*`、`veyra-dont-*`、`veyra-ancestor-*`、`veyra-replace-swap-*`、`veyra-backup-link-*`：最终清理删除。

### 6.4 真实项目文件状态

本轮开始时：

```text
branch = main
HEAD   = 0fa1bbfff9c28e1b12c94b334f23b732d059926e
```

`git status --short` 中 README.md、README_zh.md、report.md 的修改，以及 Phase 3.x 的未跟踪 docs/tests/veyra_creation/veyra_pack，都是本轮之前已存在的状态；本轮唯一新增仓库文件是这份 0.7 报告。

---

## 7. Python / Windows 兼容性

### 7.1 实测矩阵

| 项目 | 结果 |
|---|---|
| Windows 11 10.0.26300（中文家庭版） | 实测 |
| Python 3.14.7（MSYS2 UCRT64） | 16 tests OK，0.144s |
| Python 3.15.0（CPython） | 16 tests OK，0.139s |
| Python 3.10–3.13 运行时 | **未实测** |
| Windows 10 / Server | **未实测** |
| 32 位 Python | **未实测** |

### 7.2 语法兼容性

对全部原型 `.py` 文件执行：

```python
ast.parse(source, feature_version=(3, 10))
```

以及 3.11–3.15，均通过。也就是说，原型没有使用 3.10 之后才有的语法（如 `match` 本身没有使用，`|` 联合类型在 `from __future__ import annotations` 下也没有运行期求值问题）。

但语法通过不等于运行通过。以下风险仍需运行时验证：

- `ctypes` 结构体在 32 位下的对齐/大小；
- `FILE_RENAME_INFORMATION` 在 32/64 位下的 padding；
- `IO_STATUS_BLOCK` / `LARGE_INTEGER` 等结构体布局；
- `ntdll` 导出名/调用约定在不同 Windows 版本上的稳定性；
- `GetFileInformationByHandleEx(FileIdInfo)` 在非 NTFS/网络/ReFS 上的行为。

### 7.3 Python 3.10/3.11 的 junction 检测缺口

纯 Python 对照使用：

```python
checker = getattr(os.path, "isjunction", None)
return bool(checker(str(path))) if checker is not None else False
```

`os.path.isjunction` 在 Python 3.12 才提供。3.10/3.11 上 `checker is None`，函数返回 `False`，**会把 junction 当成普通目录**。这不是原生后端的依赖（原生后端用 `FileAttributeTagInfo`），但它是现有生产代码 H0 必须修复的兼容性缺口。

### 7.4 `ntdll` 风险

- `NtCreateFile`/`NtSetInformationFile` 有官方 DDI 文档，但不是 Win32 API 的稳定合同；行为可能随 Windows 版本变化。
- 原型通过 `ctypes` 直接调用，未使用第三方包；这降低了依赖，但也意味着 ABI/结构体错误不会被类型系统发现。
- 当前只在 Windows 11 + 两个 Python 版本上验证；不能把“本机通过”写成“跨 Windows 版本保证”。

### 7.5 无第三方依赖

原型导入的都是标准库或本地模块：

```text
ctypes, dataclasses, hashlib, json, os, pathlib, secrets,
shutil, stat, subprocess, sys, tempfile, typing, unittest
```

没有 `pywin32`、没有 `winsdk`、没有 `cffi`。

---

## 8. 纯 Python 对照

### 8.1 对照设计

`python_compat.py` 只做路径检查：

- `PathBoundary(root)` 保存 `Path`。
- `assert_root_safe`：`is_dir` + `is_link_like`。
- `assert_target_safe`：名字检查 + `lexists` + `is_link_like`。
- `create`：先检查，再 `Path.write_bytes`。

这正是现有生产代码容易写出的“检查后使用”模式。

### 8.2 结果

| 用例 | 场景 | 结果 | 项目外副作用 |
|---|---|---|---|
| P1 | 检查前父路径已被 junction 替换 | `E_PARENT_LINK` 拒绝 | 无 |
| P2 | 检查通过后父路径被替换，再 `write_bytes` | `ok` | `outside/p2.txt` 被创建 |
| P3 | 已检查的目录组件被替换为 junction，再写子文件 | `ok` | `outside/p3.txt` 被创建 |
| P4 | 静态最终组件是 junction | `E_TARGET_LINK` 拒绝 | 无 |

### 8.3 结论

- 纯路径检查能拒绝“检查时已经存在”的替换（P1、P4）。
- 它无法消除“检查之后、写入之前”的竞态（P2、P3）。
- 因此，**纯 Python 路径检查不能作为“项目外零写入”的验收证据**；只能作为最佳努力（best-effort）。
- 如果团队拒绝 `ntdll`，必须把这一限制写进合同和文档，不能沿用“零外部写入”的表述。

---

## 9. 未实现 / 未验证

### 9.1 未实现（原型明确不做）

| 项目 | 状态 | 影响 |
|---|---|---|
| 递归创建父目录（`ensure_dir`） | 未实现 | H1 需要；当前只接受已存在根 |
| 目录删除 / 递归删除 | 明确禁用 | 符合任务要求；但调用方需要自己的目录生命周期策略 |
| 项目根自动删除 | 未实现 | 符合“根永不自动删除” |
| 恢复/回滚 | 未实现 | 备份存在但无 restore |
| 目录 fsync / 崩溃一致性 | 未实现 | 断电/崩溃窗口未验证 |
| 备份路径边界校验 | 未实现 | 实验 I 已证明可写到项目外 |
| 替换目标身份绑定 | 未实现 | 实验 H 已证明可替换调包对象 |
| 祖先路径锚定 | 未实现 | 实验 G 已证明可写到项目外 |
| POSIX 后端（`openat`） | 未实现 | 本阶段只做 Windows |
| 生产 API / 合同 | 未实现 | 本报告只给原型结论 |

### 9.2 未验证（不能写成保证）

| 项目 | 风险 |
|---|---|
| Python 3.10–3.13 运行时 | ctypes 布局/行为未实测 |
| Windows 10 / Server | 未实测 |
| 32 位 Python | 结构体对齐/调用约定未实测 |
| 符号链接（非 junction） | 创建权限不足，未纳入通过项 |
| 硬链接 | 身份相同，删除/替换语义未测 |
| 卷挂载点 / 跨卷 | 未测；VolumeSerial 比较需要跨卷策略 |
| UNC / 网络共享 / SMB | 未测；`FileIdInfo` 可能不可用或语义不同 |
| ReFS / FAT / exFAT | 未测；reparse/identity 行为不同 |
| 8.3 短名 / 大小写仅变更 rename | 未测 |
| 长路径 `\\?\` / 尾点/尾空格 | 未测 |
| ACL / 只读属性 / 共享冲突 | 仅覆盖常规情况 |
| 并发多进程压力 | 未做压力测试 |
| 进程被 kill / 句柄泄漏 | 只依赖 `__del__` 和进程退出；未做泄漏检测 |
| 断电/崩溃后的临时文件/备份 | 未做故障注入 |
| `OBJ_DONT_REPARSE` 跨版本行为 | 只在本机观察 |

### 9.3 已观察到但未纳入 A–F 的补充实验

- G：祖先 junction 绕过根锚定（写项目外）。
- H：替换目标调包（替换未验证的新对象）。
- I：备份路径 junction（备份写项目外）。
- `OBJ_DONT_REPARSE` 跟随打开与 `FILE_OPEN_REPARSE_POINT` 的差异。

这些补充实验说明：**原型能力是“可用的原语”，不是“完整的边界”。**

---

## 10. 推荐决策

### 10.1 是否接受 `ntdll`

推荐：**有条件接受，且只作为 Windows 严格后端的内部实现，不作为跨平台承诺。**

理由：

- Win32 没有等价的“相对目录句柄创建 + POSIX 替换 + 句柄身份”组合；`NtCreateFile`/`NtSetInformationFile` 是目前能在纯标准库内验证这些语义的路径。
- 原型已证明核心原语可用，且没有引入第三方依赖。
- 但 `ntdll` 不是稳定 Win32 合同，必须配套支持矩阵、fail-closed 和独立审计。

条件（缺一不可）：

1. **根锚点**：不能再用 `CreateFileW(任意路径)` 作为安全根。必须从卷根/受信锚点逐级句柄相对打开，或使用上层已经安全创建并验证的根句柄。实验 G 是硬阻断。
2. **替换绑定**：`replace_file` 必须解决实验 H 的调包窗口；至少在合同中明确“replace 只保证名字，不保证对象”，并禁止把它用于安全关键替换。推荐增加 `expected_identity` 并重新设计提交流程。
3. **备份/临时/恢复同一边界**：`backup_path` 必须由边界内部生成或经过同样的句柄化校验；实验 I 是硬阻断。
4. **支持矩阵**：Python 3.10–3.13、Windows 10、32 位、NTFS 之外的文件系统必须逐项验证或明确 fail-closed。
5. **独立审计**：H6 由未参与实现的人复核 API、结构体、错误码和残余风险。

### 10.2 如果不接受 `ntdll`

则必须：

- 继续使用纯 Python 路径检查，但明确标注为 **best-effort**；
- 删除/改写所有“项目外零写入”的承诺；
- 把 P2/P3 的竞态写进威胁模型和用户文档；
- 不在 3.2.2.1 中声称达到严格边界。

这不是“失败”，而是诚实的降级；比用路径检查冒充严格边界更符合 Veyra 的证据可信原则。

### 10.3 是否进入 3.2.2.1

**不建议现在进入。** 建议顺序：

1. H0：根锚点 + Python 3.10/3.11 junction 检测 + 身份原语生产化。
2. H5：把原型 API 收敛成生产接口；关闭 G/H/I 三类缺口；补支持矩阵。
3. H1/H2/H3/H4：按 0.6 设计接入 `WriteBoundary`、init、digest、pack/triage。
4. H6：独立只读审计。
5. 之后再评估 3.2.2.1 的写实现。

---

## 11. 对 H0–H6 的影响

| 批次 | 本原型提供 | 仍需完成 |
|---|---|---|
| H0 兼容性与身份原语 | `FileIdentity`、`FileInfo`、reparse 分类、NTSTATUS/Win32 错误 | 3.10/3.11 `is_link_like` 修复；根锚点；跨卷/非 NTFS 策略 |
| H1 目录与原子写 | `create_file_atomic`、`replace_file` 原型；临时文件清理 | `ensure_dir`、父目录策略、目录 fsync、统一 `WriteBoundary` API |
| H2 init 与清理 | 删除身份绑定；根不自动删除；目录删除禁用 | `ProjectStore.init` 逐级创建 + 租约；移除 `created_root` 目录清理；失败时目录语义 |
| H3 日志与 digest | 句柄读取/身份查询可复用 | `append_line`、digest 遍历仍需接入；路径遍历仍有 junction 风险 |
| H4 pack/triage 适配 | 原子写/替换/删除原语 | `init_pack`、`export_pack`、`write_report_text` 迁移；错误码合同 |
| H5 严格后端 | 可行性已验证；核心 API 形状已跑通 | 关闭 G/H/I；生产 API；POSIX 后端；支持矩阵；性能/并发 |
| H6 审计与验收 | 本报告 + 实验数据 | 独立只读审计；残余风险清单；是否接受 `ntdll` 的正式决策 |

---

## 12. 与 Veyra 最终目标的一致性

一致的部分：

- **证据可信度优先**：身份来自句柄，哈希来自实际内容；不靠路径猜测，不把模型输出当事实。
- **Python 负责确定性操作**：原型只用标准库，不做 AI 推断；与“Python 提取/校验，AI 理解/归纳”的分工一致。
- **不引入不必要依赖**：没有第三方包，没有引入服务/框架。
- **不扩大产品范围**：只解决本地文件写入边界，不碰 Web 平台、GitHub App、多 Agent。

需要警惕的部分：

- `ntdll` 的复杂度和跨版本风险，可能让“安全”变成“只在开发者机器上安全”。
- 如果为了追求“零外部写入”而把替换/备份语义做得过于复杂，可能违反“优先完善现有架构，不为新功能推翻实现”。
- 当前原型已经证明：**路径检查不能提供严格边界**；继续在纯 Python 上堆检查，只会增加代码量而不消除竞态。

最终建议：

> 把 0.7 的结论写成“句柄相对原语可行，但边界尚未闭合”；接受 `ntdll` 作为 H5 的内部实现候选，同时把 G/H/I 列为进入 3.2.2.1 前的硬阻断项。不要在报告或 README 中把当前原型描述为“已实现零项目外写入”。

---

## 附录 A：复现命令与结果

### A.1 单元测试

```text
cd C:\Users\lenovo\AppData\Local\Temp\veyra-0.7-native-20261121093741-aab90b8c

C:\msys64\ucrt64\bin\python.exe -m unittest -v test_native_backend.py
Ran 16 tests in 0.144s
OK

C:\Users\lenovo\AppData\Local\Programs\Python\Python315\python.exe -m unittest -v test_native_backend.py
Ran 16 tests in 0.139s
OK
```

覆盖：

```text
test_identity_is_handle_derived
test_create_atomic_verifies_identity_and_hash
test_create_refuses_existing_target
test_create_directory_and_relative_file
test_replace_posix_with_pinned_target
test_replace_refuses_junction_target
test_delete_requires_backup_and_keeps_target
test_delete_with_backup
test_delete_refuses_existing_backup
test_delete_rejects_directory
test_delete_rejects_identity_mismatch
test_junction_component_is_exposed_as_reparse
test_junction_root_is_rejected
test_open_missing_child_status
test_pinned_root_survives_directory_rename
test_simple_name_rejects_traversal_and_paths
```

### A.2 A–F 实验

```text
python experiments.py

A: pass
B: pass
C1: pass
C2: pass
D1: pass
D2: pass
E: pass
F: pass
wrote experiments.json
exit=0
```

### A.3 纯 Python 对照

```text
python python_compat.py

P1 refused  E_PARENT_LINK
P2 bypass   outside/p2.txt
P3 bypass   outside/p3.txt
P4 refused  E_TARGET_LINK
wrote python_compat.json
exit=0
```

### A.4 补充探针

```text
python probe_dont.py
follow open dont_reparse True  -> STATUS_REPARSE_POINT_ENCOUNTERED
follow open dont_reparse False -> OK (outside target)
```

```text
python probe_ancestor.py
boundary root ok
create ok
outside/project/ancestor.txt = true
```

```text
python probe_replace_swap.py
commit OK
r.txt content = b"authorized"
r.txt is the originally checked object? = False
```

```text
python probe_backup_link.py
delete OK
outside/t.bak exists = true
outside/t.bak content = b"DATA"
```

---

## 附录 B：本轮新增/修改文件

仓库内：

- 新增 `docs/phase-3.2.2.0.7-windows-native-backend-prototype.md`（本报告）。

仓库外（临时，最终删除）：

- `%TEMP%\veyra-0.7-native-20261121093741-aab90b8c\` 下的原型源码、测试、JSON、探针。
- 各 `%TEMP%\veyra-0.7-*`、`veyra-probe-*`、`veyra-dont-*`、`veyra-ancestor-*`、`veyra-replace-swap-*`、`veyra-backup-link-*` 临时目录。

未修改：

- 生产代码、现有 `tests/`、`skills/`、`demo/`、`report.md`、`real_crash.log`、Git 历史。

---

## 附录 C：审阅时建议重点看的三件事

1. 是否接受“祖先 junction 未锚定”作为进入 3.2.2.1 的硬阻断项（实验 G）。
2. 是否接受“替换只保证名字、不保证对象”的明确合同，还是要求 H5 先解决实验 H。
3. 是否接受 `ntdll` 作为 Windows 严格后端的内部实现；如果不接受，是否接受把产品降级为 best-effort 路径检查。

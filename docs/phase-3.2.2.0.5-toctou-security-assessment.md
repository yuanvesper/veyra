# Veyra Phase 3.2.2.0.5 — `ProjectStore.init()` 目录创建 TOCTOU 专项安全评估

- 文档状态：只读安全评估，等待用户与 ChatGPT 审阅
- 评估日期：2026-11-21
- 适用基线：`main` / `0fa1bbfff9c28e1b12c94b334f23b732d059926e`
- 关联实现：`veyra_creation/store.py`、`veyra_pack/paths.py`、`veyra_pack/contract.py`
- 关联设计：`docs/phase-3.2.2-operation-executor-design.md` §5.1–§5.6
- 本轮范围：**只调查、只复现、只写评估报告**；不修改生产代码，不修改测试代码，不进入 Phase 3.2.2.1
- 结论性质：代码审计 + 临时目录中的真实 junction 受控重放；**不是**自然竞争频率的统计结论

---

## 0. 结论先行

### 0.1 对原始假设的修正

任务书中描述的简单序列是：

```text
构造 ProjectStore
→ 父目录被替换为指向 outside 的 junction
→ 调用 init()
→ mkdir(parents=True) 在 outside 创建目录
```

**这个序列不会按上述方式发生。** `init()` 的第一行实际调用是 `self._assert_internal()`（`store.py:287`），而 `_assert_internal()` 在 `store.py:442` 调用了 `_assert_no_link(self.creation_dir)`。该检查会沿 `creation/` 的完整路径逐级 `lstat`，在 `mkdir` 之前就发现父目录 junction，并抛出 `E_LINK_BOUNDARY`。

实验 A 与现有测试 `tests/test_creation_path_policy.py:200-208` 都确认：构造后、`init()` 前替换父目录时，`root.mkdir` 根本不会被调用，`outside/project` 不存在。

### 0.2 但仍然存在真实可重放的窄窗口

在 `_assert_internal()` 之后、`root.mkdir(...)` 之前，代码没有再次检查或固定父目录。如果另一个进程在这个窗口内把父目录替换为 junction，则：

1. `self.root.mkdir(parents=True, exist_ok=True)`（`store.py:343`）会跟随 junction，在项目外创建 `outside/project`；
2. 随后的 `_assert_no_link(self.root)`（`:344`）才发现 junction 并抛出 `E_LINK_BOUNDARY`；
3. 清理逻辑（`:374-394`）尝试用 `root.rmdir()` 删除 `outside/project`。

实验 B 在真实 junction 上重放该交错，观察到 `outside/project` 在检查点已经存在，随后被清理删除。**目录创建确实发生了，只是最终状态又恢复为空。**

### 0.3 清理逻辑不可靠，且可能删除项目外既有目录

- 实验 C：如果并发方在清理前向 `outside/project` 写入文件，`root.rmdir()` 因目录非空而失败，项目外留下 `outside/project/concurrent.txt`。
- 实验 E：如果在 `root.mkdir` 之后、`_assert_no_link` 之前替换父目录，并让 `outside/project` 是**原本已存在**的空目录，清理会把该既有目录删除；真正的项目目录则留在被重命名后的 `late-parent-real/project`。
- 实验 D：若 `created_root=False`，清理不会删除既有 `outside/project`，说明结果取决于时序标志，而不是可靠的目录身份。

### 0.4 总判断

| 问题 | 结论 |
|---|---|
| 原始“构造后、init 前替换”序列是否会在项目外创建目录 | **否**；`_assert_internal` 在 `mkdir` 前拦截 |
| `_assert_internal` 后、`mkdir` 前的窄窗口是否可越界创建 | **是**；真实 junction 受控重放成功，产生项目外空目录 |
| 是否可能造成项目外文件/目录创建 | **是**；已验证项目外目录创建；文件写入存在同类 check-then-use 窗口，见 §3.3 |
| 清理能否可靠恢复 | **否**；可能残留文件，也可能删除项目外既有空目录 |
| 是否影响未来 Operation Executor | **是**；执行器的 create/replace/delete、temp、backup、cleanup 会复用同一类路径操作 |
| 是否应先修复再进入 3.2.2.1 | **是，至少阻断写操作**；见 §12 |
| 本轮是否修改生产代码/测试代码 | **否**，只新增本报告 |

### 0.5 证据等级

| 结论 | 等级 | 依据 |
|---|---|---|
| 简单序列被 `_assert_internal` 拦截 | 高 | 代码路径 + 真实 junction 实验 A + 现有测试 |
| 窄窗口可创建项目外目录 | 高（受控交错） | 实验 B；真实 junction；注入点只控制替换时机 |
| 清理可残留项目外文件 | 中高（受控交错） | 实验 C；并发写入是受控模拟，不是自然发生 |
| 清理可删除既有空目录 | 高（受控交错） | 实验 E；真实 junction；既有目录被实际删除 |
| 自然竞争发生概率 | 未验证 | 未做压力统计，不能据此声称“经常/几乎不会发生” |
| 文件写入越界 | 中 | 代码分析；未做完整文件写入实验 |
| 非 Windows 平台行为 | 未验证 | 本轮只为 Windows junction；项目其他平台仍需单独评估 |

---

## 1. 评估范围与不变量

### 1.1 本次保护的对象

1. 项目根目录以外的文件系统内容不得被创建、覆盖或删除。
2. 项目内部的 `creation/`、`state.json`、`spec.json`、`decisions.ndjson` 等只能落在真实项目根内。
3. 失败清理不得误删不是本次调用创建的目录或文件。
4. 上述保证必须能在 failure injection 和真实 junction 下被测试观察到，不能只检查最终目录是否存在。

### 1.2 本轮不做的事

- 不实现修复，不改变 `ProjectStore` API。
- 不重构 `veyra_pack.paths`。
- 不实现 Operation Executor、锁、事务、备份或恢复。
- 不运行会修改项目文件的命令，不执行 Git 写操作。
- 不把“最终 outside 为空”当作“执行过程中没有 outside 写入”的证据；本报告专门区分这三类状态。

---

## 2. 当前实现调用链

### 2.1 构造器：先检查，再 resolve，再检查

`ProjectStore.__init__`（`veyra_creation/store.py:198-268`）：

1. 拒绝空字符串（`:204-209`）。
2. `normalize_lexical_path()`（`:211`）：在 `ntpath.abspath` 之前检查原始组件；拒绝尾点/尾空格、UNC、设备命名空间、ADS、保留设备名等。
3. `find_link_component(declared)`（`:228`）：对声明路径逐级 `lstat`，发现 symlink/junction 即拒绝。
4. `declared.resolve()`（`:236`）：解析真实路径。
5. `paths_equivalent(declared, resolved)`（`:243`）：要求声明路径和解析路径等价。
6. 对 `resolved` 再做一次 `find_link_component()`（`:251`），防止 resolve 期间出现链接。
7. 只有以上全部通过，才设置 `self.root` 和 `creation/` 下的各路径（`:258-268`）。

`find_link_component()`（`veyra_pack/paths.py:289-311`）的行为是逐级 `lstat`：

- 缺失组件继续检查后续组件（`:305-306`）；
- 已有组件若是 symlink/junction 则返回（`:309-310`）；
- 不调用 `resolve()`，因此不会先把链接隐藏掉。

**构造器的保证是“构造时刻的路径状态”，不是“未来所有使用时刻的路径状态”。**

### 2.2 `init()` 的执行顺序

`ProjectStore.init()`（`store.py:274-395`）的关键顺序：

```text
:287   self._assert_internal()
:288   root.exists() and not root.is_dir() -> E_PROJECT_ROOT
:290   state_path.exists() -> E_PROJECT_EXISTS
:296   creation_dir.exists() and any(...) -> E_PROJECT_EXISTS
:303   ID/version/name 校验
:327   now = self._now()
:328   spec = make_initial_spec(...)
:329   validate_spec(...)
:337   state = _default_state(...)

:339   creation_existed = self.creation_dir.exists()
:340   created_root = not self.root.exists()
:341   created = []
:342   try:
:343       self.root.mkdir(parents=True, exist_ok=True)
:344       self._assert_no_link(self.root)
:345       self.creation_dir.mkdir(parents=True, exist_ok=True)
:346       self._assert_no_link(self.creation_dir)
:347       self.runs_dir.mkdir(parents=True, exist_ok=True)
:348       self._assert_no_link(self.runs_dir)
:349       self._write_json(self.spec_path, spec)
:351       self._write_json(self.locks_path, ...)
:353       self._write_json(self.placeholders_path, ...)
:358       self._assert_no_link(self.decisions_path)
:359       self.decisions_path.write_text("", ...)
:361       event = self._append_decision_raw(...)
:373       self._write_json(self.state_path, state)
:374   except BaseException:
:375       for path in reversed(created): path.unlink()
:380       if not creation_existed:
:382           self.runs_dir.rmdir()
:386           self.creation_dir.rmdir()
:389       if created_root:
:391           self.root.rmdir()
:394       raise
```

### 2.3 `_assert_internal()` 是 `init()` 的前置检查

`store.py:435-442`：

```python
def _assert_internal(self) -> None:
    if self.root.exists() and not self.root.is_dir():
        raise CreationError(...)
    if self.creation_dir.exists() and not self.creation_dir.is_dir():
        raise CreationError(...)
    self._assert_no_link(self.creation_dir)
```

注意：这里检查的是 `self.creation_dir`，它的路径包含 `self.root`。因此，即使 `self.root` 本身还不存在，只要父级某个组件已经被替换为 junction，`find_link_component(self.creation_dir)` 也会在父级组件上发现它。

### 2.4 `_assert_no_link()` 是检测，不是原子保护

`store.py:418-433`：

```python
def _assert_no_link(self, path: Path, location: str = "project") -> None:
    link = find_link_component(path)
    if link is None:
        return
    raise CreationError(
        "E_LINK_BOUNDARY",
        f"project path must not contain a symlink or junction: {link}",
        location,
    )
```

它只能回答“**调用这个函数的瞬间**，路径中是否已有链接”。它不能：

- 阻止下一次路径操作跟随新出现的 junction；
- 把“检查 + 创建”组合成原子操作；
- 回滚已经发生的项目外创建。

因此，“在创建前后各调用一次 `_assert_no_link()`”只能缩小窗口，不能称为彻底消除 TOCTOU。

### 2.5 写入路径也有独立的 check-then-use 窗口

`store.py:444-446`：

```python
def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
    self._assert_no_link(path)
    write_json_atomic(path, payload)
```

`write_json_atomic()`（`veyra_pack/contract.py:162-190`）还会执行：

```python
path.parent.mkdir(parents=True, exist_ok=True)  # :174
fd, tmp_name = tempfile.mkstemp(..., dir=str(path.parent))  # :176-178
...
os.replace(tmp_name, path)  # :184
```

也就是说，即使 `_write_json` 的检查通过，`write_json_atomic` 仍会：

1. 用 `parents=True` 创建父目录；
2. 在父目录创建临时文件；
3. 通过路径 `os.replace` 提交。

如果父目录在检查后被替换为 junction，这些操作都可能跟随到项目外。`decisions_path.write_text()`（`:359`）同样是“先 `_assert_no_link`、后写入”的路径操作。

---

## 3. 竞态形成条件与窗口划分

### 3.1 必须区分的六个时点

| 状态 | 时点 | 当前检查 | 说明 |
|---|---|---|---|
| T0 构造时 | `__init__` 结束 | 声明/resolve 前后两次 `find_link_component` | 只证明构造时无链接 |
| T1 `init()` 入口 | `:287` | `_assert_internal()` → `:442` 检查 `creation_dir` | **能拦住“构造后、init 前”替换** |
| T2 入口检查后 | `:287` 之后到 `:343` 之前 | 无再次检查 | **本轮发现的开放窗口** |
| T3 `root.mkdir` 后 | `:343` 之后到 `:344` 之前 | 尚未检查 | 清理可能指向被替换后的路径 |
| T4 检查后写入 | `:344` 之后到 `:373` | 每次写入前再检查一次 | 仍是 check-then-use |
| T5 失败清理 | `:374-394` | 使用 `created_root`/`created` 等路径标志 | 路径可能已被重定向 |

### 3.2 竞态形成的必要条件

需要同时满足：

1. 目标项目的某个祖先目录在检查之后、路径创建之前可被重命名/删除；
2. 攻击者或并发进程能在同一位置创建 junction；
3. 被替换的祖先在 `self.root` 或 `self.creation_dir` 的路径上；
4. 目标项目根/`creation/` 在替换时不存在（或存在性在检查后发生变化）；
5. 当前进程有权限在 junction 目标位置创建目录/文件。

这是**本地文件系统竞争**，不是远程网络攻击。需要拥有项目父目录的写/删除权限；普通用户可能具备，ACL 受限用户则不一定。它不是“任何日志内容都能触发”的模型输入漏洞。

### 3.3 具体窗口

#### 窗口 R1：构造后、`init()` 前

- 发生位置：`__init__` 结束到 `:287`。
- 当前防护：`_assert_internal()` 的 `:442` 检查。
- 结果：实验 A 确认不会进入 `mkdir`，不会创建 `outside/project`。
- 结论：**原始假设不成立**；现有测试 `test_ancestor_junction_introduced_after_construction_is_still_rejected` 也覆盖了最终状态。

#### 窗口 R2：`_assert_internal()` 后、`created_root`/`mkdir` 前

- 发生位置：`:287` 之后到 `:343`。
- 当前防护：无。
- 结果：实验 B 确认 `root.mkdir` 跟随 junction，在项目外创建空目录；随后 `:344` 抛错，清理删除该空目录。
- 结论：**真实存在窄窗口**；即使最终状态为空，执行过程中确实发生了项目外创建。

#### 窗口 R3：`root.mkdir` 后、`_assert_no_link(self.root)` 前

- 发生位置：`:343` 之后、`:344` 之前。
- 当前防护：无；此时真实的 `root` 已经创建，父目录被重命名后，`self.root` 指向 junction 目标。
- 结果：实验 E 确认 `created_root=True` 的清理会通过 junction 删除项目外既有空目录；真实项目目录留在被重命名的旧父目录下。
- 结论：**清理逻辑可能删除不是本次调用创建的目录**。

#### 窗口 R4：检查后、写入前

- 发生位置：`:344` 之后到 `:373`；以及 `_write_json` 内部 `:445-446`。
- 当前防护：每次 `_assert_no_link` 后再写入；不是原子的。
- 结果：本轮未做完整文件写入实验；代码上可跟随 junction。
- 结论：**文件级残余 TOCTOU 仍存在**；需要在 executor 设计中单独处理。

#### 窗口 R5：失败清理

- 发生位置：`:374-394`。
- 当前防护：仅基于 `created_root`、`creation_existed` 和 `created` 列表。
- 结果：实验 C/E 显示可能残留文件或误删目录。
- 结论：**清理不是事务性回滚**，不能作为安全保证。

### 3.4 `mkdir(parents=True)` 在 Windows 上的行为

`Path.mkdir(parents=True, exist_ok=True)` 最终走路径型 `CreateDirectory`/`os.makedirs`。Windows 路径解析会把 junction 当作透明重定向：

- 对已存在的 junction 组件，后续路径继续在目标目录内解析；
- 该 API 没有“遇到 reparse point 就失败”的选项；
- 因此 `outside/project` 会被真实创建，而不是仅仅“解析到 outside”。

实验 B/E 使用真实 junction（`cmd /c mklink /J`，`os.path.isjunction()` 为真），不是 symlink 模拟，已验证这一点。

---

## 4. 可控复现实验

### 4.1 实验环境与约束

- 操作系统：Windows。
- Python：`C:\Users\lenovo\AppData\Local\Programs\Python\Python315\python.exe`（3.15.0）。
- 所有实验都在 `%TEMP%` 下的独立临时目录中完成，未在仓库根目录或真实项目目录实验。
- junction 使用 `cmd /c mklink /J` 创建，实验中断言 `os.path.isjunction(link) is True`；未用 symlink 代替。
- 每个实验结束都检查并删除自己的临时目录；完成核验时 `%TEMP%` 下没有残留 `veyra-toctou-*` 目录。
- 未修改任何生产文件或测试文件。

### 4.2 关于“受控交错”的说明

自然竞争窗口非常窄，单纯并发压测容易得到“没观察到”而不能证明不可达。因此本实验使用两种**只控制替换时机、不改变路径实现**的注入：

1. 包装实例的 `store._now`：它在 `:327` 被调用，位于 `_assert_internal()` 之后、`created_root`/`mkdir` 之前；包装器在该点创建 junction。
2. 包装 `Path.mkdir`：在 `root.mkdir` 返回后、`:344` 的检查前，重命名父目录并建立 junction。

这两个注入点是为了**确定性地重放一个特定交错**，不是生产代码的一部分，也不是“自然竞争频率”的证据。它们证明的是：只要外部进程在这个时点完成替换，代码路径就会进入项目外创建。

### 4.3 实验结果

#### 实验 A：构造后、`init()` 前替换父目录

| 项目 | 结果 |
|---|---|
| junction 创建 | 成功，`isjunction=True` |
| 错误 | `CreationError E_LINK_BOUNDARY`，消息指向 `late-parent` |
| `store.root.mkdir` 调用次数 | **0** |
| `outside/project` | 不存在 |
| `outside/creation` | 不存在 |
| `outside/project/creation/state.json` | 不存在 |

解释：`init()` 的 `_assert_internal()` 在 `:287` 调用，`_assert_no_link(self.creation_dir)` 在 `:442` 发现 `late-parent` junction，尚未进入 `:343`。这与现有测试 `test_ancestor_junction_introduced_after_construction_is_still_rejected`（`tests/test_creation_path_policy.py:200-208`）一致。

#### 实验 B：`_assert_internal()` 后、`mkdir` 前替换父目录

| 项目 | 结果 |
|---|---|
| 注入点 | `_now` 包装器（`:327`） |
| junction 创建 | 成功 |
| `root.mkdir` 调用 | 1 次，路径为 `...\late-parent\project` |
| 第一次断言观察 | `outside/project` 不存在（`_assert_internal` 的早期检查） |
| 第二次断言观察 | **`outside/project` 存在，内容为空**（`:344` 检查前） |
| 最终错误 | `CreationError E_LINK_BOUNDARY` |
| 最终 `outside/project` | 不存在（被清理） |
| 最终 `outside/creation` / `state.json` | 不存在 |

解释：`root.mkdir` 跟随 junction 创建了项目外目录；`_assert_no_link(self.root)` 在创建之后才发现链接。最终目录为空并不能证明过程中没有项目外创建。

#### 实验 C：清理期间出现并发文件

| 项目 | 结果 |
|---|---|
| 注入点 | `_now` 创建 junction；在 `:344` 检查前向 `outside/project/concurrent.txt` 写入 |
| 最终错误 | `E_LINK_BOUNDARY` |
| `outside/project` | **存在** |
| `outside` 树 | `project`、`project\concurrent.txt` |
| `concurrent.txt` | **存在** |
| `creation/state.json` | 不存在 |

解释：`root.rmdir()` 只能删除空目录；并发方只要在清理前放入文件，清理就失败并留下项目外内容。该文件是实验模拟的并发写入，不是自然竞争自动产生的；但它验证了“非空目录清理失败”的代码路径。

#### 实验 D：`created_root=False` 时不删除既有目录

| 项目 | 结果 |
|---|---|
| 预置 | `outside/project/pre-existing.txt` 已存在 |
| 注入点 | `_now` 创建 junction |
| `created_root` | False（`self.root.exists()` 通过 junction 为真） |
| 最终错误 | `E_LINK_BOUNDARY` |
| `outside/project` | **存在** |
| `pre-existing.txt` | **存在** |

解释：清理只在 `created_root=True` 时调用 `root.rmdir()`；该情形下不会删除既有目录。这说明是否删除取决于时序标志，而不是目录身份。

#### 实验 E：`root.mkdir` 后、检查前替换父目录

| 项目 | 结果 |
|---|---|
| 预置 | `outside/project` 为既有空目录 |
| 注入点 | `Path.mkdir` 包装器：`root.mkdir` 成功后重命名 `late-parent` 为 `late-parent-real`，建立 `late-parent -> outside` |
| `created_root` | True（替换前 `root` 不存在） |
| 最终错误 | `E_LINK_BOUNDARY` |
| `outside/project` | **不存在**（被清理删除） |
| `outside` 树 | 空 |
| `late-parent-real/project` | **存在**，空目录 |

解释：清理按旧标志 `created_root=True` 调用 `self.root.rmdir()`；此时路径经过 junction，删除的是 `outside/project`，不是本次创建的真实目录。真实项目目录留在重命名后的父目录中。这是本轮最严重的副作用：**误删项目外既有空目录**。

### 4.4 实验未覆盖的部分

- 未用自然并发线程/进程反复压测，不能给出发生概率。
- 未在所有 Windows 文件系统（网络盘、ReFS、压缩目录、OneDrive 同步目录）上重复。
- 未测试“攻击者在 `write_json_atomic` 的 `mkstemp`/`os.replace` 窗口内替换父目录”的完整文件写入实验。
- 未测试非 Windows 的 symlink 行为。

这些限制不影响“窄窗口可重放”的结论，但限制了对其现实频率和外推范围的判断。

---

## 5. 项目外副作用检查

### 5.1 结果汇总

| 实验 | `outside/project` 创建 | `outside/creation` | `state.json` | 清理后结果 |
|---|---|---|---|---|
| A：init 前替换 | 否 | 否 | 否 | 无副作用 |
| B：内部检查后替换 | **是（瞬时）** | 否 | 否 | 空目录被删除 |
| C：清理期并发文件 | 是 | 否 | 否 | `project/concurrent.txt` 残留 |
| D：既有目录 + `created_root=False` | 否（预先存在） | 否 | 否 | 既有目录和文件保留 |
| E：mkdir 后替换 | 是（预先存在） | 否 | 否 | 既有空目录被删除 |

### 5.2 不能只看最终状态

实验 B 是最重要的反例：最终 `outside` 为空，但过程中确实创建了 `outside/project`。因此：

- “最终没有 `state.json`”不能证明没有项目外写入；
- “最终目录不存在”不能证明 `mkdir` 没有越界；
- 必须同时记录创建调用、检查点快照和清理结果。

### 5.3 业务文件与状态文件

在 A、B、D、E 中，均未观察到项目外 `creation/` 或 `state.json`。原因之一是 `_assert_no_link(self.root)` 在 `:344` 紧跟 `root.mkdir`，还没有进入 `:345` 的 `creation/` 创建。

但这不构成“文件不会越界”的保证：实验 C 已证明目录可以留下；代码上 `write_json_atomic` 的父目录创建、临时文件创建和 `os.replace` 都发生在检查之后（见 §2.5），存在同类的文件级窗口。

---

## 6. 现有清理逻辑分析

### 6.1 代码结构

`store.py:339-394`：

- `creation_existed = self.creation_dir.exists()`：在 `mkdir` 前记录；
- `created_root = not self.root.exists()`：在 `mkdir` 前记录；
- `created`：只记录 `_write_json` 成功返回后的文件路径；
- `except BaseException`：
  - 逆序 `unlink` `created` 中的文件；
  - 若 `creation_existed=False`，尝试删除 `runs_dir`、`creation_dir`；
  - 若 `created_root=True`，尝试删除 `root`。

### 6.2 可靠性问题

1. **路径标志不是目录身份。** `created_root=True` 只表示“在 `:340` 时 `root` 不存在”；它不表示“`:391` 时 `root` 一定是本次创建的目录”。实验 E 证明路径被重定向后会误删外部空目录。
2. **`rmdir` 只能删除空目录。** 实验 C 证明外部文件一出现，清理就失败并留下内容。
3. **`created` 是路径列表，不是文件句柄/身份。** 如果父目录在写入与清理之间被替换，`path.unlink()` 可能删除项目外同相对路径文件；本轮未实验，但代码上存在该窗口。
4. **`creation_dir.rmdir()` / `runs_dir.rmdir()` 同样基于路径。** 在更晚的失败点，如果 `creation/` 或 `runs/` 被 junction 重定向，清理可能删除项目外目录。
5. **`write_json_atomic` 内部再次 `mkdir(parents=True)`。** 即使 `_write_json` 检查了目标路径，父目录创建仍可能跟随链接（`contract.py:174`）。
6. **清理是 best effort，不是事务回滚。** 它没有快照、没有目录句柄、没有身份校验，也没有“清理失败时明确报告残留”的机制。

### 6.3 结论

现有清理逻辑在正常路径下可用，但**不能保证竞态下的恢复安全**：

- 对空目录，可能误删项目外既有目录；
- 对非空目录，可能留下项目外文件；
- 对文件，可能通过重定向的路径执行 `unlink`；
- 清理失败被 `except OSError: pass` 吞掉（`:378`、`:383`、`:387`、`:392`），调用方只看到原始 `E_LINK_BOUNDARY`，不知道项目外留下了什么。

---

## 7. 修复方向比较

> 本节只比较方案，**不实施**。任何方案都不能仅凭“检查前后各调用一次 `_assert_no_link()`”宣称彻底消除 TOCTOU。

### 7.1 方案 A：逐级检查、逐级创建、逐级复核

**做法**：从盘符/可信父目录开始，对每个路径组件：

1. `lstat` 当前组件；发现 symlink/junction 立即拒绝；
2. 不存在时只创建这一级（不要 `parents=True`）；
3. 创建后立即 `lstat`/检查，确认不是链接；
4. 再进入下一级；任何异常都按“已创建层级”逆序清理。

| 维度 | 评估 |
|---|---|
| 能防住 | 避免 `mkdir(parents=True)` 一次性穿过未知父级；每个层级都有可见检查点；能更早发现 junction |
| 不能防住 | 检查与 `mkdir` 之间仍可被替换；文件写入仍可能越界；清理仍可能被重定向 |
| 残余 TOCTOU | 有；窗口缩小但未消除 |
| Windows Python 3.10+ | 可以，纯标准库 |
| 实现复杂度 | 低到中 |
| 对 API 的影响 | 基本无；可内部实现；若默认拒绝缺失父目录，CLI 行为会变化 |
| 对 executor 的影响 | 可作为 executor 创建父目录的基础策略；仍需文件写入方案 |
| 第三方依赖 | 无 |
| 测试难度 | 中；可用真实 junction + 受控注入重放 A/B/E |

**评价**：适合作为短期最小加固，但不能单独称为“安全”。

### 7.2 方案 B：Windows 原生目录句柄 / NT API

**做法**：用 `CreateFileW` 以 `FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT` 打开祖先目录，检查 `FILE_ATTRIBUTE_REPARSE_POINT`；持有句柄且不共享 delete，阻止祖先被重命名/替换；用 `GetFinalPathNameByHandleW` 校验最终路径；子项创建使用相对句柄的 `NtCreateFile`，或至少在持有父句柄期间创建并复核。

| 维度 | 评估 |
|---|---|
| 能防住 | 可显著收紧祖先目录被替换的窗口；能明确验证句柄对应的真实对象；可在创建期间阻止 rename/delete |
| 不能防住 | 句柄关闭后仍可被替换；子项创建后外部仍可改写；崩溃/断电；非 Windows；`NtCreateFile` 非公开稳定 API |
| 残余 TOCTOU | 仍可能存在，但可把关键祖先绑定到句柄；不能宣称绝对 |
| Windows Python 3.10+ | 需要 `ctypes` 或原生扩展；`os.mkdir` 在 Windows 没有 `dir_fd` 等价能力，标准库路径 API 不足 |
| 实现复杂度 | 高 |
| 对 API 的影响 | 可封装在 path/executor 内部；需要平台分支和回退 |
| 对 executor 的影响 | 最适合作为执行器写路径的长期安全基础 |
| 第三方依赖 | 不必须；但 `ctypes` + NT API 的可维护性/可移植性风险高 |
| 测试难度 | 高；需要 Windows 专项测试、句柄竞态、权限/共享模式测试 |

**评价**：长期方向，但不建议作为当前 3.2.2.0.5 的最小修复；需要独立设计和专项验证。

### 7.3 方案 C：限制支持路径，默认要求父目录已存在

**做法**：`init()` 不再默认创建多级父目录；要求项目根的父目录已经存在且是真实目录；只创建项目根这一级（或要求用户预先创建）。对缺失父目录返回稳定 `CreationError`，由用户显式创建。

| 维度 | 评估 |
|---|---|
| 能防住 | 消除 `parents=True` 的多级穿越；把风险收缩到“父目录已存在时根节点创建” |
| 不能防住 | 父目录本身仍可被替换；根节点创建仍有 check-then-use；文件写入仍有窗口 |
| 残余 TOCTOU | 有；但威胁面更小、更容易解释 |
| Windows Python 3.10+ | 可以，纯标准库 |
| 实现复杂度 | 低 |
| 对 API 的影响 | 有：会破坏“自动创建多级父目录”的现有便利；需要 CLI 文档/错误码调整 |
| 对 executor 的影响 | 可作为默认安全姿态；但不足以保护 replace/delete |
| 第三方依赖 | 无 |
| 测试难度 | 低；容易覆盖缺失父目录、真实父目录、junction 父目录 |

**评价**：适合作为产品层的默认约束，尤其在没有原生句柄实现时；但会改变现有行为，需要用户确认。

### 7.4 方案 D：受控 staging + 原子 rename

**做法**：在已确认可信的父目录中创建随机 staging 目录，在 staging 内构建完整 `project/creation/...`，最后 rename 到目标根；失败时只清理 staging。

| 维度 | 评估 |
|---|---|
| 能防住 | 避免目标位置出现半成品；失败清理范围更清楚；可先完成内容再提交 |
| 不能防住 | staging 的父目录仍可能是 junction；最终 rename 仍是路径操作；目标已存在/跨卷/Windows 目录 rename 语义复杂 |
| 残余 TOCTOU | 有；父目录替换仍可把 staging 或 rename 重定向 |
| Windows Python 3.10+ | 可以，标准库；但目录 rename 的原子性和覆盖语义需验证 |
| 实现复杂度 | 中 |
| 对 API 的影响 | `init()` 内部实现可保持；但目录布局/可见性变化需要测试 |
| 对 executor 的影响 | 适合多文件 operation 的批次提交，不是 `init` 的单独替代 |
| 第三方依赖 | 无 |
| 测试难度 | 中到高；需要崩溃/失败/目标存在/跨卷测试 |

**评价**：适合作为执行器事务设计的一部分，不能单独解决父目录 junction。

### 7.5 方案 E：锁 / 威胁模型接受

**做法**：使用 per-project 锁或单写者标记；在文档中声明“本地并发攻击者不在保证范围”；只防协作进程。

| 维度 | 评估 |
|---|---|
| 能防住 | 两个 Veyra 进程同时写；常见重复执行 |
| 不能防住 | 非协作进程/攻击者；锁文件本身也可能被 junction 重定向 |
| 残余 TOCTOU | 基本不变 |
| Windows Python 3.10+ | 可以（`msvcrt`/`O_CREAT|O_EXCL`） |
| 实现复杂度 | 低到中 |
| 对 API 的影响 | 需要锁生命周期与错误码 |
| 对 executor 的影响 | 只能作为并发控制，不是安全边界 |
| 第三方依赖 | 无 |
| 测试难度 | 中 |

**评价**：只能作为补充，不能单独接受该风险。

### 7.6 方案比较结论

| 方案 | 防项目外创建 | 防清理误删 | 完全消除 TOCTOU | 当前可实现性 | 建议 |
|---|---|---|---|---|---|
| A 逐级创建 | 部分 | 否（需配身份校验） | 否 | 高 | **短期必做的一部分** |
| B 原生句柄 | 较强 | 可配合 | 否 | 低 | 长期/执行器专项 |
| C 限制父目录 | 部分 | 否 | 否 | 高 | **短期产品约束候选** |
| D staging | 部分 | 部分 | 否 | 中 | 执行器事务候选 |
| E 锁/接受 | 否 | 否 | 否 | 高 | 只能补充 |

**不能把 A 或“前后两次 `_assert_no_link`”描述为彻底消除 TOCTOU。**

---

## 8. Windows Python 兼容性

- 项目声明 Python 3.10+；CI 的 Linux job 使用 3.10，本机验证使用 3.15.0。
- `Path.mkdir(parents=True)`、`os.replace`、`Path.rmdir` 都是标准库路径 API；Windows 上没有 `os.mkdir` 的 `dir_fd` 等价能力，不能简单用 Unix 的 `openat` 模式解决。
- `os.path.isjunction` 在较新 Python 中存在；项目已有 `getattr(os.path, "isjunction", None)` 回退（`veyra_pack/paths.py:92-99`），因此 Python 3.10 不会因缺少该函数而崩溃，但 junction 检测在旧版本上可能退化为 symlink 检查。当前实现仍以 `Path.is_symlink()` + 回退为主。
- 方案 A/C/D 只需标准库，3.10+ 可用；方案 B 需要 `ctypes` 调用 Win32/NT API，3.10+ 理论可用，但 API 稳定性、错误码、共享模式、测试成本都显著更高。
- 方案 B 必须保留非 Windows 分支；不能用“Windows 上用原生句柄”作为唯一实现而破坏 Linux/macOS。
- 任何修复都要继续使用真实 junction 测试；symlink 在普通 Windows 用户下常因权限失败，不能以 skip 代替 junction 验证。

---

## 9. 对未来 Operation Executor 的影响

### 9.1 直接影响

未来的 executor 会做：

- 用户自然语言生成文件；
- Plan/Operation 的 create/replace/delete；
- 写入 `creation/operations`、`creation/backups`、临时文件和锁；
- 失败后的恢复/清理。

这些操作与 `init()` 共享同一类风险：

1. **路径检查后创建/替换**：`os.replace(temp, target)`、`write_text()`、`mkdir()` 都可能跟随新出现的 junction。
2. **临时文件与备份目录**：`write_json_atomic` 的 `path.parent.mkdir(parents=True)` 和 `mkstemp` 会先于最终 replace 发生。
3. **清理/回滚**：基于路径的 `unlink`/`rmdir` 可能被重定向；实验 E 已经展示了“清理删除外部目录”的现实路径。
4. **用户批准不等于路径安全**：用户批准的是 operation 内容，不保证执行瞬间父目录没有被替换。
5. **项目外隔离是最高资产**：设计文档 §5.1 已把“项目根外文件：绝对不写”列为最高保护目标；当前实现还达不到这个承诺。

### 9.2 设计文档现有的 T3 结论需要收紧

设计文档 §5.2 T3 目前写的是“接受残余窗口并在文档/测试中明确，不宣称完全消除 TOCTOU”；这对**单文件替换**是诚实的。但本轮发现：

- 构造/初始化阶段的父目录可以导致 `mkdir` 在项目外创建；
- 清理可能删除项目外既有空目录，而不只是“可能覆盖目标文件”；
- 该问题发生在 executor 之前的基础设施，不能只靠 executor 的“操作前再检查一次”解决。

因此建议把 T3 的接受范围限定为“在已确认的父目录内对单文件替换的不可避免窗口”，并把“父目录创建/清理”从 T3 中拆出，作为 3.2.2.1 的开工前修复项。

### 9.3 对 executor 的具体要求

在实现 create/replace/delete、temp、backup 之前，至少应满足：

1. 不调用 `parents=True` 创建项目根/父目录；
2. 每个目录创建后立即检查，并记录身份（句柄或稳定文件 ID）；
3. 清理只删除“身份匹配”的目录/文件；身份不可证明时保留并报告，不盲目 `rmdir`/`unlink`；
4. 临时文件与目标同目录，但父目录必须先验证且尽量用句柄固定；
5. 对 replace 的最终目标做 no-follow/句柄级验证，不能只做一次 `find_link_component`；
6. 用失败注入测试覆盖“检查后替换”“清理期间写入”“既有目录”“文件写入窗口”。

---

## 10. 推荐方案与取舍

### 10.1 推荐的最小加固（进入 3.2.2.1 写操作之前）

按优先级：

1. **A：逐级创建 + 每级复核**。`init()` 不应对项目根使用 `parents=True`；从可信父目录开始逐级 `lstat`、`mkdir`、复核。
2. **C：默认要求父目录已存在**，或把“创建多级父目录”变成显式 opt-in，并由 CLI 明确提示。若产品不接受行为变化，则至少对项目根以外的层级做逐级创建，不要一次 `parents=True`。
3. **清理身份化**：在创建目录后记录稳定身份（Windows 文件 ID/句柄，或至少 `lstat` 的卷/文件索引）；清理前比对身份；不匹配则保留并记录 `cleanup_residue`，不能按旧路径标志 `rmdir`。
4. **移除 `write_json_atomic` 内部的隐式 `parents=True`**，或要求调用方传入已验证的父目录；写入前再次检查，且不要宣称这能完全消除文件级 TOCTOU。
5. **补测试**：A/B/C/D/E 五类交错至少各一个；真实 junction，不使用 symlink 代替；测试要断言“过程中是否创建”而不仅是最终目录。

### 10.2 不建议的方案

- 只增加一次 `_assert_no_link`：不能阻止已发生的创建，也不能保护清理。
- 只加锁：锁不防非协作进程，且锁文件本身可能被重定向。
- 只把清理改成 `shutil.rmtree`：范围更大，误删风险更高，明确不建议。
- 直接声称“绝对安全”或“已消除 TOCTOU”：与实验和代码事实不符。

### 10.3 取舍理由

- 当前是本地 CLI，原始简单序列已被拦截，窄窗口需要本地并发写权限；因此不必立即上马复杂的 NT 句柄方案。
- 但 **清理误删项目外既有目录** 是数据破坏，且 executor 会扩大创建/删除路径；不能把它当作“未来平台化才处理”的问题。
- A + C + 身份化清理可以用标准库实现、可测试、API 影响小，足以把风险从“未知路径重定向”降到“明确的、有限的残余窗口”。
- B 是更强的长期方案，但成本和维护风险高，适合在 executor 的安全需求明确后单独立项。

---

## 11. 未验证事项与残余风险

1. **自然竞争频率未验证**：本报告证明代码路径可达，不证明在真实机器上容易触发。
2. **完整文件写入越界未实验**：`write_json_atomic` 的 `parents=True`、`mkstemp`、`os.replace` 在检查后执行；代码上存在窗口，但本轮没有做完整文件级重放。
3. **清理 `unlink` 重定向未实验**：`created` 路径在清理时可能已经被 junction 重定向；只做了目录 `rmdir` 实验。
4. **非空目录误删不会发生**：`rmdir` 对非空目录失败；但会留下外部文件，且错误被吞掉。
5. **网络盘/同步盘/ReFS/压缩目录**：未验证，junction/rename 语义可能不同。
6. **非 Windows 平台**：未评估；symlink 的创建权限和 `Path.resolve` 行为不同。
7. **攻击者权限**：需要父目录的写/删除权限；ACL 受限或只读父目录下不可利用。
8. **并发进程/线程**：本报告使用受控注入，不是自然并发压力测试；未测试多进程同时 `init`。
9. **崩溃/断电窗口**：未测试；清理只能 best effort。
10. **executor 的后续实现**：尚未实现，不能把本轮结论当作 executor 已满足安全要求的证明。

---

## 12. 是否建议阻断 3.2.2.1

**建议：阻断 3.2.2.1 的写操作（create/replace/delete、temp、backup、cleanup），直到完成 §10.1 的最小加固并验收。**

具体边界：

- **不阻断**：只读设计、Operation 数据契约讨论、路径策略文档、测试设计。
- **阻断**：任何会在用户目录中创建/替换/删除文件的 executor 实现；包括内部 `creation/operations`、`creation/backups`、临时文件和失败清理。
- **当前 CLI**：可以继续按既有方式使用，但应把本报告作为已知残余风险记录；不能把“当前简单序列被拦截”当成 executor 的安全证明。
- **如果产品选择接受本地并发风险**：至少必须先修复清理误删（身份化清理），因为那是数据破坏，不是单纯的越权写入；其余残余窗口可以显式记录并加测试，但不能声称已消除。

理由：项目把“项目根外文件绝对不写”列为最高资产；实验 B/E 已证明当前 `init` 在特定交错下会创建项目外目录，清理还会删除项目外既有空目录。executor 的写操作面更大，不能在这个基础上直接开工。

---

## 13. 本轮是否修改生产代码

**否。**

- 未修改 `veyra_creation/`、`veyra_pack/` 下任何生产代码。
- 未修改 `tests/` 下任何测试代码。
- 只新增本文件：`docs/phase-3.2.2.0.5-toctou-security-assessment.md`。
- 未执行 `git add`、`git commit`、`git push`、`git reset`、`git checkout`、`git clean`、`git rebase` 或任何历史重写。
- 未删除项目文件、日志或缓存；实验临时目录均已清理，`%TEMP%` 下无残留 `veyra-toctou-*`。

### 13.1 保护文件核验

| 文件 | 大小 | mtime | SHA-256 | 本轮操作 |
|---|---:|---|---|---|
| `report.md` | 1570 bytes | 2026-11-11 22:07:06 | `A11D09D49217CA3C6CD7C269268C148558EA488067E8D1399BCAFD7ABBC2D68D` | 仅计算哈希，未读取内容、未修改 |
| `real_crash.log` | 21999 bytes | 2026-11-05 18:40:14 | `CC1418B7690284787B55D3417383F23DD6DEAA47619BC41702F9F33895DCD54D` | 仅 stat/哈希，未读取、未修改 |

### 13.2 Git 状态

- 分支：`main`
- HEAD：`0fa1bbfff9c28e1b12c94b334f23b732d059926e`
- 本轮没有 Git 写操作。
- 报告写入后 `git status --short` 的构成：
  - 已修改：`README.md`、`README_zh.md`、`report.md`（均为本轮之前已有的工作区修改）；
  - 未跟踪：`docs/`（含本报告及既有设计与报告）、`tests/`（12 个文件）、`veyra_creation/`、`veyra_pack/`；
  - 本轮新增的未跟踪文件只有本报告；未改变既有 `report.md` 或源代码。
- 工作区不是 clean 状态；本报告不把既有未提交内容归因于本轮。

### 13.3 环境与验证方式

- Python：3.15.0
- 实验平台：Windows
- junction：`cmd /c mklink /J`，真实 junction
- 所有实验：独立 `%TEMP%` 临时目录
- 未运行仓库 `unittest` 套件：本轮没有修改实现或测试；A–E 是独立临时探针的受控重放，不冒充测试套件结果。

---

## 附录 A：复现实验的最小结构（供后续测试设计参考）

后续测试不应复制本报告的临时脚本，而应把交错固化为可审查的测试辅助函数；建议至少覆盖：

1. 构造 `ProjectStore(base / "late" / "project")`；
2. 在 `late` 不存在时完成构造；
3. 在 `init()` 前建立 `late -> outside`，断言 `E_LINK_BOUNDARY` 且无 `mkdir`；
4. 在 `_assert_internal` 之后、`mkdir` 之前建立 junction，断言 `outside/project` 曾在检查点存在；
5. 在 `root.mkdir` 之后、检查之前替换父目录，断言清理不会删除既有 `outside/project`；
6. 在清理前向 `outside/project` 写入文件，断言残留被报告而不是静默吞掉；
7. 对 `write_json_atomic` 做父目录替换，验证文件写入边界。

测试必须使用临时目录、真实 junction，并在 cleanup 中删除 junction；不能把 skip 计为通过。

## 附录 B：关键代码位置索引

| 位置 | 作用 |
|---|---|
| `veyra_creation/store.py:198-268` | `ProjectStore.__init__` 的路径规范化/resolve/链接检查 |
| `veyra_creation/store.py:274-395` | `init()` 的预检、创建、写入、清理 |
| `veyra_creation/store.py:287` | `_assert_internal()` 调用，原始简单序列的拦截点 |
| `veyra_creation/store.py:339-348` | `created_root` / `creation_existed` 与 `mkdir`/检查顺序 |
| `veyra_creation/store.py:374-394` | 失败清理 |
| `veyra_creation/store.py:418-433` | `_assert_no_link()` |
| `veyra_creation/store.py:435-442` | `_assert_internal()` |
| `veyra_creation/store.py:444-446` | `_write_json()` 的 check-then-write |
| `veyra_pack/contract.py:162-190` | `write_json_atomic()` 的父目录创建/临时文件/replace |
| `veyra_pack/paths.py:238-263` | `normalize_lexical_path()` |
| `veyra_pack/paths.py:289-311` | `find_link_component()` |
| `tests/test_creation_path_policy.py:200-208` | 现有“构造后替换祖先”测试（只断言最终外部目录不存在） |

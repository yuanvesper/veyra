# Phase 3.2.1-R4 Windows 路径边界收尾报告

- 日期：2026-11-21
- 范围：`ProjectStore` 项目根路径策略、构造器 fail-closed、`init` 直接创建 `decisions.ndjson` 前的检查，以及 R3 路径/decisions 回归。
- 结论：R3 验收发现的四类 Windows 路径边界已关闭；新增 18 个测试，creation **115 tests OK（skipped=1）**，全量 **296 tests OK（skipped=2）**。本轮不进入 3.2.2，不自行宣布 Phase 3.2.1 最终验收。

## 1. 根因

R3 的 `normalize_lexical_path` 只调用 `os.path.abspath`。它在 Windows 上不会替 Win32 处理每个组件的尾随点和空格：

- `base\...\link\project`：`...` 被 Win32 当作可忽略的尾随点组件，`Path.resolve()` 实际落到 `base\link\project`；构造器在 resolve 前只看到不存在的 `...`，于是接受了实际上会穿过 junction 的 root。
- `base\missing\.. \link\project`：`.. ` 不是词法上的 `..`，`abspath` 保留该组件；后续 `init` 在底层路径操作中抛裸 `FileNotFoundError`，CLI 只能落到泛化的 `E_OS`。
- `base\missing\...\link\project`：构造器接受声明路径，实际创建路径被 Win32 规范化为 `base\missing\link\project`，声明与实际不一致。
- `\\?\GLOBALROOT\...`、`\\.\C:\...` 等设备命名空间此前没有在路径策略层被拒绝。

## 2. 路径策略

### 接受

- 普通绝对盘符路径，例如 `C:\Projects\veyra`。
- 普通相对路径；在构造时由 `ntpath.abspath` 转成绝对路径。
- 组件中的 `.` 和 `..` 按词法折叠；缺失的中间目录不会终止后续 link 检查。
- 大小写差异不改变路径身份；`paths_equivalent` 使用 `normcase`。

### 拒绝（稳定错误码）

| 路径形态 | 错误码 |
| --- | --- |
| `\\server\share\...`、`\\?\UNC\...` | `E_PROJECT_PATH_UNC` 或 `E_PROJECT_PATH_DEVICE` |
| `\\?\...`、`\\.\...`、`\??\...`、`\Device\...`、`\GLOBAL??\...` | `E_PROJECT_PATH_DEVICE` |
| `C:foo`、`C:` | `E_PROJECT_PATH_DRIVE_RELATIVE` |
| `\foo`（无盘符的 root-relative） | `E_PROJECT_PATH_INVALID` |
| `...`、`.. `、`. `、`... `、`link.`、`link ` 等尾随点/空格组件 | `E_PROJECT_PATH_TRAILING` |
| ADS，例如 `project:stream` | `E_PROJECT_PATH_ADS` |
| `CON`、`NUL`、`COM1` 等保留设备名 | `E_PROJECT_PATH_RESERVED` |
| 空路径、NUL、`<>"|?*`、控制字符 | `E_PROJECT_PATH_INVALID` 或 `E_PROJECT_PATH_TYPE` |

> **R4.1 修正（2026-11-21）：** 上表“空路径”在 R4 时只由 `normalize_lexical_path("")` 覆盖；`ProjectStore("")` 与 CLI `init ""` 仍会把空串交给 `Path("")` 并落到当前工作目录。R4.1 已在原始字符串入口 fail closed，详见 §13。

设计取舍：

- **不支持 UNC**。远程共享上的 reparse point、权限和可用性无法在本地构造阶段可靠检查，因此本轮显式拒绝，而不是静默接受后把不确定性留到写入阶段。这是策略选择，不是“UNC 已安全”。
- **不支持所有 `\\?\` 扩展长度路径**。扩展长度路径与设备命名空间共用前缀；本轮选择 fail closed，避免为长路径重新引入 `resolve()` 语义差异。长路径属于已知限制。
- 尾随点/空格在 `abspath` **之前**逐组件检查，避免 Win32 在 `abspath`/`resolve` 中把它们规范化后绕过检查。

## 3. 构造器与文件操作入口

`ProjectStore.__init__` 的顺序现在是：

1. `normalize_lexical_path`：执行 Windows 策略，再 `ntpath.abspath` 做纯词法折叠。
2. `find_link_component(declared)`：逐组件 `lstat`，缺失组件继续，存在 link 立即拒绝。
3. `declared.resolve()`：`OSError`/`RuntimeError`/`ValueError` 转 `E_PROJECT_ROOT`。
4. `paths_equivalent(declared, resolved)`：声明路径与实际解析路径不一致时拒绝；8.3 短名扩展也会因不一致而 fail closed。
5. 对 `resolved` 再做一次 link 检查，作为 resolve 期间 link 出现的显式兜底。
6. `init` 在创建 `runs/` 后增加 `_assert_no_link`；直接写 `decisions.ndjson` 前增加 `_assert_no_link`。

已复核入口：`init`、`load_state`、`load_spec`、`load_decisions`、`append_decision`、`lock`、`build_digest`、`verify`。构造后祖先被替换为 junction 时：

- 上述前七项抛 `E_LINK_BOUNDARY`；
- `verify` 按既有接口返回含 `E_LINK_BOUNDARY` 的 issue 列表，CLI 退出 1。

## 4. 修改文件

- `D:\Projects_vesper\veyra\veyra_pack\paths.py`
  - 新增 `PathPolicyError`、Windows 路径策略、`paths_equivalent`。
  - `normalize_lexical_path` 在 Windows 上先验证策略，再 `ntpath.abspath`，并对规范化结果二次验证。
- `D:\Projects_vesper\veyra\veyra_creation\store.py`
  - 构造器捕获 `PathPolicyError` 并保留稳定错误码；增加 resolve 前后的一致性检查与二次 link 检查。
  - `init` 增加 `runs/` 与直接创建 `decisions.ndjson` 前的 link 检查。
- `D:\Projects_vesper\veyra\tests\test_creation_path_policy.py`（新增，18 tests）
  - 8 个 Windows 策略测试；10 个真实 junction/入口测试。
- `D:\Projects_vesper\veyra\docs\phase-3.2.1-r1-integrity-report.md`、`D:\Projects_vesper\veyra\docs\phase-3.2.1-state-store-report.md`
  - 同步 R4 策略、计数与剩余边界。

R4.1 追加修改 `D:\Projects_vesper\veyra\veyra_creation\cli.py` 并新增 `D:\Projects_vesper\veyra\tests\test_creation_empty_path.py`；详见 §13。

## 5. 新增测试与关键断言

Windows 策略测试：

- 正常绝对盘符路径可构造并 `init`。
- 正常 UNC、扩展 UNC 被拒绝。
- `\\?\GLOBALROOT\...`、`\\.\C:\...`、`\\?\C:\...`、`\??\C:\...` 被拒绝。
- `C:foo`、`C:` 被拒绝。
- `\project` 被拒绝。
- `...`、`.. `、`. `、`... `、`link.`、`link ` 均被拒绝。
- ADS、`CON`、`bad?name` 被拒绝。
- 空路径被拒绝。
  - **R4.1 修正：** R4 时该断言只覆盖 `normalize_lexical_path("")`；真实构造器与 CLI 空参数当时仍 fail-open。R4.1 新增构造器/CLI 入口测试后，这一句才在真实入口上成立。

真实 junction/入口测试：

- `base\...\link\project`、`base\.. \link\project`、`base\missing\.. \link\project`、`base\missing\...\link\project` 均由**构造器**拒绝，而不是等 `init`。
- `base\link.\project`、`base\link \project` 由构造器拒绝。
- 正式 CLI `init` 对 `base\...\link\project` 返回 2，stderr 含 `E_PROJECT_PATH_TRAILING`，项目外无 `state.json`。
- R3 的 `base\missing\..\link\project` 仍在构造器抛 `E_LINK_BOUNDARY`。
- 构造后祖先替换为 junction：`init` 仍抛 `E_LINK_BOUNDARY`，outside 无 `project`。
- 全部入口在祖先被替换后 fail closed：`init/load_state/load_spec/load_decisions/append_decision/lock/build_digest` 抛 `E_LINK_BOUNDARY`，`verify` 返回 `E_LINK_BOUNDARY`。

## 6. 动态验证结果

- `test_creation_path_policy`：18 tests OK（0.28s）。
- 真实 junction 均通过 `cmd /c mklink /J` 创建；构造器拒绝后，测试断言 outside 的 `project/creation/state.json` 与 `outside/creation/state.json` 都不存在。
- 当前 Windows 账户无 symlink 权限；symlink 分支仍只有代码检查与既有 skip，**没有**用 junction 冒充 symlink。

## 7. 回归结果

```powershell
& "C:\Users\lenovo\AppData\Local\Programs\Python\Python315\python.exe" -m unittest discover -s tests -p "test_creation_*.py"
& "C:\Users\lenovo\AppData\Local\Programs\Python\Python315\python.exe" -m unittest discover -s tests
```

| 范围 | 结果 |
| --- | --- |
| `test_creation_*` | **115 tests，OK（skipped=1）**，3.749s |
| 全量 `tests/` | **296 tests，OK（skipped=2）**，5.711s |
| 新增 `test_creation_path_policy.py` | 18 tests OK |

R4.1 新增 `tests/test_creation_empty_path.py` 9 tests 后，最新结果为 **creation 124 tests（skipped=1）**、**全量 305 tests（skipped=2）**；上表 115/296 是 R4 审核当时的数字，保留为历史记录。

跳过的两项仍是 Windows symlink 权限（`test_creation_digest...test_digest_skips_symlinks`、`test_pack_validation...test_symlink_is_rejected`）。

decisions 空白行、合法首次追加、连续追加、confirm 幂等、approved Plan 约束、非 superseded Plan 校验均包含在 115/296 的回归中，没有回归。

## 8. CLI 验证

系统临时目录，已清理：

| 场景 | 结果 |
| --- | --- |
| 正常 `init` | exit 0 |
| 正常 `status --json` | exit 0 |
| 正常 `digest` | exit 0 |
| 正常 `verify --json` | exit 0 |
| `init ""`（真实空参数，R4.1） | exit 2，stderr 含 `E_PROJECT_PATH_INVALID`，cwd 无 `creation/` |
| `\\?\GLOBALROOT\...` | exit 2，`E_PROJECT_PATH_DEVICE` |
| `\\server\share\...` | exit 2，`E_PROJECT_PATH_UNC` |
| `base\...\link\project` | exit 2，`E_PROJECT_PATH_TRAILING` |

CLI 正常场景未向仓库根写入任何文件；临时目录已删除。

## 9. 保护文件

- `D:\Projects_vesper\veyra\report.md`：1570 字节，SHA-256 `A11D09D49217CA3C6CD7C269268C148558EA488067E8D1399BCAFD7ABBC2D68D`；只核验哈希，未读取、未修改。
- `D:\Projects_vesper\veyra\real_crash.log`：21999 字节，`.gitignore:29` 忽略；未读取、未修改、未删除、未重新跟踪。
- `README.md`、`README_zh.md`、`report.md` 的既有工作区修改未被覆盖；没有删除任何项目文件、日志或缓存。

## 10. Git 状态

- 分支 `main`，HEAD `0fa1bbfff9c28e1b12c94b334f23b732d059926e`。
- 本轮未执行 `git add/commit/push/pull/fetch/reset/checkout/clean/rebase/stash`。
- 新增/修改仅限上述路径策略、store、测试和文档；`git status` 仍应只显示既有修改与本轮新增文件。

## 11. 剩余风险

- **TOCTOU**：`find_link_component` 检查与 `mkdir`/写入之间仍可被并发替换；`paths_equivalent` 与每次写前 `_assert_no_link` 能降低但不能消除。`init` 直接 `decisions_path.write_text("")` 在检查后写，仍不是原子“检查 + 创建”。
- **无跨进程锁/事务**：仍不保证两个进程同时写同一项目时的跨文件一致性。
- **Windows symlink 未实测**：当前账户无 symlink 权限；junction 实测不能替代 symlink 动态结论。
- **UNC/扩展长度路径不支持**：这是本轮显式策略；需要网络共享或长路径的用户会被拒绝，而不是静默降级。
- **8.3 短名**：`paths_equivalent` 会因声明/解析不一致而 fail closed；这是安全取舍，可能拒绝合法短名路径。
- **`verify` 接口**：路径错误以 issue 列表返回，不以异常返回；CLI 退出 1，库调用方需要检查 `issues`。
- **CI 覆盖**：现有 GitHub Actions 只运行 `ubuntu-latest`；本轮 18 个 Windows 路径策略测试在 CI 上会跳过，当前验证依赖本机 Windows + Python 3.15。CI 的 Windows 覆盖仍是验证缺口。

## 12. 结论

R4 已关闭 R3 独立验收列出的 Windows 路径边界：`...`/`.. `/尾随点空格组件、设备命名空间、UNC 与盘符相对路径均在构造阶段被拒绝；构造器记录并在 resolve 前后校验声明/实际路径一致性；`init` 的直接 decisions 写入前增加了边界检查。115/296 测试与 CLI smoke 通过。剩余 TOCTOU、无跨进程锁和 symlink 未实测仍是明确边界。

**本轮不宣布 Phase 3.2.1 最终验收，不进入 3.2.2，等待独立只读审核。**

## 13. R4.1 补丁：空路径 fail-closed 修复（2026-11-21）

### 13.1 独立审核发现

R4 独立只读审核在构造器与 CLI 入口发现 P2 fail-open：

- `ProjectStore("")` 被接受，root 实际变成当前工作目录。
- CLI `init ""` 被接受，可能在该 cwd 下创建 `creation/state.json`。
- 已有的 `test_empty_path_is_rejected` 只调用 `normalize_lexical_path("")`，没有覆盖真实入口。

根因是构造器先执行 `Path(root)`，再调用 `normalize_lexical_path`：

```python
declared = normalize_lexical_path(Path(root).expanduser())
```

`Path("")` 与 `Path(".")` 是同一个对象，空字符串信息在这一步已经丢失；CLI 的 `_store` 又先做了一次 `ProjectStore(Path(path))`，同样丢失空串。`normalize_lexical_path("")` 本身仍会拒绝，但不能代表构造器入口的行为。

### 13.2 修复

- `ProjectStore.__init__` 在 `Path(root)` 之前检查原始参数：`isinstance(root, str) and root == ""` 时抛 `CreationError(code="E_PROJECT_PATH_INVALID")`。
- `cli._store` 在 `Path(path)` 之前检查原始 CLI 参数 `path == ""`，抛同一错误码，然后才构造 `ProjectStore(path)`。
- 明确的 `"."`、`Path(".")`、普通绝对路径和普通相对路径行为不变。
- 记录 API 边界：调用方若已把空字符串转换成 `Path("")`，结果与主动传入 `Path(".")` 无法区分；本修复只能在原始字符串入口拦截，不能从 `Path` 对象恢复原始空串。

### 13.3 新增测试

新增 `D:\Projects_vesper\veyra\tests\test_creation_empty_path.py`（9 tests），全部使用系统临时目录：

1. `ProjectStore("")` 抛 `E_PROJECT_PATH_INVALID`。
2. CLI `init ""` 返回非零退出码。
3. CLI 空路径 stderr 包含 `E_PROJECT_PATH_INVALID`。
4. CLI 空路径执行后 cwd 不存在 `creation/`，且目录没有任何新增文件。
5. `ProjectStore(".")` 仍可正常初始化。
6. `ProjectStore(Path("."))` 仍可正常初始化。
7. CLI `init .` 仍可正常初始化。
8. 正常绝对路径仍可初始化。
9. 普通相对路径仍可初始化。

### 13.4 验证结果

- `test_creation_*`：**124 tests，OK（skipped=1）**，4.563s。
- 全量 `tests/`：**305 tests，OK（skipped=2）**，6.703s。
- `test_creation_path_policy.py`：18 tests OK。
- 真实空参数 CLI smoke：exit 2，stderr `project: [E_PROJECT_PATH_INVALID] project path must not be empty`；空路径执行后 cwd 条目数为 0，`creation/` 不存在。
- 随后 `init .`、`status .`、`digest .`、`verify .` 均 exit 0；`creation/state.json` 存在。

### 13.5 未解决的边界

R4.1 只修复空字符串入口，不改变以下已披露风险：

- `find_link_component` 检查与 `mkdir`/写入之间的 TOCTOU。
- 无跨进程锁/事务。
- Windows symlink 因权限未实测。
- UNC、扩展长度/设备命名空间仍是显式拒绝策略，不是安全支持。
- 8.3 短名仍 fail closed，可能拒绝合法短名路径。

**R4.1 完成后仍等待独立只读复核；不宣布 Phase 3.2.1 最终验收，不进入 3.2.2。**


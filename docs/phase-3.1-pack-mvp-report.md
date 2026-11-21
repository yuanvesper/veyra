# Veyra Phase 3.1 实施报告：通用 Pack 契约与离线打包 MVP

> 实施日期：2026-11-21
> 基线：`main` 分支 HEAD `0fa1bbfff9c28e1b12c94b334f23b732d059926e`（Phase 1–2B 发布提交）
> 范围：只新增 `veyra_pack/`、`tests/` 中的打包测试、`docs/pack-contract.md`、本报告与 README 说明；未修改 `triage_agent.py`、Skills、JSON 契约与 schema、既有测试、CI、安装脚本。

## 1. 本阶段目标与完成情况

目标是把 Veyra 从"崩溃诊断层"向"创作层"推进最小一步：先定义可被未来 AI 创作 Agent 复用的通用 Pack 契约，并提供**离线、确定性、可验证**的初始化和导出工具，而不接入游戏、不调用模型、不扩大为平台。

本阶段交付：

| 要求 | 状态 |
| --- | --- |
| 通用 Pack 契约文档 | 已完成：`docs/pack-contract.md` |
| `pack init`（生成清单骨架） | 已完成：`python -m veyra_pack init` |
| `pack validate`（结构/路径/摘要/尺寸校验） | 已完成：`python -m veyra_pack validate`，支持 `--json` |
| `pack export`（ZIP 输出、原子替换） | 已完成：`python -m veyra_pack export` |
| 确定性构建 | 已完成：同一输入输出字节级一致（有测试） |
| 未知字段与版本兼容策略 | 已完成并写入契约；未知字段警告 + 原样保留，不重写清单 |
| 新功能测试 | 74 个新 unittest；全量 181 个通过，1 个环境性跳过 |
| 不破坏现有能力 | `triage_agent.py` 零改动，Markdown/JSON 诊断契约未变 |
| 不引入第三方依赖 | 仅 Python 标准库 |

## 2. 交付物与规模

新增代码：

| 文件 | 行数 | 职责 |
| --- | --- | --- |
| `veyra_pack/contract.py` | 195 | 契约常量、`Limits`、诊断对象、原子 JSON 写入 |
| `veyra_pack/paths.py` | 149 | 安全相对路径检查、符号链接/junction 检测、根目录包含判断 |
| `veyra_pack/imaging.py` | 89 | 零依赖读取 PNG/JPEG/GIF/WebP 头部尺寸 |
| `veyra_pack/validation.py` | 705 | 清单解析与全部结构、路径、摘要、尺寸、限额校验 |
| `veyra_pack/init_project.py` | 110 | 新包骨架生成、pack id 生成、非空目录保护 |
| `veyra_pack/exporter.py` | 185 | 确定性 ZIP 构建、临时文件、回读校验、原子替换 |
| `veyra_pack/cli.py` | 139 | `init` / `validate` / `export` 子命令与退出码 |
| `veyra_pack/__init__.py` / `__main__.py` | 38 | 公共导出与 `python -m veyra_pack` 入口 |

新增测试共 74 个：

| 文件 | 测试数 | 覆盖重点 |
| --- | --- | --- |
| `tests/test_pack_init.py` | 10 | 骨架内容、默认值、空目录、拒绝覆盖、非法参数 |
| `tests/test_pack_validation.py` | 39 | 清单错误、schema 版本、路径攻击、摘要/尺寸、图片尺寸、许可、限额、junction |
| `tests/test_pack_export.py` | 14 | 成员集合/顺序/内容、字节级确定性、覆盖保护、包内输出拒绝、失败清理、ZIP 回读校验 |
| `tests/test_pack_cli.py` | 11 | 三个子命令、退出码、`--json` 可解析、`--version`、`python -m` 入口、端到端往返 |

新增文档：`docs/pack-contract.md`（175 行，契约的规范文本）。README 中英文各增加一个"Creation packs"小节、目录项与一条"验证有限"的限制说明。

## 3. 契约设计（详见 `docs/pack-contract.md`）

### 3.1 形态

Pack 是一个目录，根目录必须有 `pack.json`；清单内的其它路径都是相对包根的 POSIX 路径。默认布局约定 `assets/` 与 `files/`，但校验器不强制目录名。

必需顶层字段（12 个）：`schema_version`、`tool`、`pack_id`、`name`、`description`、`pack_type`、`target_game`、`version`、`author`、`assets`、`files`、`metadata`。

- `pack_type` 是受控枚举：`character_kit`、`desktop_theme`、`companion`、`asset_pack`、`mod`、`plugin`、`other`。新增类型必须升 schema 版本，避免下游静默误读。
- `tool` 记录生产工具名与版本；`init` 写入 `{"name": "veyra-pack", "version": "0.1.0"}`。
- `assets[]` 描述素材（`id`/`path`/`kind`，可选 `sha256`、`size_bytes`、`width`/`height`、`license`、`description`），素材可独立声明，不必重复列进 `files[]`。
- `files[]` 描述需要随包分发的文件（`path` 必需，`sha256`/`size_bytes` 可选）。
- 每个 issue 带稳定 `code`、JSON 指针式 `location`、人类可读 `message` 与 `severity`；错误阻断导出，警告不阻断。

### 3.2 未知字段策略

顶层、`tool`、`assets[]`、`files[]`、`license` 中的未知字段一律产生 `W_UNKNOWN_FIELD` 警告，但校验器**只读**：不重写、不删除、不重新序列化 `pack.json`。导出时把源文件字节原样写入 ZIP 的第一个成员，因此未知字段在物理上保持原样。这样既允许未来版本增加字段，也不会让旧工具静默丢失新字段。

### 3.3 版本兼容策略

- `MINOR` 只做**新增**（新可选字段、新警告码、放宽检查），1.0 清单继续有效。
- `MAJOR` 才允许删除/改名/改类型/把可选变必需。
- 本工具实现 `1.0.x`：`MAJOR != 1` 或 `MINOR > 0` 直接报 `E_SCHEMA_UNSUPPORTED`；patch 不参与兼容判断。
- 消费方必须先读 `schema_version`，不得依赖工具版本号推断清单格式。

### 3.4 与崩溃诊断契约的关系

两个契约完全独立、版本号各自演进：

- `triage_agent.py` 不导入 `veyra_pack`，`veyra_pack` 也不导入 `triage_agent.py`；
- 诊断证据 ID `E1..En` 与真实行号仍由 Python 提取器唯一拥有，Pack 不是证据，不能冒充证据；
- JSON 输出契约（`docs/json-contract.md`）与 `docs/report.schema.json` 未做任何修改；
- 未来 Skill 若要读 Pack，只能依据 `docs/pack-contract.md` 判断可信字段。

### 3.5 本阶段对契约的调整及理由

实现中做了三处明确调整，均已写入契约文档：

1. **增加并必需 `tool` 块。** 原任务要求契约包含"tool"；把它定义为生产工具身份（名称+版本）而不是散落在 `metadata`，便于未来定位"哪个工具写坏了包"。因为尚无已发布的真实 Pack，此破坏性变更是安全的。
2. **`target_game` / `author` 采用 `string | null`，不用嵌套对象。** 保持跨游戏、最小可用；结构化扩展留给后续 minor 版本，避免现在猜测游戏生态的字段。
3. **显式拒绝 NTFS junction。** Windows 上普通用户常无法创建 symlink，但 junction 不需要特权且同样能把读取重定向到包外；`os.path.isjunction`（Python 3.12+）存在时检查，缺失时退化为 symlink 检查。

## 4. 安全与确定性实现

### 4.1 路径安全

在触碰文件系统之前拒绝：空路径、NUL、反斜杠、绝对路径、Windows 盘符、`.`/`..` 段、以点或空格结尾的段、Windows 保留设备名（`CON`、`NUL`、`COM1`–`COM9`、`LPT1`–`LPT9` 等）、超过 512 字符或 32 段的路径。

通过后逐段 `lstat`：符号链接或 junction 报 `E_SYMLINK`；解析后的真实路径必须仍在包根内，否则 `E_PATH_ESCAPE`；只接受普通文件，目录报 `E_NOT_A_FILE`。`pack.json` 不允许出现在 `files[]`/`assets[]` 中（导出器自动把它放在首位）。

### 4.2 完整性校验

- 声明了 `sha256` 就重算并比对（`E_HASH_MISMATCH`）；缺失只给 `W_HASH_MISSING` 警告。
- 声明了 `size_bytes` 就与实际字节数比对（`E_SIZE_MISMATCH`）。
- PNG/JPEG/GIF/WebP 只读文件头取尺寸，不解码像素；声明尺寸不符报 `E_IMAGE_DIMENSION_MISMATCH`，无法识别的头部只给警告。
- 默认限额：`pack.json` 1 MiB、单文件 64 MiB、总计 256 MiB、文件数 512、图片单边 16384 px、总像素 64M；库调用方可传入自定义 `Limits`。

### 4.3 确定性导出

- 成员顺序固定：`pack.json`（源字节）在首位，其后按路径排序；
- 不写目录条目、不包含未声明文件；
- `ZIP_DEFLATED` level 9，固定 DOS 时间 `1980-01-01 00:00:00`，`create_system=3`，权限 `0644`；
- 同一输入两次导出字节级一致（`test_export_is_byte_for_byte_reproducible`）。

### 4.4 原子性与覆盖保护

- 先完整校验；不合法则**不写出任何归档**，已有输出文件保持不变（测试覆盖）。
- 在目标目录内创建 `.veyra-export-*.zip.tmp`，写入后重新打开校验：CRC（`testzip`）、成员名重复、成员集合完全一致、每个成员与源文件 SHA-256 一致、`pack.json` 字节一致。
- 全部通过才 `os.replace`；任何异常（包括 `os.replace` 失败）都会删除临时文件，不留下半成品（测试用 mock 注入失败验证）。
- 输出已存在时默认拒绝（`E_OUTPUT_EXISTS`），必须显式 `--overwrite`；输出路径落在包根内时拒绝（`E_EXPORT_INSIDE_PROJECT`）。

### 4.5 离线与依赖

无网络调用、无模型调用、无子进程；全部为标准库。Windows 上 junction 检查使用 `getattr(os.path, "isjunction", None)` 保护，Python 3.10（CI 矩阵）仍可运行。

## 5. 测试与验证

### 5.1 自动化测试

执行：

```text
python -m unittest discover -s tests
```

结果（本机 Python 3.15）：

```text
Ran 181 tests in 1.943s
OK (skipped=1)
```

- 新增 74 个，既有 107 个全部继续通过；无失败。
- 唯一跳过：`test_symlink_is_rejected` —— 当前 Windows 会话不允许创建 symlink（`OSError`）。等价的 NTFS junction 测试在同一环境实际执行并通过；Linux CI 上 symlink 测试会执行、junction 测试会跳过，两个环境合计覆盖两条重定向路径。
- 测试全部在 `tempfile.TemporaryDirectory()` 中创建临时包，不触碰仓库内用户文件；`test_module_entry_point_prints_version` 以仓库根为 cwd 运行子进程，只读取代码。

### 5.2 手工端到端验证

在系统临时目录执行（完成后已删除）：

```text
> python -m veyra_pack init <tmp>\kit --name "Demo Kit" --type character_kit --author "Demo Author"
created <tmp>\kit\pack.json

> python -m veyra_pack validate <tmp>\kit
pack valid: 0 files, 0 errors, 0 warnings

> python -m veyra_pack validate <tmp>\kit --json
{"ok": true, "errors": [], "warnings": [], "file_count": 0}

> python -m veyra_pack export <tmp>\kit -o <tmp>\kit.zip
exported <tmp>\kit.zip (1 entries, 315 bytes)

> python -m veyra_pack export <tmp>\kit -o <tmp>\kit.zip
<tmp>\kit.zip: [E_OUTPUT_EXISTS] pass --overwrite to replace it explicitly   (exit 1)
```

重新打开 ZIP：成员列表为 `['pack.json']`，`testzip()` 为 `None`（无损坏），与契约一致。

### 5.3 验证充分的部分

- 合法/非法清单的字段级诊断与稳定错误码；
- 路径穿越、盘符、UNC、反斜杠、保留名、符号链接/junction 拒绝；
- 摘要/尺寸不符、图片尺寸不符、限额超限；
- 导出成员集合、顺序、内容、字节级确定性；
- 覆盖保护、包内输出拒绝、校验失败时旧文件不变、替换失败清理临时文件；
- CLI 退出码（0/1/2）、`--json` 可解析、`--version`、`python -m veyra_pack` 入口；
- `init → validate → export` 全链路。

### 5.4 验证不足或未验证

- **未用真实游戏素材或真实游戏导入流程验证。** 只用了合成 PNG 与文本文件；尚未证明某个游戏能加载 Veyra 导出的包。
- **图片解析只验证了头部路径。** PNG 尺寸由合成生成器写入合法 IHDR；JPEG/GIF/WebP 分支有单元测试，但未用大规模真实图片做模糊测试。
- **无并发/竞态保证。** 校验与导出之间文件可能被外部修改；导出使用导出时刻读取的字节，且回读校验与源文件当前字节比较，但不会重新比对清单里声明的哈希。作为单用户离线工具可接受，并发场景需上层加锁。
- **大文件内存占用。** 导出按文件整体读入内存（单文件上限 64 MiB），未做流式压缩；超大包需要改成分块读取。
- **ZIP64、压缩炸弹、极端文件名。** 依赖标准库行为，未专门做对抗测试。
- **许可只检查结构。** `license.spdx` 不校验 SPDX 列表；缺失只警告，不阻止导出。
- **Windows 专属路径语义未在 Linux CI 全量复现**（junction、保留名大小写等）。

## 6. 兼容性

- 新入口 `python -m veyra_pack` 与既有 `python triage_agent.py` 完全分离；旧 CLI 参数、Markdown/JSON 输出、Skill、安装脚本、CI 均未改动。
- 仅标准库；代码使用 `from __future__ import annotations` 与 `X | None` 注解，CI 的 Python 3.10 可解析；`os.path.isjunction` 有存在性保护。
- 新增只读公共 API：`veyra_pack.validate_pack`、`veyra_pack.export_pack`、`veyra_pack.init_pack` 及 `Limits`/`Issue`/`ValidationResult`，可供未来 Skill/服务复用。
- 契约版本 `1.0.0` 与诊断 JSON 的 `schema_version: "1.0.0"` **同名但互不相关**，文档中已明确区分，避免下游误用。

## 7. 风险与后续建议

1. **Phase 3.2 再接入 Skill 与自然语言层。** 当前工具只解决"确定性打包"；自然语言生成、修改、修复包仍不存在。建议下一阶段先让 Skill 读取 `pack.json` 与诊断结果，生成"可解释的打包计划"，而不是直接调用导出。
2. **真实游戏闭环优先于功能扩张。** 选择一个游戏做最小导入试验（读取包 → 游戏能识别 → 再回写），比增加更多 pack_type 更有价值。
3. **内存与流式处理。** 若真实素材接近上限，把导出改成流式写入并逐块哈希，避免 64 MiB 级内存峰值。
4. **许可与来源治理。** 若要公开发布 Pack，建议增加 SPDX 白名单与 `source` 必填的策略（可作为 1.1.0 的可选检查，不破坏 1.0）。
5. **可选签名/校验和清单。** 现阶段 ZIP 只保证"写的时候没坏"，不保证"分发后没被替换"；未来可加 detached signature，但不应现在引入依赖。

## 8. 合规声明

- 未执行任何 `git add / commit / push / pull / fetch / reset / checkout / clean / rebase / stash`；未改写历史。
- `report.md` 未被读取内容、未被覆盖或重写；本阶段开始与结束的 SHA-256 均为 `A11D09D49217CA3C6CD7C269268C148558EA488067E8D1399BCAFD7ABBC2D68D`，工作区中它仍是用户未提交修改。
- 未读取、恢复或重新跟踪 `real_crash.log`；未使用任何真实玩家日志。
- 未删除日志、缓存或项目文件；测试临时目录在测试结束/手工验证后清理。
- 未修改 `triage_agent.py`、`skills/`、`docs/json-contract.md`、`docs/report.schema.json`、既有测试、CI、安装脚本。
- 未引入第三方依赖；未发起网络请求。

## 9. 结论

Phase 3.1 的最小目标已达成：契约有规范文本、初始化/校验/导出可离线运行、失败路径有测试、产物确定性且原子落盘，且没有触碰诊断层的证据可信度机制。当前状态**适合进入评审与 Phase 3.2 规划**，但尚不具备"真实游戏可用"的结论——真实素材、真实导入与 Skill/自然语言创作仍是未验证部分。

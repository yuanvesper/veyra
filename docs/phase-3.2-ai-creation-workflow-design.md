# Veyra Phase 3.2 — AI Creation Workflow 设计

> 状态：**设计稿，未实现**。本阶段只新增本文档，不修改代码、不执行 Git 写操作。
> 基线：`main` @ `0fa1bbfff9c28e1b12c94b334f23b732d059926e`（Phase 3.1 的 `veyra_pack/` 与 181 个测试在工作区，未提交）。
> 关系：Crash Triage（`triage_agent.py`）与 `veyra_pack` 均保留；本文设计的是位于两者之上的创作工作流，不替换任何现有能力。

---

## 0. 一句话结论

Veyra 的创作链路应当是：

```text
自然语言
  → 可审阅的 Creation Spec（事实与意图分离）
  → 确定性的文件级 Plan
  → 单步、可回滚的 Operations（模型只产出候选，不直接写盘）
  → 确定性 Validation
  → 有边界的 Repair
  → 复用 Phase 3.1 的 Pack 校验与确定性导出
```

AI 不拥有项目状态，项目状态在磁盘上；AI 不拥有“完成”的判定权，判定权在校验器与运行记录。这样既能持续理解同一个项目，也能避免每次修改都重建整个项目。

---

## 1. 目标与非目标

### 1.1 本阶段目标

定义一条从自然语言到可交付创作项目的完整工作流，并明确：

1. Creation Spec 的结构与生命周期；
2. 项目操作（Project Operations）的协议、边界与回滚；
3. 模型调用、上下文装配、成本与失败处理；
4. 每次修改后的确定性校验与修复闭环；
5. 模型提供方的抽象与未来多模型切换；
6. 需要用户授权的操作类型与授权记录；
7. 首个验证案例（鸣潮「锁暝」主题）的完整过程；
8. 同一套能力迁移到崩铁与其他游戏、最终到真实 Mod 的路径；
9. MVP 范围、测试方案与分阶段实施计划。

### 1.2 非目标（本阶段明确不做）

- 不实现代码；本文不产生任何可运行模块。
- 不做游戏内注入、不做游戏客户端资源提取、不做内存修改。
- 不做多 Agent 编排、不做自主循环代理。
- 不做 Web/桌面 UI、账号、市场、支付。
- 不调用真实模型做验证，不产生 API 费用。
- 不修改 `triage_agent.py`、`skills/`、现有 JSON 契约、Pack 契约、既有测试与 CI。
- 不承诺“一键生成可玩的 Mod”。

### 1.3 产品定位边界

最终目标是通用 AI 游戏 Mod 创作平台；但第一阶段的“创作”是**可交付的创作包**（角色主题、桌面主题、素材包、陪伴文案等），不是游戏内 Mod。鸣潮、崩铁是早期主题风格与用户场景的切入口，不是架构依赖。

首个案例因此定义为「锁暝主题包」，而不是「鸣潮游戏内 Mod」。这既符合当前能力，也避免把未经验证的注入能力写进产品承诺。

---

## 2. 设计原则与关键决策

### 2.1 原则

1. **确定性内核，AI 在外围。** 目录、路径、哈希、校验、导出、运行记录由 Python 负责；模型只做理解、候选生成与措辞。
2. **项目即状态。** 不引入数据库、向量库或隐式会话记忆；`spec.json`、`decisions.ndjson`、`runs/` 与 `pack.json` 就是状态。
3. **文件级修改，不做整项目重写。** 每次变更是一个有目标、有前置哈希、可审阅的 operation。
4. **模型输出不可信。** 任何模型产物都必须通过确定性校验才能进入项目或导出。
5. **用户确认优先于模型效率。** 覆盖、删除、付费、外部操作必须显式授权，并留下记录。
6. **复用而非重写。** Pack 的校验、路径安全、原子写与导出直接复用 `veyra_pack`；不复制一套平行实现。
7. **不提前抽象。** 只有出现第二个真实消费者时才抽取公共层；不为“平台感”引入框架。
8. **诚实报告能力。** 没有运行记录就不说“已构建/已测试”；没有适配器就不说“支持该游戏”。

### 2.2 关键决策

| 编号 | 决策 | 理由 |
| --- | --- | --- |
| D1 | 创作状态放在项目根的 `creation/`，**不列入** `pack.json` 的 `assets[]`/`files[]` | 导出器只打包 `resolved_files`（已声明文件），内部状态自然被排除；Pack 契约无需为工作流膨胀 |
| D2 | `pack.json` 仍是唯一发布清单，Creation Spec 是内部规格 | 发布契约稳定、可被未来消费者复用；规格可以快速演进 |
| D3 | 所有写入走 Operation 协议，模型不能直接写文件 | 可审计、可回滚、可做路径与前置条件检查 |
| D4 | Plan 由确定性规划器生成骨架，模型只提议补充 | 避免模型决定项目结构；同一 Spec 得到稳定 Plan |
| D5 | 审批与锁定写入 append-only 的 `decisions.ndjson` | 用户确认可追溯，重放时可判断“哪些内容已确认” |
| D6 | 单进程、单线程、顺序执行；不引入 Agent 框架 | 当前任务不需要并行决策；顺序执行更可解释、更省 Token |
| D7 | Provider 采用 OpenAI 兼容的 HTTP 接口，标准库实现 | 无第三方依赖；未来可替换 endpoint/model |
| D8 | Pack 契约暂不改；真实 Mod 通过 Adapter 与后续 minor 演进 | 避免在无真实需求时破坏 1.0 契约 |
| D9 | Skill 是薄对话层，不承担项目状态与校验 | 与现有 triage Skill 的边界一致；避免“把全部逻辑放进提示词” |

---

## 3. 产品流程

### 3.1 用户视角（早期：对话 + 一个命令）

```text
用户描述创意
  → 系统用最多 5 个问题补齐关键缺口（平台、尺寸、动态形式、授权、预算）
  → 系统展示 Creation Spec 摘要与素材清单
  → 用户确认（可修改字段、锁定已有内容）
  → 系统展示 Plan 与预计成本
  → 用户批准执行
  → 系统逐项生成/写入，每步校验
  → 有错误则展示问题与修复建议（需批准才重试）
  → 全部通过后导出 ZIP
  → 用户本地试用并反馈
```

普通玩家不需要理解 JSON；早期由 Skill 负责提问、解释和把命令转成“下一步做什么”。CLI 是内部执行入口，不是玩家界面。

### 3.2 系统状态机

```text
draft ──spec_confirm──▶ specified ──plan──▶ planned ──approve──▶ executing
  ▲                         ▲                                      │
  │                         └──────────── revise ◀──────────────────┤
  │                                                                ▼
  └──────────── revise ◀──── checked ◀──validate── executing ──▶ failed
                               │                                   │
                               └──────── export ──▶ exported       └── repair ──▶ executing
```

状态保存在 `creation/spec.json` 的 `state` 与 `creation/runs/`，不依赖对话历史。任何一步失败都可停在当前状态，下一次从磁盘恢复。

### 3.3 两个入口，一个内核

- **CLI**：`python -m veyra_creation ...`，负责确定性执行与机器可读输出。
- **Skill**：`skills/creation-workflow/SKILL.md`（计划新增），负责对话式收集意图、解释结果、生成草稿；它只能调用/建议 CLI 命令，不能假设自己能写文件。
- **未来 UI**：复用同一套 Service API，不重新实现业务逻辑。

---

## 4. 项目状态模型

### 4.1 目录布局

```text
my-theme/
  pack.json                 # 发布清单（Phase 3.1 契约，1.0.0）
  assets/                   # 对外素材（会被声明并导出）
  theme/                    # 对外主题描述（会被声明并导出）
  companion/                # 陪伴文案等（会被声明并导出）
  creation/                 # 内部状态，永不导出
    spec.json               # 创作规格（唯一意图来源）
    plan.json               # 当前计划
    decisions.ndjson        # 审批、锁定、解锁、修订、操作结果的追加日志（权威）
    locks.json              # 路径 -> 锁定哈希（权威）
    runs/                   # 每次模型/生成/校验运行记录
      <run_id>.json
    placeholders.json       # 占位/降级素材登记；导出前必须为空
    digest.json             # 派生摘要缓存，可删除重建，不是事实来源
    cache/                  # 可选：按内容哈希复用的候选产物
    context/                # 可选：派生的上下文缓存（可删除重建）
```

**事实来源**：`spec.json`、`plan.json`、`decisions.ndjson`、`locks.json`、`runs/` 与项目中的真实文件是权威状态；`digest.json`、`cache/`、`context/` 都是**可重建的派生视图**。删除派生视图不得丢失任何用户确认、锁定或执行历史。

约束：

- `creation/` 下的文件**不得**出现在 `pack.json` 的 `files[]` 或 `assets[]` 中；`check` 会拒绝这种声明。
- 导出时只打包 `pack.json` 与已声明文件，`creation/` 不在 ZIP 中。这是复用 Phase 3.1 导出器的直接结果，不需要新逻辑。
- 用户手写/替换的素材可以放在 `assets/`，由 `spec` 或 operation 显式登记；未登记的文件既不校验也不导出。

### 4.2 `creation/spec.json`（schema_version 1.0.0）

Spec 是“用户要什么”的唯一结构化表达。字段设计如下（示例值来自首个案例；为便于阅读省略了部分字段，`…` 表示占位，示例本身不是可直接解析的完整 JSON）：

```json
{
  "schema_version": "1.0.0",
  "spec_id": "spec_suoming_theme",
  "revision": 3,
  "state": "specified",
  "created_at": "2026-11-21T00:00:00Z",
  "updated_at": "2026-11-21T00:00:00Z",
  "source": {
    "input_text": "我想制作一套锁暝主题，黑红金配色，有动态水墨背景、机械伞和角色互动。",
    "locale": "zh-CN"
  },
  "project": {
    "pack_id": "suoming-theme",
    "name": "锁暝主题包",
    "pack_type": "desktop_theme",
    "target_game": "Wuthering Waves",
    "version": "0.1.0"
  },
  "creative": {
    "theme": "锁暝",
    "palette": [
      {"id": "ink", "hex": "#0B0B0D", "role": "background"},
      {"id": "crimson", "hex": "#8E1B1F", "role": "accent"},
      {"id": "gold", "hex": "#C8A24A", "role": "highlight"}
    ],
    "motifs": ["动态水墨", "机械伞", "黑红金"],
    "mood": "冷峻、克制、压迫感",
    "characters": [
      {"id": "suoming", "display_name": "锁暝", "ownership": "user-created", "notes": "用户提供的外观与气质描述"}
    ],
    "references": []
  },
  "deliverables": [
    {
      "id": "wallpaper-desktop",
      "kind": "image",
      "role": "wallpaper",
      "path": "assets/wallpaper/ink-4k.png",
      "format": "png",
      "width": 3840,
      "height": 2160,
      "generator": "image",
      "acceptance": ["a_wallpaper_dimensions"]
    },
    {
      "id": "persona",
      "kind": "data",
      "role": "companion-persona",
      "path": "companion/persona.json",
      "format": "json",
      "generator": "text",
      "acceptance": ["a_persona_schema"]
    }
  ],
  "constraints": {
    "license": {
      "spdx": "LicenseRef-Original",
      "source": "user",
      "note": "用户原创设定；不含游戏客户端提取资源"
    },
    "no_game_assets": true,
    "ai_disclosure": true,
    "budget": {
      "max_usd": 2.0,
      "max_input_tokens": 60000,
      "max_output_tokens": 12000,
      "max_images": 4
    },
    "platforms": ["windows-desktop"]
  },
  "acceptance": [
    {
      "id": "a_wallpaper_dimensions",
      "check": "image_dimensions",
      "severity": "error",
      "params": {"width": 3840, "height": 2160, "format": "png"}
    }
  ],
  "assumptions": ["‘动态水墨’以静态分层图 + 动画说明交付；Phase 3.2 不生成视频"],
  "unknowns": ["是否需要手机竖屏版本", "是否允许使用第三方字体"]
}
```

规则：

- **Spec 不接受模型自由发挥的事实**：`characters[].ownership`、`license`、`no_game_assets` 必须来自用户输入或用户确认；模型只能提出候选，并把不确定项写入 `unknowns`/`assumptions`。
- **Spec 中的每个 deliverable 必须有稳定 `id` 与目标 `path`**，路径必须通过 `veyra_pack.paths` 的同一套安全规则。
- **Spec 版本独立于 Pack 版本**，`revision` 每次确认/修订递增；`state` 只由 CLI 迁移，模型不能改。
- **不存储完整对话**；只存 `source.input_text` 与必要的澄清答复摘要，避免把聊天记录当项目状态。

### 4.3 `creation/plan.json`

Plan 是“如何从当前状态到目标状态”的确定性步骤列表。模型可以建议 `generator`/`prompt_ref`，但不能改变目标路径集合。

```json
{
  "schema_version": "1.0.0",
  "plan_id": "plan_0007",
  "spec_revision": 3,
  "state": "proposed",
  "steps": [
    {
      "id": "s1",
      "action": "write_text",
      "target": "theme/theme.json",
      "generator": "deterministic",
      "depends_on": [],
      "budget": {"max_output_tokens": 0}
    },
    {
      "id": "s2",
      "action": "generate_image",
      "target": "assets/wallpaper/ink-4k.png",
      "generator": "image:default",
      "depends_on": ["s1"],
      "budget": {"max_images": 1, "max_usd": 0.5},
      "acceptance": ["a_wallpaper_dimensions"]
    },
    {
      "id": "s3",
      "action": "generate_text",
      "target": "companion/persona.json",
      "generator": "text:default",
      "depends_on": ["s1"],
      "budget": {"max_output_tokens": 3000}
    }
  ]
}
```

规则：

- Plan 只描述**新增/修改**，不描述“重建整个项目”。
- 每一步的 `target` 必须来自 Spec 的 deliverables 或确定性派生清单（如 `theme/theme.json`）。
- `depends_on` 形成有向无环图；执行器按拓扑序串行执行。
- 若某步失败，已完成步骤保留；重试只重跑失败步骤及其下游。
- Plan 中的成本上限不得超过 Spec 的 `constraints.budget`。
- **独立校验**：任何来源（模型或确定性代码）的 Plan 在写入或执行前，必须通过 `plan_check` 的结构、路径、依赖、预算与 Spec 一致性校验；模型输出不经过该校验器不得进入 `plan.json`，执行器也不得绕过它。

### 4.4 `creation/decisions.ndjson`

追加日志，每行一个事件。它是“用户确认过什么”的唯一权威记录。

```json
{"ts":"2026-11-21T00:01:00Z","actor":"user","action":"confirm_spec","spec_revision":3,"reason":"确认配色与交付清单"}
{"ts":"2026-11-21T00:02:00Z","actor":"user","action":"approve_plan","plan_id":"plan_0007","max_usd":2.0}
{"ts":"2026-11-21T00:03:00Z","actor":"system","action":"lock","path":"assets/avatar/suoming.png","sha256":"…","reason":"用户手写素材"}
{"ts":"2026-11-21T00:04:00Z","actor":"user","action":"unlock","path":"assets/avatar/suoming.png","reason":"允许重新生成"}
```

事件类型（MVP）：

| action | 作用 |
| --- | --- |
| `confirm_spec` | 冻结当前 spec revision |
| `approve_plan` | 批准计划与预算 |
| `approve_operation` | 批准单个高风险 operation（覆盖/删除/付费重试） |
| `lock` / `unlock` | 锁定/解锁某个路径的内容哈希 |
| `accept_issue` | 接受某条警告（不阻断导出，但记录理由） |
| `reject` | 拒绝候选产物，保留旧文件 |

`lock` 记录 `sha256`；后续任何写入若目标当前哈希与锁定值一致且未解锁，一律拒绝。

### 4.5 `creation/runs/<run_id>.json`

记录每次模型调用、图片生成、校验与导出尝试，用于成本核算、失败恢复与“是否真的验证过”的判断。

```json
{
  "run_id": "run_20261121_0003",
  "task": "image",
  "provider": "openai-compatible",
  "model": "image-model",
  "started_at": "2026-11-21T00:03:00Z",
  "duration_ms": 18400,
  "status": "ok",
  "attempts": 1,
  "usage": {"input_tokens": 0, "output_tokens": 0, "images": 1},
  "cost_estimate_usd": 0.12,
  "prompt_hash": "sha256:…",
  "outputs": [{"path": "assets/wallpaper/ink-4k.png", "sha256": "…"}],
  "error_code": null
}
```

不默认保存完整 prompt 与原始响应（隐私与体积）；需要调试时由用户显式开启 `--debug-prompts`，并落在 `creation/runs/` 的单独文件中。

### 4.6 项目摘要（Project Digest）

Project Digest 是一个**可重建的派生视图**，不是项目状态本身。真实状态以 `spec.json`、`plan.json`、`decisions.ndjson`、`locks.json`、`runs/` 和项目文件为准；删除 `digest.json`、`cache/`、`context/` 后必须能从这些权威来源重新构建出等价摘要。

每次需要模型参与时，由 Python 从权威来源构建一个**确定性、可截断**的摘要，而不是把整个项目塞进上下文：

```json
{
  "schema_version": "1.0.0",
  "digest_id": "dig_…",
  "project_id": "prj_…",
  "project_version": 7,
  "spec": {
    "spec_id": "spec_suoming_theme",
    "revision": 3,
    "state": "specified",
    "sha256": "…",
    "project": {"pack_id": "suoming-theme", "pack_type": "desktop_theme", "target_game": "Wuthering Waves"}
  },
  "plan": {"plan_id": "plan_0007", "state": "approved", "spec_revision": 3, "sha256": "…"},
  "files": [{"path": "assets/wallpaper/ink-4k.png", "size": 812345, "sha256": "…"}],
  "locks": [{"path": "assets/avatar/suoming.png", "sha256": "…"}],
  "decisions": {
    "count": 4,
    "sha256": "…",
    "confirmed_spec_revision": 3,
    "approved_plan_id": "plan_0007"
  },
  "runs": [{"run_id": "run_…", "task": "image", "status": "ok", "started_at": "…", "outputs": [{"path": "…", "sha256": "…"}]}],
  "budget": {"max_usd": 2.0, "used_usd": 0.32, "max_images": 4, "used_images": 1}
}
```

摘要规则：

- **文件清单、SHA-256、项目版本、用户确认与执行历史**必须可从摘要读出，且与磁盘/日志一致；摘要不得成为这些事实的唯一来源。
- 只包含当前步骤相关的 deliverable 与问题，不默认包含全部文件内容；`files[]` 只列路径、大小与哈希，不内联内容。
- 文件内容按需读取，单个文件超过阈值时按行/块切片，并标注 `[truncated]` 与行号范围。
- 摘要由确定性代码生成：路径排序、JSON 规范化、`digest_id` 只由内容字段计算；`generated_at` 等观测字段不参与哈希。
- 摘要可缓存到 `creation/digest.json` / `creation/context/`；缓存可随时删除重建，不是事实来源。
- 摘要不包含完整 prompt、模型原始响应、密钥或 `real_crash.log` 内容。

---

## 5. 模块边界与依赖

### 5.1 模块划分

| 模块 | 职责 | 不做什么 | 首期 |
| --- | --- | --- | --- |
| `veyra_creation/spec.py` | Spec schema、解析、规范化、版本检查、审查摘要 | 不调用模型、不写文件 | 是 |
| `veyra_creation/store.py` | 项目布局、state、spec/plan/decisions/locks/runs 读取与管理、digest、revision | 不做生成、不做业务校验 |
| `veyra_creation/plan.py` | 从 Spec + 当前状态生成确定性 Plan；diff/依赖图 | 不决定创意内容 | 是 |
| `veyra_creation/plan_check.py` | **独立**校验 Plan 的结构、路径、依赖 DAG、预算、Spec 一致性与审批绑定 | 不生成、不写文件、不执行 | 是 |
| `veyra_creation/operations.py` | Operation 协议、前置/后置哈希、审批绑定、原子应用、失败恢复记录 | 不接受任意路径写 | 是 |
| `veyra_creation/check.py` | 调用 Pack 校验 + 素材/主题/文案校验，汇总问题 | 不修改文件 | 是 |
| `veyra_creation/export.py` | 调用 `veyra_pack.export_pack`；导出前强制 check | 不重新实现 ZIP 逻辑 | 是 |
| `veyra_creation/providers/` | Provider 协议、OpenAI 兼容实现、Fake 实现 | 不做重试编排以外的业务 | 文本先、图片后 |
| `veyra_creation/profiles/` | 创作风格 profile 数据与加载器：配色、默认尺寸、母题词汇、预算预设 | 不承载游戏规则、不写文件 | 是 |
| `veyra_creation/orchestrator.py` | 状态机、步骤执行、预算、失败恢复 | 不做多 Agent 决策 | 是 |
| `veyra_creation/adapters/` | 游戏 Adapter：能力声明、游戏特定校验、未来构建/测试/导出映射 | 不直接写用户文件、不决定创作风格 | 游戏规则先、Mod 后 |
| `veyra_creation/cli.py` | 子命令、JSON 信封、退出码 | 不承载业务规则 | 是 |
| `skills/creation-workflow/` | 对话收集意图、解释结果、生成文案草稿 | 不写文件、不校验、不伪造运行结果 | 后置 |
| `triage_agent.py` | Crash Triage，保持不变 | 不并入创作流程 | 保持 |

### 5.2 依赖方向

```text
CLI / (未来) UI / Skill
        │
        ▼
Orchestrator ──► Plan ──► PlanCheck ──► Operations ──► Store
        │                                      │
        ├──► Providers                         └──► veyra_pack.validation / exporter
        ├──► Profiles（数据：风格/默认值）      │
        ├──► Adapters（游戏规则/能力/构建）     │
        └──► Check（Pack + 素材 + profile + adapter）
```

硬边界：

- `veyra_creation` 可以导入 `veyra_pack`；`veyra_pack` **不得**导入 `veyra_creation`。
- `triage_agent.py` 不导入 `veyra_creation`，`veyra_creation` 不导入 `triage_agent.py`。
- **Profile 与 Adapter 分离**：Profile 只提供创作风格、默认尺寸、母题与预算预设（数据）；Adapter 只提供游戏特定规则、能力声明与未来构建/测试/导出映射（代码/规则）。两者不得互相承担对方职责。
- 任何 Plan 必须先通过 `plan_check` 才能写入或执行；执行器不得绕过。
- Adapter 只能返回事实、能力声明与校验结果，不能直接写文件。
- Provider 只能返回候选内容，不能决定落盘路径。
- Skill 只能读 CLI 的 JSON 输出，不能自行推断“已完成/已构建”。

### 5.3 明确不建立的模块（现阶段）

- 向量库/长期记忆服务：`spec`、`decisions`、`runs` 足够。
- 多 Agent 编排器/任务队列：单线程执行已足够。
- 插件注册表/抽象基类体系：只有两个真实 Provider 时才抽象。
- 数据库：项目目录就是数据库。

---

## 6. 接口设计

### 6.1 CLI 子命令（计划）

```bash
# 从自然语言创建项目骨架（生成 pack.json + creation/spec.json）
python -m veyra_creation new <dir> --from-intent intent.txt \
  --pack-type desktop_theme --target-game "Wuthering Waves" [--json]

# 查看/修订 Spec；修订后 revision+1，state 回到 specified
python -m veyra_creation spec show <dir> [--json]
python -m veyra_creation spec revise <dir> [--from-intent FILE | --set key=value] [--json]

# 从 Spec 生成计划；不写业务文件
python -m veyra_creation plan <dir> [--json]

# 执行计划（可单步、可恢复）；写入前检查审批与预算
python -m veyra_creation run <dir> [--step s2] [--resume] [--dry-run] [--json]

# 校验项目：Pack 契约 + 素材格式/尺寸 + profile 规则 + 内部状态一致性
python -m veyra_creation check <dir> [--json]

# 审批/锁定；不夹带业务写入
python -m veyra_creation approve <dir> --spec-revision 3 [--plan plan_0007] [--json]
python -m veyra_creation lock <dir> --path assets/avatar/suoming.png [--json]
python -m veyra_creation unlock <dir> --path assets/avatar/suoming.png [--json]

# 导出：必须 check 通过；复用 veyra_pack 的原子/确定性导出
python -m veyra_creation export <dir> [-o out.zip] [--overwrite] [--json]

# 状态摘要
python -m veyra_creation status <dir> [--json]

# 一致性核验：state/spec/plan/locks/runs/digest 与磁盘文件对照（只读）
python -m veyra_creation verify <dir> [--json]
```

约定：

- `--json` 时 stdout 只输出单个 JSON 对象；人类提示与日志写 stderr。
- 退出码沿用现有语义：`0` 成功；`1` 校验/执行失败；`2` 用法错误。
- 所有写操作先写入同目录临时文件，再原子替换；不产生半成品。
- `run` 默认拒绝执行未审批的 Plan；`--dry-run` 只展示将要写的路径与成本，不落盘。

### 6.2 JSON 信封

```json
{
  "schema_version": "1.0.0",
  "command": "check",
  "ok": false,
  "project": {"pack_id": "suoming-theme", "spec_revision": 3},
  "data": {"checked_files": 7, "pack_errors": 0, "artifact_errors": 1},
  "errors": [
    {"code": "E_IMAGE_DIMENSION_MISMATCH", "location": "assets/wallpaper/ink-4k.png", "message": "expected 3840x2160, got 2560x1440", "severity": "error"}
  ],
  "warnings": [],
  "run_id": "run_20261121_0005"
}
```

复用 `veyra_pack.contract.Issue.as_dict()` 的字段：`code/location/message/severity`，避免两套诊断格式。

### 6.3 Python API（供 CLI 与未来 UI 复用）

```python
from pathlib import Path
from veyra_creation.spec import load_spec, validate_spec
from veyra_creation.store import ProjectStore
from veyra_creation.plan import build_plan
from veyra_creation.plan_check import validate_plan
from veyra_creation.operations import apply_operations
from veyra_creation.check import check_project
from veyra_creation.export import export_creation

store = ProjectStore(Path("my-theme"))
spec = load_spec(store)
plan = build_plan(store, spec)          # 确定性
plan_issues = validate_plan(plan, spec, store)  # 独立校验；0 errors 才可写入/执行
if plan_issues.errors:
    raise SystemExit(plan_issues.errors[0].format())
result = apply_operations(store, plan)  # 原子、带前置条件
check_issues = check_project(store)     # Pack + 素材 + profile + adapter
if not check_issues.errors:
    exported = export_creation(store, overwrite=False)
```

API 原则：所有函数只接收明确路径与数据对象；不读取全局配置；不发起网络请求（Provider 由调用方注入）。

### 6.4 Provider 协议

```python
class TextProvider(Protocol):
    name: str
    def complete(self, request: ModelRequest) -> ModelResponse: ...

class ImageProvider(Protocol):
    name: str
    def generate(self, request: ImageRequest) -> ImageResponse: ...
```

`ModelRequest` 关键字段：

| 字段 | 说明 |
| --- | --- |
| `task` | `extract_spec` / `plan` / `copy` / `repair_suggestion` |
| `system_prompt` | 版本化模板，含禁止项与输出 schema |
| `messages` | 用户意图、digest、必要文件切片；带来源标签 |
| `response_schema` | 期望 JSON schema；无法满足则视为失败 |
| `max_output_tokens` | 单次上限 |
| `temperature` | 默认低温度；创意文案可略高 |
| `timeout_s` | 单次超时 |
| `metadata` | `run_id`、`spec_revision`、`prompt_version` |

MVP 不启用 streaming、不启用 tool calling、不允许模型指定任意路径。Provider 实现使用标准库 `urllib.request`，避免 SDK 依赖。

**Provider 配置**（文本与图像分离，均可指向 OpenAI-compatible API）：

| 配置 | 说明 |
| --- | --- |
| `text.base_url` / `text.model` | 文本任务 endpoint（含 `/v1`）与模型 ID，可换成任意兼容服务 |
| `text.api_key_env` | 只存环境变量名（如 `VEYRA_TEXT_API_KEY`），不存密钥本身 |
| `text.timeout_s` / `text.max_output_tokens` | 单次调用上限 |
| `image.base_url` / `image.model` / `image.size` | 图像 endpoint、模型与目标尺寸；未配置则明确降级为 placeholder |
| `image.api_key_env` / `image.timeout_s` | 图像密钥环境变量名与超时 |

文本与图像是两个独立 Provider 实例，可分别配置、分别计量、分别失败；不要求同一厂商。配置保存在项目之外的用户配置或环境变量中，项目目录只记录 `provider`/`model` 名称用于追溯。

### 6.5 Profile 与 Adapter 协议（分离）

**Style Profile** 是数据，不是游戏规则：描述配色、母题词汇、默认尺寸、预算预设与少量风格提示词片段。它可由用户自定义，不包含任何“游戏客户端能做什么”的判断。

```python
class StyleProfile(Protocol):
    id: str                       # 如 "dark-ink-mechanical"
    def defaults(self) -> dict: ...       # palette / dimensions / budget_preset
    def validate(self, spec: dict) -> list[Issue]: ...  # 只校验风格字段
```

**Game Adapter** 是代码/规则：描述某游戏的 UGC 能力、允许的 `pack_type`/`role`、游戏特定校验，以及未来构建/测试/导出映射。Adapter 不决定配色、母题或文案风格。

```python
class GameAdapter(Protocol):
    id: str                       # 如 "wuthering-waves"
    def capabilities(self) -> Capabilities: ...
    def validate_artifact(self, deliverable: dict, path: Path) -> list[Issue]: ...
    def validate_project(self, root: Path, spec: dict) -> list[Issue]: ...
    # 未来（真实 Mod）
    def build(self, root: Path, timeout_s: int) -> BuildResult: ...
    def test(self, root: Path, timeout_s: int) -> TestResult: ...
    def export(self, store: "ProjectStore", out: Path) -> ExportResult: ...
```

`Capabilities` 声明：

- 支持的 `pack_type` 与素材 `role`；
- 允许的格式/尺寸/时长；
- 是否需要构建/测试，以及**当前是否真正实现**（`build_supported=false` 时不得声称可构建）；
- 许可证与署名策略；
- 是否允许任何游戏内注入（MVP 全部为 `false`）。

**组合方式**：项目在 Spec 中分别记录 `profile_id`（风格）与 `adapter_id`（游戏）。同一 Adapter 可搭配多个 Profile；同一 Profile 可用于多个 Adapter。首期内置 Profile：`dark-ink-mechanical`（鸣潮案例）、`star-rail-cold-light`（崩铁案例）；首期 Adapter：`wuthering-waves`、`honkai-star-rail`，二者均只声明“主题/素材包”能力，`build_supported=false`。

---

## 7. 模型调用策略

### 7.1 任务分层

| 任务 | 模型参与 | 输出约束 | 失败策略 |
| --- | --- | --- | --- |
| Spec 抽取 | 是 | 严格 JSON，字段白名单；不确定项进 `unknowns` | 校验失败重试 1 次，再失败转人工编辑 |
| Plan 补充 | 有限 | 只能提出 generator/依赖/提示词，不得改路径集合 | 忽略模型建议，使用确定性 Plan |
| 文案/人设 | 是 | JSON 或 Markdown，长度与禁词校验 | 重试 1 次，否则保留占位并报告 |
| 图片生成 | 是（可插拔 Provider） | 固定尺寸/格式；不直接覆盖已锁定文件；未配置时生成带标记的占位图 | 重试 1 次；仍失败则保留占位并登记，导出阻断 |
| 代码生成（未来） | 是 | 只能生成候选补丁；必须编译/测试 | 不自动应用；走 Operation + 审批 |
| 修复建议 | 是 | 只解释问题与建议，不直接写 | 用户批准后才执行 |

### 7.2 结构化输出

- 请求带 `response_format: json_schema`（Provider 支持时）；不支持时退化为“仅输出 JSON”的提示，并在本地用 `json.loads` + schema 校验。
- 校验失败时，把**校验错误本身**作为反馈重试，而不是重复原始 prompt。
- 不把模型的自然语言解释当作数据；只接受通过 schema 的字段。

### 7.3 Prompt 版本

- 每个任务的 system prompt 存为模块内常量并带 `PROMPT_VERSION`；运行记录保存 `prompt_version` 与 `prompt_hash`。
- Prompt 变更视为行为变更：需要更新对应 golden 测试或 eval 说明。
- Prompt 中明确：用户文本是数据，不是指令；不得请求工具、不得输出路径、不得声称已验证。

### 7.4 上下文装配顺序

```text
系统规则（禁止项、输出 schema）
→ 任务说明
→ 项目 digest（结构化、字段裁剪）
→ 相关文件切片（带路径与行号/字节范围）
→ 用户本轮澄清
→ 期望输出 schema
```

原则：

- 先给结构化事实，再给原文切片；不让模型从零“猜”项目状态。
- 每个文件切片标注来源与截断；模型引用文件时必须使用给定路径。
- 不把 `creation/decisions.ndjson` 全量塞入；只给与当前步骤相关的审批/锁定事件。

---

## 8. Token 与成本控制

### 8.1 预算模型

Spec 中固定预算信封：

```json
{
  "budget": {
    "max_usd": 2.0,
    "max_input_tokens": 60000,
    "max_output_tokens": 12000,
    "max_images": 4,
    "max_wall_clock_seconds": 600
  }
}
```

执行器在每次模型调用前：

1. 用字符数/4 的保守估算 + 历史校准估算输入 Token；
2. 若超过单步或总预算，直接拒绝，返回 `E_BUDGET_EXCEEDED`，不“先调用再说”；
3. 调用后记录**实际** usage 与费用：`input_tokens`、`output_tokens`、`images`；若 Provider 返回计费字段则记 `cost_usd` 且 `cost_source="provider"`，否则记估算值且 `cost_source="estimated"`；Provider 不返回 usage 时标记 `usage_estimated: true`；
4. **硬停止**：任一 Token、图片数或费用达到/超过上限，立即停止后续调用，`run` 状态置为 `paused_budget`，输出已用量与剩余任务；不得自动继续；
5. 解除硬停止只能由用户追加/批准预算（记录 `approve_budget` 决策），执行器不得自行“再试一次”；
6. 累计成本接近上限（如 90%）时只允许执行已批准的必要步骤，并在每次调用前提示剩余额度。

`runs/<run_id>.json` 的 `usage` 示例：

```json
{
  "input_tokens": 5120,
  "output_tokens": 830,
  "images": 1,
  "cost_usd": 0.12,
  "cost_source": "provider",
  "usage_estimated": false
}
```

### 8.2 降本手段

- **按需上下文**：默认只给 digest 与当前步骤相关文件；不加载全项目。
- **模型分层**：抽取/分类用小模型；文案/规划用强模型；图片按需。
- **缓存**：同一 `prompt_hash + model + params` 的确定性文本结果可复用；图片默认不复用（避免“假重生成”），除非用户显式 `--reuse-cache`。
- **批量**：同一类文案一次生成多条候选，减少往返；但不得把多个不相关任务混在一次调用。
- **限制重试**：只对瞬时错误重试；校验错误最多反馈 1 次。
- **先确定性后模型**：调色板、命名、路径、manifest、模板文案能确定性生成的，不调用模型。

### 8.3 成本可见性

- `status` 输出本会话累计 Token、Provider 返回值与估算费用（分别标注来源）、剩余图片额度。
- `run` 结束打印一行人类可读摘要；`--json` 返回 `usage`、`cost_usd`、`cost_source` 与 `usage_estimated`。
- 不静默产生付费调用：首次真实 Provider 调用需要用户显式批准（可在配置中一次性授权）。

---

## 9. 失败、超时与上下文过长

### 9.1 错误分类

| 错误码 | 含义 | 是否重试 | 用户可见建议 |
| --- | --- | --- | --- |
| `E_PROVIDER_AUTH` | 密钥无效/缺失 | 否 | 检查环境变量 |
| `E_PROVIDER_TIMEOUT` | 单次超时 | 是（2 次，指数退避） | 稍后重试/减小任务 |
| `E_PROVIDER_RATE_LIMIT` | 限流 | 是（尊重 Retry-After） | 等待后重试 |
| `E_PROVIDER_UNAVAILABLE` | 5xx/网络错误 | 是（2 次） | 检查网络/切换 Provider |
| `E_INVALID_OUTPUT` | JSON/schema 不符 | 最多反馈 1 次 | 展示具体字段错误 |
| `E_CONTEXT_OVERFLOW` | 超上下文 | 否 | 缩小交付项/拆分任务 |
| `E_BUDGET_EXCEEDED` | 预算不足 | 否 | 追加预算或减少产物 |
| `E_APPROVAL_REQUIRED` | 缺少审批 | 否 | 展示待批准操作 |
| `E_CONFLICT` | 文件被外部修改/锁定 | 否 | 展示差异，要求确认 |
| `E_VALIDATION_FAILED` | 产物校验失败 | 修复后可重跑 | 展示问题列表 |
| `E_PLAN_INVALID` | Plan 未通过独立校验 | 否 | 展示具体步骤/路径/依赖错误 |
| `E_PLACEHOLDER_ASSET` | 交付物仍是占位/降级产物 | 否 | 必须替换为真实产物后才能导出 |
| `E_DIGEST_STALE` | 缓存 digest 与权威状态不一致 | 否 | 重新 `digest --write` |
| `E_STATE_CORRUPT` | 状态文件损坏/版本不支持 | 否 | 展示文件与行号，要求修复或恢复备份 |

### 9.2 超时与恢复

- 单次文本调用默认 60 秒，图片 180 秒；总运行时间受 `max_wall_clock_seconds` 限制。
- 每步完成后写 run 记录与 operation；失败时保留已完成步骤。
- `run --resume` 从最后一个未完成步骤继续；不重复已成功且文件哈希未变的步骤。
- 生成中的候选先写临时文件；校验通过后才替换目标文件；失败不污染项目。

### 9.3 上下文过长

按顺序尝试：

1. 移除与当前步骤无关的 deliverable 与历史决策；
2. 把文件切片从“全文”降为“相关片段 + 行号范围”；
3. 用确定性摘要替代长文档，并显式标注 `[truncated]`；
4. 拆分任务（一次只生成一个交付项）；
5. 仍超限则拒绝，要求用户缩小范围或换用更大上下文模型。

**禁止**：静默截断用户约束、许可证要求或验收标准；这些字段永远优先保留。

---

## 10. 修改与“保留用户已确认内容”

### 10.1 Operation 协议

```json
{
  "schema_version": "1.0.0",
  "plan_id": "plan_0007",
  "spec_revision": 3,
  "operations": [
    {
      "op": "create",
      "path": "theme/theme.json",
      "expected_absent": true,
      "source": {"kind": "deterministic"},
      "content_sha256": "…"
    },
    {
      "op": "update",
      "path": "assets/wallpaper/ink-4k.png",
      "expected_sha256": "…",
      "source": {"kind": "generated", "run_id": "run_…"},
      "content_sha256": "…"
    },
    {
      "op": "delete",
      "path": "assets/wallpaper/draft.png",
      "expected_sha256": "…",
      "approval_id": "dec_…",
      "reason": "用户批准删除草稿"
    }
  ]
}
```

应用规则：

1. 路径必须先通过 `veyra_pack.paths` 的检查，并且必须是 Plan 声明的 target；
2. `expected_sha256` 不匹配 → `E_CONFLICT`，不写入；
3. 锁定路径（未解锁）→ `E_LOCKED`；
4. `delete`、覆盖已存在文件、修改锁定文件属于**高风险操作**，必须携带 `approval_id`，且审批记录要绑定 `plan_id + op_id + path + expected_sha256`；仅批准 Plan 不足以执行高风险操作；
5. 每个操作先写临时文件、计算哈希、再 `os.replace`；多个操作按顺序执行，失败停止并报告已应用项；
6. 每个操作（成功或失败）都追加一条 `operation_result` 决策，记录 `event_id`、`plan_id`、`op_id`、`action`、`path`、`before_sha256`、`after_sha256`、`result`、`error_code`、`temp_path`（若存在）与 `recovery` 说明；`before_sha256` 在写入前计算，`after_sha256` 在 `os.replace` 后回读计算；
7. 失败恢复所需信息（临时文件路径、是否已替换、可重试步骤、备份路径）必须写入该事件；执行器重启后据此判断重试或回滚，不得只依赖内存状态。

### 10.2 只改指定文件

- 模型不能返回“完整项目”让执行器覆盖；它只能返回候选内容或候选操作。
- 执行器只接受 Plan 内、且路径安全的操作；任何未声明的写入一律拒绝。
- 用户手写文件不进入 Plan，除非用户显式登记为 deliverable 或提供替换指令。

### 10.3 保留已确认内容

- 用户确认 Spec 后，Spec revision 冻结；修改必须 `spec revise` 并产生新 revision。
- 用户锁定某个文件后，`lock` 事件记录哈希；后续生成若目标已存在且哈希匹配，拒绝覆盖，除非 `unlock`。
- 用户拒绝的候选产物记录 `reject`，同一候选哈希不会再次自动提议。
- 外部修改检测：`check` 对比 `pack.json`/`decisions` 中的哈希与磁盘实际值；发现漂移时报告 `E_EXTERNAL_CHANGE`，要求用户确认，不静默覆盖。

---

## 11. 验证与修复闭环

### 11.1 三层验证

| 层 | 内容 | 权威性 |
| --- | --- | --- |
| L1 契约 | `pack.json` 12 字段、类型、枚举、路径、哈希、尺寸、限额 | 最高；复用 `veyra_pack.validation` |
| L2 产物 | 图片头尺寸/格式、JSON schema、文案长度/禁词、调色板引用（视频元数据检查留待视频能力加入后） | 高；确定性实现 |
| L3 语义/质量 | 风格一致性、构图、文案语气、是否像目标角色 | 人工评审；模型只能辅助，不作为通过依据 |

### 11.2 Check 的组成

`check` 必须同时执行：

1. `veyra_pack.validate_pack(root)`；
2. 内部状态一致性：`pack.json` 与 `spec` 的 `pack_id/version` 一致；`creation/` 未被声明；
3. 每个 deliverable：文件存在、路径匹配、格式/尺寸符合 Spec 与 profile/adapter；若文件是占位/降级产物（Provider 未配置或生成失败），必须登记在 `creation/placeholders.json` 并产生 `W_PLACEHOLDER_ASSET`；**正式导出一律阻断未替换的占位素材**，不提供绕过开关；
4. 锁定与哈希：锁文件未被改动；声明哈希与实际一致；
5. 许可证与披露：`license` 存在、`no_game_assets` 有用户确认、AI 披露标记存在；
6. 未声明文件：在项目根发现未登记素材时给 `W_UNREGISTERED_FILE`，不自动打包。
### 11.3 修复策略

- **确定性可修复**：manifest 字段缺失、路径不合规、尺寸声明错误 → 生成修复 operation，需用户批准后应用。
- **需要重生成**：图片尺寸/风格不符、文案超长 → 使用带约束的错误反馈重试（最多 1 次），不静默改用户文件。
- **不可自动修复**：许可证缺失、游戏资源来源不明、内容违规 → 停止并说明，不猜测。
- **修复不扩大范围**：一次只修当前问题；不得借修复之名重建其他文件。

---

## 12. 安全、授权与合规

### 12.1 授权矩阵

| 操作 | 默认 | 授权要求 |
| --- | --- | --- |
| 读取项目内文件 | 允许 | 无 |
| 在项目内创建 Plan 中声明的新文件 | 允许 | Plan 已批准 |
| 覆盖已存在、未锁定、哈希匹配的文件 | 允许但属高风险 | Plan 已批准 + `approve_operation`（绑定 `plan_id/op_id/path/expected_sha256`） |
| 覆盖用户锁定文件 | 拒绝 | `unlock` + 新 Plan + `approve_operation` |
| 删除文件 | 拒绝 | `approve_operation` + 记录恢复信息 |
| 写入项目根之外 | 拒绝 | 不提供 |
| 调用模型/图片 Provider | 允许（已配置时） | 首次付费调用需批准 |
| 执行构建/测试命令（未来） | 拒绝 | Adapter 声明 + 沙箱 + 审批 |
| 发布/上传/发送 | 拒绝 | 不在 MVP 范围 |

高风险操作的确认是**两阶段**的：先由用户产生 `approve_operation` 决策（含目标路径、当前哈希、绑定的 Plan/操作 ID），执行器再校验审批与运行时目标完全一致；审批不能复用于不同路径、不同哈希或不同 Plan。

### 12.2 路径与文件安全

- 所有路径复用 `veyra_pack.paths.check_relative_path` 与 `resolve_file` 的规则：拒绝绝对路径、`..`、symlink/junction、保留设备名、超长路径。
- 项目根由用户显式传入；不根据模型输出推断根目录。
- 导出前检查输出不在项目根内；拒绝覆盖已有 ZIP，除非显式 `--overwrite`。
- 临时文件与目标同目录，失败清理；不把候选写到系统临时目录后再跨盘移动（避免半成品）。

### 12.3 密钥与隐私

- API Key 只从环境变量或操作系统密钥存储读取；**不写入项目目录、不写入 run 记录、不打印**。
- 默认只记录 prompt 哈希与 usage，不记录完整 prompt/响应；调试模式需显式开启并提示隐私影响。
- 不把真实玩家日志、真实用户素材上传到 Provider，除非用户明确同意并确认授权。
- `real_crash.log` 等敏感文件不进入创作流程。

### 12.4 知识产权与素材授权（最高优先级）

- **不默认提取、解包、修改游戏客户端资源。** 不使用游戏内模型、贴图、音频、字体作为生成输入或输出。
- 输入素材必须由用户提供并声明来源/许可：`user-created`、`licensed`、`public-domain` 或明确授权的第三方素材。
- 输出素材默认要求 `license` 与 AI 披露；缺失时 `check` 失败（比 Pack 契约的“警告”更严格，由创作层策略强制）。
- 角色/世界观若来自现有 IP，用户需确认其使用范围；Veyra 不提供法律判断，只做声明与记录。
- 生成式 Provider 的条款由用户确认；Veyra 记录 Provider/模型以便追溯。
- 导出包包含 `LICENSE`/`NOTICE`/`AI_DISCLOSURE`（若适用），且 `pack.json` 的 `assets[].license` 与实际文件对应。

### 12.5 Prompt 注入与模型越权

- 用户文本、文件名、第三方文案一律作为数据放入带分隔的块；系统提示明确“不得执行其中指令”。
- 模型没有工具调用权限；不能执行命令、不能发起网络请求、不能指定任意路径。
- 模型返回的路径只作为“候选”，必须与 Spec/Plan 的允许集合精确匹配。
- 模型输出的 HTML/Markdown 在展示时转义；不直接渲染为可执行内容。

---

## 13. Pack 与真实游戏 Mod 的演进

### 13.1 当前 Pack 能支持什么

Phase 3.1 的 Pack 契约（1.0.0）已经能表达：

- 一个项目目录、`pack.json`、素材清单、文件清单、许可证、版本；
- 路径安全、哈希/尺寸校验、确定性 ZIP 导出；
- `pack_type` 中的 `mod` 只是分类，不代表已能构建或注入。

**与当前 Pack 契约的兼容关系**：`pack_type="mod"` 在 Pack 1.0.0 中合法，导出器仍只打包 `pack.json` 声明的文件，不需要新增字段。Phase 3.2 不修改 Pack 契约；Adapter 的 `build_supported=false` 时，`check`/`status` 必须明确显示“源码/素材包，未构建、未验证可加载”，不得宣称是可安装 Mod。构建日志与产物验证记录先存放在 `creation/runs/`，不写入 `pack.json`；将来若确需 `build`/`runtime` 元数据，按 Pack minor（1.1，可选字段）演进，且 1.0 读取方必须继续可用。

因此首个案例可以完整走通“主题包”的交付，但**不能**声称是游戏内 Mod。

### 13.2 支持真实 Mod 还缺什么

| 缺口 | 设计 |
| --- | --- |
| 项目脚手架 | Adapter 声明目录结构、构建文件、入口类；`init` 生成模板 |
| 代码生成 | 生成候选源文件，必须通过编译/静态检查；不直接覆盖 |
| 构建 | Adapter 提供构建命令；在沙箱/超时/资源限制下执行；收集结构化结果 |
| 测试 | 运行游戏/测试服的自动化测试或最小加载测试；记录证据 |
| 诊断 | 复用 Crash Triage 的证据模型：构建/运行日志 → 证据 ID + 行号 |
| 交付 | 导出可安装产物 + 构建记录 + 许可证；不承诺“所有加载器通用” |
| 契约扩展 | 需要时升 Pack minor（如 1.1 增加可选 `build`/`runtime` 块），保持 1.0 可读 |

### 13.3 首个真实 Mod Adapter

建议顺序：

1. **Minecraft / NeoForge**：现有 triage 已覆盖 Java 崩溃日志，生态与构建链成熟，适合验证“生成 → 编译 → 启动 → 诊断”闭环。
2. 其他 Java 系加载器（Fabric/Forge）复用大部分适配逻辑。
3. Unity/Unreal/Godot 等项目，需要新的 Adapter 与工具链，不在近期。

真实 Mod 的验收必须包含：构建成功、最小加载成功、日志无致命错误、产物可安装；仅“模型说完成了”不算。

---

## 14. 普通玩家如何不接触 CLI 与 JSON

早期（无 UI）也能做到“玩家只对话”：

1. **Skill 作为对话壳**：用自然语言问 3–5 个关键问题，生成 Spec 摘要；玩家只确认/修改，不看 JSON。
2. **一键命令由宿主执行**：若运行环境允许，Skill 调用 CLI 并把人类可读结果转述；若不允许，Skill 给出**一条**可复制的命令与预期结果，不让玩家理解参数含义。
3. **默认值优先**：平台、尺寸、格式、预算都有安全默认；高级选项折叠。
4. **错误翻译**：CLI 的 `code` 映射为中文建议（缺什么、怎么改、是否收费），不暴露堆栈。
5. **预览与确认**：导出前展示素材缩略图/文案摘要/许可证声明，玩家确认后才生成 ZIP。
6. **未来 UI**：桌面/网页把同一套 Service API 包成按钮与预览；CLI 保留为自动化与调试入口。

---

## 15. 首个验证案例：鸣潮「锁暝」主题

> 说明：「锁暝」按用户提供的原创/授权概念处理，不假定其官方设定；不提取游戏客户端资源。案例目标是验证工作流，不是宣称官方联动。

### 15.1 输入

```text
我想制作一套锁暝主题，黑红金配色，有动态水墨背景、机械伞和角色互动。
```

### 15.2 澄清（最多 5 问）

1. 交付形态：桌面主题包 / 角色素材包 / 两者？
2. 目标平台与尺寸：Windows 桌面 3840×2160，是否需要手机竖屏 1080×1920？
3. “动态水墨”：Phase 3.2 MVP 不生成视频；用静态分层图 + 动画说明表达，视频生成留待后续版本（本次不承诺）。
4. “角色互动”：陪伴文案/语音脚本，还是可执行交互（当前不做游戏内）？
5. 授权与预算：素材是否均为原创/已授权？本次图片生成预算上限？

默认答案（用户不回答时）：桌面主题包；3840×2160 + 1080×1920；静态分层图 + 动画说明（无视频）；陪伴文案 JSON；原创素材、预算 $2、最多 4 张图。

### 15.3 Spec 摘要

- `pack_type`: `desktop_theme`；`target_game`: `Wuthering Waves`
- 调色板：`#0B0B0D` 墨黑、`#8E1B1F` 暗红、`#C8A24A` 旧金
- 母题：水墨扩散、机械伞骨架、金线勾边
- 交付：
  - `assets/wallpaper/ink-4k.png` 3840×2160
  - `assets/wallpaper/ink-mobile.png` 1080×1920
  - `assets/avatar/suoming.png` 1024×1024
  - `assets/icons/umbrella.png`、`assets/icons/ink.png`、`assets/icons/gold.png`，各 512×512
  - `theme/theme.json`（调色板、字体建议、动画说明；不生成视频）
  - `companion/persona.json`（人设、语气、10 条互动台词）
  - `README.md`、`LICENSE`、`AI_DISCLOSURE.md`

### 15.4 端到端过程

#### 步骤 1：创建项目

```bash
python -m veyra_creation new ./suoming-theme \
  --from-intent ./intent.txt \
  --pack-type desktop_theme \
  --target-game "Wuthering Waves" --json
```

结果：

- 调用 `veyra_pack.init_pack` 生成 `pack.json`（`pack_id` 稳定、`tool` 记录 `veyra-pack 0.1.0`）；
- 生成 `creation/spec.json`（`state=draft`，`revision=1`）；
- 不生成任何素材，不调用模型。

#### 步骤 2：抽取与确认 Spec

```bash
python -m veyra_creation spec revise ./suoming-theme --from-intent ./intent.txt --json
python -m veyra_creation spec show ./suoming-theme
```

模型只负责把输入映射到 schema；缺失字段进入 `unknowns`，不猜测。用户确认后：

```bash
python -m veyra_creation approve ./suoming-theme --spec-revision 1   # revision 以 spec show 输出为准
```

写入 `confirm_spec`，`state=specified`。

#### 步骤 3：生成 Plan

```bash
python -m veyra_creation plan ./suoming-theme --json
```

确定性步骤：

```text
s1 write_text theme/theme.json            （调色板/字体/动画说明，无模型）
s2 generate_image wallpaper/ink-4k.png    （模型/图片 Provider，可选）
s3 generate_image wallpaper/ink-mobile.png
s4 generate_image avatar/suoming.png
s5 generate_text companion/persona.json   （模型文案）
s6 write_text README/LICENSE/AI_DISCLOSURE（确定性模板）
s7 check + export
```

Plan 显示预计成本与需要审批的步骤；用户批准：

```bash
python -m veyra_creation approve ./suoming-theme --plan plan_0001
```

#### 步骤 4：执行

```bash
python -m veyra_creation run ./suoming-theme --json
```

执行规则：

- `s1` 先写入主题描述（无模型，确定性）；
- `s2`–`s5` 每步：构建 prompt → 调用 Provider → 校验候选 → 写临时文件 → 通过后替换；
- 每步写 `runs/<run_id>.json`，记录模型、Token、费用、prompt 哈希；
- `s6` 为确定性模板写入，不调用模型；
- 任何一步失败：停止，保留已完成项，输出错误码与恢复命令。

#### 步骤 5：校验与修复

```bash
python -m veyra_creation check ./suoming-theme --json
```

检查项：

| 检查 | 失败示例 | 处理 |
| --- | --- | --- |
| Pack 契约 | 缺少 `schema_version` | 确定性修复/拒绝导出 |
| 图片尺寸/格式 | 实际 2560×1440 ≠ 3840×2160 | 重新生成（需批准） |
| 主题 JSON | `palette` 引用不存在的颜色 ID | 确定性修复 |
| 人设 schema | 台词缺 `id`/`tone` | 重新生成或手工补 |
| 许可证 | 缺 `license`/来源 | 阻断导出 |
| 未登记文件 | `assets/` 有未声明文件 | 警告，不打包 |
| 锁定文件 | 用户手写头像被改动 | `E_CONFLICT`，拒绝覆盖 |

#### 步骤 6：导出

```bash
python -m veyra_creation export ./suoming-theme -o ./suoming-theme.zip --json
```

结果：`veyra_pack.export_pack` 生成确定性 ZIP，包含 `pack.json`、素材、主题、人设、README/LICENSE；不包含 `creation/`。导出后再次回读校验 CRC/成员/哈希。

#### 步骤 7：本地试用与反馈

- 用户在桌面壁纸/播放器中查看效果；
- 记录 `decisions`：接受/拒绝哪些素材、是否重生成、是否锁定；
- 反馈进入 Spec 的下一个 revision，而不是覆盖历史。

### 15.5 验收标准（案例）

- 全流程在临时目录完成，不触碰仓库文件；
- `check` 0 errors；导出 ZIP 可解压、成员与清单一致，导出后重新校验 CRC/成员/哈希通过；
- 所有素材有来源/许可声明，无游戏客户端资源；
- 不锁定/不覆盖用户手写文件；锁定文件被外部修改能被发现，用户确认后恢复；
- 预算未超限，运行记录可追溯；Token 与费用分别有实际/估算来源标记；
- 关闭并重新打开项目后，`status`/`spec`/`plan`/`locks`/`runs` 可从磁盘恢复，删除 `digest.json` 后可重建出相同 `digest_id`；
- 只修改一个指定文件时，Plan 只包含该文件目标，其他文件哈希不变；
- 模拟 Provider 失败或进程中断后，已完成步骤保留；重跑不重复已成功的付费调用，只补失败步骤；
- 导出内容不含 `creation/`、`digest.json`、临时文件或未替换的占位素材；
- 视觉质量由人工评审，不以模型自评代替。

---

## 16. 迁移到崩铁与其他游戏

### 16.1 崩铁角色主题（同能力，换 Profile + Adapter）

同一套核心、同一 Spec schema，只改变：

| 维度 | 鸣潮案例 | 崩铁案例 |
| --- | --- | --- |
| `target_game` | Wuthering Waves | Honkai: Star Rail |
| 风格 Profile（数据） | `dark-ink-mechanical`：水墨、机械、黑红金 | `star-rail-cold-light`：星轨、冷光、深蓝/银白（示例） |
| 默认交付 | 桌面壁纸、伞形图标、陪伴文案 | 竖屏壁纸、星轨图标、列车/角色文案 |
| Game Adapter（规则） | `wuthering-waves`：主题/素材能力，`build_supported=false` | `honkai-star-rail`：主题/素材能力，`build_supported=false` |
| 校验差异 | 尺寸/角色 role（Profile）+ 游戏允许范围（Adapter） | 同样结构，不同默认尺寸/role |
| 授权 | 用户原创/授权 | 同左；不提取游戏资源 |

迁移步骤：

1. 复制 `spec` 的 `creative`/`deliverables` 模板；
2. 换 `project.target_game`、`pack_id`；
3. **分别**选择新的 Style Profile 与 Game Adapter（两者可自由组合）；
4. `plan` 重新生成（路径/尺寸/角色变化）；
5. 复用同一 `check` 与 `export`；
6. 增加该组合的黄金用例（1 个正向、3 个负向）。

**不改核心代码**；如果发现需要改核心，说明抽象错了，应先修 Profile/Adapter 边界。

### 16.2 其他游戏与真实 Mod

- 新游戏 = 新 Game Adapter（能力声明、游戏特定校验、未来构建/测试）；风格可复用现有 Profile，也可新增 Profile 数据。
- 真实 Mod = Adapter 增加 `build`/`test`/`export`，加沙箱与工具链检测；Pack 用 `pack_type=mod`，导出源码/构建文件/说明；在 `build_supported=true` 且有真实构建产物之前，不得宣称“可安装”。
- 游戏不支持 UGC 时，产品只提供“主题/素材包”，不提供注入；Adapter 的能力声明必须诚实反映这一点。
- 不把鸣潮/崩铁的字段写进核心；它们只是 Profile 数据与 Adapter 规则，二者不得混在同一个模块。

---

## 17. MVP 范围

### 17.1 MVP 包含

1. `new` / `spec` / `plan` / `run` / `check` / `export` / `status` 子命令；
2. Spec、Plan、Operation、Decisions、Run 五种数据结构；
3. 确定性 Plan 与文件级 Operations；
4. 复用 `veyra_pack` 的校验、路径安全、原子写、导出；
5. 一个文本 Provider（OpenAI 兼容，测试用 Fake）；
6. 一个图片 Provider 接口（可插拔；未配置时生成带标记占位并登记，**正式导出阻断**）；
7. Style Profile `dark-ink-mechanical`/`star-rail-cold-light` 与 Game Adapter `wuthering-waves`/`honkai-star-rail`，二者分离；
8. 锁定/审批/冲突检测；
9. Token/成本记录与硬上限；
10. 首个案例的端到端脚本（临时目录）与 golden 测试。

### 17.2 MVP 不包含

- 游戏内注入、客户端资源提取、内存修改；
- 代码编译/游戏启动测试（留给真实 Mod 阶段）；
- 多 Agent、自动长循环、无人值守生成；
- Web/桌面 UI、账号、支付、市场；
- 视频生成：明确不在 Phase 3.2 MVP（静态分层图 + 动画说明替代）；未来单独评估；
- 自动发布到任何平台；
- 对模型输出做“艺术质量”自动判定。

### 17.3 成功标准

- 一个不懂 CLI 的用户，在 Skill 引导下能完成一次主题包导出；
- 全流程失败可恢复，不产生半成品或静默覆盖；
- 所有生成物有来源、许可、运行记录；
- 同一 Spec + 同一输入重复执行，确定性产物字节一致（模型产物不要求）；
- 不触碰 Crash Triage 与既有 181 个测试。

---

## 18. 测试方案

### 18.1 单元测试

| 对象 | 用例 |
| --- | --- |
| Store | init/reopen、state 版本递增、损坏状态、重复初始化、路径逃逸 |
| Spec | 必填字段、类型、版本、未知字段、revision、state 迁移、确认后冻结 |
| Digest | 确定性、文件清单/SHA-256、锁定/确认/运行摘要、删除重建、陈旧检测、排除 `creation/` |
| Plan | 依赖拓扑、路径集合、预算继承、禁止重建、独立校验器拒绝越权 |
| Operations | create/update/delete、前置/后置哈希、锁定、审批绑定、路径逃逸、原子失败清理、恢复信息 |
| Decisions | 追加、重放、重复 event_id、损坏行、confirm/approve/lock/unlock 语义 |
| Runs | 开始/完成、重复 run_id、未知 run、损坏 run、usage 记录 |
| Check | Pack 错误透传、尺寸/格式、许可证、未登记文件、外部改动、占位阻断 |
| Budget | 估算、硬上限、usage 缺失、90% 停止 |
| Context | 超限降级顺序、约束不被截断 |
| Provider | Fake 正常/超时/限流/非法 JSON/重试 |
| Adapter | Style Profile 能力、role/尺寸校验、未知 role、`build_supported` 诚实性 |

### 18.2 集成测试（全部临时目录）

1. `new → spec → plan → run(Fake) → check → export` 全链路；
2. 导出 ZIP 不含 `creation/`；成员/哈希与清单一致；
3. 未批准 Plan 拒绝执行；未批准删除拒绝；
4. 锁定文件被修改 → `E_CONFLICT`；
5. 图片尺寸不符 → check 失败 → 修复后重跑；
6. 预算耗尽 → 停止且不写半成品；
7. Provider 超时 → 保留已完成步骤 → `--resume` 继续；
8. 导出失败 → 旧 ZIP 不变；
9. 外部手改文件 → 漂移检测；
10. 两套 Profile + Adapter 组合的同一 Spec 模板分别通过；
11. 关闭进程后重开 → state/spec/plan/locks/runs 完整恢复，digest 重建一致；
12. 锁定文件被外部修改 → `verify` 报 `E_LOCK_DRIFT`，恢复后通过；未解锁时覆盖被拒；
13. 局部修改：只改一个 deliverable，Plan 只含该目标，其他文件哈希不变；
14. 失败恢复：Provider 失败/进程中断后已完成步骤保留，重跑不重复付费调用；
15. 导出后重新打开 ZIP，逐成员 CRC/哈希校验通过，且不含 `creation/` 与占位素材。

### 18.3 Golden / Eval

- `case-01-suoming-theme`：固定 Spec + Fake Provider，断言最终 ZIP 成员、`pack.json` 字段、`check` 0 errors；
- `case-02-hsr-theme`：同结构，不同 Profile + Adapter；
- 负向用例：缺许可证、使用 `../` 路径、声明 4K 实际 1080p、未批准覆盖、预算超限、图片 Provider 返回损坏文件、导出时仍有占位素材；
- 每个用例记录“期望错误码”，不比较模型文案原文。

### 18.4 CI

- 不发起真实网络请求；Provider 用 Fake；
- Ubuntu + Windows 矩阵；Python 3.10+；
- 运行 `python -m unittest discover -s tests`；
- 校验导出 ZIP 的确定性与契约；
- 不在 CI 中保存真实用户素材/日志。

### 18.5 手工验证

- 真实 Provider 的首次调用（用户批准，记录费用）；
- 生成图片的尺寸/格式/颜色人工检查；
- 桌面/播放器实际使用；
- 许可证与 AI 披露人工复核；
- 失败注入：断网、超时、限流、非法 JSON。

---

## 19. 分阶段实施计划

> 以下为**计划**，不是本阶段交付。每阶段结束输出实施报告，区分已验证/验证不足/未完成。

### 3.2.1 状态存储与 Project Digest（无模型，本次授权范围）

- `store.py`：项目布局、`state.json`、spec/plan 读取与基础结构校验、decisions 追加/重放、locks、runs、revision、项目版本；
- `spec.py`：Spec 基础 schema、加载、版本与路径安全校验（不接模型）；
- `plan.py` / `plan_check.py`：Plan 结构、路径、依赖 DAG、预算与 Spec 一致性校验；`save_plan`、`approve_plan` 与 `verify` 均调用独立校验器；
- `digest.py`：确定性文件清单/SHA-256、Digest 生成/写缓存/陈旧检测、从真实文件重建；
- CLI：`init/status/digest/verify`（JSON 信封）；
- 测试：临时目录、正常/异常/损坏状态、重复执行、关闭重开、锁定漂移、digest 重建、junction/ADS/大小写/决策删除/内容篡改的负向回归。

**闸门**：关闭并重开项目后状态可完整恢复；删除 `digest.json` 可重建相同 `digest_id`；锁定文件漂移可被发现；既有 181 测试全绿。`check/export` 与模型能力不在本阶段。

### 3.2.2 Plan 与 Operations（无模型）

- `plan.py`：确定性步骤、依赖、预算（`plan_check.py` 静态校验已在 3.2.1 落地，执行绑定留在本阶段）；
- `operations.py`：协议、前置/后置哈希、锁定、审批绑定、原子应用、恢复记录；
- `check.py`/`export.py`：确定性校验 + 复用 `veyra_pack` 导出；占位素材阻断；
- CLI：`plan/run/check/export/approve/lock/unlock`；
- Fake Generator：按模板写文本/占位 PNG；
- 测试：并发外改、冲突、删除审批、失败清理、ZIP 重新校验。

**闸门**：`new → plan → run(Fake) → check → export` 全链路；未批准操作 100% 拒绝。

### 3.2.3 文本 Provider 与成本控制

- Provider 协议、OpenAI 兼容实现、Fake；
- Spec 抽取、文案生成、修复建议；
- 预算、usage、超时、重试、错误映射；
- 测试：Mock HTTP（本地 socket/自定义 opener），不访问外网。

**闸门**：真实 Provider 手动冒烟一次（用户批准）；无密钥时给出清晰错误；预算超限不调用。

### 3.2.4 图片 Provider 与首个案例

- 图片 Provider 接口 + 至少一个实现（或明确“未配置”降级）；
- 尺寸/格式/哈希校验；
- 案例 `case-01-suoming-theme`；
- 成本上限与图片数量限制。

**闸门**：案例导出 ZIP；图片全部通过尺寸/格式/许可检查；Provider 失败可产生带标记 placeholder 作为中间态，但**未替换为真实素材前导出必须阻断**。

### 3.2.5 Skill、崩铁迁移与 Evals

- 新增 `skills/creation-workflow/SKILL.md`（薄层）；
- `case-02-hsr-theme`；
- 文档：用户指南、错误码、授权与许可；
- README 增加“Creation workflow（实验性）”说明；
- 评测：正向/负向用例，人工评审视觉质量。

**闸门**：Skill 不写文件、不伪造运行结果；两套 Profile + Adapter 组合使用同一 Spec 结构通过；全套测试与 181 基线全绿。

### 3.2.6 真实 Mod 预研（不承诺实现）

- 选择 Minecraft/NeoForge 做 Adapter 可行性验证；
- 定义构建/测试沙箱与证据格式；
- 评估 Pack 1.1 是否需要 `build` 元数据；
- 只出设计与 spike，不进入产品承诺。

---

## 20. 风险与开放问题

### 20.1 风险

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| 模型生成质量不稳定 | 素材不可用、反复重试 | 确定性模板 + 人工评审 + 重试上限 |
| 成本失控 | 用户流失 | 预算信封、调用前估算、硬停止 |
| 版权/许可 | 法律与社区风险 | 不提取客户端资源、许可证强制、披露 |
| 游戏不支持 UGC | 产品承诺落空 | 先做主题包；Adapter 能力声明诚实 |
| 上下文过长 | 失败/质量下降 | digest、切片、拆分任务、明确拒绝 |
| 复杂度膨胀 | 交付延期 | MVP 只做单线程、文件级操作、Profile/Adapter 组合 |
| 模型越权/注入 | 文件被改/数据泄露 | 模型无工具权限、路径白名单、审批 |
| Pack 契约被创作需求污染 | 兼容性破坏 | 内部状态放 `creation/`；必要时 minor 演进 |

### 20.2 需要产品确认的开放问题

1. 首个真实图片 Provider 选哪家？是否接受其条款与费用？
2. 首个交付平台是 Windows 桌面、手机壁纸，还是仅素材包？
3. 视频是否列入后续版本？Phase 3.2 已确定不做视频；未来是否值得单独评估，待真实用户反馈。
4. 角色 IP 的使用边界：仅用户原创，还是允许用户声明的同人授权？
5. 是否允许 Skill 在目标环境执行 CLI？若不允许，交互降级为“复制一条命令”。
6. MVP 预算上限与目标用户的可接受价格。
7. 真实 Mod 的首个游戏是否确定为 Minecraft/NeoForge。

---

## 21. 与现有资产的关系与合规声明

- **Crash Triage 保留**：`triage_agent.py`、`docs/json-contract.md`、`docs/report.schema.json` 不改；现有 107 个非 Pack 测试（诊断、Skill 校验、CLI、文件安全等）保持不变。
- **Pack 复用**：`veyra_pack` 的 `validate_pack`、`export_pack`、路径安全、原子写、确定性 ZIP 直接复用；不复制实现。
- **契约独立**：创作 Spec 1.0.0 与 Pack 1.0.0、诊断 JSON 1.0.0 各自版本化，不共享字段。
- **测试基线**：当前 181 个测试（1 个 Windows symlink 跳过）必须保持全绿；新增测试全部使用临时目录。
- **本阶段合规**：设计文档阶段只新增/修改本文档，未修改代码、未执行 Git 写操作、未读取 `real_crash.log`、未调用模型、未联网；`report.md` 未触碰。3.2.1 实施阶段的改动与验证另见 `docs/phase-3.2.1-state-store-report.md`。
- **能力边界**：本文描述的是设计与计划，不代表已实现；真实游戏导入、真实 Mod、真实 Provider 费用与质量均未验证。

---

## 22. 九个必须回答的问题（索引）

| 问题 | 答案所在 | 一句话回答 |
| --- | --- | --- |
| 如何让 AI 持续理解同一个项目？ | §4、§7.4 | 每次从 `spec/decisions/locks/runs` 构建确定性 digest，不依赖聊天记忆。 |
| 如何避免每次修改都重建整个项目？ | §4.3、§10 | Plan 只列增量步骤；Operation 只改目标文件；哈希/缓存复用未变内容。 |
| 如何只修改指定文件？ | §10.1、§10.2 | 模型只能产出候选；执行器只接受 Plan 内、路径安全、前置哈希匹配的操作。 |
| 如何保留用户已经确认的内容？ | §4.4、§10.3 | `confirm_spec` 冻结 revision；`lock` 记录哈希；未解锁拒绝覆盖。 |
| 如何验证生成的代码和资源？ | §11 | L1 Pack 契约 / L2 格式尺寸 schema / L3 人工评审；模型自评不算通过。 |
| 如何管理模型成本和 Token？ | §8 | 预算信封、调用前估算、模型分层、缓存、硬停止、Token 与实际费用分别记录。 |
| 如何处理生成失败、超时和上下文过长？ | §9 | 错误分类、有限重试、步骤级恢复、上下文降级顺序、明确拒绝。 |
| 如何让 Pack 未来支持真正的游戏 Mod？ | §13 | Adapter 增加构建/测试/沙箱与证据；必要时 Pack minor；先 NeoForge。 |
| 如何让普通玩家不需要理解 CLI 和 JSON？ | §14 | Skill 对话 + 默认值 + 错误翻译 + 预览确认；CLI 仅内部执行。 |

---

## 23. 审核修订对照（11 条）

> 本节对应 2026-11-21 审核意见；每条注明设计落点与 3.2.1 是否已实现。设计修订本身不改变“证据/文件优先于模型叙述”的原则。

| # | 审核要求 | 设计落点 | 3.2.1 状态 |
| --- | --- | --- | --- |
| 1 | Digest 是可重建派生视图，真实状态以文件/操作记录/锁为准 | §4.1 事实来源、§4.6 摘要规则 | 已实现存储与 digest；派生缓存可删除重建 |
| 2 | 所有模型 Plan 必须过独立校验器，模型不得直接写盘 | §4.3、§5.1 `plan_check.py`、§5.2、§6.3、§10.1 | 静态 `plan_check.py` 已在 3.2.1 实现；模型 Plan 接入与执行绑定在 3.2.2+ |
| 3 | Operation 记录前后哈希、结果、恢复信息 | §10.1 第 5–7 条 | 未实现（3.2.2） |
| 4 | delete/覆盖/锁定修改等高风险操作需明确权限与确认 | §10.1、§12.1 两阶段审批 | 未实现（3.2.2）；设计已定 |
| 5 | Profile 管风格配置，Adapter 管游戏规则/构建/测试/导出，二者分离 | §5.1、§5.2、§6.5、§16 | 3.2.1 不实现 Profile/Adapter；边界已定 |
| 6 | 明确 `mod` 与当前 Pack 契约兼容，不宣称未实现能力 | §13.1 | 已明确：Pack 1.0 不变，`mod` 仅分类 |
| 7 | 图像生成可插拔；未配置用明确标记占位；正式导出阻断未替换占位 | §11.2、§15.5、§17.1 | 未实现（3.2.3/3.2.4）；设计已定 |
| 8 | 视频移出 3.2 MVP | §7.1、§15.2–§15.5、§17.2、§20.2 | 已从案例/验收移除 |
| 9 | Provider 支持 OpenAI-compatible；端点/模型可配；文本与图像分离 | §6.4 | 未实现（3.2.3）；设计已定 |
| 10 | 预算同时记录 Token 与实际费用；超预算硬停止并等用户确认 | §8.1、§8.3 | 未实现（3.2.3）；设计已定 |
| 11 | 端到端验收含关闭重开、锁定保护、局部修改、失败恢复、ZIP 重新校验 | §15.5、§18.2 | 3.2.1 已覆盖关闭重开/锁定/digest 重建；其余在 3.2.2+ |

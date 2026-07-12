# MoocManus Agent Skills 核心链路设计

**状态：** 已批准，待用户审阅书面规格

**日期：** 2026-07-12

**适用仓库：** `mooc-manus`

**首期范围：** 标准 Skill ZIP 的管理、发现、渐进加载、沙箱同步与执行

## 1. 决策摘要

MoocManus 将按照开放的 Agent Skills 规范实现一条完整但克制的 Skill 垂直链路：设置页操作者上传 Skill ZIP，`SkillRegistry` 持久化并管理 Skill，Agent 在任务开始时获得已启用 Skill 的轻量元数据目录，并通过 `load_skill(name)` 按需加载完整 `SKILL.md`。首次加载时，系统把完整 Skill 包同步到当前会话沙箱；随后 Agent 使用现有 File 和 Shell 工具按需读取 `references/`、使用 `assets/`、执行 `scripts/`。

当前项目没有用户或租户身份模型，因此首期 Skill Registry 是系统全局资源，适用于所有新会话。

首期保留以下产品能力：

- 设置页上传、列表、详情预览、启停、删除和同名覆盖。
- 基于 `description` 的模型自动匹配。
- 聊天输入框 `$skill-name` 显式选择和调用。
- 同一任务组合加载多个 Skill。
- Planner、ReAct 与 Team 各类 Agent 的 Skill 目录和加载能力。
- 复用现有 `ToolEvent` 展示 Skill 加载过程。
- 完整 Skill 包同步到会话沙箱，以及资源读取和脚本执行。
- 正在运行的任务使用创建时快照，管理操作只影响后续任务。

首期明确不实现：

- Skill 版本历史、版本号、回滚或版本选择。
- 安全审批、来源签名、恶意脚本扫描或独立权限审查系统。
- Skill 市场、Git 仓库安装、远程 Registry 联邦或插件分发。
- Agent 在对话中创建、修改或沉淀 Skill。
- 新的 `read_skill_resource` 或 `run_skill_script` 工具。
- LLM Provider 或 Agent 框架迁移。

## 2. 外部调研结论

Agent Skills 已形成明确的开放格式和实现共识：Skill 是包含 `SKILL.md` 的目录，可选包含 `scripts/`、`references/` 和 `assets/`；运行时采用三层渐进披露，仅在初始上下文中提供名称和描述，匹配后加载完整指令，再按需访问资源。

| 实现 | 发现与存储 | 激活方式 | 对本项目的启示 |
|---|---|---|---|
| Agent Skills 开放规范 | 文件目录与 `SKILL.md` | 元数据目录、完整指令、按需资源三层加载 | 采用标准包结构和渐进披露 |
| OpenAI Codex | 仓库、用户、管理员和系统级目录 | 自动匹配或 `$skill-name` 显式调用 | 同时支持自动与显式触发 |
| OpenAI API | 上传 ZIP/目录形成托管 Skill Bundle，并挂载到 Shell 环境 | 模型按元数据选择后读取完整 Skill | Web Agent 需要 Registry 和运行时挂载 |
| Anthropic Claude | 本地文件系统或 API 上传 Skill | 自动发现并按需加载 | Skill 包与执行环境分离 |
| GitHub Copilot | 项目级和用户级 Skill 目录 | 根据描述自动选择，也支持显式调用 | 描述是模型路由的主要依据 |
| Microsoft Agent Framework | Provider 配合文件、代码、类或 MCP Source | `load_skill`、资源读取和脚本执行工具 | Provider/Registry 边界适合服务端 Agent |
| LangChain Deep Agents | Middleware 从文件系统或远端 Backend 发现 | 目录进提示词，模型通过文件工具读取完整内容 | 可复用现有文件与沙箱工具 |
| Gemini CLI | 工作区和用户目录 | 发现后按需激活 | Skill 与常驻项目指令应分开 |

MoocManus 不是纯本地 CLI，而是 FastAPI 服务加临时任务沙箱。纯文件扫描无法覆盖设置页上传和跨任务持久化；直接使用 OpenAI 托管 Skills 又会破坏当前 DeepSeek/OpenAI-compatible 模型兼容和自有沙箱。因此首期采用项目原生的 `SkillRegistry + SkillRuntime + SkillTool`，同时复用现有 PostgreSQL、OSS、Agent、Tool、Memory、Sandbox、SSE 和 UI 设置框架。

## 3. 当前项目状态

当前主要执行链路为：

```text
Session route
  -> AgentService
  -> AgentTaskRunner
  -> PlannerReActFlow / TeamFlow
  -> Planner / ReAct / Team Agents
  -> BaseAgent tool loop
  -> ToolEvent / MessageEvent / DoneEvent
  -> Redis Stream + Session JSONB + SSE
```

当前系统具备本设计需要复用的基础能力：

- `BaseAgent` 已支持 OpenAI function tool schema、工具循环和工具结果进入 Memory。
- `PlannerReActFlow` 和 `TeamFlow` 都在构造 Agent 时组装工具列表。
- `AgentTaskRunner` 持有本轮 Sandbox、MCP、A2A 和文件存储，并统一处理 ToolEvent。
- Sandbox 已支持二进制文件上传、文件访问和 Shell 命令执行。
- 设置页已有 LLM、MCP、A2A 的列表、启停、删除和新增交互模式。
- PostgreSQL 与 OSS 已是应用现有持久化依赖。

当前没有 Skill 领域模型、Registry、管理接口、Agent 目录提示词、Skill Tool、沙箱 Skill 目录或前端 Skill 管理入口。

## 4. 目标与非目标

### 4.1 产品目标

- 上传一个包含 `SKILL.md` 和可选资源的 ZIP 后，新任务能够发现并使用该 Skill。
- Agent 初始上下文只承载已启用 Skill 的 `name` 和 `description`。
- Agent 可以自动判断相关 Skill，也可以响应用户的显式 `$skill-name`。
- `load_skill` 将完整 `SKILL.md` 放入调用 Agent 的独立 Memory。
- Skill 的相对资源路径在会话沙箱中可用。
- React 和 Team 模式都可以加载 Skill。
- 设置页可以完成 Skill 的最小生命周期管理。
- 所有 Skill 对当前系统的全部新会话全局生效，不增加用户级或会话私有作用域。

### 4.2 首期非目标

- 不建立 Skill 发布、审核、签名、版本或回滚平台。
- 不实现语义向量检索、关键词 Router 或单独的 Skill Selector Agent。
- 不让 Skill 绕过 Agent 当前已有的工具集合和 Team ToolPolicy。
- 不新增运行配置项，不修改 `.env`，也不要求新增 Docker Volume。
- 不替换 OpenAI-compatible Chat Completions 调用链。
- 不改变 PlannerReActFlow 或 TeamFlow 的任务编排语义。

## 5. 总体架构

```text
Settings UI
  -> POST Skill ZIP
  -> SkillService
  -> SkillParser
  -> SkillRegistry
       -> PostgreSQL Skill metadata + SKILL.md
       -> OSS Skill ZIP

New chat task
  -> AgentService
  -> SkillRegistry creates enabled Skill snapshot
  -> AgentTaskRunner owns SkillRuntime + Sandbox
  -> Planner / ReAct / Team Agents receive Skill catalog
  -> automatic match or explicit $skill-name
  -> SkillTool.load_skill(name)
  -> SkillRuntime syncs ZIP to Sandbox once
  -> full SKILL.md enters the calling Agent Memory
  -> existing File/Shell tools use references/assets/scripts
  -> existing ToolEvent reaches Redis/PostgreSQL/SSE/UI
```

架构边界如下：

- Registry 是 Skill 持久化状态和管理操作的唯一入口。
- Snapshot 固定单个任务可见的 Skill 集合和 ZIP 内容。
- Runtime 只负责当前任务中的目录披露、同步和物理路径解析。
- SkillTool 只负责把指定 Skill 加载到当前 Agent 上下文。
- File/Shell 继续负责资源读取和脚本执行，不复制工具能力。
- UI 不参与匹配逻辑，只提供管理入口和显式提及交互。

## 6. 领域模型与持久化

### 6.1 Skill

新增 `Skill` 领域模型及 PostgreSQL `skills` 表：

```text
id            UUID 字符串，主键
name          Skill 名称，唯一
description   用于模型匹配的描述
skill_md      完整 SKILL.md 文本
root_path     ZIP 中 Skill 根目录的相对路径
bundle_key    当前 ZIP 的 OSS 对象 key
enabled       是否对新任务可见
created_at    创建时间
updated_at    更新时间
```

不增加版本字段。同名上传复用原 `id` 和 `enabled`，替换其他可变字段。

### 6.2 SkillSnapshot

`SkillSnapshot` 是任务内不可变对象，不写入数据库：

```text
id
name
description
skill_md
root_path
bundle_bytes 或 bundle_load_error
```

创建新 `AgentTaskRunner` 时一次性读取已启用 Skill 的元数据并下载当前 ZIP 内容。这样同名覆盖或删除 OSS 当前对象不会改变已经运行的任务，也不需要引入用户可见的版本系统。

### 6.3 OSS 对象

Skill ZIP 使用独立前缀：

```text
skills/{skill_id}/{upload_id}.zip
```

`upload_id` 只用于避免覆盖过程破坏旧对象，不构成版本能力。成功切换数据库 `bundle_key` 后，旧对象立即做尽力删除；失败对象也做尽力清理。Registry 只暴露数据库当前指向的一个 ZIP。

## 7. 核心组件职责

### 7.1 SkillParser

`SkillParser` 接收 ZIP 字节并完成运行所必需的解析：

1. 在 ZIP 中定位 `SKILL.md`。
2. 解析 YAML frontmatter。
3. 提取 `name`、`description`、完整 `SKILL.md` 和根目录相对路径。

首期不增加独立的格式校验阶段，不检查名称规范、目录名一致性、文件数量、文件大小、脚本内容、来源或签名。若 ZIP 中出现多个 `SKILL.md`，按 ZIP 条目顺序使用第一个；若无法得到运行所需字段，解析本身失败，上传接口返回功能错误。同名覆盖使用 `name` 的精确字符串匹配。

### 7.2 SkillRegistry

`SkillRegistry` 提供：

```text
list_skills()
get_skill(id)
upsert_bundle(zip_bytes)
set_enabled(id, enabled)
delete_skill(id)
create_enabled_snapshot()
```

Registry 协调 SkillRepository 与 SkillBundleStorage，但不参与 Agent 推理。

### 7.3 SkillBundleStorage

新增轻量 `SkillBundleStorage` 协议和 OSS 实现，只提供 ZIP 的写入、读取和删除。它不复用会话附件的 File 领域模型，避免 Skill 包进入普通文件列表。

### 7.4 SkillRuntime

每个 `AgentTaskRunner` 创建一个 `SkillRuntime`，持有任务 SkillSnapshot、Sandbox、同步缓存和每个 Skill 的异步锁。

它负责：

- 生成可供系统提示词使用的 Skill 目录。
- 按名称查找当前快照中的 Skill。
- 首次激活时把 ZIP 上传到 Sandbox。
- 使用 Sandbox 已有 Python 解压能力展开 Skill 包。
- 返回 Skill 在 Sandbox 中的绝对根目录。
- 并发 Team Worker 加载同一 Skill 时只执行一次物理同步。

Sandbox 使用内部 UUID 路径，不把 Skill 名称拼入命令：

```text
/home/ubuntu/.mooc-manus/skills/{skill_id}/bundle.zip
/home/ubuntu/.mooc-manus/skills/{skill_id}/content/
```

### 7.5 SkillTool

新增一个工具集 `SkillTool`，首期只有一个函数：

```text
load_skill(name: string)
```

成功结果使用结构化包裹：

```text
<skill_content name="...">
[完整 SKILL.md]

Skill directory: /home/ubuntu/.mooc-manus/skills/{id}/content/{root_path}
Relative paths in this skill are relative to the skill directory.
</skill_content>
```

每个 Agent 获得独立 SkillTool 实例，以便记录本 Agent 已加载的 Skill 并避免重复注入；这些实例共享同一 SkillRuntime，以便复用 Sandbox 同步结果。

### 7.6 Skill 目录提示词

Agent 初始系统提示词附加轻量目录：

```text
<available_skills>
  <skill>
    <name>...</name>
    <description>...</description>
  </skill>
</available_skills>

When a task matches a skill description, call load_skill before proceeding.
Resolve relative paths against the returned skill directory.
```

目录包含当前任务快照内的全部已启用 Skill；即使某个 ZIP 在创建快照时下载失败，也保留其元数据，使后续 `load_skill` 能通过 ToolEvent 明确暴露失败。没有 Skill 时不添加空目录，也不注册 SkillTool。

## 8. Agent 与 Flow 接入

### 8.1 PlannerReActFlow

- PlannerAgent 获得 Skill 目录，并且可用工具仅增加 SkillTool。
- ReActAgent 获得 Skill 目录，SkillTool 与现有 File、Shell、Browser、Search、Message、MCP、A2A 工具并列。
- Planner 当前强制 `tool_choice="none"`；有 Skill 时允许 Planner 在生成结构化计划前调用 `load_skill`，没有 Skill 时保持原行为。
- Planner 和 ReAct 使用不同 SkillTool 实例，因此需要使用同一 Skill 时各自加载到自己的 Memory。

### 8.2 TeamFlow

- TeamPlannerAgent、TaskWorker 和 TeamSynthesizerAgent 都获得相同的任务级 Skill 目录。
- Planner 与 Synthesizer 的工具集只包含 SkillTool。
- 每个 TaskWorker 在原 ToolPolicy 结果之外始终获得 SkillTool；SkillTool 只加载指令，不授予额外业务工具。
- Worker 是否能读取普通文件、调用 Browser、执行 Shell、MCP 或 A2A，仍由节点 capability 和现有 ToolPolicy 决定。
- Team Agent 包装层转发现有 ToolEvent，使 Skill 加载在事件流中可见。

### 8.3 自动与显式激活

自动激活完全由模型根据目录中的 `description` 判断，不新增后端匹配器。

显式激活由前端在输入框中插入普通文本 `$skill-name`。该文本随用户消息进入现有聊天协议；后端不增加专用消息字段或语法解析器，Agent 根据明确提及调用 `load_skill`。

### 8.4 多 Skill 组合

`BaseAgent` 已将单次模型响应截断为一个工具调用，并在工具返回后继续迭代。Agent 因此可以连续调用多个 `load_skill`，无需支持并行 Skill 加载。每个 Skill 的完整正文只进入实际调用它的 Agent Memory。

## 9. API 设计

接口沿用现有设置模块风格：

```text
GET  /api/app-config/skills
POST /api/app-config/skills
GET  /api/app-config/skills/{skill_id}
POST /api/app-config/skills/{skill_id}/enabled
POST /api/app-config/skills/{skill_id}/delete
```

### 9.1 列表

列表项包含：

```text
id
name
description
enabled
```

### 9.2 上传

`POST /skills` 使用 multipart 接收一个 ZIP。新 Skill 默认启用；同名 Skill 覆盖当前记录并保留原启停状态。

上传顺序：

1. 读取 ZIP 并完成 SkillParser 解析。
2. 查询同名 Skill，决定新建或覆盖。
3. 上传新的 OSS 对象。
4. 保存数据库当前指向。
5. 提交成功后尽力删除旧对象；保存失败则尽力删除新对象。

### 9.3 详情

详情接口在列表字段之外返回完整 `skill_md`，用于设置页预览。ZIP 本身不提供下载入口。

### 9.4 启停与删除

启停只修改 `enabled`。删除移除 Registry 当前记录并尽力删除当前 OSS 对象。运行中任务已经持有内存快照，不受影响。

## 10. 前端设计

### 10.1 设置页

在现有 `ManusSettings` 左侧增加 Skill 菜单，右侧提供：

- 上传 ZIP 按钮。
- Skill 名称、描述和启停状态列表。
- `SKILL.md` 详情预览。
- 启停开关。
- 删除操作。

列表操作沿用 MCP/A2A 的加载状态、乐观更新、失败回滚和 Toast 反馈模式。同名覆盖不增加额外版本 UI。

### 10.2 聊天输入框

输入 `$` 时，从 Skill 列表中过滤已启用项并显示名称与描述。选择后插入 `$skill-name`，不改变现有 ChatRequest 类型。

### 10.3 ToolEvent 展示

不新增 `SkillEvent`。`load_skill` 会自然产生：

```text
ToolEvent(tool_name="skill", function_name="load_skill", status="calling")
ToolEvent(tool_name="skill", function_name="load_skill", status="called")
```

`ToolContent` 联合类型增加 `SkillToolContent(name, skill_dir)`，`AgentTaskRunner` 在 `called` 阶段填充该摘要。前端增加 Skill Tool 的预览组件，展示名称、加载状态和 Sandbox 目录，不在时间线展开完整指令正文。完整正文仍可在设置页查看。

## 11. 资源与脚本使用

`load_skill` 同步的是完整 ZIP，而不是只同步 `SKILL.md`。Agent 根据返回的根目录和 Skill 指令：

- 具备 File 工具的 Agent 使用它读取 `references/` 中的文本。
- 具备 File 或 Shell 工具的 Agent 使用它访问 `assets/`。
- 具备 Shell 工具的 Agent 使用它执行 `scripts/`。

首期不新增资源或脚本专用工具，也不解释或执行 `allowed-tools` 为新的权限来源。Skill 只能使用调用 Agent 原本拥有的工具；在 Team 模式中，Worker 仍受 capability ToolPolicy 约束。

## 12. Memory 与上下文

- Skill 目录属于每个 Agent 的实例系统提示词，不修改全局常量。
- 完整 `SKILL.md` 作为 `load_skill` 的 Tool message 进入调用 Agent Memory。
- 当前 Memory 压缩只移除特定 Browser 结果和 reasoning 内容；Skill Tool message 必须继续保留。
- 每个 Agent 的已加载集合独立，避免一个 Agent 的加载错误地阻止另一个 Agent 获取指令。
- 同一 Agent 重复加载同一 Skill 时返回“已加载”结果，不再次注入完整正文。

## 13. 错误处理

| 场景 | 行为 |
|---|---|
| 找不到或无法解析 `SKILL.md` | 上传返回请求错误，不改 Registry |
| 缺少运行所需的 name/description | 解析失败，不改 Registry |
| OSS 上传失败 | 上传失败，不改数据库 |
| 数据库保存失败 | 上传失败，保留原 Skill，尽力删除新 OSS 对象 |
| Skill 不存在 | 管理接口返回 404 |
| Snapshot 下载 ZIP 失败 | 保留带错误的 Snapshot 项；调用时返回失败 ToolResult |
| `load_skill` 名称不在快照 | 返回失败 ToolResult，Flow 不直接崩溃 |
| Sandbox 上传或解压失败 | 沿用 BaseAgent 工具重试；最终以 ToolResult 失败告知 Agent |
| references/assets/scripts 使用失败 | 沿用现有 File/Shell 错误事件和结果 |
| 同一 Skill 被并发加载 | SkillRuntime 按 Skill ID 加锁，仅同步一次 |
| 管理期间任务已运行 | 当前任务继续使用内存快照，新任务读取新状态 |

## 14. 测试策略

### 14.1 后端单元测试

- SkillParser 能提取元数据、正文和根路径。
- SkillRegistry 支持新增、同名覆盖、列表、详情、启停和删除。
- 新建 Skill 默认启用，同名覆盖保留启停状态。
- Catalog 只包含 Snapshot 中可见的 Skill。
- SkillRuntime 能把 ZIP 同步到预期 Sandbox 目录。
- 同一任务的重复或并发加载只同步一次。
- 每个 Agent 的 SkillTool 独立去重，多 Agent 可分别取得正文。
- 多 Skill 可以连续加载。
- Snapshot 在 Registry 变化后保持不变。
- Snapshot 下载错误会在 `load_skill` 时形成失败结果。

### 14.2 Agent 集成测试

使用 Fake LLM 和 Fake Sandbox 验证：

- Agent 系统提示词包含 Skill 元数据，不包含未加载的完整正文。
- PlannerAgent 可先调用 SkillTool，再返回结构化 Plan。
- ReActAgent 可调用 SkillTool，再按指令继续调用现有工具。
- TeamPlanner、TaskWorker、TeamSynthesizer 获得目录并可加载 Skill。
- Skill Tool message 保留在 Memory 压缩结果中。
- Skill 调用继续产生现有 ToolEvent。

### 14.3 API 测试

- ZIP 上传、新增与同名覆盖。
- 列表、详情、启停和删除。
- 不存在资源的 404。
- 无法解析 Skill 的请求错误。
- 存储失败时 Registry 保持原状态。

### 14.4 前端验证

不为本功能额外引入新的前端测试框架。验证设置页的上传、列表、预览、启停、删除，以及聊天输入框 `$` 选择和 Skill ToolEvent 展示。

按照仓库 `AGENTS.md`，实施阶段可以先编写测试，但运行 `pytest`、前端检查、构建、启动服务、容器编排或访问本地服务验证前，必须先取得用户明确同意。

## 15. 验收标准

- 设置页能够上传包含 `SKILL.md` 的 Skill ZIP。
- 上传后能够查看名称、描述、启用状态和完整 `SKILL.md`。
- 能够启停、删除和同名覆盖 Skill。
- 聊天输入 `$` 能够选择已启用 Skill。
- Agent 初始上下文仅包含 Skill 名称和描述。
- Agent 能根据任务描述自动选择 Skill。
- Agent 能根据 `$skill-name` 显式加载 Skill。
- `load_skill` 后完整 `SKILL.md` 进入调用 Agent 的 Memory。
- Skill 加载过程通过现有 ToolEvent 展示。
- 完整 Skill 包只在首次加载时同步到当前会话 Sandbox。
- Agent 能按需读取 references/assets，并在具备 Shell 工具时执行 scripts。
- React 和 Team 模式均能使用 Skill。
- 同一任务能够组合使用多个 Skill。
- 管理操作不改变运行中任务的 Skill 快照。
- 系统不提供 Skill 版本历史、回滚、安全审批、签名或脚本扫描。
- 不修改 `.env` 或其他本地运行配置。

## 16. 主要风险与取舍

| 风险或取舍 | 首期决定 |
|---|---|
| ZIP 与脚本不做安全审核 | 按用户确认不实现审批、签名或扫描；继续依赖现有会话 Sandbox 隔离 |
| 不限制 ZIP 数量和大小 | 按用户确认不增加独立校验阶段 |
| Snapshot 预取全部启用 ZIP 增加任务创建开销 | 为保证运行中任务不受替换和删除影响，接受首期成本 |
| Planner 开放 SkillTool 可能增加一次模型工具轮次 | 这是让 Planner 真正使用 Skill 指令的必要成本 |
| Team Worker 仍受单一 capability 工具策略限制 | Skill 不扩大工具权限；复杂 Skill 由 Team Planner 拆成适当 capability 节点 |
| OSS 与 PostgreSQL 不能形成单一事务 | 以数据库当前指向为真相，失败对象和旧对象做尽力清理 |
| 前端缺少现成测试框架 | 首期不为 Skill 单独恢复整套测试基础设施 |

## 17. 参考资料

- Agent Skills Specification: https://agentskills.io/specification
- Agent Skills Client Implementation Guide: https://agentskills.io/client-implementation/adding-skills-support
- OpenAI Codex Build Skills: https://developers.openai.com/codex/skills
- OpenAI API Skills: https://developers.openai.com/api/docs/guides/tools-skills
- Anthropic Agent Skills: https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview
- GitHub Copilot Agent Skills: https://docs.github.com/en/copilot/how-tos/copilot-on-github/customize-copilot/customize-cloud-agent/add-skills
- Microsoft Agent Framework Skills: https://learn.microsoft.com/en-us/agent-framework/agents/skills
- LangChain Deep Agents Skills: https://docs.langchain.com/oss/python/deepagents/skills
- Gemini CLI Agent Skills Announcement: https://github.com/google-gemini/gemini-cli/discussions/17790

## 18. 最终决策

首期采用项目原生的 `SkillRegistry + SkillRuntime + SkillTool` 架构，实现从设置页 ZIP 管理到 Agent 渐进加载、Sandbox 资源使用和现有 ToolEvent 展示的完整垂直链路。实现严格复用当前基础设施，不引入新的 Agent 框架、Provider API、版本系统或高级安全治理能力。

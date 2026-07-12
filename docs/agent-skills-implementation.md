# WhiskerAgent Agent Skills 功能实现详解

本文档整理 `feat/agent-skills` 分支在当前工作区中的 Skill 功能实现，内容只以当前分支相对 `main` 的有效业务代码为依据。本文明确排除测试代码、历史设计文档、计划文档、锁文件和仓库说明文件，不把未运行的设计稿内容写成已经实现的行为。文档覆盖 Skill 的总体目标、架构分层、领域模型、ZIP 解析、全局 Registry、对象存储、任务快照、沙箱同步、`load_skill` 工具、单 Agent 接入、Team Agent 接入、事件投影、管理接口、前端设置页、输入框显式引用、工具预览、错误边界和当前最小实现没有覆盖的范围。

按 `git diff --numstat main -- api/app ui/src api/alembic` 统计，并排除测试、文档、Markdown、锁文件和设计素材，当前分支有效代码涉及 73 个文件，新增 3394 行、删除 461 行。这个数字包含同一分支上为 Team DAG、OSS 适配和通用运行链路带来的有效代码。若只按 Skill 垂直链路筛选，包括 Skill 管理、Skill 运行时、Agent 接入、事件摘要和前端交互在内，主要涉及 38 个有效代码文件，新增 2438 行、删除 117 行。其中后端涉及 28 个文件，新增 1781 行、删除 104 行；前端涉及 10 个文件，新增 657 行、删除 13 行。本文聚焦后一个口径。

## 一、这套实现要解决的是“按需加载能力说明和资源包”，而不是新增一套工具权限系统

当前 Skill 功能实现的是一条从设置页上传 Skill ZIP，到 Agent 在任务中按需调用 `load_skill`，再把完整 Skill 指令和资源目录引入当前 Agent 上下文的垂直链路。它没有把 Skill 做成新的 Agent 类型，也没有让 Skill 绕过现有工具权限。Skill 的本质是“带有标准入口文件 `SKILL.md` 的说明和资源包”，运行时真正执行动作的仍是已有 File、Shell、Browser、Search、MCP、A2A 等工具。

换句话说，Skill 在这套系统里更接近“可被 Agent 主动翻开的操作手册”，而不是“一个新的插件进程”。上传 Skill 只是把手册和相关资源登记到系统里；真正做事时，Agent 先看到手册目录，如果它判断当前任务需要某本手册，就通过 `load_skill` 把完整内容加载进自己的上下文。加载之后，Agent 仍然只能用它原本有权使用的工具去读文件、跑脚本或访问外部能力。

这个分支选择了“全局管理、任务快照、渐进加载、沙箱同步”的结构。设置页上传 ZIP 后，后端解析其中的 `SKILL.md`，把名称、描述、完整正文、根目录和对象存储 key 写入数据库。新建任务时，`AgentService` 从全局 Registry 取出所有已启用 Skill，并固定成当前任务的 `SkillSnapshot`。Agent 初始系统提示词只看到轻量目录，也就是 Skill 名称和描述；只有模型调用 `load_skill(name)` 后，完整 `SKILL.md` 才进入当前 Agent 的 Memory，同时完整 ZIP 被同步并解压到当前会话沙箱。

这里最容易误解的是“全局管理”和“任务快照”的关系。设置页里的 Skill 是全局配置，影响后续新任务；但一个任务一旦启动，它看到的 Skill 集合就固定了。这样可以避免一个长任务运行到一半时，管理员在设置页覆盖了 ZIP，导致前后两个 Worker 读到不同版本的 Skill。代码没有引入显式版本号，但用 Snapshot 达到了运行时一致性的效果。

这里的“按需”体现在三个层面。第一层是任务创建时只读取已启用 Skill，禁用项不会进入新任务快照。第二层是系统提示词只放元数据，不把完整 Skill 正文提前塞进上下文。第三层是沙箱资源只在首次加载某个 Skill 时同步，多个 Agent 或多个 Worker 共享同一个 `SkillRuntime`，因此同一任务里同一个 Skill 只会物理上传和解压一次。

这种按需策略主要是在控制上下文和运行成本。完整 `SKILL.md` 可能很长，ZIP 里还可能带有参考资料和脚本；如果每次任务一开始就把所有 Skill 全部塞进系统提示词，会浪费大量 token，也会让模型在不相关的指令之间摇摆。当前实现把“发现”和“加载”拆开，让模型先用名称和描述判断相关性，再加载真正需要的内容。

整体数据流可以概括为下面这条链路。管理链路和运行链路通过 Registry 里的持久化状态连接，但运行中的任务使用的是创建时固定的快照，不会随着设置页后续修改而变化。

```text
设置页上传 Skill ZIP
        │
        ▼
POST /api/app-config/skills
        │
        ▼
SkillService
        │
        ▼
SkillRegistry
        │
        ├── SkillParser 读取 ZIP 中首个 SKILL.md
        ├── PostgreSQL skills 表保存元数据和完整 SKILL.md
        └── OSS 保存原始 ZIP

新会话任务启动
        │
        ▼
AgentService._create_task()
        │
        ▼
SkillRegistry.create_enabled_snapshot()
        │
        ▼
AgentTaskRunner 持有 SkillRuntime
        │
        ├── PlannerReActFlow 注入 Skill 目录和 SkillTool
        └── TeamFlow 注入 Skill 目录和 SkillTool
                │
                ▼
       Agent 根据目录调用 load_skill(name)
                │
                ▼
       SkillRuntime 同步 ZIP 到 Sandbox
                │
                ▼
       SkillTool 返回完整 SKILL.md + skill_dir
                │
                ▼
       BaseAgent 把工具结果写入当前 Agent Memory
                │
                ▼
       Runner 只向前端事件摘要暴露 name + skill_dir
```

## 二、架构职责被拆成 Parser、Registry、Runtime、Tool 和 UI 管理层

`SkillParser` 只负责从 ZIP 中找到第一个 `SKILL.md`，读取 YAML frontmatter 中的 `name` 和 `description`，并记录该 `SKILL.md` 所在目录作为 `root_path`。它不校验 `scripts/`、`references/` 或 `assets/` 是否存在，也不解释 `allowed-tools` 之类的扩展字段。这个职责边界保证上传阶段只做运行必需信息提取。

Parser 的输入是一段 ZIP bytes，输出不是数据库对象，而是一个临时的 `ParsedSkill`。这意味着 Parser 不知道 Skill 是否同名、是否启用、应该保存到哪个对象存储 key，也不关心前端怎么展示。它只回答一个问题：这个 ZIP 是否至少包含一个系统能识别的 Skill 入口，以及这个入口的基本元数据是什么。

`SkillRegistry` 是系统全局 Skill 的唯一管理入口。它协调数据库仓库、对象存储和解析器，提供列表、详情、上传覆盖、启停、删除和创建任务快照。上传同名 Skill 时，它复用原 id 并保留原启停状态，同时替换 `skill_md`、`description`、`root_path` 和 ZIP 对象。这个逻辑位于 Registry，而不是分散在接口层或前端。

Registry 处在“配置管理”和“任务运行”之间。设置页调用它完成 CRUD；AgentService 也调用它创建运行快照。把这两个入口放到同一个 Registry 后，上传覆盖、启停、删除和快照生成都遵守同一套规则，不会出现设置页看到一种状态、任务启动又用另一种状态的情况。

`SkillRuntime` 是任务级运行时对象。每个 `AgentTaskRunner` 只创建一个 `SkillRuntime`，它持有本任务的 `SkillSnapshot`、Sandbox、同步缓存和按 Skill id 划分的异步锁。它负责生成轻量目录提示词，也负责把 ZIP 上传到沙箱并解压到稳定目录。Runtime 不持久化新状态，也不参与设置页管理。

Runtime 的生命周期和任务绑定，而不是和请求、Agent 或 Worker 绑定。React 模式里 Planner 和 ReAct 可以共享它；Team 模式里 Planner、多个 Worker 和 Synthesizer 也共享它。共享 Runtime 的意义不是共享 Memory，而是共享“这个 Skill 的资源包是否已经同步到沙箱”这个物理状态。

`SkillTool` 是 Agent 能看到的工具集，当前只有 `load_skill(name)` 一个函数。它调用 `SkillRuntime.load()`，把完整 Skill 正文和沙箱目录包装成工具结果返回给 BaseAgent。每个 Agent 拿到独立的 `SkillTool` 实例，用于记录“这个 Agent 已经加载过哪些 Skill”；这些工具实例共享同一个 Runtime，用于复用沙箱同步结果。

这里要区分两个缓存。`SkillRuntime` 缓存的是沙箱目录，避免重复上传和解压 ZIP；`SkillTool` 缓存的是当前 Agent 是否已经把某个 Skill 正文注入过 Memory，避免同一个 Agent 重复吃进同一份长文本。前者跨 Agent 共享，后者只属于单个 Agent。

前端管理层由 `SkillSettings`、`skillApi`、输入框 `$` 补全和工具预览组成。设置页负责上传、查看、启停和删除；输入框只把 `$skill-name` 插入普通文本，不增加专用聊天字段；工具时间线只展示加载摘要，不展示完整 `SKILL.md`。

## 三、领域模型把“全局 Skill”和“任务快照”分成两种对象

Skill 相关领域模型集中在 `api/app/domain/models/skill.py`。`Skill` 是全局持久化对象，包含 id、名称、描述、完整 `SKILL.md`、ZIP 内根路径、对象存储 key、启停状态和时间戳。`ParsedSkill` 是解析器的中间结果，只保存从 ZIP 中提取出来的运行必需信息。`SkillSnapshot` 是任务创建时固定下来的不可变对象，除了元数据和完整 `SKILL.md` 外，还可以保存已经下载好的 ZIP bytes 或下载失败原因。

这三个模型分别对应三个不同时间点。上传时先得到 `ParsedSkill`；保存成功后形成全局 `Skill`；任务启动时再从启用的全局 Skill 生成 `SkillSnapshot`。如果把这三层混成一个通用字典，代码很容易在上传阶段误用运行时字段，或者在运行时误改全局配置。当前实现用三个模型把这些边界显式分开。

### 领域模型源码走读

下面是当前实现中的三个模型。`SkillSnapshot` 使用 `ConfigDict(frozen=True)`，说明快照创建后不应再被业务逻辑修改。任务运行期间后续上传、禁用或删除全局 Skill，都不会改变已经创建的 Snapshot 对象。

```python
class Skill(BaseModel):
    """系统全局 Skill。"""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: str
    skill_md: str
    root_path: str = ""
    bundle_key: str = ""
    enabled: bool = True
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class ParsedSkill(BaseModel):
    """从 ZIP 中提取出的运行必需信息。"""

    name: str
    description: str
    skill_md: str
    root_path: str


class SkillSnapshot(BaseModel):
    """任务创建时固定的 Skill 快照。"""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    description: str
    skill_md: str
    root_path: str
    bundle_bytes: bytes | None = None
    bundle_load_error: str | None = None
```

`Skill.root_path` 不是用户上传时额外传入的字段，而是 `SkillParser` 根据 `SKILL.md` 在 ZIP 中的位置推导出来的。例如 ZIP 中入口是 `demo/SKILL.md`，则 `root_path` 是 `demo`；如果入口在根目录，则 `root_path` 是空字符串。Runtime 后续会用这个字段生成 Agent 可访问的 Skill 根目录。

这个字段解决的是资源相对路径问题。Skill 文档里通常会写“读取 `references/foo.md`”或“运行 `scripts/bar.py`”。如果 `SKILL.md` 在 ZIP 的 `demo/` 子目录中，那么这些相对路径应该以 `demo/` 为根，而不是以 ZIP 解压后的最外层目录为根。`root_path` 把这个根目录保存下来，避免 Agent 拿到一个错误的资源路径。

`SkillSnapshot.bundle_load_error` 是一个有意保留的失败通道。如果创建快照时 OSS 下载失败，系统仍把该 Skill 的名称、描述和 `skill_md` 放入快照，并记录失败原因。这样 Agent 后续调用 `load_skill` 时能够得到明确失败，而不是让 Skill 在目录中静默消失。

这种处理方式让失败变得可观察。目录中仍然可能出现这个 Skill，模型调用时会收到“ZIP 不可用”或对象存储错误，而不是完全不知道为什么某个本应启用的 Skill 不可用。它没有兜底伪造资源，也没有在快照阶段静默过滤失败项。

## 四、持久化层使用 PostgreSQL 保存元数据，使用 OSS 保存完整 ZIP

当前代码新增 `skills` 表，并通过 `SkillModel`、`DBSkillRepository` 和 `SkillBundleStorage` 协议把元数据与 ZIP 内容分开。数据库保存完整 `SKILL.md`，因此设置页详情接口可以直接预览正文；OSS 保存原始 ZIP，供 Runtime 在任务中同步完整资源包到沙箱。

数据库和 OSS 的分工很明确。数据库负责查询和展示：列表、详情、启停、同名覆盖都依赖数据库字段。OSS 负责运行时资源：只有在任务需要把 Skill 包放进沙箱时，才需要下载 ZIP。这样列表接口不需要读 OSS，运行时也不需要根据数据库重新拼 ZIP。

没有复用普通附件文件模型是一个重要边界。Skill ZIP 是系统配置资产，不是某个会话上传的业务附件，也不应该出现在会话文件列表里。对象存储 key 使用 `skills/{skill_id}/{uuid}.zip` 前缀，和普通文件路径隔离。

这也避免了权限和生命周期混乱。普通文件会跟会话、附件预览、文件列表和下载行为绑定；Skill ZIP 则跟系统配置和后续任务可见性绑定。如果复用 File 模型，删除会话文件、同步附件、展示会话文件列表时都可能误触 Skill 包。

### 数据库模型源码走读

`SkillModel` 与领域模型字段基本一一对应，并在 `name` 上建立唯一约束。唯一约束配合 Registry 的同名覆盖逻辑，使同名 Skill 在产品语义上表现为“覆盖当前版本”，而不是创建多个并列记录。

```python
class SkillModel(Base):
    """Skill ORM 模型。"""

    __tablename__ = "skills"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_skills_id"),
        UniqueConstraint("name", name="uq_skills_name"),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    skill_md: Mapped[str] = mapped_column(Text, nullable=False)
    root_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    bundle_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        onupdate=datetime.now,
        server_default=text("CURRENT_TIMESTAMP(0)"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(0)"),
    )
```

仓库层只提供 Skill 所需的最小查询和写入方法。`get_enabled()` 单独存在，是为了任务创建时只读取启用项，而不是让应用服务拿全量列表再过滤。`get_by_name()` 则服务于同名覆盖。

这层仓库没有暴露分页、模糊搜索、版本查询或批量操作，因为当前功能没有用到。它也没有返回 ORM 对象，而是转换成领域模型 `Skill`，让上层业务不用感知 SQLAlchemy 的会话状态。

```python
class SkillRepository(Protocol):
    """Skill 持久化协议。"""

    async def save(self, skill: Skill) -> None: ...

    async def get_all(self) -> list[Skill]: ...

    async def get_enabled(self) -> list[Skill]: ...

    async def get_by_id(self, skill_id: str) -> Skill | None: ...

    async def get_by_name(self, name: str) -> Skill | None: ...

    async def delete_by_id(self, skill_id: str) -> None: ...
```

对象存储协议也保持极小，只提供上传、下载和删除。OSS 实现把同步的阻塞 SDK 调用包在 `run_in_threadpool` 中，避免直接阻塞 FastAPI 的事件循环。

这里没有把 OSS key 暴露给前端，也没有提供 ZIP 下载接口。对象存储 key 是后端内部运行时细节，前端只需要知道 Skill 的名称、描述、启停状态和完整 `SKILL.md` 预览。这样可以减少无关接口，也避免用户拿到可直接访问对象存储的内部路径。

```python
class SkillBundleStorage(Protocol):
    """Skill ZIP 对象存储协议。"""

    async def upload_bundle(self, skill_id: str, bundle: bytes) -> str: ...

    async def download_bundle(self, key: str) -> bytes: ...

    async def delete_bundle(self, key: str) -> None: ...


class OSSSkillBundleStorage(SkillBundleStorage):
    """在 OSS 中保存当前 Skill ZIP。"""

    def __init__(self, oss: OSS) -> None:
        self._oss = oss

    async def upload_bundle(self, skill_id: str, bundle: bytes) -> str:
        key = f"skills/{skill_id}/{uuid.uuid4()}.zip"
        await run_in_threadpool(self._oss.bucket.put_object, key, bundle)
        return key
```

## 五、SkillParser 只解析 ZIP 中首个 SKILL.md 的运行必需信息

上传入口接收的是 ZIP 文件。`SkillParser` 用 Python 标准库 `ZipFile` 读取压缩包，遍历 `archive.namelist()`，找到第一个文件名正好为 `SKILL.md` 的条目。这里不要求入口文件必须在根目录，因此 `demo/SKILL.md`、`skills/foo/SKILL.md` 都可以被识别。

“首个 `SKILL.md`”是当前代码的实际规则。也就是说，如果一个 ZIP 里有多个目录都包含 `SKILL.md`，解析器不会把它们当作多个 Skill，也不会报错要求用户拆包，而是选择遍历顺序中的第一个入口。这个实现足够支撑单 Skill 包上传，但不承担批量导入多个 Skill 的语义。

解析器随后要求 `SKILL.md` 必须以 YAML frontmatter 开头，并且 frontmatter 必须是对象，且包含非空字符串 `name` 与 `description`。其他字段不会写入领域模型，也不会参与权限判断。正文则原样保存为 `skill_md`，后续 `load_skill` 返回给 Agent 的也是这份完整文本。

这套校验只保证运行链路所需的最低条件。`name` 是工具调用时的查找键，`description` 是模型判断是否需要加载该 Skill 的目录信息，完整正文是加载后注入上下文的内容。除此之外的字段即便存在，也只是正文的一部分，不会改变后端行为。

### 解析器源码走读

下面这段代码体现了两个关键选择。第一，入口定位只看文件名是否是 `SKILL.md`，因此支持嵌套目录；第二，`root_path` 来自入口文件父目录，使 Runtime 后续能够把相对路径解析到正确的沙箱目录。

```python
class SkillParser:
    """从 Skill ZIP 的首个 SKILL.md 提取元数据。"""

    _frontmatter = re.compile(
        r"\A---[ \t]*\r?\n(?P<yaml>.*?)(?:\r?\n)---[ \t]*(?:\r?\n|\Z)",
        re.DOTALL,
    )

    def parse(self, bundle: bytes) -> ParsedSkill:
        try:
            with ZipFile(BytesIO(bundle)) as archive:
                skill_path = next(
                    (
                        name
                        for name in archive.namelist()
                        if not name.endswith("/")
                        and PurePosixPath(name).name == "SKILL.md"
                    ),
                    None,
                )
                if skill_path is None:
                    raise SkillParseError("ZIP 中未找到 SKILL.md")
                skill_md = archive.read(skill_path).decode("utf-8")
        except (BadZipFile, UnicodeDecodeError) as exc:
            raise SkillParseError("无法读取 Skill ZIP") from exc

        match = self._frontmatter.match(skill_md)
        if match is None:
            raise SkillParseError("SKILL.md 缺少 YAML frontmatter")
```

解析错误使用 `SkillParseError` 表示，应用服务会把它转换成 `BadRequestError`。这意味着“ZIP 格式不对、没有 `SKILL.md`、frontmatter 不合法、缺少 name 或 description”属于用户输入错误，而不是任务运行错误。

这类错误在上传阶段就应该暴露给设置页操作者，而不是等到 Agent 执行任务时才失败。当前实现没有尝试自动修复 YAML，也没有默认生成 name 或 description，因为这两个字段会直接影响路由和用户可见列表，自动补全反而容易制造不可预测行为。

```python
        try:
            metadata = yaml.safe_load(match.group("yaml")) or {}
        except yaml.YAMLError as exc:
            raise SkillParseError("SKILL.md YAML frontmatter 无法解析") from exc
        if not isinstance(metadata, dict):
            raise SkillParseError("SKILL.md YAML frontmatter 必须是对象")
        name = metadata.get("name")
        description = metadata.get("description")
        if not isinstance(name, str) or not name.strip():
            raise SkillParseError("SKILL.md 缺少 name")
        if not isinstance(description, str) or not description.strip():
            raise SkillParseError("SKILL.md 缺少 description")

        parent = PurePosixPath(skill_path).parent.as_posix()
        return ParsedSkill(
            name=name.strip(),
            description=description.strip(),
            skill_md=skill_md,
            root_path="" if parent == "." else parent,
        )
```

## 六、SkillRegistry 是全局 Skill 生命周期的唯一业务入口

`SkillRegistry` 持有三个依赖：UoW 工厂、Skill ZIP 对象存储和解析器。它不参与 Agent 推理，也不操作前端展示结构。所有系统全局 Skill 的列表、详情、上传覆盖、启停、删除和快照创建都经过这个类。

Registry 的价值在于把“一个 Skill 的两份存储”当成一个业务整体处理。数据库里有一条记录，OSS 里有一个 ZIP 对象；上传、覆盖、删除都必须让这两边尽量保持一致。如果这些逻辑分散在路由、服务和 Runner 里，很容易出现数据库指向已经删除的 ZIP，或者 ZIP 上传成功但数据库没保存的悬挂对象。

上传逻辑采用“先解析、再查同名、再上传新 ZIP、最后写库”的顺序。如果是新 Skill，默认 `enabled=True`；如果是同名覆盖，则复用原 id、保留 `enabled` 和 `created_at`。数据库写入成功后，再尽力删除旧 ZIP；如果数据库写入失败，则尽力删除新 ZIP 并抛出异常。这保证失败时不会悄悄把数据库切到一个对象存储中不存在的新包。

这个顺序也解释了为什么覆盖时会保留启停状态。用户禁用了一个 Skill，通常表达的是“这个名字的能力暂时不要对新任务生效”；如果后续上传同名 ZIP 是为了修正文档或资源，系统不应该因为覆盖内容而自动重新启用它。当前代码把“内容更新”和“启停状态”拆开处理。

### 上传覆盖源码走读

下面是 `upsert_bundle()` 的核心。`current` 只按名称查找，因此名称是覆盖语义的唯一依据。覆盖时 `bundle_key` 先保留旧值，等新 ZIP 上传成功后才替换为 `new_key`。

```python
async def upsert_bundle(self, bundle: bytes) -> Skill:
    parsed = self._parser.parse(bundle)
    async with self._uow_factory() as uow:
        current = await uow.skill.get_by_name(parsed.name)

    now = datetime.now()
    if current is None:
        skill = Skill(
            name=parsed.name,
            description=parsed.description,
            skill_md=parsed.skill_md,
            root_path=parsed.root_path,
            updated_at=now,
        )
        old_key = ""
    else:
        skill = Skill(
            id=current.id,
            name=parsed.name,
            description=parsed.description,
            skill_md=parsed.skill_md,
            root_path=parsed.root_path,
            bundle_key=current.bundle_key,
            enabled=current.enabled,
            created_at=current.created_at,
            updated_at=now,
        )
        old_key = current.bundle_key

    new_key = await self._bundle_storage.upload_bundle(skill.id, bundle)
    skill.bundle_key = new_key
    try:
        async with self._uow_factory() as uow:
            await uow.skill.save(skill)
            await uow.commit()
    except Exception:
        await self._delete_bundle_best_effort(new_key)
        raise

    if old_key and old_key != new_key:
        await self._delete_bundle_best_effort(old_key)
    return skill
```

启停只更新数据库，不删除对象存储。禁用后的 Skill 不会进入新任务快照，但历史任务如果已经持有 Snapshot，仍然可以按自己的快照继续运行。删除则先删数据库，再尽力删除 ZIP；如果对象存储删除失败，只记录 warning，不影响接口成功返回。

这里的删除是“从全局配置中删除”，不是“强制终止所有正在使用它的任务”。运行中的 Runner 已经持有 Snapshot 和可能已经下载好的 ZIP bytes，删除全局记录不会回溯修改这些 Runner。这种行为和任务快照的设计保持一致。

```python
async def set_enabled(
    self,
    skill_id: str,
    enabled: bool,
) -> Skill | None:
    async with self._uow_factory() as uow:
        skill = await uow.skill.get_by_id(skill_id)
        if skill is None:
            return None
        skill.enabled = enabled
        skill.updated_at = datetime.now()
        await uow.skill.save(skill)
        await uow.commit()
        return skill


async def delete_skill(self, skill_id: str) -> bool:
    async with self._uow_factory() as uow:
        skill = await uow.skill.get_by_id(skill_id)
        if skill is None:
            return False
        await uow.skill.delete_by_id(skill_id)
        await uow.commit()

    await self._delete_bundle_best_effort(skill.bundle_key)
    return True
```

## 七、任务快照把运行中的 Agent 和设置页后续修改隔离开

`AgentService._create_task()` 创建新的 `AgentTaskRunner` 前，会调用 `SkillRegistry.create_enabled_snapshot()`。这个方法只读取启用项，并尝试立即把每个启用 Skill 的 ZIP 从对象存储下载到内存快照里。快照作为参数传给 Runner，Runner 再用它创建 `SkillRuntime`。

把下载动作放在任务创建阶段，而不是 `load_skill` 时再查数据库，是为了让任务看到一个稳定的世界。`load_skill` 不需要再访问全局 Registry，也不需要担心设置页刚刚把这条记录删除。它只面对当前任务自己的 Snapshot 集合。

这样做的核心意义是任务级一致性。一次任务开始时可见哪些 Skill、每个 Skill 的 `SKILL.md` 是什么、ZIP 内容是什么，都在创建 Runner 时确定。设置页后续覆盖同名 Skill、禁用 Skill 或删除 Skill，不会修改已在运行的 Runner。

快照还简化了并发 Team 模式。多个 Worker 可能同时启动，如果它们每次加载 Skill 都去读数据库和 OSS，就会在任务内形成竞态：某个 Worker 读到旧包，另一个 Worker 读到新包。当前代码让所有 Worker 共享同一个 Runtime 和同一批 Snapshot，避免了这种分裂。

### 快照创建源码走读

`create_enabled_snapshot()` 先在数据库事务内读取启用项，然后在事务外逐个下载 ZIP。下载失败不会阻止快照创建，而是写入 `bundle_load_error`。运行时真正调用 `load_skill` 时，再把这个错误转换成工具调用失败。

```python
async def create_enabled_snapshot(self) -> tuple[SkillSnapshot, ...]:
    async with self._uow_factory() as uow:
        skills = await uow.skill.get_enabled()

    snapshots: list[SkillSnapshot] = []
    for skill in skills:
        try:
            bundle = await self._bundle_storage.download_bundle(
                skill.bundle_key
            )
            load_error = None
        except Exception as exc:
            bundle = None
            load_error = str(exc)
        snapshots.append(
            SkillSnapshot(
                id=skill.id,
                name=skill.name,
                description=skill.description,
                skill_md=skill.skill_md,
                root_path=skill.root_path,
                bundle_bytes=bundle,
                bundle_load_error=load_error,
            )
        )
    return tuple(snapshots)
```

`AgentService` 把快照创建放在沙箱和浏览器准备之后、Runner 创建之前。Runner 一旦创建，后续 Flow、Agent 和 Tool 都共享这一个 Runtime。

如果一个会话已经有正在运行的 Task，`AgentService` 不会为追加读取事件重新创建 Runner，因此也不会重新创建 Skill 快照。只有创建新的任务 Runner 时，才会读取当前全局启用 Skill。

```python
# 5.固定本任务可见的 Skill 快照
skill_snapshots = await self._skill_registry.create_enabled_snapshot()

# 6.创建AgentTaskRunner
task_runner = AgentTaskRunner(
    uow_factory=self._uow_factory,
    llm=self._llm,
    agent_config=self._agent_config,
    mcp_config=self._mcp_config,
    a2a_config=self._a2a_config,
    session_id=session.id,
    file_storage=self._file_storage,
    json_parser=self._json_parser,
    browser=browser,
    search_engine=self._search_engine,
    sandbox=sandbox,
    skill_snapshots=skill_snapshots,
)
```

## 八、SkillRuntime 生成轻量目录，并在首次加载时同步完整 ZIP 到沙箱

`SkillRuntime` 是运行时最关键的类。它接收 Snapshot 和 Sandbox，内部按 Skill 名称建立快照索引，按 Skill id 建立 `asyncio.Lock`，并用 `_synced_dirs` 记录已经同步过的沙箱目录。目录提示词由 `catalog_prompt` 生成，只有名称和描述，不包含完整 `SKILL.md`。

Runtime 一边面向模型上下文，一边面向沙箱文件系统。面向模型时，它提供轻量目录；面向沙箱时，它负责把 ZIP 变成一个实际目录。它不关心哪个 Agent 调用，也不关心调用发生在 React 还是 Team，只要名称在当前 Snapshot 中，它就能返回同一套加载结果。

当 Agent 调用 `load_skill(name)` 时，Runtime 先按名称查找当前任务快照；如果不存在，抛出 `SkillNotFoundError`。如果存在，则调用 `_ensure_synced()`。同步过程会把 ZIP 上传到 `/home/ubuntu/.whisker-manus/skills/{skill_id}/bundle.zip`，再在同一 base 目录下解压到 `content/`，最后根据 `root_path` 返回真正的 Skill 根目录。

这里返回的是“Skill 根目录”，不是 ZIP 解压后的固定 `content` 目录。这个区别对嵌套包很重要：如果 `SKILL.md` 在 `demo/SKILL.md`，Agent 应该把相对路径解析到 `content/demo/`；如果 `SKILL.md` 在根目录，才解析到 `content/`。Runtime 把这个细节隐藏掉，Tool 只需要返回一个最终目录。

### 目录提示词源码走读

`catalog_prompt` 对 name 和 description 做 `html.escape()`，再包进 `<available_skills>`。提示词要求匹配描述或用户显式提到 `$<skill-name>` 时调用 `load_skill`，同时要求相对路径以返回的 skill directory 为基准。

```python
@property
def catalog_prompt(self) -> str:
    if not self._snapshots:
        return ""
    entries = "\n".join(
        "  <skill>\n"
        f"    <name>{html.escape(snapshot.name)}</name>\n"
        "    <description>"
        f"{html.escape(snapshot.description)}"
        "</description>\n"
        "  </skill>"
        for snapshot in self._snapshots.values()
    )
    return (
        "<available_skills>\n"
        f"{entries}\n"
        "</available_skills>\n\n"
        "When a task matches a skill description, call load_skill "
        "before proceeding.\n"
        "When the user explicitly mentions $<skill-name>, call that "
        "skill.\n"
        "Resolve relative paths against the returned skill directory."
    )
```

这里没有空目录占位。没有启用 Skill 时，`catalog_prompt` 返回空字符串，Flow 不会注册 `SkillTool`，Planner 的 `tool_choice="none"` 也不会被打开。

因此“没有 Skill”不是一个特殊运行模式，而是自然退化为原有 Agent 行为。系统提示词没有额外段落，工具列表没有 `load_skill`，模型也不会看到不存在的能力入口。

### 沙箱同步源码走读

同步路径使用 Skill id 而不是 Skill name，避免名称中的特殊字符进入命令路径。命令只引用后端构造的固定路径，并使用 `python3 -m zipfile -e` 解压。解压前会 `rm -rf content_dir`，确保同一个 Skill id 重新同步时不会留下旧内容。

使用 Skill id 还有另一个好处：同名覆盖仍然复用 id，因此同一个任务里的路径语义稳定；不同 Skill 即使名称里包含空格、斜杠或 shell 敏感字符，也不会影响命令拼接。当前命令里可变部分都来自后端生成的 id 和目录常量，而不是直接来自用户上传的 name。

```python
async def _ensure_synced(self, snapshot: SkillSnapshot) -> str:
    synced = self._synced_dirs.get(snapshot.id)
    if synced:
        return synced

    async with self._locks[snapshot.id]:
        synced = self._synced_dirs.get(snapshot.id)
        if synced:
            return synced
        if snapshot.bundle_load_error:
            raise SkillLoadError(snapshot.bundle_load_error)
        if snapshot.bundle_bytes is None:
            raise SkillLoadError(f"Skill ZIP 不可用: {snapshot.name}")

        base_dir = f"/home/ubuntu/.whisker-manus/skills/{snapshot.id}"
        bundle_path = f"{base_dir}/bundle.zip"
        content_dir = f"{base_dir}/content"
        skill_dir = (
            f"{content_dir}/{snapshot.root_path}"
            if snapshot.root_path
            else content_dir
        )
        session_id = f"skill-{snapshot.id}"
```

同一任务里多个 Agent 并发加载同一个 Skill 时，外层 `_synced_dirs` 和内层 lock 会共同保证只执行一次上传和解压。其他调用拿到已经缓存的 `skill_dir` 后直接返回。

这个锁主要服务 Team 模式。多个 ready Worker 可能同时判断自己需要同一个 Skill，如果没有锁，它们会同时上传同一个 ZIP、同时删除并解压同一个目录，轻则浪费资源，重则互相覆盖。当前实现用每个 Skill id 一个锁，把物理同步收敛为一次。

```python
        try:
            upload_result = await self._sandbox.upload_file(
                file_data=BytesIO(snapshot.bundle_bytes),
                filepath=bundle_path,
                filename="bundle.zip",
            )
            self._require_success(upload_result, "上传")

            extract_result = await self._sandbox.exec_command(
                session_id=session_id,
                exec_dir=base_dir,
                command=(
                    f"rm -rf {content_dir} && "
                    f"python3 -m zipfile -e {bundle_path} {content_dir}"
                ),
            )
            self._require_success(extract_result, "解压")
            if self._result_value(extract_result, "status") == "running":
                extract_result = await self._sandbox.wait_process(
                    session_id,
                    seconds=60,
                )
                self._require_success(extract_result, "解压")
            if self._result_value(extract_result, "returncode") != 0:
                raise SkillLoadError(f"Skill ZIP 解压失败: {snapshot.name}")
        except SkillLoadError:
            raise
        except Exception as exc:
            raise SkillLoadError(
                f"Skill 同步失败[{snapshot.name}]: {exc}"
            ) from exc

        self._synced_dirs[snapshot.id] = skill_dir
        return skill_dir
```

## 九、SkillTool 只负责把完整指令加载到当前 Agent，不授予额外工具

`SkillTool` 是一个标准 `BaseTool` 子类，工具箱名称是 `skill`，函数名是 `load_skill`。它不直接读取 `references/`，也不执行 `scripts/`。它只返回两类信息：完整 Skill 指令包装文本，以及该 Skill 在沙箱中的根目录。之后 Agent 要读取引用文件或执行脚本，仍必须使用当前 Agent 已经拥有且被授权的 File 或 Shell 工具。

因此 SkillTool 的权限很窄。它能做的事只有“把说明书放到模型上下文里”和“确保说明书所在的资源目录存在”。它不能代替 FileTool 读取任意文件，也不能代替 ShellTool 运行脚本。这个边界保证了 Team Worker 的 capability 策略仍然有效。

每个 `SkillTool` 实例内部有 `_loaded` 字典，记录本 Agent 已经加载过的 Skill。重复加载同名 Skill 时，它返回成功结果，但 `content` 为 `None`，避免把同一份完整 `SKILL.md` 多次注入同一个 Agent 的 Memory。不同 Agent 拿到不同 `SkillTool` 实例，所以 Planner 加载过的 Skill 不会自动进入 ReAct Agent 的 Memory；Worker 之间也不会共享 Memory。

这种“每个 Agent 自己加载”的行为看起来会多一次工具调用，但它避免了上下文串味。Planner 加载 Skill 是为了规划，ReAct 加载 Skill 是为了执行，Synthesizer 加载 Skill 是为了汇总口径；它们看到的历史消息和任务目标不同，不应该共享一份已经注入过的 Memory。

### SkillTool 源码走读

下面是当前 `load_skill` 的完整行为。不存在的 Skill 被转换成失败的 `ToolResult`，让 Agent 在正常工具调用循环中看到错误；Runtime 同步异常没有在 `SkillTool` 里兜底吞掉，会交给 BaseAgent 的工具重试逻辑处理。

```python
class SkillTool(BaseTool):
    """把完整 Skill 指令加载到当前 Agent。"""

    name = "skill"

    def __init__(self, runtime: SkillRuntime) -> None:
        super().__init__()
        self._runtime = runtime
        self._loaded: dict[str, str] = {}

    @tool(
        name="load_skill",
        description=(
            "加载一个已启用 Skill 的完整指令，并把完整 Skill 包同步到"
            "当前任务沙箱。"
        ),
        parameters={
            "name": {
                "type": "string",
                "description": "available_skills 目录中的 Skill 名称",
            }
        },
        required=["name"],
    )
    async def load_skill(self, name: str) -> ToolResult:
        if name in self._loaded:
            return ToolResult(
                success=True,
                message="Skill 已在当前 Agent 中加载",
                data={
                    "name": name,
                    "skill_dir": self._loaded[name],
                    "content": None,
                    "already_loaded": True,
                },
            )
```

首次加载成功时，工具结果中的 `content` 使用 `<skill_content>` 包住完整 `SKILL.md`，并在末尾追加沙箱目录和相对路径说明。BaseAgent 会把整个 `ToolResult` 作为工具消息写入 Memory，因此这段 `content` 才是真正进入模型上下文的完整 Skill 指令。

`already_loaded` 字段不是给前端展示用的主要字段，而是告诉模型这次重复调用没有重新返回正文。模型如果再次调用同名 Skill，会看到已经加载过，可以继续使用 Memory 里的先前内容和返回的目录。

```python
        try:
            loaded = await self._runtime.load(name)
        except SkillNotFoundError as exc:
            return ToolResult(success=False, message=str(exc))

        content = (
            f'<skill_content name="{html.escape(loaded.name, quote=True)}">\n'
            f"{loaded.skill_md}\n\n"
            f"Skill directory: {loaded.skill_dir}\n"
            "Relative paths in this skill are relative to the skill "
            "directory.\n"
            "</skill_content>"
        )
        self._loaded[name] = loaded.skill_dir
        return ToolResult(
            success=True,
            data={
                "name": loaded.name,
                "skill_dir": loaded.skill_dir,
                "content": content,
                "already_loaded": False,
            },
        )
```

## 十、BaseAgent 负责把轻量目录放进系统提示词，并把工具结果写入 Memory

Skill 功能没有给 Agent 写一条独立推理循环，而是复用 `BaseAgent.invoke()` 的已有工具调用机制。Flow 创建 Agent 时把 `skill_runtime.catalog_prompt` 作为 `system_prompt_suffix` 传入。`BaseAgent.__init__()` 会把它追加到具体 Agent 的系统提示词后面。

这点很关键：Skill 不是一个“外置预处理器”。系统没有在用户消息到达前主动解析 `$skill-name` 并把正文塞进去，而是让模型在正常工具调用过程中自己决定是否加载。这样自动匹配和显式 `$skill-name` 都走同一条工具路径，事件、Memory 和重试逻辑也都复用原有机制。

另一个关键行为是 `tool_choice`。Team Planner 和 Synthesizer 原本设置 `_tool_choice = "none"`，表示不允许调用工具。但 Skill 功能需要它们在生成结构化 JSON 前按需加载 Skill 指令。因此 `BaseAgent` 在发现工具列表里存在 `load_skill` 时，会把 `tool_choice` 从 `"none"` 打开为 `None`。如果没有 SkillTool，则保持原来的禁止工具调用行为。

这不是把 Planner 和 Synthesizer 放开成任意工具调用。它们的工具列表本身仍然只有 SkillTool，所以即使 `tool_choice` 不再是 `"none"`，模型也只能选择 `load_skill`。真正的业务工具没有传给它们。

### BaseAgent 源码走读

构造函数里只拼接系统提示词后缀，不改变类级 `_system_prompt`。这意味着不同任务、不同快照可以拥有不同目录，而不会污染全局 Agent 定义。

```python
base_prompt = type(self)._system_prompt
self._system_prompt = (
    f"{base_prompt.rstrip()}\n\n{system_prompt_suffix}"
    if system_prompt_suffix
    else base_prompt
)
self._tool_choice = type(self)._tool_choice
if self._tool_choice == "none" and any(
        tool.has_tool("load_skill") for tool in self._tools
):
    self._tool_choice = None
```

当持久化 Memory 已经存在系统消息时，`_ensure_memory()` 会用当前系统提示词替换第一条 system message。这一点对 React 模式尤其重要：同一个会话后续新任务可能看到新的 Skill 目录，不能继续使用旧目录。

这里替换的是系统提示词，不是清空历史消息。原有会话历史仍然保留，但第一条 system message 会更新成当前 Agent 实例的提示词。对于 Skill 来说，这意味着目录可以随新 Runner 创建而刷新，而不会永久停留在首次会话时的目录。

```python
if self._memory.messages:
    if self._memory.messages[0].get("role") == "system":
        self._memory.messages[0]["content"] = self._system_prompt
    else:
        self._memory.messages.insert(
            0,
            {"role": "system", "content": self._system_prompt},
        )
```

BaseAgent 的工具循环没有区分 Skill 和其他工具。模型返回 `load_skill` 工具调用后，BaseAgent 发出 calling 事件，执行工具，再发出 called 事件，并把 `ToolResult` JSON 作为 tool message 追加到 Memory。Skill 完整正文正是通过这个通用路径进入上下文。

这也是前端能看到 Skill 加载过程的原因。因为 `load_skill` 没有绕过工具系统，它会自然产生 ToolEvent，Runner 可以在 called 阶段补充摘要，SSE 可以按既有 ToolEvent 发送，前端也可以按工具调用渲染。

```python
yield ToolEvent(
    tool_call_id=tool_call_id,
    tool_name=tool.name,
    function_name=function_name,
    function_args=function_args,
    status=ToolEventStatus.CALLING,
)

result = await self._invoke_tool(tool, function_name, function_args)

yield ToolEvent(
    tool_call_id=tool_call_id,
    tool_name=tool.name,
    function_name=function_name,
    function_args=function_args,
    function_result=result,
    status=ToolEventStatus.CALLED,
)

tool_messages.append({
    "role": "tool",
    "tool_call_id": tool_call_id,
    "function_name": function_name,
    "content": result.model_dump_json(),
})
```

## 十一、PlannerReActFlow 为 Planner 和 ReAct 分别创建 SkillTool

单 Agent 模式仍是原来的 Planner + ReAct 组合。Skill 接入点位于 `PlannerReActFlow.__init__()`。Flow 创建基础工具列表后，从 Runtime 读取 `catalog_prompt`。如果目录不为空，就给 Planner 加一个 `SkillTool`，同时给 ReAct 的工具列表也加一个 `SkillTool`。两个工具实例独立，但共享同一个 Runtime。

Planner 和 ReAct 使用 Skill 的目的不同。Planner 加载 Skill 是为了理解某类任务应该怎么拆步骤，ReAct 加载 Skill 是为了具体执行某个步骤时遵循操作说明或读取资源。即使它们加载的是同名 Skill，也会分别写入各自 Memory。

Planner 只有 SkillTool，没有 File、Shell、Browser 等业务工具。这样 Planner 可以在规划前加载 Skill 指令，但不能借 Skill 功能去执行文件读写或搜索。ReAct Agent 则保留原有工具集合，并额外获得 `load_skill`。

这保证了单 Agent 模式里的权限结构没有被 Skill 打破。规划阶段仍然只规划，执行阶段才执行。Skill 只是为两个阶段提供可选说明，不改变阶段职责。

### React 模式接入源码走读

下面这段构造逻辑说明 Skill 目录是可选的。没有启用 Skill 时，`catalog` 为空，Planner 工具列表仍为空，ReAct 工具列表也没有 `load_skill`。

```python
react_tools = [
    FileTool(sandbox=sandbox),
    ShellTool(sandbox=sandbox),
    BrowserTool(browser=browser),
    SearchTool(search_engine=search_engine),
    MessageTool(),
    mcp_tool,
    a2a_tool,
]
planner_tools = []
catalog = skill_runtime.catalog_prompt
if catalog:
    planner_tools.append(SkillTool(skill_runtime))
    react_tools.append(SkillTool(skill_runtime))
```

创建 Agent 时，两个 Agent 都拿到同一份目录后缀。Planner 与 ReAct 的 Memory 策略仍遵循原有代码：未传入 Memory 时使用会话持久化 Memory；Skill 完整正文只会进入实际调用 `load_skill` 的那个 Agent Memory。

如果 Planner 没有调用 `load_skill`，ReAct 仍然可以在执行阶段根据同一份目录自行调用。反过来，Planner 调用了也不代表 ReAct 自动拥有完整正文。这个行为来自“工具实例独立 + Memory 独立”，不是额外分支逻辑。

```python
self.planner = PlannerAgent(
    uow_factory=uow_factory,
    session_id=session_id,
    agent_config=agent_config,
    llm=llm,
    json_parser=json_parser,
    tools=planner_tools,
    system_prompt_suffix=catalog,
)

self.react = ReActAgent(
    uow_factory=uow_factory,
    session_id=session_id,
    agent_config=agent_config,
    llm=llm,
    json_parser=json_parser,
    tools=react_tools,
    system_prompt_suffix=catalog,
)
```

## 十二、TeamFlow 中 Skill 是所有逻辑 Agent 的共享目录，但不是 Worker 权限提升

Team 模式有三个角色：Planner、Worker 和 Synthesizer。Skill 接入时，三类 Agent 都获得同一个任务级 Skill 目录。Planner 与 Synthesizer 的工具集只包含 SkillTool；Worker 在原有 `ToolPolicy` 计算出的工具集合之外额外获得 SkillTool，并把 `load_skill` 加入 allowed names。

Team 模式里最重要的是不要让 Skill 成为“万能 Worker”。DAG 节点的 capability 仍然决定 Worker 能做什么。Skill 可以告诉 Worker 怎么做、读哪些相对路径、脚本在什么位置，但 Worker 是否能读文件或执行脚本仍由 capability 暴露的工具决定。

这个设计让每个 Team Agent 都能按需加载同一份 Skill 指令，但 Worker 仍受 capability 限制。比如 analysis Worker 没有 File、Shell、Search 等业务工具，只能调用 `load_skill`；file_read Worker 即使加载 Skill，也只有 `read_file`、`search_in_file` 和 `find_files`；shell Worker 才能使用 Shell 工具。Skill 不改变 capability，不扩大业务权限。

同时，Team 模式的 Agent 都使用短期 `Memory()`，不是复用会话持久化 Memory。这样每个 Worker 的上下文只包含当前任务、依赖结果、工具调用和自己加载过的 Skill，避免并发 Worker 之间互相污染。

### Team 构建源码走读

`build_team_flow()` 先构造基础业务工具，再创建 `ToolPolicy`。Skill 目录存在时，Planner 直接获得 SkillTool；否则工具列表为空。

```python
tools = [
    FileTool(sandbox=sandbox),
    ShellTool(sandbox=sandbox),
    BrowserTool(browser=browser),
    SearchTool(search_engine=search_engine),
    mcp_tool,
    a2a_tool,
]
policy = ToolPolicy(tools)
catalog = skill_runtime.catalog_prompt
planner = TeamPlannerAgent(
    uow_factory=uow_factory,
    session_id=session_id,
    agent_config=agent_config,
    llm=llm,
    json_parser=json_parser,
    tools=[SkillTool(skill_runtime)] if catalog else [],
    memory=Memory(),
    system_prompt_suffix=catalog,
)
```

Worker 工厂先按 capability 拿业务工具和允许函数名，再有条件地追加 SkillTool 和 `load_skill`。`allowed_tool_names` 仍会在 BaseAgent `_get_tool()` 中生效，因此即使某个工具对象被传入，未授权函数也不能调用。

这里有两道限制。第一道是 `policy.tools_for()` 决定传哪些工具对象；第二道是 `allowed_tool_names` 决定这些工具对象里的哪些函数可调用。Skill 只是在第二道限制里额外加入 `load_skill`，没有把其他函数加入白名单。

```python
def worker_factory(graph_id, agent_id, task, attempt):
    worker_tools = policy.tools_for(task.capability)
    allowed_names = set(policy.allowed_names(task.capability))
    if catalog:
        worker_tools = [*worker_tools, SkillTool(skill_runtime)]
        allowed_names.add("load_skill")
    return TaskWorker(
        uow_factory=uow_factory,
        session_id=session_id,
        agent_config=worker_config,
        llm=llm,
        json_parser=json_parser,
        tools=worker_tools,
        memory=Memory(),
        allowed_tool_names=frozenset(allowed_names),
        system_prompt_suffix=catalog,
        graph_id=graph_id,
        task=task,
        agent_id=agent_id,
        attempt=attempt,
    )
```

Synthesizer 与 Planner 一样，只能加载 Skill，不能调用业务工具。它的职责仍是汇总已经完成的图结果。

Synthesizer 加载 Skill 的场景通常是为了遵循某种输出格式或总结口径，而不是为了补充事实。它拿到的是整张图的结构化结果，不能通过 Skill 获得搜索、文件或 Shell 能力去新增事实。

```python
def synthesizer_factory():
    return TeamSynthesizerAgent(
        uow_factory=uow_factory,
        session_id=session_id,
        agent_config=agent_config,
        llm=llm,
        json_parser=json_parser,
        tools=[SkillTool(skill_runtime)] if catalog else [],
        memory=Memory(),
        system_prompt_suffix=catalog,
    )
```

## 十三、Team Planner 和 Synthesizer 会转发 Skill 工具事件，Worker 会补齐任务归属

Team Planner 和 Synthesizer 的输出最终要被 Flow 消费为结构化对象，所以它们不能像普通对话那样直接把 ToolEvent 交给上层生成器。当前实现给二者加了 `_skill_events` 队列：Agent 内部遇到 ToolEvent 时先保存，并可通过 `emit` 回调实时发出；Flow 在 Planner 或 Synthesizer 调用结束后再 `drain_skill_events()`，确保 Skill 加载过程能出现在事件流中。

这段处理是为了兼顾两个目标：一方面 Planner/Synthesizer 方法的返回值必须是结构化模型，不能把 ToolEvent 混在返回值里；另一方面用户界面又应该看到它们确实加载过 Skill。用内部队列暂存 ToolEvent，调用结束后排空，是当前代码选择的桥接方式。

TaskWorker 的处理略有不同。Worker 运行在 Orchestrator 内部，工具事件需要标记属于哪张图、哪个任务、哪个 Worker、哪次 attempt。`TaskWorker.execute()` 在遇到 ToolEvent 时直接补上 `graph_id`、`task_id`、`agent_id` 和 `attempt` 后，通过 emit 回调发给 TeamFlow 的事件队列。

这些归属字段不只用于展示，也用于理解并发执行。多个 Worker 同时调用 `load_skill` 时，前端和事件存储需要知道每个工具调用属于哪个 DAG 节点。否则用户只能看到一串“正在加载 Skill”，却无法判断是谁在加载、加载服务于哪个任务。

### Team Agent 事件源码走读

Planner 的 `create_graph()` 遇到 ToolEvent 时不会解析成计划，而是保存并继续循环。最终 MessageEvent 才会被 JSONParser 解析为 `PlannedTaskGraph`。

```python
async def create_graph(
    self,
    message: Message,
    validation_error: str | None = None,
    emit: Callable[[BaseEvent], Awaitable[None]] | None = None,
) -> PlannedTaskGraph:
    query = json.dumps(
        {
            "goal": message.message,
            "attachments": message.attachments,
            "previous_validation_error": validation_error,
        },
        ensure_ascii=False,
    )
    async for event in self.invoke(query):
        if isinstance(event, ToolEvent):
            self._skill_events.append(event)
            if emit is not None:
                await emit(event)
            continue
        if isinstance(event, ErrorEvent):
            raise RuntimeError(event.error)
        if isinstance(event, MessageEvent):
            parsed = await self._json_parser.invoke(event.message)
            return PlannedTaskGraph.model_validate(parsed)
    raise RuntimeError("planner produced no graph")
```

Worker 则把工具事件归属补齐后立即发出。前端后续会根据 `task_id` 把工具调用挂到对应 Team 任务行下。

这里补齐的是事件对象本身，而不是额外包一层 TeamToolEvent。这样后端事件类型保持统一，前端也只需要扩展 ToolEvent 的归并逻辑，不需要为 Skill 或 Team 再维护一套平行事件协议。

```python
async for event in self.invoke(query):
    if isinstance(event, ToolEvent):
        event.graph_id = self._graph_id
        event.task_id = self._task.id
        event.agent_id = self._agent_id
        event.attempt = self._attempt
        await emit(event)
    elif isinstance(event, ErrorEvent):
        raise RuntimeError(event.error)
    elif isinstance(event, MessageEvent):
        parsed = await self._json_parser.invoke(event.message)
        return WorkerResult.model_validate(parsed)
```

## 十四、AgentTaskRunner 是 Skill 事件摘要和持久化的边界

`AgentTaskRunner` 创建时接收 `skill_snapshots`，并立刻构造一个任务级 `SkillRuntime`。React Flow 和 Team Flow 都共享这个 Runtime。Runner 还负责在工具事件 called 阶段补齐前端可展示的 `SkillToolContent`。

Runner 是整个运行链路的“事件出口”。无论 Skill 是 Planner 加载、ReAct 加载、Worker 加载还是 Synthesizer 加载，只要最后变成 ToolEvent 流经 Runner，就会在这里被处理成前端更容易展示的摘要。

这里有一个重要的隐私和上下文边界：完整 `SKILL.md` 存在于 `ToolResult.data.content`，会进入 Agent Memory；但 Runner 写给前端的 `tool_content` 只包含 `name` 和 `skill_dir`。因此时间线和工具预览不会展开完整 Skill 正文，完整正文只在设置页详情中展示。

这个边界还有一个产品层面的效果：用户在聊天时间线里关注的是 Agent 做了什么，而不是完整 Skill 指令文本。完整文本可能很长，也可能包含给模型看的细节说明；把它塞到每次工具事件里会让事件流膨胀，也会降低对话区可读性。

### Runner 初始化源码走读

Runner 的 Runtime 是按任务创建的，而不是全局单例。React Flow 持久存在于 Runner 内，Team Flow 则按消息创建，但它们共享同一个 Runtime。

```python
self._skill_runtime = SkillRuntime(skill_snapshots, sandbox)
self._react_flow = PlannerReActFlow(
    uow_factory=uow_factory,
    llm=llm,
    agent_config=agent_config,
    session_id=session_id,
    json_parser=json_parser,
    browser=browser,
    sandbox=sandbox,
    search_engine=search_engine,
    mcp_tool=self._mcp_tool,
    a2a_tool=self._a2a_tool,
    skill_runtime=self._skill_runtime,
)
self._team_flow_factory = lambda: build_team_flow(
    uow_factory=uow_factory,
    session_id=session_id,
    agent_config=agent_config,
    llm=llm,
    json_parser=json_parser,
    browser=browser,
    sandbox=sandbox,
    search_engine=search_engine,
    mcp_tool=self._mcp_tool,
    a2a_tool=self._a2a_tool,
    skill_runtime=self._skill_runtime,
)
```

`_handle_tool_event()` 处理 Skill 时，只在 called 且结果成功时生成摘要。失败的 `load_skill` 仍会作为普通 ToolEvent 存在，但没有 SkillToolContent 摘要。

因此前端看到没有 `skill_dir` 的 Skill 工具事件时，并不代表事件丢失，而是表示这次调用还在 calling 阶段或最终失败。具体失败原因仍在 `function_result` 中，但当前预览摘要只展示成功加载后的目录。

```python
elif event.tool_name == "skill":
    data = event.function_result.data if event.function_result else None
    if (
            event.function_result
            and event.function_result.success
            and isinstance(data, dict)
    ):
        event.tool_content = SkillToolContent(
            name=str(data.get("name", "")),
            skill_dir=str(data.get("skill_dir", "")),
        )
```

## 十五、事件模型复用 ToolEvent，只新增 SkillToolContent 摘要

后端没有新增 `SkillEvent`。Skill 加载过程天然是工具调用，因此继续使用 `ToolEvent`。领域事件只是在 `ToolContent` 联合类型里新增 `SkillToolContent`，包含 Skill 名称和沙箱目录。接口 SSE 层的 `ToolEventData.content` 是 `Any`，因此可以透传这个新摘要。

复用 ToolEvent 的好处是链路短。Redis Stream、数据库事件表、SSE 映射、前端工具列表都已经理解工具调用的 calling/called 生命周期。Skill 加载本身就是一次工具调用，所以没有必要为它创建一套新的生命周期事件。

这个选择让 Skill 加载在前端时间线中表现为“工具使用”，不会引入第二套事件生命周期。React 模式下，Skill 工具事件可以作为独立工具项或挂在当前 step 下；Team 模式下，Worker 工具事件根据 `task_id` 挂在对应任务下。

同时，Skill 的完整正文没有进入 `ToolContent` 联合。`ToolContent` 是给前端看的摘要，不是 Agent Memory。Agent Memory 通过 ToolResult 保存完整正文，前端事件通过 SkillToolContent 保存摘要，两条用途不同的数据路径被明确分开。

### 事件模型源码走读

`SkillToolContent` 很小，刻意不包含完整正文、ZIP key 或数据库 id。前端能展示用户需要理解的运行行为，但不能从事件流中拿到完整 Skill 包内容。

这也让事件存储更轻。会话事件会被持久化并重放，如果每次 Skill 加载都把完整 `SKILL.md` 放进事件，历史会话体积会快速膨胀。当前实现把大文本留在 Agent Memory 和数据库详情中，事件只保存运行摘要。

```python
class SkillToolContent(BaseModel):
    """Skill 加载摘要。"""
    name: str
    skill_dir: str


ToolContent = Union[
    BrowserToolContent,
    SearchToolContent,
    ShellToolContent,
    FileToolContent,
    MCPToolContent,
    A2AToolContent,
    SkillToolContent,
]
```

SSE 层继续使用通用 ToolEventData。`content` 是可选 `Any`，`graph_id`、`task_id`、`agent_id`、`attempt` 也会透传，用于 Team 工具归属。

```python
class ToolEventData(BaseEventData):
    """工具事件数据"""
    tool_call_id: str
    name: str
    status: ToolEventStatus
    function: str
    args: Dict[str, Any]
    content: Optional[Any] = None
    graph_id: Optional[str] = None
    task_id: Optional[str] = None
    agent_id: Optional[str] = None
    attempt: Optional[int] = None
```

## 十六、管理接口提供最小生命周期，不提供 ZIP 下载或版本历史

接口层新增 `skill_routes.py`，路由前缀是 `/app-config/skills`。它提供列表、上传、详情、启停和删除五个接口。上传使用 multipart 的 `file` 字段；详情返回完整 `skill_md`；列表和上传响应只返回 id、name、description、enabled，不暴露 `bundle_key`。

这组接口是设置模块的一部分，而不是聊天模块的一部分。用户管理 Skill 的动作不会直接触发 Agent 运行；它只是改变后续新任务可见的全局配置。聊天任务是否使用某个 Skill，仍发生在 Agent 运行时。

`SkillService` 是很薄的应用服务。它只把解析错误转成 BadRequest，把不存在转成 NotFound，其余存储或数据库异常继续向上抛出，由全局异常处理链路处理。这里没有在应用服务里做 ZIP 格式兜底，也没有在接口层重复实现业务规则。

这种薄服务写法保持了职责清楚：路由处理 HTTP 形态，应用服务处理接口级异常转换，Registry 处理业务规则。上传覆盖、启停保留、旧 ZIP 清理这些真正的业务行为都不在路由里。

### 应用服务与路由源码走读

`SkillService.upload()` 的职责很清晰：读取上传文件 bytes，调用 Registry，捕获解析错误。

```python
class SkillService:
    """设置模块的 Skill 应用服务。"""

    def __init__(self, registry: SkillRegistry) -> None:
        self._registry = registry

    async def upload(self, file: UploadFile) -> Skill:
        try:
            return await self._registry.upsert_bundle(await file.read())
        except SkillParseError as exc:
            raise BadRequestError(str(exc)) from exc

    async def list_skills(self) -> list[Skill]:
        return await self._registry.list_skills()

    async def get_skill(self, skill_id: str) -> Skill:
        skill = await self._registry.get_skill(skill_id)
        if skill is None:
            raise NotFoundError("Skill 不存在")
        return skill
```

路由层只做 HTTP 形态转换。删除使用 `POST /{skill_id}/delete`，与当前设置模块里已有的写操作风格保持一致。

```python
router = APIRouter(prefix="/app-config/skills", tags=["设置模块"])


@router.get("", response_model=Response[ListSkillsResponse])
async def list_skills(
    service: SkillService = Depends(get_skill_service),
) -> Response[ListSkillsResponse]:
    skills = await service.list_skills()
    return Response.success(
        data=ListSkillsResponse(
            skills=[SkillListItem.model_validate(skill) for skill in skills]
        )
    )


@router.post("", response_model=Response[SkillListItem])
async def upload_skill(
    file: UploadFile = File(...),
    service: SkillService = Depends(get_skill_service),
) -> Response[SkillListItem]:
    skill = await service.upload(file)
    return Response.success(
        msg="Skill 上传成功",
        data=SkillListItem.model_validate(skill),
    )
```

响应 schema 不暴露对象存储 key。详情只比列表多一个 `skill_md` 字段。

前端列表页只需要概要信息，详情弹窗才需要完整正文。这个拆分避免列表接口一次返回所有 Skill 的长文本，也为后续列表分页或搜索留下空间。

```python
class SkillListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str
    enabled: bool


class SkillDetail(SkillListItem):
    skill_md: str
```

## 十七、依赖注入把 SkillRegistry 同时接到设置接口和 AgentService

`interfaces/service_dependencies.py` 中新增 `get_skill_registry()` 和 `get_skill_service()`。同一个 Registry 构造方式被设置页接口和 AgentService 共用，确保管理链路和运行链路看到的是同一套数据库与 OSS 存储。

依赖注入层是这条链路的连接点。Skill 管理接口需要 Registry，AgentService 也需要 Registry；如果二者分别手写构造，很容易在某个地方漏掉 OSS 实现、解析器或 UoW 工厂。当前代码把构造逻辑集中在一个依赖函数里。

`get_agent_service()` 通过依赖注入拿到 `skill_registry`，再传入 `AgentService`。这一步使 AgentService 在创建新任务时可以固定 Snapshot。配置文件本身没有被 Skill 功能改写，Skill 的启停和内容都走数据库与 OSS。

因此用户不需要修改 `.env` 或配置 YAML 来添加 Skill。Skill 是运行时可管理数据，不是本地启动配置。这也符合设置页上传的产品形态。

### 依赖注入源码走读

```python
def get_skill_registry(
        oss: OSS = Depends(get_oss),
) -> SkillRegistry:
    return SkillRegistry(
        uow_factory=get_uow,
        bundle_storage=OSSSkillBundleStorage(oss),
        parser=SkillParser(),
    )


def get_skill_service(
        registry: SkillRegistry = Depends(get_skill_registry),
) -> SkillService:
    return SkillService(registry)
```

AgentService 构造函数新增 `skill_registry`，保存后只在 `_create_task()` 使用。已有聊天接口不需要知道具体有哪些 Skill。

```python
def get_agent_service(
        oss: OSS = Depends(get_oss),
        skill_registry: SkillRegistry = Depends(get_skill_registry),
) -> AgentService:
    ...
    return AgentService(
        uow_factory=get_uow,
        llm=llm,
        agent_config=app_config.agent_config,
        mcp_config=app_config.mcp_config,
        a2a_config=app_config.a2a_config,
        sandbox_cls=DockerSandbox,
        task_cls=RedisStreamTask,
        json_parser=RepairJSONParser(),
        search_engine=BingSearchEngine(),
        file_storage=file_storage,
        skill_registry=skill_registry,
    )
```

## 十八、前端设置页完成上传、查看、启停和删除

前端新增 `SkillSettings` 组件，并把它挂到 `ManusSettings` 的左侧菜单。组件首次渲染时调用 `skillApi.list()` 拉取列表；上传按钮触发隐藏 file input；上传成功后把返回项插入或替换到本地列表；启停和删除都使用乐观更新，失败时回滚本地状态。

设置页的交互模型是即时生效的。上传、启停、删除都不是先改本地草稿再统一保存，而是每个动作直接调用接口。这和 LLM 通用配置页不同，因为 Skill 管理涉及文件上传和对象存储，不适合放在一个“保存全部”的按钮后面。

详情预览通过 `skillApi.detail(id)` 获取完整 `skill_md`，在 Dialog 里用 `pre` 展示。ZIP 本身没有下载入口，也没有版本选择入口。设置页管理的是系统全局 Skill，不区分当前会话。

这个 UI 也只展示当前有效记录。由于后端没有版本系统，前端不会提供版本历史、回滚按钮或多个同名 Skill 的选择器。用户上传同名 ZIP 后，列表中仍然只有一个同名 Skill。

### 设置页源码走读

`replaceSkill()` 按 id 替换或追加，并按名称排序。上传同名 Skill 时后端复用 id，因此前端会自然替换原列表项。

如果上传的是一个新名称，后端会返回新 id，前端会追加到列表；如果上传的是同名覆盖，后端返回旧 id，前端会替换已有项。前端不需要自己判断“同名”规则，因为这个规则属于 Registry。

```tsx
const replaceSkill = (next: SkillListItem) => {
  setSkills((current) => {
    const exists = current.some((skill) => skill.id === next.id);
    const updated = exists
      ? current.map((skill) => (skill.id === next.id ? next : skill))
      : [...current, next];
    return updated.sort((a, b) => a.name.localeCompare(b.name));
  });
};

const handleUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
  const file = event.target.files?.[0];
  if (!file) return;
  setUploading(true);
  try {
    replaceSkill(await skillApi.upload(file));
    toast.success("Skill 已上传或覆盖");
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "Skill 上传失败");
  } finally {
    setUploading(false);
    event.target.value = "";
  }
};
```

启停和删除都先改 UI，再调用接口。失败时恢复原对象，避免界面与服务端状态长期不一致。

乐观更新让设置操作看起来更直接，但代码仍然保存了失败回滚路径。尤其是删除操作，如果接口失败，原 Skill 会被重新放回列表；启停失败时也会恢复原 enabled 状态。

```tsx
const handleToggle = async (skill: SkillListItem, enabled: boolean) => {
  replaceSkill({ ...skill, enabled });
  try {
    replaceSkill(await skillApi.setEnabled(skill.id, enabled));
    toast.success(`${skill.name} 已${enabled ? "启用" : "禁用"}`);
  } catch (error) {
    replaceSkill(skill);
    toast.error(error instanceof Error ? error.message : "操作失败");
  }
};

const handleDelete = async (skill: SkillListItem) => {
  setSkills((current) => current.filter((item) => item.id !== skill.id));
  try {
    await skillApi.delete(skill.id);
    toast.success(`已删除 Skill「${skill.name}」`);
  } catch (error) {
    replaceSkill(skill);
    toast.error(error instanceof Error ? error.message : "删除失败");
  }
};
```

`ManusSettings` 只新增一个菜单项和一个条件渲染分支，没有把 Skill 管理耦合到通用配置的保存按钮。

```tsx
type SettingTab = 'common-setting' | 'llm-setting' | 'skill-setting' | 'a2a-setting' | 'mcp-setting'

const SETTING_MENUS = [
  {key: 'common-setting', icon: Settings, title: '通用配置'},
  {key: 'llm-setting', icon: Languages, title: '模型提供商'},
  {key: 'skill-setting', icon: BookOpen, title: 'Skills'},
  {key: 'a2a-setting', icon: LayoutGrid, title: 'A2A Agent 配置'},
  {key: 'mcp-setting', icon: Wrench, title: 'MCP 服务器'},
]

...

{activeSetting === 'skill-setting' && <SkillSettings/>}
```

## 十九、输入框的 `$skill-name` 是普通文本提示，不是新的聊天协议

聊天输入框新增了 `$` 补全。用户输入 `$` 且光标前满足匹配规则时，前端拉取 Skill 列表，并只展示已启用项。上下键可以切换候选，回车插入 `$skill.name `。插入后的内容仍是普通 message 文本。

这个功能主要是帮用户输入正确的 Skill 名称，而不是改变后端执行路径。用户也可以手动打出 `$some-skill`，只要文本进入消息，后端看到的效果一样。补全只是降低拼错名称的概率。

后端 `ChatRequest` 没有新增 `skill_ids`、`selected_skills` 或类似字段。显式激活完全依赖 `$skill-name` 进入普通用户消息，再由系统提示词中的 “When the user explicitly mentions $<skill-name>, call that skill.” 引导模型调用 `load_skill`。这种实现保持协议最小，但也意味着最终是否调用 Skill 仍由模型工具调用决定。

这条边界很重要：当前实现不是确定性命令系统。`$skill-name` 是强提示，不是后端强制执行。模型如果因为供应商差异、上下文拥挤或工具调用失败没有调用 `load_skill`，后端不会单独解析文本并代替模型调用工具。

### 输入框源码走读

`getSkillMention()` 只识别行首或空白后的 `$xxx`，并且不跨空白。它返回当前 mention 的起始位置和小写 query。

```tsx
type SkillMention = { start: number; query: string };

function getSkillMention(value: string, cursor: number): SkillMention | null {
  const prefix = value.slice(0, cursor);
  const match = prefix.match(/(?:^|\s)\$([^\s$]*)$/);
  if (!match) return null;
  return {
    start: cursor - match[1].length - 1,
    query: match[1].toLowerCase(),
  };
}
```

候选列表只包含启用项，并按名称或描述做包含匹配。这里没有做远程搜索，列表由 `skillApi.list()` 拉一次后在本地过滤。

由于 Skill 列表通常是设置级资源，不是高频大数据列表，本地过滤足够满足当前最小实现。如果后续 Skill 数量变多，再考虑服务端搜索或分页会更合适。

```tsx
const matchingSkills = mention
  ? skills.filter((skill) => {
      if (!skill.enabled) return false;
      const query = mention.query;
      return (
        skill.name.toLowerCase().includes(query) ||
        skill.description.toLowerCase().includes(query)
      );
    })
  : [];
```

插入时，前端只替换当前 mention 片段为 `$name `。发送时依然走原来的 `onSend(trimmedMessage, files)`。

插入后额外补一个空格，是为了结束当前 mention，让用户可以继续输入自然语言任务描述。比如输入 `$report-skill 总结这个文件`，后端收到的就是一段普通文本。

```tsx
const insertSkill = (skill: SkillListItem) => {
  if (!mention) return;
  const cursor = textareaRef.current?.selectionStart ?? inputValue.length;
  const inserted = `$${skill.name} `;
  const next =
    inputValue.slice(0, mention.start) + inserted + inputValue.slice(cursor);
  const nextCursor = mention.start + inserted.length;
  setInputValue(next);
  onInputValueChange?.(next);
  setMention(null);
  requestAnimationFrame(() => {
    textareaRef.current?.focus();
    textareaRef.current?.setSelectionRange(nextCursor, nextCursor);
  });
};
```

## 二十、前端工具时间线把 load_skill 识别为 Skill 工具

前端工具组件体系新增 `skill` 类型。`getToolKind()` 会在 `name === "skill"` 或 `function === "load_skill"` 时返回 `skill`。`getFriendlyToolLabel()` 会生成“正在加载 Skill xxx”的时间线文案。`SkillTool` 组件使用 `BookOpen` 图标渲染一个 ToolBadge。

时间线里的 Skill 工具和其他工具保持同一种视觉语义：它表示 Agent 正在使用某个能力。用户不需要看到完整正文，只需要知道 Agent 在当前步骤中加载了哪个 Skill，这对排查“它有没有按我的 Skill 工作”已经足够。

工具预览面板则新增 `SkillPreview`，展示 Skill 名称、加载状态和沙箱目录。它从 `tool.content` 中读取 `name` 与 `skill_dir`，如果 called 事件还没带 content，则从参数里回退读取 name。完整 `SKILL.md` 不在这里展示。

沙箱目录是一个对高级用户有用的调试信息。它可以解释后续文件读取或 Shell 命令为什么会访问某个 `/home/ubuntu/.whisker-manus/skills/...` 路径，也能帮助确认资源包是否已经同步成功。

### 工具识别源码走读

```tsx
export function getToolKind(data: ToolEvent | null | undefined): ToolKind {
  if (!data) return 'default'
  const name = (data.name ?? '').toLowerCase()
  const fn = (data.function ?? '').toLowerCase()

  if (data.function === 'message_notify_user' || data.function === 'message_ask_user') {
    return 'message'
  }
  if (name === 'skill' || fn === 'load_skill') {
    return 'skill'
  }
  ...
}

export function getFriendlyToolLabel(data: ToolEvent | null | undefined): string {
  if (!data) return '—'
  const name = (data.name ?? '').toLowerCase()
  const fn = (data.function ?? '').toLowerCase()
  const args = data.args && typeof data.args === 'object' ? data.args : {}

  if (name === 'skill' || fn === 'load_skill') {
    const skillName = getArg(args, 'name')
    return skillName ? `正在加载 Skill ${truncate(skillName, 60)}` : '正在加载 Skill'
  }
  ...
}
```

### 工具预览源码走读

`SkillPreview` 不展示工具结果里的 `content` 字段。即使后端 ToolResult 中包含完整正文，Runner 已经在 `tool_content` 层裁剪掉了它。

因此前端组件不需要防御一大段 Skill 正文撑爆预览区域。它拿到的 content 已经是后端整理过的摘要对象，组件只负责展示摘要字段。

```tsx
function SkillPreview({ tool }: { tool: ToolEvent }) {
  const content = getToolContent(tool);
  const name =
    typeof content?.name === "string"
      ? content.name
      : getArg(tool.args, "name");
  const skillDir =
    typeof content?.skill_dir === "string" ? content.skill_dir : "";

  return (
    <div className="flex h-full flex-col gap-4 p-4">
      <div className="rounded-lg border bg-gray-50 p-4 text-sm">
        <div>
          <span className="text-gray-500">Skill：</span>
          <span className="text-gray-800">{name || "未知"}</span>
        </div>
        <div>
          <span className="text-gray-500">状态：</span>
          <span className="text-gray-800">
            {tool.status === "called" ? "已加载" : "正在加载"}
          </span>
        </div>
        {skillDir && (
          <div className="mt-2">
            <div className="text-gray-500">沙箱目录：</div>
            <code className="mt-1 block break-all rounded bg-white p-2 text-xs text-gray-700">
              {skillDir}
            </code>
          </div>
        )}
      </div>
    </div>
  );
}
```

## 二十一、事件归并逻辑让 Skill 工具在 React 和 Team 两种模式下都能展示

前端 `session-events.ts` 负责把 SSE 事件归并成时间线。Skill 没有特殊归并分支，因为它就是 ToolEvent。React 模式下，如果当前存在 `lastStepId`，工具会挂到当前 step；否则作为独立工具项展示。Team 模式下，只要 ToolEvent 带 `task_id`，前端就尝试根据 `graph_id:task_id` 找到对应任务行，并把工具加入该任务的 `tools` 数组。

这意味着 Skill 的展示位置取决于它发生在哪个执行上下文里。Planner 阶段加载的 Skill 可能作为独立工具出现；ReAct 执行某个步骤时加载的 Skill 会挂在该步骤下；Team Worker 加载的 Skill 会挂在对应 DAG 节点下。前端不需要知道 Skill 的业务含义，只根据事件上下文归位。

这也解释了为什么 TaskWorker 必须补齐 `graph_id` 和 `task_id`。没有这些字段，Team Worker 的 Skill 加载就只能作为独立工具漂在时间线里，无法归属到具体 DAG 节点。

归并逻辑还会用 `tool_call_id` 更新 calling/called 状态。用户看到的是同一条工具记录从“正在加载”变成“已加载”，而不是两条重复记录。这和搜索、文件、Shell 等工具的体验一致。

### Team 工具归并源码走读

```tsx
case "tool": {
  const tool = ev.data as ToolEvent;
  if (tool.task_id) {
    const teamStepIndex = tool.graph_id
      ? teamStepIndexes.get(`${tool.graph_id}:${tool.task_id}`)
      : undefined;
    if (teamStepIndex !== undefined) {
      const teamStep = list[teamStepIndex];
      if (teamStep.kind === "step") {
        const existingToolIndex = tool.tool_call_id
          ? teamStep.tools.findIndex(
              (item) =>
                item.tool_call_id === tool.tool_call_id &&
                item.attempt === tool.attempt,
            )
          : -1;
        const tools = [...teamStep.tools];
        if (existingToolIndex >= 0) {
          tools[existingToolIndex] = tool;
        } else {
          tools.push(tool);
        }
        list[teamStepIndex] = { ...teamStep, tools };
      }
    }
    break;
  }
```

独立工具也会根据 `tool_call_id` 更新最后一个同 id 工具项。这样 `calling` 和 `called` 两个事件可以合并为同一条工具展示。

## 二十二、一次典型 Skill 使用会按照上传、快照、目录、加载和资源使用推进

一次完整的 Skill 使用链路可以按时间顺序拆成下面几个阶段。

第一阶段是设置页上传 ZIP。前端把文件作为 multipart 传给 `/api/app-config/skills`。后端解析 ZIP 中首个 `SKILL.md`，校验 name 和 description，把 `skill_md` 与元数据写入 `skills` 表，并把原始 ZIP 上传到 OSS。如果同名 Skill 已存在，复用原 id 并保留启停状态。

这个阶段结束后，Skill 只是“可被后续任务发现”。它不会自动修改正在运行的任务，也不会主动推送给某个会话。用户如果想让新 Skill 生效，需要发起新的任务或创建新的 Runner。

第二阶段是用户发起新任务。`AgentService._create_task()` 准备沙箱和浏览器后，调用 `create_enabled_snapshot()`。此时只有启用项进入快照，且快照会尝试下载对应 ZIP bytes。随后 Runner 创建一个 `SkillRuntime`，React Flow 或 Team Flow 从这个 Runtime 取得轻量目录。

如果某个 Skill 在设置页是禁用状态，它从这里开始就不会被新任务看见。即使用户手动在输入框写 `$disabled-skill`，Runtime 的当前快照中也没有这个名称，`load_skill` 会按“不存在或未启用”处理。

第三阶段是 Agent 初始推理。系统提示词中出现 `<available_skills>`，其中只有 name 和 description。用户如果输入了 `$skill-name`，这也只是普通消息文本。模型根据目录和用户消息判断是否调用 `load_skill`。

这里的目录既服务自动匹配，也服务显式匹配。自动匹配依赖 description；显式匹配依赖 `$skill-name` 文本和提示词约束。两者最终都落到同一个工具函数。

第四阶段是 `load_skill` 工具调用。BaseAgent 发出 calling ToolEvent，然后 `SkillTool` 调 Runtime。Runtime 首次加载该 Skill 时把 ZIP 上传并解压到沙箱，返回 `skill_dir`；SkillTool 再把完整 `SKILL.md` 和 `skill_dir` 包成工具结果。BaseAgent 把工具结果写入当前 Agent Memory，并继续调用 LLM。

如果这是同一个 Agent 的第二次同名加载，SkillTool 不会再次返回完整正文；如果是另一个 Agent 的首次加载，它会再次把正文注入那个 Agent 的 Memory，但 Runtime 不会重复同步 ZIP。这个区别来自 Tool 和 Runtime 的两个缓存层。

第五阶段是资源使用。Agent 后续若要读取 Skill 包里的 `references/`，必须使用当前 Agent 拥有的 File 工具；若要执行 `scripts/`，必须拥有 Shell 工具。Skill 本身不会自动读文件或运行脚本。

所以当一个 Skill 文档里写了“运行 scripts/build.py”，只有拥有 Shell 能力的 Agent 才真的能执行。analysis Worker 读到这条指令，也没有 Shell 工具可用，这不是 bug，而是 capability 策略在生效。

## 二十三、错误处理遵循“上传错误走管理接口，运行错误走工具结果或 Runner 事件”

上传阶段的 ZIP 解析错误会转成 400。Skill 不存在、详情不存在、启停目标不存在或删除目标不存在会转成 404。对象存储上传、数据库提交等系统级错误不会在 SkillService 中被包装成成功结果，而是向上抛出。

管理接口的错误基本面向设置页操作者。比如 ZIP 不合法，用户需要重新打包；Skill id 不存在，说明页面状态过期或对象已被删除；存储失败则说明系统基础设施异常。代码没有把这些错误转换成“上传成功但不可用”这样的模糊状态。

运行阶段的“当前快照中没有这个名称”由 `SkillTool` 转成失败 `ToolResult`，模型可以看到工具失败并继续处理。快照下载失败、ZIP 不可用、上传沙箱失败、解压失败等 Runtime 级错误由 `SkillLoadError` 表示，并进入 BaseAgent `_invoke_tool()` 的工具重试流程。重试耗尽后，BaseAgent 返回失败 ToolResult，让模型处理。

这条链路没有保留“带 bug 的分支”。找不到 Skill、ZIP 不可用、解压失败都会以明确错误往下传递。系统不会在失败后伪造一个空 Skill 目录，也不会假装加载成功继续执行。

事件展示阶段只在工具调用成功且结果 data 为 dict 时生成 `SkillToolContent`。失败的 Skill 工具调用仍会出现在事件流中，但预览里不会显示沙箱目录。

因此排查运行问题时，可以从三个位置看信息：ToolEvent 的函数参数说明模型想加载哪个名称；ToolResult 说明加载是否成功以及错误消息；SkillToolContent 只在成功时告诉用户最终目录。

### 运行错误源码走读

`SkillRuntime.load()` 区分不存在和同步失败。不存在使用 `SkillNotFoundError`，同步失败使用 `SkillLoadError`。

```python
async def load(self, name: str) -> LoadedSkill:
    snapshot = self._snapshots.get(name)
    if snapshot is None:
        raise SkillNotFoundError(f"Skill 不存在或未启用: {name}")

    skill_dir = await self._ensure_synced(snapshot)
    return LoadedSkill(
        name=snapshot.name,
        skill_md=snapshot.skill_md,
        skill_dir=skill_dir,
    )
```

`SkillTool` 只捕获“不存在”，其他异常留给 Agent 工具重试层。这个边界避免把沙箱同步失败误报成普通不存在。

如果所有 Runtime 错误都在 SkillTool 里转成失败 ToolResult，就会绕过 BaseAgent 的工具重试。当前代码只把确定性的“名称不在快照中”转成失败结果，把可能暂时性的同步错误交给重试层。

```python
try:
    loaded = await self._runtime.load(name)
except SkillNotFoundError as exc:
    return ToolResult(success=False, message=str(exc))
```

BaseAgent 工具调用失败会按 `agent_config.max_retries` 重试，最终返回失败 ToolResult，而不是直接中断整个 Agent 循环。

```python
async def _invoke_tool(self, tool: BaseTool, tool_name: str, arguments: Dict[str, Any]) -> ToolResult:
    err = ""
    for _ in range(self._agent_config.max_retries):
        try:
            return await tool.invoke(tool_name, **arguments)
        except Exception as e:
            err = str(e)
            logger.exception(f"调用工具[{tool_name}]出错, 错误: {str(e)}")
            await asyncio.sleep(self._retry_interval)
            continue

    return ToolResult(success=False, message=err)
```

## 二十四、当前最小实现没有做版本系统、专用资源工具和强制 Skill 路由

当前代码没有 Skill 版本历史、回滚、版本选择或发布审核。上传同名 Skill 就是覆盖当前记录，旧 ZIP 在新记录保存成功后被尽力删除。数据库里也没有 version 字段。

这意味着文档中不能把“版本”描述成已经具备的能力。当前用户能看到和管理的永远是每个 name 对应的一条当前记录。运行时一致性由 Snapshot 保证，不是由版本号选择保证。

当前代码没有 `read_skill_resource` 或 `run_skill_script` 这样的专用工具。Skill 包同步到沙箱后，Agent 只能通过现有 File 和 Shell 工具访问资源；Team Worker 仍受 capability 限制。`SKILL.md` frontmatter 中除 name 和 description 以外的字段不会参与工具授权。

这也意味着 Skill 作者在写 `SKILL.md` 时，不能假设所有 Agent 都能运行脚本或读文件。Skill 可以描述需要的操作，但最终能不能执行，要看调用它的 Agent 当前暴露了哪些工具。

当前代码没有后端强制解析 `$skill-name` 并自动调用 `load_skill`。前端只是插入普通文本，后端只是把目录提示词交给模型。模型可以根据用户显式提及或任务描述调用 Skill，但这不是一个确定性路由器。

如果后续需要确定性路由，可以在 Agent 调用前解析用户消息中的 `$skill-name`，并自动注入对应 Skill；但那会改变当前“模型自主选择工具”的执行语义，也需要重新考虑事件、错误和多 Skill 顺序。

当前代码没有用户级、租户级或会话私有 Skill。`skills` 表是系统全局资源，启用状态对后续所有新任务生效。

因此当前权限模型非常简单：只要一个 Skill 启用，所有新任务都能看到它；只要禁用，所有新任务都看不到它。没有按用户隔离，也没有按会话固定私有目录。

当前代码没有把完整 `SKILL.md` 放进 SSE 工具事件。时间线只显示加载摘要，设置页详情才显示完整正文。

这是刻意的数据边界，不是前端遗漏。完整正文属于管理详情和 Agent Memory，运行时间线只展示行为摘要。

## 二十五、后端有效代码文件分别承担管理、运行时、Agent 接入和事件传输职责

Skill 后端核心文件可以按职责分成下面几组。

`api/app/domain/models/skill.py` 定义全局 Skill、解析结果和任务快照。`api/app/infrastructure/models/skill.py` 定义数据库表映射。`api/alembic/versions/9a4f6c2e1d70_create_skills_table.py` 创建 `skills` 表。

`api/app/domain/services/skills/parser.py` 解析 ZIP 和 `SKILL.md`。`api/app/domain/services/skills/registry.py` 管理全局 Skill 生命周期和任务快照。`api/app/domain/services/skills/runtime.py` 生成目录提示词并同步 ZIP 到沙箱。`api/app/domain/services/tools/skill.py` 暴露 `load_skill`。

`api/app/domain/repositories/skill_repository.py`、`api/app/infrastructure/repositories/db_skill_repository.py` 定义并实现数据库仓库。`api/app/domain/external/skill_bundle_storage.py`、`api/app/infrastructure/external/skill_bundle_storage/oss_skill_bundle_storage.py` 定义并实现 ZIP 对象存储。

`api/app/application/services/skill_service.py` 和 `api/app/interfaces/endpoints/skill_routes.py` 负责设置模块接口。`api/app/interfaces/schemas/skill.py` 定义前端可见的响应结构。

`api/app/application/services/agent_service.py` 在任务创建时固定 Skill 快照。`api/app/domain/services/agent_task_runner.py` 创建 Runtime、把 Runtime 注入 React 和 Team Flow，并裁剪 Skill 工具事件摘要。`api/app/domain/services/agents/base.py` 把 Skill 目录加入系统提示词，并允许原本禁用工具的 Planner/Synthesizer 调用 `load_skill`。

`api/app/domain/services/flows/planner_react.py` 把 SkillTool 接入单 Agent Planner 和 ReAct。`api/app/domain/services/flows/team.py` 把 SkillTool 接入 Team Planner、Worker 和 Synthesizer。`api/app/domain/services/agents/team_planner.py`、`task_worker.py`、`team_synthesizer.py` 处理 Team 模式下 Skill 工具事件的转发和归属。`api/app/domain/services/team/policy.py` 保证 Worker 的业务工具权限仍由 capability 决定。

`api/app/domain/models/event.py` 和 `api/app/interfaces/schemas/event.py` 增加 Skill 工具摘要和 SSE 透传字段。`api/app/interfaces/service_dependencies.py` 把 Registry 同时接到设置接口和 AgentService。

## 二十六、前端有效代码文件完成设置管理、显式引用和工具展示

`ui/src/lib/api/skill.ts` 封装 Skill 管理接口。`ui/src/lib/api/types.ts` 增加 Skill 列表、详情、工具内容和事件类型。`ui/src/components/skill-settings.tsx` 实现上传、列表、详情、启停和删除。`ui/src/components/manus-settings.tsx` 把 Skills 加入设置弹窗菜单。

`ui/src/components/chat-input.tsx` 实现 `$` mention 检测、Skill 列表拉取、候选过滤、键盘选择和文本插入。它没有改变聊天协议，发送时仍是普通 message。

`ui/src/components/tool-use/skill-tool.tsx`、`tool-use/index.tsx` 和 `tool-use/utils.ts` 让时间线识别并渲染 Skill 工具。`ui/src/components/tool-preview-panel.tsx` 增加 Skill 预览，展示名称、状态和沙箱目录。`ui/src/lib/session-events.ts` 复用 ToolEvent 归并逻辑，使 React 和 Team 模式下的 `load_skill` 都能出现在对应时间线位置。

## 二十七、总结

当前 Agent Skills 实现是一条克制的运行时加载链路。它把 Skill 做成系统全局配置资产，通过 ZIP + `SKILL.md` 解析进入数据库和对象存储；新任务创建时固定启用 Skill 的快照；Agent 初始只看到轻量目录；真正需要时调用 `load_skill`，把完整指令注入当前 Agent Memory，并把完整资源包同步到沙箱。

这套实现没有把 Skill 做成新的权限系统，也没有做版本平台或确定性路由器。它的边界清楚：Registry 管全局状态，Snapshot 管任务一致性，Runtime 管沙箱同步，SkillTool 管当前 Agent 的指令注入，Runner 管事件摘要，前端管上传管理和加载展示。后续如果要增强版本、权限、强制路由或资源专用工具，应在这些边界上扩展，而不是让 Skill 绕过现有 Agent 与 ToolPolicy 体系。

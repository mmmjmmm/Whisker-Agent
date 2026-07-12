import asyncio

from app.domain.models.event import (
    BaseEvent,
    DoneEvent,
    ErrorEvent,
    MessageEvent,
    TaskGraphEvent,
    TeamTaskEvent,
    TitleEvent,
)
from app.domain.models.file import File
from app.domain.models.memory import Memory
from app.domain.models.message import Message
from app.domain.models.team import (
    TaskGraph,
    TaskGraphStatus,
    TeamTaskStatus,
)
from app.domain.services.agents.task_worker import TaskWorker
from app.domain.services.agents.team_planner import TeamPlannerAgent
from app.domain.services.agents.team_synthesizer import TeamSynthesizerAgent
from app.domain.services.flows.base import BaseFlow
from app.domain.services.team.graph import build_task_graph
from app.domain.services.team.orchestrator import TeamOrchestrator
from app.domain.services.team.policy import ToolPolicy
from app.domain.services.tools.browser import BrowserTool
from app.domain.services.tools.file import FileTool
from app.domain.services.tools.search import SearchTool
from app.domain.services.tools.shell import ShellTool
from app.domain.services.tools.skill import SkillTool
from app.domain.services.skills.runtime import SkillRuntime


class QueuedEventEmitter:
    def __init__(self):
        self._queue: asyncio.Queue[BaseEvent | None] = asyncio.Queue()
        self._closed = False

    async def emit(
        self,
        event: BaseEvent,
    ) -> None:
        if self._closed:
            raise RuntimeError("event emitter is closed")
        await self._queue.put(event)

    async def get(self) -> BaseEvent | None:
        return await self._queue.get()

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            await self._queue.put(None)


class TeamFlow(BaseFlow):
    def __init__(
        self,
        *,
        team_max_tasks: int,
        planner,
        orchestrator,
        synthesizer_factory,
    ):
        self._team_max_tasks = team_max_tasks
        self._planner = planner
        self._orchestrator = orchestrator
        self._synthesizer_factory = synthesizer_factory
        self._graph: TaskGraph | None = None
        self._producer: asyncio.Task[TaskGraph] | None = None
        self._done = True

    @property
    def done(self) -> bool:
        return self._done

    async def invoke(self, message: Message):
        self._done = False
        validation_error = None
        for _ in range(2):
            try:
                planned = await self._planner.create_graph(
                    message,
                    validation_error,
                )
                for event in getattr(
                    self._planner,
                    "drain_skill_events",
                    lambda: [],
                )():
                    yield event
                self._graph = build_task_graph(
                    planned,
                    self._team_max_tasks,
                )
                break
            except ValueError as exc:
                for event in getattr(
                    self._planner,
                    "drain_skill_events",
                    lambda: [],
                )():
                    yield event
                validation_error = str(exc)
            except Exception as exc:
                for event in getattr(
                    self._planner,
                    "drain_skill_events",
                    lambda: [],
                )():
                    yield event
                self._done = True
                yield ErrorEvent(error=f"Team Planner 失败: {exc}")
                return
        else:
            self._done = True
            yield ErrorEvent(
                error=f"Team Planner 生成无效 DAG: {validation_error}"
            )
            return

        yield TitleEvent(title=self._graph.title)
        yield TaskGraphEvent(graph=self._graph.model_copy(deep=True))

        emitter = QueuedEventEmitter()

        async def produce() -> TaskGraph:
            try:
                return await self._orchestrator.run(
                    self._graph,
                    message.attachments,
                    emitter.emit,
                )
            finally:
                await emitter.close()

        self._producer = asyncio.create_task(produce())
        try:
            while True:
                event = await emitter.get()
                if event is None:
                    break
                yield event

            try:
                self._graph = await self._producer
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._graph.status = TaskGraphStatus.FAILED
                self._graph.error = f"scheduler_error: {exc}"
                self._done = True
                yield TaskGraphEvent(graph=self._graph.model_copy(deep=True))
                yield ErrorEvent(error=f"Team 调度失败: {exc}")
                return

            yield TaskGraphEvent(graph=self._graph.model_copy(deep=True))

            if self._graph.status in {
                TaskGraphStatus.COMPLETED,
                TaskGraphStatus.PARTIAL,
            }:
                last_error = None
                for _ in range(2):
                    synthesizer = self._synthesizer_factory()
                    try:
                        final = await synthesizer.synthesize(self._graph)
                        for event in getattr(
                            synthesizer,
                            "drain_skill_events",
                            lambda: [],
                        )():
                            yield event
                        yield MessageEvent(
                            role="assistant",
                            message=final.message,
                            attachments=[
                                File(filepath=path)
                                for path in final.attachments
                            ],
                        )
                        break
                    except Exception as exc:
                        for event in getattr(
                            synthesizer,
                            "drain_skill_events",
                            lambda: [],
                        )():
                            yield event
                        last_error = str(exc)
                else:
                    self._done = True
                    yield ErrorEvent(error=f"Team 汇总失败: {last_error}")
                    return
            elif self._graph.status is TaskGraphStatus.FAILED:
                self._done = True
                yield ErrorEvent(
                    error="\n".join(
                        ["Team 执行失败："]
                        + [
                            f"- {task.description}：{task.error}"
                            for task in self._graph.tasks
                            if task.error
                        ]
                    )
                )
                return

            self._done = True
            yield DoneEvent()
        finally:
            if self._producer is not None and not self._producer.done():
                self._producer.cancel()
                await asyncio.gather(
                    self._producer,
                    return_exceptions=True,
                )
            self._done = True

    async def cancel_events(self) -> list[BaseEvent]:
        if self._graph is None:
            self._done = True
            return []

        active_statuses = {
            TeamTaskStatus.PENDING,
            TeamTaskStatus.RUNNING,
            TeamTaskStatus.RETRYING,
            TeamTaskStatus.CANCELLED,
        }
        active_ids = {
            task.id
            for task in self._graph.tasks
            if task.status in active_statuses
        }
        if self._producer is not None and not self._producer.done():
            self._producer.cancel()
            await asyncio.gather(self._producer, return_exceptions=True)

        events: list[BaseEvent] = []
        for task in self._graph.tasks:
            if task.id not in active_ids:
                continue
            task.status = TeamTaskStatus.CANCELLED
            task.error = "cancelled_by_user"
            events.append(
                TeamTaskEvent(
                    graph_id=self._graph.id,
                    task=task.model_copy(deep=True),
                    agent_id=task.assigned_agent_id,
                    attempt=task.attempt_count,
                )
            )

        self._graph.status = TaskGraphStatus.CANCELLED
        self._graph.error = "cancelled_by_user"
        events.append(
            TaskGraphEvent(graph=self._graph.model_copy(deep=True))
        )
        self._done = True
        return events


def build_team_flow(
    *,
    uow_factory,
    session_id,
    agent_config,
    llm,
    json_parser,
    browser,
    sandbox,
    search_engine,
    mcp_tool,
    a2a_tool,
    skill_runtime: SkillRuntime,
) -> TeamFlow:
    """使用本轮共享基础设施构建一个短生命周期 TeamFlow。"""
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
    worker_config = agent_config.model_copy(
        update={
            "max_iterations": agent_config.team_max_worker_iterations,
        }
    )

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

    orchestrator = TeamOrchestrator(
        worker_factory=worker_factory,
        is_parallel_safe=policy.is_parallel_safe,
        max_workers=agent_config.team_max_workers,
        max_retries=agent_config.team_max_task_retries,
        timeout_seconds=agent_config.team_task_timeout_seconds,
    )

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

    return TeamFlow(
        team_max_tasks=agent_config.team_max_tasks,
        planner=planner,
        orchestrator=orchestrator,
        synthesizer_factory=synthesizer_factory,
    )

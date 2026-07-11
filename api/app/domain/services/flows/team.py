import asyncio
from collections.abc import Callable
from dataclasses import dataclass

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
from app.domain.models.session import SessionStatus
from app.domain.models.team import (
    TaskGraph,
    TaskGraphStatus,
    TeamTaskStatus,
)
from app.domain.repositories.uow import IUnitOfWork
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


@dataclass
class EventEnvelope:
    event: BaseEvent
    published: asyncio.Future[None]

    def confirm(self) -> None:
        if not self.published.done():
            self.published.set_result(None)

    def abort(self) -> None:
        if not self.published.done():
            self.published.cancel()


class QueuedEventEmitter:
    def __init__(self):
        self._queue: asyncio.Queue[EventEnvelope | None] = asyncio.Queue()
        self._closed = False

    async def emit(
        self,
        event: BaseEvent,
        wait_for_publish: bool = True,
    ) -> None:
        if self._closed:
            raise RuntimeError("event emitter is closed")
        future = asyncio.get_running_loop().create_future()
        await self._queue.put(EventEnvelope(event, future))
        if wait_for_publish:
            await future

    async def get(self) -> EventEnvelope | None:
        return await self._queue.get()

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            await self._queue.put(None)

    async def abort_pending(self) -> None:
        """关闭队列并取消尚未得到发布确认的信封。"""
        self._closed = True
        while True:
            try:
                envelope = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if envelope is not None:
                envelope.abort()


class TeamFlow(BaseFlow):
    def __init__(
        self,
        *,
        uow_factory: Callable[[], IUnitOfWork],
        session_id: str,
        team_max_tasks: int,
        planner,
        orchestrator,
        synthesizer_factory,
    ):
        self._uow = uow_factory()
        self._session_id = session_id
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
        async with self._uow:
            await self._uow.session.update_status(
                self._session_id,
                SessionStatus.RUNNING,
            )

        validation_error = None
        for _ in range(2):
            try:
                planned = await self._planner.create_graph(
                    message,
                    validation_error,
                )
                self._graph = build_task_graph(
                    planned,
                    self._team_max_tasks,
                )
                break
            except ValueError as exc:
                validation_error = str(exc)
            except Exception as exc:
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
                envelope = await emitter.get()
                if envelope is None:
                    break
                try:
                    yield envelope.event
                except BaseException:
                    envelope.abort()
                    raise
                else:
                    envelope.confirm()

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
                    try:
                        final = await self._synthesizer_factory().synthesize(
                            self._graph
                        )
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
                        last_error = str(exc)
                else:
                    self._done = True
                    yield ErrorEvent(error=f"Team 汇总失败: {last_error}")
                    return
            elif self._graph.status is TaskGraphStatus.FAILED:
                self._done = True
                yield ErrorEvent(
                    error=self._graph.error or "所有 Team Task 均失败"
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
            await emitter.abort_pending()
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
    planner = TeamPlannerAgent(
        uow_factory=uow_factory,
        session_id=session_id,
        agent_config=agent_config,
        llm=llm,
        json_parser=json_parser,
        tools=[],
        memory=Memory(),
        persist_memory=False,
        allowed_tool_names=frozenset(),
    )
    worker_config = agent_config.model_copy(
        update={
            "max_iterations": agent_config.team_max_worker_iterations,
        }
    )

    def worker_factory(graph_id, agent_id, task, attempt):
        return TaskWorker(
            uow_factory=uow_factory,
            session_id=session_id,
            agent_config=worker_config,
            llm=llm,
            json_parser=json_parser,
            tools=policy.tools_for(task.capability),
            memory=Memory(),
            persist_memory=False,
            allowed_tool_names=policy.allowed_names(task.capability),
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
            tools=[],
            memory=Memory(),
            persist_memory=False,
            allowed_tool_names=frozenset(),
        )

    return TeamFlow(
        uow_factory=uow_factory,
        session_id=session_id,
        team_max_tasks=agent_config.team_max_tasks,
        planner=planner,
        orchestrator=orchestrator,
        synthesizer_factory=synthesizer_factory,
    )

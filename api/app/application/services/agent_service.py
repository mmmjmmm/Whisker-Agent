#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/05/04 17:12
@Author  : thezehui@gmail.com
@File    : agent_service.py
"""
import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import AsyncGenerator, Optional, List, Type, Callable

from pydantic import TypeAdapter

from app.application.errors.exceptions import (
    ResearchTeamDisabledError,
    RunAlreadyActiveError,
)
from app.domain.external.file_storage import FileStorage
from app.domain.external.json_parser import JSONParser
from app.domain.external.llm import LLM
from app.domain.external.sandbox import Sandbox
from app.domain.external.search import SearchEngine
from app.domain.external.task import Task
from app.domain.models.app_config import AgentConfig, MCPConfig, A2AConfig
from app.domain.models.agent_run import AgentMode, AgentRun, RunStatus
from app.domain.models.event import BaseEvent, ErrorEvent, MessageEvent, Event, DoneEvent, WaitEvent
from app.domain.models.run_command import StartRunCommand
from app.domain.models.session import Session, SessionStatus
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.agent_task_runner import AgentTaskRunner
from app.domain.services.flows.base import BaseFlow

logger = logging.getLogger(__name__)


@dataclass
class PreparedChat:
    session_id: str
    command: StartRunCommand | None
    task: Task | None
    initial_events: list[BaseEvent]
    latest_event_id: str | None
    created_task: bool


class AgentService:
    """Manus智能体服务"""

    def __init__(
            self,
            uow_factory: Callable[[], IUnitOfWork],
            llm: LLM,
            agent_config: AgentConfig,
            mcp_config: MCPConfig,
            a2a_config: A2AConfig,
            sandbox_cls: Type[Sandbox],
            task_cls: Type[Task],
            json_parser: JSONParser,
            search_engine: SearchEngine,
            file_storage: FileStorage,
            research_team_enabled: bool = False,
            research_flow_factory: Callable[[str], BaseFlow] | None = None,
    ) -> None:
        """构造函数，完成Agent服务初始化"""
        self._uow_factory = uow_factory
        self._uow = uow_factory()
        self._llm = llm
        self._agent_config = agent_config
        self._mcp_config = mcp_config
        self._a2a_config = a2a_config
        self._sandbox_cls = sandbox_cls
        self._task_cls = task_cls
        self._json_parser = json_parser
        self._search_engine = search_engine
        self._file_storage = file_storage
        self._research_team_enabled = research_team_enabled
        self._research_flow_factory = research_flow_factory
        logger.info(f"AgentService初始化成功")

    async def _get_task(self, session: Session) -> Optional[Task]:
        """根据传递的任务会话获取任务实例"""
        # 1.从会话中取出任务id
        task_id = session.task_id
        if not task_id:
            return None

        # 2.调用人物类的get方法获取对应的任务实例
        return self._task_cls.get(task_id)

    async def _create_task(
            self,
            session: Session,
            mode: AgentMode,
    ) -> Task:
        """根据传递的会话创建一个新任务"""
        sandbox = None
        browser = None
        if mode == AgentMode.REACT:
            sandbox_id = session.sandbox_id
            if sandbox_id:
                sandbox = await self._sandbox_cls.get(sandbox_id)
            if not sandbox:
                sandbox = await self._sandbox_cls.create()
                session.sandbox_id = sandbox.id
                async with self._uow:
                    await self._uow.session.save(session)
            browser = await sandbox.get_browser()
            if not browser:
                logger.error(f"获取沙箱[{sandbox.id}]中的浏览器实例失败")
                raise RuntimeError(f"获取沙箱[{sandbox.id}]中的浏览器实例失败")
        elif self._research_flow_factory is None:
            raise RuntimeError("ResearchTeamFlow 尚未配置")

        research_flow_factory = (
            (lambda: self._research_flow_factory(session.id))
            if self._research_flow_factory is not None
            else None
        )
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
            research_flow_factory=research_flow_factory,
        )

        task = self._task_cls.create(task_runner=task_runner)
        session.task_id = task.id
        async with self._uow:
            await self._uow.session.save(session)

        return task

    async def _safe_update_unread_count(self, session_id: str) -> None:
        """在独立的后台任务中安全地更新未读消息计数

        该方法通过asyncio.create_task()调用，运行在一个全新的asyncio Task中，
        因此不受sse_starlette的anyio cancel scope影响，数据库操作可以正常完成。
        使用uow_factory创建全新的UoW实例，避免与被取消的上下文共享数据库连接。
        """
        try:
            uow = self._uow_factory()
            async with uow:
                await uow.session.update_unread_message_count(session_id, 0)
        except Exception as e:
            logger.warning(f"会话[{session_id}]后台更新未读消息计数失败: {e}")

    async def prepare_chat(
            self,
            session_id: str,
            message: str | None = None,
            attachments: list[str] | None = None,
            latest_event_id: str | None = None,
            timestamp: datetime | None = None,
            mode: AgentMode = AgentMode.REACT,
            budget_profile: str = "default",
    ) -> PreparedChat:
        del budget_profile
        resolved_mode = mode if isinstance(mode, AgentMode) else AgentMode(mode)
        attachment_ids = list(attachments or [])

        async with self._uow:
            session = await self._uow.session.get_by_id(session_id)
            active_run = await self._uow.agent_run.get_active_by_session(
                session_id,
            )
        if session is None:
            raise RuntimeError("任务会话不存在, 请核实后重试")

        task = await self._get_task(session)
        if message is None:
            return PreparedChat(
                session_id=session_id,
                command=None,
                task=task,
                initial_events=[],
                latest_event_id=latest_event_id,
                created_task=False,
            )

        if active_run is not None:
            raise RunAlreadyActiveError(
                active_run.id,
                active_run.status.value,
            )
        if (
            resolved_mode == AgentMode.RESEARCH_TEAM
            and not self._research_team_enabled
        ):
            raise ResearchTeamDisabledError()
        if (
            resolved_mode == AgentMode.RESEARCH_TEAM
            and session.status in {SessionStatus.RUNNING, SessionStatus.WAITING}
        ):
            raise RunAlreadyActiveError(
                session.task_id or session.id,
                session.status.value,
            )

        created_task = False
        if session.status != SessionStatus.RUNNING or task is None:
            task = await self._create_task(session, resolved_mode)
            created_task = True
        if task is None:
            raise RuntimeError(f"会话[{session_id}]创建任务失败")

        command = StartRunCommand(
            run_id=str(uuid.uuid4()),
            session_id=session_id,
            mode=resolved_mode,
            message=message,
            attachment_ids=attachment_ids,
        )
        if resolved_mode == AgentMode.RESEARCH_TEAM:
            async with self._uow:
                await self._uow.agent_run.add(AgentRun(
                    id=command.run_id,
                    session_id=session_id,
                    mode=resolved_mode,
                    status=RunStatus.PENDING,
                    goal=message,
                    budget_snapshot=command.budget,
                ))

        async with self._uow:
            await self._uow.session.update_latest_message(
                session_id=session_id,
                message=message,
                timestamp=timestamp or datetime.now(),
            )
            db_attachments = [
                await self._uow.file.get_by_id(file_id)
                for file_id in attachment_ids
            ]

        message_event = MessageEvent(
            session_id=session_id,
            run_id=command.run_id,
            role="user",
            message=message,
            attachments=[
                attachment
                for attachment in db_attachments
                if attachment is not None
            ],
        )
        event_id = await task.input_stream.put(command.model_dump_json())
        message_event.id = event_id
        async with self._uow:
            await self._uow.session.add_event(session_id, message_event)
        await task.invoke()

        return PreparedChat(
            session_id=session_id,
            command=command,
            task=task,
            initial_events=[message_event],
            latest_event_id=latest_event_id,
            created_task=created_task,
        )

    async def stream_prepared_chat(
            self,
            prepared: PreparedChat,
    ) -> AsyncGenerator[BaseEvent, None]:
        session_id = prepared.session_id
        latest_event_id = prepared.latest_event_id
        try:
            for event in prepared.initial_events:
                yield event

            task = prepared.task
            while task and not task.done:
                event_id, event_str = await task.output_stream.get(
                    start_id=latest_event_id,
                    block_ms=0,
                )
                latest_event_id = event_id
                if event_str is None:
                    continue
                event = TypeAdapter(Event).validate_json(event_str)
                event.id = event_id
                async with self._uow:
                    await self._uow.session.update_unread_message_count(
                        session_id,
                        0,
                    )
                yield event
                if isinstance(event, (DoneEvent, WaitEvent)):
                    break
                if isinstance(event, ErrorEvent) and event.task_id is None:
                    break
        finally:
            try:
                asyncio.create_task(self._safe_update_unread_count(session_id))
            except RuntimeError:
                logger.warning(
                    "会话[%s]无法创建后台任务更新未读消息计数",
                    session_id,
                )

    async def chat(
            self,
            session_id: str,
            message: Optional[str] = None,
            attachments: Optional[List[str]] = None,
            latest_event_id: Optional[str] = None,
            timestamp: Optional[datetime] = None,
            mode: AgentMode = AgentMode.REACT,
            budget_profile: str = "default",
    ) -> AsyncGenerator[BaseEvent, None]:
        """兼容旧调用方的聊天流入口。"""
        try:
            prepared = await self.prepare_chat(
                session_id=session_id,
                message=message,
                attachments=attachments,
                latest_event_id=latest_event_id,
                timestamp=timestamp,
                mode=mode,
                budget_profile=budget_profile,
            )
            async for event in self.stream_prepared_chat(prepared):
                yield event
        except Exception as exc:
            logger.error("任务会话[%s]对话出错: %s", session_id, exc)
            event = ErrorEvent(session_id=session_id, error=str(exc))
            try:
                async with self._uow:
                    await self._uow.session.add_event(session_id, event)
            except (asyncio.CancelledError, Exception) as add_err:
                logger.warning(
                    "会话[%s]添加错误事件失败: %s",
                    session_id,
                    add_err,
                )
            yield event

    async def stop_session(self, session_id: str) -> None:
        """根据传递的会话id停止指定会话"""
        # 1.查找会话是否存在
        async with self._uow:
            session = await self._uow.session.get_by_id(session_id)
        if not session:
            logger.error(f"尝试停止不存在的会话[{session_id}]")
            raise RuntimeError("任务会话不存在, 请核实后重试")

        # 2.根据会话获取任务信息
        task = await self._get_task(session)
        if task:
            task.cancel()

        # 3.更新会话任务状态
        async with self._uow:
            await self._uow.session.update_status(session_id, SessionStatus.COMPLETED)

    async def shutdown(self) -> None:
        """关闭Agent服务"""
        logger.info("正在清除所有会话任务资源并释放")
        await self._task_cls.destroy()
        logger.info("所有会话任务资源清除成功")

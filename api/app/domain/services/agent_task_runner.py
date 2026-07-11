#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/05/22 0:52
@Author  : thezehui@gmail.com
@File    : agent_task_runner.py
"""
import asyncio
import io
import logging
import uuid
from typing import List, AsyncGenerator, Callable, BinaryIO

from fastapi import UploadFile
from pydantic import TypeAdapter, ValidationError

from app.domain.external.browser import Browser
from app.domain.external.file_storage import FileStorage
from app.domain.external.json_parser import JSONParser
from app.domain.external.llm import LLM
from app.domain.external.sandbox import Sandbox
from app.domain.external.search import SearchEngine
from app.domain.external.task import TaskRunner, Task
from app.domain.models.app_config import AgentConfig, MCPConfig, A2AConfig
from app.domain.models.agent_run import AgentMode
from app.domain.models.event import ErrorEvent, Event, MessageEvent, BaseEvent, ToolEvent, ToolEventStatus, \
    BrowserToolContent, SearchToolContent, ShellToolContent, FileToolContent, MCPToolContent, A2AToolContent, \
    TitleEvent, WaitEvent, DoneEvent
from app.domain.models.file import File
from app.domain.models.message import Message
from app.domain.models.run_command import (
    CancelRunCommand,
    RunCommand,
    StartRunCommand,
)
from app.domain.models.search import SearchResults
from app.domain.models.session import SessionStatus
from app.domain.models.tool_result import ToolResult
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.flows.base import BaseFlow, FlowRequest
from app.domain.services.flows.flow_router import FlowRouter
from app.domain.services.flows.planner_react import PlannerReActFlow
from app.domain.services.tools.a2a import A2ATool
from app.domain.services.tools.mcp import MCPTool
from app.infrastructure.storage.oss import get_oss

logger = logging.getLogger(__name__)

RUN_COMMAND_ADAPTER = TypeAdapter(RunCommand)
EVENT_ADAPTER = TypeAdapter(Event)


class AgentTaskRunner(TaskRunner):
    """基于Agent智能体的任务运行器"""

    def __init__(
            self,
            uow_factory: Callable[[], IUnitOfWork],  # uow模块
            llm: LLM,  # 大语言模型
            agent_config: AgentConfig,  # 智能体配置
            mcp_config: MCPConfig,  # mcp配置
            a2a_config: A2AConfig,  # a2a配置
            session_id: str,  # 会话id
            file_storage: FileStorage,  # 文件存储桶
            json_parser: JSONParser,  # json解析器
            browser: Browser | None,  # 浏览器
            search_engine: SearchEngine,  # 搜索引擎
            sandbox: Sandbox | None,  # 沙箱
            flow_router: FlowRouter | None = None,
            research_flow_factory: Callable[[], BaseFlow] | None = None,
            mcp_tool: MCPTool | None = None,
            a2a_tool: A2ATool | None = None,
    ) -> None:
        """构造函数，完成Agent任务运行器的创建"""
        self._uow_factory = uow_factory
        self._uow = uow_factory()
        self._session_id = session_id
        self._sandbox = sandbox
        self._mcp_config = mcp_config
        self._mcp_tool = mcp_tool or MCPTool()
        self._a2a_config = a2a_config
        self._a2a_tool = a2a_tool or A2ATool()
        self._file_storage = file_storage
        self._browser = browser
        self._flows: dict[AgentMode, BaseFlow] = {}
        self._sandbox_initialized = False
        self._mcp_initialized = False
        self._a2a_initialized = False

        def create_react_flow() -> BaseFlow:
            if self._browser is None or self._sandbox is None:
                raise RuntimeError("React Flow 需要 Browser 和 Sandbox")
            return PlannerReActFlow(
                uow_factory=uow_factory,
                llm=llm,
                agent_config=agent_config,
                session_id=session_id,
                json_parser=json_parser,
                browser=self._browser,
                sandbox=self._sandbox,
                search_engine=search_engine,
                mcp_tool=self._mcp_tool,
                a2a_tool=self._a2a_tool,
            )

        def research_not_configured() -> BaseFlow:
            raise RuntimeError("ResearchTeamFlow 尚未配置")

        self._flow_router = flow_router or FlowRouter(
            react_factory=create_react_flow,
            research_factory=research_flow_factory or research_not_configured,
        )

    async def _put_and_add_event(self, task: Task, event: Event) -> None:
        """往指定任务的消息队列中添加事件"""
        # 1.往任务的输出消息队列中新增事件
        event_id = await task.output_stream.put(event.model_dump_json())
        event.id = event_id

        # 2.将事件添加到对应的会话中
        async with self._uow:
            await self._uow.session.add_event(self._session_id, event)

    async def _pop_command(
            self,
            task: Task,
    ) -> tuple[StartRunCommand | CancelRunCommand | None, MessageEvent | None]:
        """优先读取 RunCommand，并兼容历史 MessageEvent 输入。"""
        event_id, event_str = await task.input_stream.pop()
        if event_str is None:
            logger.warning("AgentTaskRunner接收到空消息")
            return None, None

        try:
            command = RUN_COMMAND_ADAPTER.validate_json(event_str)
        except ValidationError:
            event = EVENT_ADAPTER.validate_json(event_str)
            event.id = event_id
            if not isinstance(event, MessageEvent):
                raise ValueError(f"不支持的历史输入事件: {event.type}")
            if not event.message:
                return None, event
            command = StartRunCommand(
                run_id=str(uuid.uuid4()),
                session_id=self._session_id,
                mode=AgentMode.REACT,
                message=event.message,
                attachment_ids=[attachment.id for attachment in event.attachments],
            )
            return command, event

        if command.session_id != self._session_id:
            raise ValueError("命令的 session_id 与 Runner 不匹配")
        return command, None

    async def _sync_file_to_sandbox(self, file_id: str) -> File:
        """根据文件id将文件同步到沙箱中"""
        try:
            # 1.调用文件存储下载文件信息
            file_data, file = await self._file_storage.download_file(file_id)

            # 2.组装沙箱文件路径
            filepath = f"/home/ubuntu/upload/{file.filename}"

            # 3.调用沙箱将文件上传至沙箱
            tool_result = await self._sandbox.upload_file(
                file_data=file_data,
                filepath=filepath,
                filename=file.filename
            )

            # 4.判断是否上传成功
            if tool_result.success:
                file.filepath = filepath
                async with self._uow:
                    await self._uow.file.save(file)  # 可以更新也可以不更新
                return file
        except Exception as e:
            logger.exception(f"AgentTaskRunner同步文件[{file_id}]失败: {str(e)}")

    async def _sync_message_attachments_to_sandbox(self, event: MessageEvent) -> None:
        """将消息事件中的附件同步到沙箱中"""
        # 1.定义附件列表
        attachments: List[str] = []

        try:
            # 2.判断消息中是否存在附件
            if event.attachments:
                # 3.循环遍历所有的消息附件
                for attachment in event.attachments:
                    # 4.根据同步文件的id将数据同步到沙箱中
                    file = await self._sync_file_to_sandbox(attachment.id)

                    # 5.文件是否同步成功
                    if file:
                        attachments.append(file)
                        async with self._uow:
                            await self._uow.session.add_file(self._session_id, file)

            # 6.更新消息事件中的attachments
            event.attachments = attachments
        except Exception as e:
            logger.exception(f"AgentTaskRunner同步消息附件到沙箱失败: {str(e)}")

    @classmethod
    def _get_stream_size(cls, f: BinaryIO) -> int:
        """根据传递的文件流，获取计算文件的大小"""
        # 1.记录当前文件指针位置
        current_pos = f.tell()

        # 2.将指针移动到文件末尾, seek，0: 偏移量、2: 相对文件末尾
        f.seek(0, 2)

        # 3.获取当前位置，也就是文件大小
        size = f.tell()

        # 4.恢复指针到原始位置
        f.seek(current_pos)

        return size

    async def _sync_file_to_storage(self, filepath: str) -> File:
        """将沙箱中指定的文件路径数据同步到存储桶中"""
        try:
            # 1.根据文件路径从会话中查找文件数据
            async with self._uow:
                file = await self._uow.session.get_file_by_path(self._session_id, filepath)

            # 2.从沙箱中下载文件
            file_data = await self._sandbox.download_file(filepath)

            # 3.判断会话中的文件是否存在
            if file:
                async with self._uow:
                    await self._uow.session.remove_file(self._session_id, file.filepath)

            # 4.提取文件名字、文件信息并更新文件路径
            filename = filepath.split("/")[-1]
            upload_file = UploadFile(
                file=file_data,
                filename=filename,
                size=self._get_stream_size(file_data),
            )

            # 5.上传文件到文件存储桶
            file = await self._file_storage.upload_file(upload_file)
            file.filepath = filepath

            # 6.往会话中新增一个文件信息
            async with self._uow:
                await self._uow.session.add_file(self._session_id, file)
            return file
        except Exception as e:
            logger.exception(f"AgentTaskRunner同步消息附件到文件存储桶失败: {str(e)}")

    async def _sync_message_attachments_to_storage(self, event: MessageEvent) -> None:
        """将消息事件的附件同步到文件存储桶中"""
        # 1.定义附件列表存储数据
        attachments: List[File] = []

        try:
            # 2.判断消息中是否存在附件
            if event.attachments:
                # 3.循环遍历所有附件
                for attachment in event.attachments:
                    # 4.根据文件路径将数据同步到文件存储桶
                    file = await self._sync_file_to_storage(attachment.filepath)
                    if file:
                        attachments.append(file)

            # 5.更新时间中的附件列表资源
            event.attachments = attachments
        except Exception as e:
            logger.exception(f"AgentTaskRunner同步消息附件到存储桶失败: {str(e)}")

    async def _get_browser_screenshot(self) -> str:
        """获取浏览器截图并返回截图文件对应的在线URL"""
        # 1.调用浏览器完成截图
        screenshot = await self._browser.screenshot()

        # 2.将浏览器截图上传到文件存储中
        file = await self._file_storage.upload_file(UploadFile(
            file=io.BytesIO(screenshot),
            filename=f"{str(uuid.uuid4())}.png",
            # bugfix:添加size尺寸
            size=self._get_stream_size(io.BytesIO(screenshot)),
        ))

        # 3.根据 OSS 配置组装完整公开 URL
        return get_oss().public_url(file.key)

    async def _handle_tool_event(self, event: ToolEvent) -> None:
        """额外处理工具消息，使其前端交互更友好"""
        try:
            # 1.如果事件状态为已调用则执行以下代码
            if event.status == ToolEventStatus.CALLED:
                # 2.工具为浏览器则补全工具浏览器工具内容
                if event.tool_name == "browser":
                    event.tool_content = BrowserToolContent(
                        screenshot=await self._get_browser_screenshot(),
                    )
                elif event.tool_name == "search":
                    # 3.工具为搜索则添加搜索工具内容
                    search_results: ToolResult[SearchResults] = event.function_result
                    logger.info(f"搜索工具结果: {search_results}")
                    event.tool_content = SearchToolContent(results=search_results.data.results)
                elif event.tool_name == "shell":
                    # 4.工具为shell则生成shell工具内容
                    if "session_id" in event.function_args:
                        shell_result = await self._sandbox.read_shell_output(
                            event.function_args["session_id"],
                            console=True,
                        )
                        event.tool_content = ShellToolContent(
                            console=(shell_result.data or {}).get("console_records", [])
                        )
                    else:
                        event.tool_content = ShellToolContent(console="(No console)")
                elif event.tool_name == "file":
                    # 5.工具为file则将文件同步到对象存储
                    if "filepath" in event.function_args:
                        filepath = event.function_args["filepath"]
                        file_read_result = await self._sandbox.read_file(filepath)
                        file_content: str = (file_read_result.data or {}).get("content", "")
                        event.tool_content = FileToolContent(content=file_content)
                        # bugfix:修改为同步文件到storage
                        await self._sync_file_to_storage(filepath)
                    else:
                        event.tool_content = FileToolContent(content="(No Content)")
                elif event.tool_name in ["mcp", "a2a"]:
                    # 6.工具为mcp/a2a则处理调用结果
                    logger.info(f"处理MCP/A2A工具事件, function_result: {event.function_result}")
                    if event.function_result:
                        # 7.如果结果包含data则提取data
                        if hasattr(event.function_result, "data") and event.function_result.data:
                            logger.info(f"MCP/A2A工具调用结果: {event.function_result.data}")
                            event.tool_content = MCPToolContent(result=event.function_result.data) \
                                if event.tool_name == "mcp" \
                                else A2AToolContent(a2a_result=event.function_result.data)
                        elif hasattr(event.function_result, "success") and event.function_result.success:
                            # 8.mcp/a2a工具调用正常，但是无结果产生
                            logger.info(f"MCP/A2A工具调用成功返回，但无结果: {event.function_result}")
                            result_data = event.function_result.model_dump() \
                                if hasattr(event.function_result, "model_dump") \
                                else str(event.function_result)
                            event.tool_content = MCPToolContent(result=result_data) \
                                if event.tool_name == "mcp" \
                                else A2AToolContent(a2a_result=result_data)
                        else:
                            # 9.其他情况将结果转换成字符串进行传递
                            logger.info(f"MCP/A2A工具额记过: {event.function_result}")
                            event.tool_content = MCPToolContent(result=str(event.function_result)) \
                                if event.tool_name == "mcp" \
                                else A2AToolContent(a2a_result=str(event.function_result))
                    else:
                        logger.warning("MCP/A2A工具调用结果未发现")
                        event.tool_content = MCPToolContent(result="(MCP工具无可用结果)") \
                            if event.tool_name == "mcp" \
                            else A2AToolContent(a2a_result="(A2A智能体无可用结果)")
        except Exception as e:
            logger.exception(f"AgentTaskRunner生成工具内容失败: {str(e)}")

    async def _ensure_flow_resources(self, mode: AgentMode) -> None:
        requirements = self._flow_router.requirements_for(mode)
        if requirements.sandbox and not self._sandbox_initialized:
            if self._sandbox is None:
                raise RuntimeError("React Flow 需要 Sandbox")
            await self._sandbox.ensure_sandbox()
            self._sandbox_initialized = True
        if requirements.browser and self._browser is None:
            raise RuntimeError("React Flow 需要 Browser")
        if requirements.mcp and not self._mcp_initialized:
            await self._mcp_tool.initialize(self._mcp_config)
            self._mcp_initialized = True
        if requirements.a2a and not self._a2a_initialized:
            await self._a2a_tool.initialize(self._a2a_config)
            self._a2a_initialized = True

    async def _build_flow_request(
            self,
            command: StartRunCommand,
            legacy_event: MessageEvent | None,
    ) -> FlowRequest:
        attachments: list[str] = []
        if command.mode == AgentMode.REACT:
            if legacy_event is not None:
                await self._sync_message_attachments_to_sandbox(legacy_event)
                attachments = [
                    attachment.filepath
                    for attachment in legacy_event.attachments
                    if attachment.filepath
                ]
            else:
                for file_id in command.attachment_ids:
                    file = await self._sync_file_to_sandbox(file_id)
                    if file is not None and file.filepath:
                        attachments.append(file.filepath)
                        async with self._uow:
                            await self._uow.session.add_file(
                                self._session_id,
                                file,
                            )
        else:
            attachments = list(command.attachment_ids)

        return FlowRequest(
            command=command,
            message=Message(
                message=command.message,
                attachments=attachments,
            ),
        )

    def _get_flow(self, mode: AgentMode) -> BaseFlow:
        flow = self._flows.get(mode)
        if flow is None:
            flow = self._flow_router.create(mode)
            self._flows[mode] = flow
        return flow

    async def _run_flow(
            self,
            request: FlowRequest,
    ) -> AsyncGenerator[BaseEvent, None]:
        if not request.message.message:
            logger.warning("AgentTaskRunner接收了一条空消息")
            yield ErrorEvent(error="空消息错误")
            return

        flow = self._get_flow(request.command.mode)
        async for event in flow.invoke(request):
            if (
                request.command.mode == AgentMode.REACT
                and isinstance(event, ToolEvent)
            ):
                await self._handle_tool_event(event)
            elif (
                request.command.mode == AgentMode.REACT
                and isinstance(event, MessageEvent)
            ):
                await self._sync_message_attachments_to_storage(event)
            yield event

    async def _cleanup_tools(self) -> None:
        """清理MCP和A2A工具资源，确保在同一任务上下文中释放

        注意：该方法必须在初始化MCP/A2A的同一个asyncio Task中调用，
        否则anyio的cancel scope会检测到任务上下文切换并抛出RuntimeError。
        """
        try:
            if self._mcp_initialized:
                await self._mcp_tool.cleanup()
                self._mcp_initialized = False
        except Exception as e:
            logger.warning(f"清理MCP工具资源时出错: {e}")
        try:
            if self._a2a_initialized:
                await self._a2a_tool.manager.cleanup()
                self._a2a_initialized = False
        except Exception as e:
            logger.warning(f"清理A2A工具资源时出错: {e}")

    async def _project_session_event(self, event: BaseEvent) -> bool:
        if isinstance(event, TitleEvent):
            async with self._uow:
                await self._uow.session.update_title(
                    self._session_id,
                    event.title,
                )
        elif isinstance(event, MessageEvent):
            async with self._uow:
                await self._uow.session.update_latest_message(
                    self._session_id,
                    event.message,
                    event.created_at,
                )
                await self._uow.session.increment_unread_message_count(
                    self._session_id,
                )
        elif isinstance(event, WaitEvent):
            async with self._uow:
                await self._uow.session.update_status(
                    self._session_id,
                    SessionStatus.WAITING,
                )
            return True
        return False

    async def invoke(self, task: Task) -> None:
        """根据传递的任务处理agent消息队列并运行agent流"""
        done_published = False
        try:
            logger.info("AgentTaskRunner任务处理开始")
            while not await task.input_stream.is_empty():
                command, legacy_event = await self._pop_command(task)
                if command is None:
                    await self._put_and_add_event(
                        task,
                        ErrorEvent(
                            session_id=self._session_id,
                            error="空消息错误",
                        ),
                    )
                    continue
                if isinstance(command, CancelRunCommand):
                    if not done_published:
                        await self._put_and_add_event(
                            task,
                            DoneEvent(session_id=self._session_id),
                        )
                        done_published = True
                    break

                logger.info(
                    "AgentTaskRunner接收到%s模式消息: %s...",
                    command.mode.value,
                    command.message[:50],
                )
                await self._ensure_flow_resources(command.mode)
                request = await self._build_flow_request(
                    command,
                    legacy_event,
                )

                async for event in self._run_flow(request):
                    await self._put_and_add_event(task, event)
                    if isinstance(event, DoneEvent):
                        done_published = True
                    if await self._project_session_event(event):
                        return
                    if (
                        command.mode == AgentMode.REACT
                        and not await task.input_stream.is_empty()
                    ):
                        break

            async with self._uow:
                await self._uow.session.update_status(
                    self._session_id,
                    SessionStatus.COMPLETED,
                )
        except asyncio.CancelledError:
            logger.info("AgentTaskRunner任务运行取消")
            if not done_published:
                await self._put_and_add_event(
                    task,
                    DoneEvent(session_id=self._session_id),
                )
                done_published = True
            async with self._uow:
                await self._uow.session.update_status(
                    self._session_id,
                    SessionStatus.COMPLETED,
                )
            raise
        except Exception as e:
            logger.exception(f"AgentTaskRunner运行出错: {str(e)}")
            await self._put_and_add_event(
                task,
                ErrorEvent(
                    session_id=self._session_id,
                    error=f"AgentTaskRunner出错: {str(e)}",
                ),
            )
            if not done_published:
                await self._put_and_add_event(
                    task,
                    DoneEvent(session_id=self._session_id),
                )
                done_published = True
            async with self._uow:
                await self._uow.session.update_status(
                    self._session_id,
                    SessionStatus.COMPLETED,
                )
        finally:
            await self._cleanup_tools()

    async def destroy(self) -> None:
        """销毁任务运行器并释放资源"""
        # 1.清除沙箱
        logger.info(f"开始清除销毁AgentTaskRunner资源")
        if self._sandbox:
            logger.info("销毁AgentTaskRunner中的沙箱环境")
            await self._sandbox.destroy()

        # 2.清除mcp和a2a工具（幂等操作，如果invoke()中已清理则不会重复执行）
        await self._cleanup_tools()

    async def on_done(self, task: Task) -> None:
        """任务结束时执行的回调函数"""
        logger.info(f"AgentTaskRunner任务执行结束")

#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
import uuid
from typing import AsyncGenerator

from app.domain.models.event import (
    StepEventStatus,
    StepEvent,
    ToolEvent,
    MessageEvent,
    ErrorEvent,
    ToolEventStatus,
    WaitEvent,
    BaseEvent,
    MessageDeltaEvent,
)
from app.domain.models.file import File
from app.domain.models.message import Message
from app.domain.models.plan import Plan, Step, ExecutionStatus
from app.domain.models.trace import TraceSpanStatus
from app.domain.services.prompts.react import (
    REACT_SYSTEM_PROMPT,
    EXECUTION_PROMPT,
    SUMMARIZE_PROMPT,
    SUMMARIZE_TEXT_PROMPT,
)
from app.domain.services.prompts.system import SYSTEM_PROMPT
from .base import BaseAgent

logger = logging.getLogger(__name__)


class ReActAgent(BaseAgent):
    """基于ReAct架构的执行Agent"""
    name: str = "react"
    _system_prompt: str = SYSTEM_PROMPT + REACT_SYSTEM_PROMPT
    _format: str = "json_object"  # format控制的是content、工具调用控制的是tool_calls两者不冲突

    async def execute_step(self, plan: Plan, step: Step, message: Message) -> AsyncGenerator[BaseEvent, None]:
        """根据传递的消息+规划+子步骤，执行相应的子步骤"""
        """对 Agent 每一步执行过程中的每一个工具调用及输出结果进行处理"""
        async with self._trace_agent_operation(
                name="react.execute_step",
                operation="execute_step",
                input={
                    "message": message.message,
                    "attachments": message.attachments,
                    "step": step.model_dump(mode="json"),
                },
                attributes={
                    "plan_id": plan.id,
                    "step_id": step.id,
                },
        ) as trace_scope:
            # 1.根据传递的内容生成执行消息
            query = EXECUTION_PROMPT.format(
                message=message.message,
                attachments="\n".join(message.attachments),
                language=plan.language,
                step=step.description,
            )

            # 2.更新步骤的执行状态为运行中并返回Step事件
            step.status = ExecutionStatus.RUNNING
            # 给上层流程 / 前端”事件通知“
            yield StepEvent(step=step, status=StepEventStatus.STARTED)

            # 3.调用invoke获取agent返回的事件内容
            # invoke 会 yield 事件信息，获取这些信息事件（工具调用中/已调用、最终文本、Error...）
            async for event in self.invoke(query):
                # 4.判断事件类型执行不同操作
                if isinstance(event, ToolEvent):
                    # 5.工具事件需要判断工具的名称是否为message_ask_user
                    if event.function_name == "message_ask_user":
                        # 6.工具如果在调用中，我们需要返回一条消息告知用户需要让用户处理什么
                        # calling 时，讲参数中的 text（要问用户的问题）包成一个 Message 给用户看
                        if event.status == ToolEventStatus.CALLING:
                            yield MessageEvent(
                                role="assistant",
                                message=event.function_args.get("text", "")
                            )
                        elif event.status == ToolEventStatus.CALLED:
                            # 7.如果工具事件为已调用，则需要返回等待事件并中断程序
                            if trace_scope is not None:
                                trace_scope.finish(
                                    status=TraceSpanStatus.WAITING,
                                    output={
                                        "step_id": step.id,
                                        "status": "waiting",
                                    },
                                )
                            yield WaitEvent()
                            return
                        continue
                elif isinstance(event, MessageEvent):
                    # 8.返回消息事件，意味着content有内容，content有内容则代表执行Agent已运行完毕
                    step.status = ExecutionStatus.COMPLETED

                    # 9.message中输出的数据结构为json，需要提取并解析
                    parsed_obj = await self._json_parser.invoke(event.message)
                    new_step = Step.model_validate(parsed_obj)

                    # 10.更新子步骤的数据
                    step.success = new_step.success
                    step.result = new_step.result
                    step.attachments = new_step.attachments
                    if trace_scope is not None:
                        trace_scope.finish(output=step.model_dump(mode="json"))

                    # 11.返回步骤完成事件
                    yield StepEvent(step=step, status=StepEventStatus.COMPLETED)

                    # 12.如果子步骤拿到了结果，还需要返回一段消息给用户(将结果返回给用户)
                    if step.result:
                        yield MessageEvent(role="assistant", message=step.result)
                    continue
                elif isinstance(event, ErrorEvent):
                    # 13.错误事件更新步骤的状态
                    step.status = ExecutionStatus.FAILED
                    step.error = event.error
                    if trace_scope is not None:
                        trace_scope.finish(
                            output=step.model_dump(mode="json"),
                            error={"message": event.error},
                        )

                    # 14.返回子步骤对应事件
                    yield StepEvent(step=step, status=StepEventStatus.FAILED)

                # 15.其他场景将事件直接返回
                yield event

            # 16.循环迭代完成后代表子步骤已实现，需要更新状态
            if step.status is ExecutionStatus.RUNNING:
                step.status = ExecutionStatus.COMPLETED

    async def summarize(self) -> AsyncGenerator[BaseEvent, None]:
        """所有步骤结束，调用Agent汇总历史的消息并生成最终回复+附件"""
        async with self._trace_agent_operation(
                name="react.summarize",
                operation="summarize",
        ) as trace_scope:
            # 1.构建请求query
            query = SUMMARIZE_PROMPT

            # 2.调用invoke方法获取Agent生成的事件
            # 这里 invoke 会把 query 追加进 memory 再发给大模型
            async for event in self.invoke(query):
                # 3.判断事件类型是否为消息事件，如果是则表示Agent结构化生成汇总内容
                if isinstance(event, MessageEvent):
                    # 4.记录日志并解析输出内容
                    logger.info(f"执行Agent生成汇总内容: {event.message}")
                    parsed_obj = await self._json_parser.invoke(event.message)

                    # 5.将解析数据转换为Message对象
                    message = Message.model_validate(parsed_obj)
                    if trace_scope is not None:
                        trace_scope.finish(output=message.model_dump(mode="json"))

                    # 6.提取消息中的附件信息
                    attachments = [File(filepath=filepath) for filepath in message.attachments]

                    # 7.返回消息事件并将消息+附件进行相应
                    yield MessageEvent(
                        role="assistant",
                        message=message.message,
                        attachments=attachments,
                    )
                else:
                    if isinstance(event, ErrorEvent) and trace_scope is not None:
                        trace_scope.finish(error={"message": event.error})
                    # 8.其他事件则直接返回
                    yield event

    async def summarize_stream(
        self,
        attachments: list[str] | None = None,
    ) -> AsyncGenerator[BaseEvent, None]:
        """所有步骤结束，流式生成最终自然语言回复。"""
        attachment_paths = attachments or []
        stream_id = str(uuid.uuid4())
        async with self._trace_agent_operation(
                name="react.summarize",
                operation="summarize",
        ) as trace_scope:
            query = SUMMARIZE_TEXT_PROMPT.format(
                attachments="\n".join(attachment_paths) or "无",
            )
            content_parts: list[str] = []
            async for event in self._invoke_llm_streaming_text(
                [{"role": "user", "content": query}],
                stream_id,
            ):
                if isinstance(event, MessageDeltaEvent):
                    content_parts.append(event.delta)
                    yield event
                    continue
                if isinstance(event, ErrorEvent):
                    if trace_scope is not None:
                        trace_scope.finish(error={"message": event.error})
                    yield event
                    return
                yield event

            message = "".join(content_parts)
            if trace_scope is not None:
                trace_scope.finish(
                    output={
                        "message": message,
                        "attachments": attachment_paths,
                    }
                )
            yield MessageEvent(
                role="assistant",
                message=message,
                attachments=[File(filepath=filepath) for filepath in attachment_paths],
                stream_id=stream_id,
            )

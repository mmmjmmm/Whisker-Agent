import uuid
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from app.domain.external.json_parser import JSONParser
from app.domain.external.llm import LLM, LLMInvocationError, LLMUsage
from app.domain.models.agent_run import CapabilityProfile
from app.domain.models.event import (
    BaseEvent,
    ResearchUsageEvent,
    ToolEvent,
    ToolEventStatus,
)
from app.domain.models.tool_result import ToolResult
from app.domain.services.research.budget import RunBudgetManager
from app.domain.services.research.errors import (
    ResearchErrorCode,
    ResearchExecutionError,
)
from app.domain.services.research.memory_store import AgentMemoryStore
from app.domain.services.research.tool_policy import ToolPolicy


TOutput = TypeVar("TOutput")
EventCallback = Callable[[BaseEvent], Awaitable[None]]


class AgentRuntimeContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_id: str
    run_id: str
    task_id: str | None = None
    attempt_id: str | None = None
    agent_id: str
    agent_profile: str


class TeamAgentRuntime:
    def __init__(
            self,
            llm: LLM,
            tool_policy: ToolPolicy,
            budget: RunBudgetManager,
            memory_store: AgentMemoryStore,
            json_parser: JSONParser,
            context: AgentRuntimeContext,
            emit: EventCallback | None = None,
            max_iterations: int = 20,
    ) -> None:
        self._llm = llm
        self._tool_policy = tool_policy
        self._budget = budget
        self._memory_store = memory_store
        self._json_parser = json_parser
        self._context = context
        self._emit_callback = emit
        self._max_iterations = max_iterations

    async def run(
            self,
            *,
            prompt: str,
            output_type: type[TOutput],
            profile: CapabilityProfile,
            memory_key: str,
    ) -> TOutput:
        memory = await self._memory_store.load(memory_key)
        memory.add_message({"role": "user", "content": prompt})
        await self._memory_store.save(memory_key, memory)
        repair_attempts = 0

        for _ in range(self._max_iterations):
            result = await self._invoke_llm(
                messages=memory.get_messages(),
                tools=self._tool_policy.schemas(profile),
            )
            message = result.message
            memory.add_message(message)
            await self._memory_store.save(memory_key, memory)

            tool_calls = message.get("tool_calls") or []
            if tool_calls:
                for tool_call in tool_calls:
                    function = tool_call.get("function") or {}
                    function_name = function.get("name", "")
                    tool = self._tool_policy.resolve(profile, function_name)
                    arguments = await self._parse_arguments(
                        function.get("arguments", "{}")
                    )
                    await self._budget.reserve_tool()
                    tool_call_id = tool_call.get("id") or str(uuid.uuid4())
                    await self._emit(self._tool_event(
                        tool_call_id=tool_call_id,
                        tool_name=tool.name,
                        function_name=function_name,
                        function_args=arguments,
                        status=ToolEventStatus.CALLING,
                    ))
                    tool_result = await tool.invoke(function_name, **arguments)
                    await self._emit(self._tool_event(
                        tool_call_id=tool_call_id,
                        tool_name=tool.name,
                        function_name=function_name,
                        function_args=arguments,
                        status=ToolEventStatus.CALLED,
                        function_result=ToolResult(
                            success=tool_result.success,
                            message=tool_result.message,
                        ),
                    ))
                    memory.add_message({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "function_name": function_name,
                        "content": tool_result.model_dump_json(),
                    })
                    await self._memory_store.save(memory_key, memory)
                continue

            content = message.get("content")
            try:
                if isinstance(content, str):
                    return TypeAdapter(output_type).validate_json(content)
                return TypeAdapter(output_type).validate_python(content)
            except ValidationError as exc:
                if repair_attempts >= 1:
                    raise ResearchExecutionError(
                        code=ResearchErrorCode.MODEL_OUTPUT_INVALID,
                        message=str(exc),
                        retryable=False,
                        scope="task",
                    ) from exc
                repair_attempts += 1
                memory.add_message({
                    "role": "user",
                    "content": (
                        "The previous response was invalid. Return only JSON that "
                        f"matches this schema: {TypeAdapter(output_type).json_schema()}"
                    ),
                })
                await self._memory_store.save(memory_key, memory)

        raise ResearchExecutionError(
            code=ResearchErrorCode.MODEL_OUTPUT_INVALID,
            message="agent runtime exceeded maximum iterations",
            retryable=False,
            scope="task",
        )

    async def _invoke_llm(
            self,
            messages: list[dict[str, Any]],
            tools: list[dict[str, Any]],
    ):
        input_estimate = max(1, len(str(messages)) // 4)
        reservation = await self._budget.reserve_llm(
            input_estimate + self._llm.max_tokens
        )
        try:
            result = await self._llm.invoke_with_usage(
                messages=messages,
                tools=tools,
                response_format={"type": "json_object"},
                tool_choice=None,
            )
        except LLMInvocationError:
            await self._budget.settle_llm(reservation, LLMUsage())
            raise
        usage = await self._budget.settle_llm(reservation, result.usage)
        await self._emit(ResearchUsageEvent(
            **self._correlation_fields(),
            budget=self._budget.budget.model_dump(mode="json"),
            usage=usage.model_dump(mode="json"),
            remaining={
                "llm_calls": self._budget.budget.max_llm_calls - usage.llm_calls,
                "tool_calls": self._budget.budget.max_tool_calls - usage.tool_calls,
                "tokens": self._budget.budget.max_total_tokens - usage.total_tokens,
            },
        ))
        return result

    async def _parse_arguments(self, raw_arguments: Any) -> dict[str, Any]:
        if isinstance(raw_arguments, dict):
            return raw_arguments
        parsed = await self._json_parser.invoke(raw_arguments, default_value={})
        if not isinstance(parsed, dict):
            raise ResearchExecutionError(
                code=ResearchErrorCode.MODEL_OUTPUT_INVALID,
                message="tool arguments must be a JSON object",
                retryable=False,
                scope="task",
            )
        return parsed

    def _tool_event(
            self,
            *,
            tool_call_id: str,
            tool_name: str,
            function_name: str,
            function_args: dict[str, Any],
            status: ToolEventStatus,
            function_result: ToolResult | None = None,
    ) -> ToolEvent:
        return ToolEvent(
            **self._correlation_fields(),
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            function_name=function_name,
            function_args=function_args,
            function_result=function_result,
            status=status,
            agent_profile=self._context.agent_profile,
        )

    def _correlation_fields(self) -> dict[str, Any]:
        return {
            "session_id": self._context.session_id,
            "run_id": self._context.run_id,
            "task_id": self._context.task_id,
            "attempt_id": self._context.attempt_id,
            "agent_id": self._context.agent_id,
        }

    async def _emit(self, event: BaseEvent) -> None:
        if self._emit_callback is not None:
            await self._emit_callback(event)

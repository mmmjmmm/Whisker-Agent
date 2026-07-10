#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/5/17 17:21
@Author  : thezehui@gmail.com
@File    : openai_llm.py
"""
import logging
from typing import List, Dict, Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    BadRequestError as OpenAIBadRequestError,
    RateLimitError,
)

from app.application.errors.exceptions import ServerRequestsError
from app.domain.external.llm import (
    LLM,
    LLMErrorKind,
    LLMInvocationError,
    LLMInvocationResult,
    LLMUsage,
)
from app.domain.models.app_config import LLMConfig

logger = logging.getLogger(__name__)


def classify_openai_error(error: Exception) -> LLMInvocationError:
    if isinstance(error, RateLimitError):
        return LLMInvocationError(LLMErrorKind.RATE_LIMITED, str(error), True)
    if isinstance(error, APITimeoutError):
        return LLMInvocationError(LLMErrorKind.TIMEOUT, str(error), True)
    if isinstance(error, APIConnectionError):
        return LLMInvocationError(LLMErrorKind.CONNECTION, str(error), True)
    if isinstance(error, OpenAIBadRequestError):
        return LLMInvocationError(LLMErrorKind.INVALID_REQUEST, str(error), False)
    if isinstance(error, APIStatusError):
        retryable = getattr(error, "status_code", 500) >= 500
        return LLMInvocationError(LLMErrorKind.PROVIDER, str(error), retryable)
    return LLMInvocationError(LLMErrorKind.PROVIDER, str(error), False)


class OpenAILLM(LLM):
    """基于OpenAI SDK/兼容OpenAI格式的LLM调用类"""

    def __init__(self, llm_config: LLMConfig, **kwargs) -> None:
        """构造函数，完成异步OpenAI客户端的创建和参数初始化"""
        # 1.初始化异步客户端
        self._client = AsyncOpenAI(
            base_url=str(llm_config.base_url),
            api_key=llm_config.api_key,
            **kwargs,
        )

        # 2.完成其他参数的存储
        self._model_name = llm_config.model_name
        self._temperature = llm_config.temperature
        self._max_tokens = llm_config.max_tokens
        self._timeout = 3600

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def temperature(self) -> float:
        return self._temperature

    @property
    def max_tokens(self) -> int:
        return self._max_tokens

    async def invoke(
            self,
            messages: List[Dict[str, Any]],
            tools: List[Dict[str, Any]] = None,
            response_format: Dict[str, Any] = None,
            tool_choice: str = None,
    ) -> Dict[str, Any]:
        """保留现有 Agent 使用的消息返回和错误类型。"""
        try:
            result = await self.invoke_with_usage(
                messages=messages,
                tools=tools,
                response_format=response_format,
                tool_choice=tool_choice,
            )
            return result.message
        except LLMInvocationError as exc:
            raise ServerRequestsError("调用OpenAI客户端向LLM发起请求出错") from exc

    async def invoke_with_usage(
            self,
            messages: List[Dict[str, Any]],
            tools: List[Dict[str, Any]] = None,
            response_format: Dict[str, Any] = None,
            tool_choice: str = None,
    ) -> LLMInvocationResult:
        """调用 LLM 并返回结构化响应元数据，不记录完整模型响应。"""
        try:
            if tools:
                logger.info(f"调用OpenAI客户端向LLM发起请求并携带工具信息: {self._model_name}")
                response = await self._client.chat.completions.create(
                    model=self._model_name,
                    temperature=self._temperature,
                    max_tokens=self._max_tokens,
                    messages=messages,
                    response_format=response_format,
                    tools=tools,
                    tool_choice=tool_choice,
                    parallel_tool_calls=False,  # 关闭并行工具调用(deepseek没有这个参数的)
                    timeout=self._timeout,
                )
            else:
                logger.info(f"调用OpenAI客户端向LLM发起请求未携带: {self._model_name}")
                response = await self._client.chat.completions.create(
                    model=self._model_name,
                    temperature=self._temperature,
                    max_tokens=self._max_tokens,
                    messages=messages,
                    response_format=response_format,
                    timeout=self._timeout,
                )

            choice = response.choices[0]
            response_usage = response.usage
            usage = LLMUsage(
                input_tokens=getattr(response_usage, "prompt_tokens", 0) or 0,
                output_tokens=getattr(response_usage, "completion_tokens", 0) or 0,
                total_tokens=getattr(response_usage, "total_tokens", 0) or 0,
            )
            result = LLMInvocationResult(
                message=choice.message.model_dump(),
                model=self._model_name,
                provider_request_id=getattr(response, "id", None),
                finish_reason=getattr(choice, "finish_reason", None),
                usage=usage,
            )
            logger.info(
                "OpenAI客户端调用完成 model=%s request_id=%s finish_reason=%s total_tokens=%s",
                result.model,
                result.provider_request_id,
                result.finish_reason,
                result.usage.total_tokens,
            )
            return result
        except Exception as exc:
            classified = classify_openai_error(exc)
            logger.error(
                "调用OpenAI客户端发生错误 kind=%s retryable=%s",
                classified.kind.value,
                classified.retryable,
            )
            raise classified from exc


if __name__ == "__main__":
    import asyncio


    async def main():
        llm = OpenAILLM(LLMConfig(
            base_url="https://api.deepseek.com",
            api_key="",
            model_name="deepseek-chat",
        ))
        response = await llm.invoke([{"role": "user", "content": "Hi"}])
        print(response)


    asyncio.run(main())

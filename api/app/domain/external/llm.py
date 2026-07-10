#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/5/17 17:14
@Author  : thezehui@gmail.com
@File    : llm.py
"""
from enum import Enum
from typing import Any, Dict, List, Protocol

from pydantic import BaseModel, Field


class LLMUsage(BaseModel):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)


class LLMInvocationResult(BaseModel):
    message: Dict[str, Any]
    model: str
    provider_request_id: str | None = None
    finish_reason: str | None = None
    usage: LLMUsage = Field(default_factory=LLMUsage)


class LLMErrorKind(str, Enum):
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    INVALID_REQUEST = "invalid_request"
    PROVIDER = "provider"


class LLMInvocationError(RuntimeError):
    def __init__(self, kind: LLMErrorKind, message: str, retryable: bool) -> None:
        self.kind = kind
        self.retryable = retryable
        super().__init__(message)


class LLM(Protocol):
    """用于Agent应用与LLM进行交互的接口协议"""

    async def invoke(
            self,
            messages: List[Dict[str, Any]],
            tools: List[Dict[str, Any]] = None,
            response_format: Dict[str, Any] = None,
            tool_choice: str = None,
    ) -> Dict[str, Any]:
        """传递消息列表、工具列表、响应格式、工具选择策略调用LLM接口"""
        ...

    async def invoke_with_usage(
            self,
            messages: List[Dict[str, Any]],
            tools: List[Dict[str, Any]] = None,
            response_format: Dict[str, Any] = None,
            tool_choice: str = None,
    ) -> LLMInvocationResult:
        """调用 LLM 并返回可审计的模型与 token 用量。"""
        ...

    @property
    def model_name(self) -> str:
        """只读属性，返回LLM的名字"""
        ...

    @property
    def temperature(self) -> float:
        """只读属性，返回LLM的温度"""
        ...

    @property
    def max_tokens(self) -> int:
        """只读属性，返回LLM的最大生成token数"""
        ...

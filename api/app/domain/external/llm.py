#!/usr/bin/env python
# -*- coding: utf-8 -*-
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol, List, Dict, Any


@dataclass(frozen=True)
class LLMStreamChunk:
    """LLM流式文本片段。"""
    content: str = ""
    usage: Dict[str, Any] | None = None


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

    async def stream(
            self,
            messages: List[Dict[str, Any]],
            tools: List[Dict[str, Any]] = None,
            response_format: Dict[str, Any] = None,
            tool_choice: str = None,
    ) -> AsyncIterator[LLMStreamChunk]:
        """传递消息列表并以增量文本片段返回LLM响应"""
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

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/5/17 17:44
@Author  : thezehui@gmail.com
@File    : exceptions.py
"""
from typing import Any


class AppException(RuntimeError):
    """基础应用异常类，继承RuntimeError"""

    def __init__(
            self,
            code: int = 400,  # 自定义业务错误码
            status_code: int = 400,
            msg: str = "应用发生错误请稍后尝试",
            data: Any = None,
    ):
        """构造函数，完成错误数据初始化"""
        self.code = code
        self.status_code = status_code
        self.msg = msg
        self.data = data
        super().__init__()


class BadRequestError(AppException):
    """客户端请求错误"""

    def __init__(self, msg: str = "客户端请求错误，请检查后重试"):
        super().__init__(status_code=400, code=400, msg=msg)


class NotFoundError(AppException):
    """资源未找到错误"""

    def __init__(self, msg: str = "资源未找到，请核实后重试"):
        super().__init__(status_code=404, code=404, msg=msg)


class ValidationError(AppException):
    """数据校验错误"""

    def __init__(self, msg: str = "请求参数数据校验错误，请核实后重试"):
        super().__init__(status_code=422, code=422, msg=msg)


class TooManusRequestsError(AppException):
    """请求过多错误（触发限流）"""

    def __init__(self, msg: str = "请求过多，触发限流，请稍后重试"):
        super().__init__(status_code=429, code=429, msg=msg)


class ServerRequestsError(AppException):
    """服务器异常错误"""

    def __init__(self, msg: str = "服务器出现异常请稍后重试"):
        super().__init__(status_code=500, code=500, msg=msg)


class RunAlreadyActiveError(AppException):
    """会话中已存在不允许并行或切换的运行。"""

    def __init__(self, run_id: str, status: str) -> None:
        super().__init__(
            status_code=409,
            code=409,
            msg="当前会话已有运行中的任务",
            data={
                "error_code": "RUN_ALREADY_ACTIVE",
                "run_id": run_id,
                "status": status,
            },
        )


class ResearchTeamDisabledError(AppException):
    """研究团队能力尚未在当前环境开放。"""

    def __init__(self) -> None:
        super().__init__(
            status_code=403,
            code=403,
            msg="研究团队模式尚未开放",
            data={"error_code": "RESEARCH_TEAM_DISABLED"},
        )

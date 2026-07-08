#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/07/08 14:20
@File    : oss_health_checker.py
"""
import logging

from app.domain.external.health_checker import HealthChecker
from app.domain.models.health_status import HealthStatus
from app.infrastructure.storage.oss import OSS

logger = logging.getLogger(__name__)


class OSSHealthChecker(HealthChecker):
    """阿里云 OSS 健康检查器。"""

    def __init__(self, oss: OSS) -> None:
        self._oss = oss

    async def check(self) -> HealthStatus:
        try:
            await self._oss.check()
            return HealthStatus(service="oss", status="ok")
        except Exception as e:
            logger.error(f"OSS健康检查失败: {str(e)}")
            return HealthStatus(
                service="oss",
                status="error",
                details=str(e),
            )

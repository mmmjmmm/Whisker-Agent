#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/07/08 14:20
@File    : oss.py
"""
import logging
import asyncio
from functools import lru_cache
from typing import Optional

import oss2

from core.config import Settings, get_settings

logger = logging.getLogger(__name__)


class OSS:
    """阿里云 OSS 对象存储客户端。"""

    def __init__(self):
        self._settings: Settings = get_settings()
        self._bucket: Optional[oss2.Bucket] = None

    def _has_required_config(self) -> bool:
        """检查 OSS 文件能力所需配置是否完整。"""
        return all([
            self._settings.oss_access_key_id,
            self._settings.oss_access_key_secret,
            self._settings.oss_endpoint,
            self._settings.oss_bucket,
        ])

    def _endpoint_url(self) -> str:
        """返回带 scheme 的 OSS endpoint。"""
        endpoint = self._settings.oss_endpoint.strip()
        if endpoint.startswith(("http://", "https://")):
            return endpoint
        return f"{self._settings.oss_scheme}://{endpoint}"

    def public_url(self, key: str) -> str:
        """根据对象 key 生成公开访问 URL。"""
        if self._settings.oss_public_base_url:
            return f"{self._settings.oss_public_base_url.rstrip('/')}/{key.lstrip('/')}"

        endpoint = self._settings.oss_endpoint.strip().rstrip("/")
        if endpoint.startswith("http://"):
            endpoint = endpoint.removeprefix("http://")
        elif endpoint.startswith("https://"):
            endpoint = endpoint.removeprefix("https://")
        return f"{self._settings.oss_scheme}://{self._settings.oss_bucket}.{endpoint}/{key.lstrip('/')}"

    async def check(self) -> None:
        """向 OSS 发起轻量请求，校验 endpoint、bucket 和密钥是否可用。"""
        await asyncio.to_thread(self.bucket.get_bucket_info)

    async def init(self) -> None:
        """初始化阿里云 OSS Bucket 客户端。"""
        if self._bucket is not None:
            logger.warning("阿里云OSS已初始化，无需重复操作")
            return

        if not self._has_required_config():
            logger.warning("阿里云OSS配置不完整，跳过初始化，文件上传/下载能力将不可用")
            return

        try:
            auth = oss2.Auth(
                self._settings.oss_access_key_id,
                self._settings.oss_access_key_secret,
            )
            self._bucket = oss2.Bucket(auth, self._endpoint_url(), self._settings.oss_bucket)
            await self.check()
            logger.info("阿里云OSS初始化成功")
        except Exception as e:
            logger.error(f"阿里云OSS初始化失败: {str(e)}")
            raise

    async def shutdown(self) -> None:
        """关闭阿里云 OSS 客户端。"""
        if self._bucket is not None:
            self._bucket = None
            logger.info("关闭阿里云OSS成功")

        get_oss.cache_clear()

    @property
    def bucket(self) -> oss2.Bucket:
        """返回已初始化的阿里云 OSS Bucket。"""
        if self._bucket is None:
            raise RuntimeError("阿里云OSS未初始化或配置不完整，请检查OSS配置")
        return self._bucket


@lru_cache()
def get_oss() -> OSS:
    """获取阿里云 OSS 实例。"""
    return OSS()

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/07/08 14:20
@File    : oss_file_storage.py
"""
import logging
import os.path
import uuid
from datetime import datetime
from typing import Tuple, BinaryIO, Callable

from fastapi import UploadFile
from starlette.concurrency import run_in_threadpool

from app.domain.external.file_storage import FileStorage
from app.domain.models.file import File
from app.domain.repositories.uow import IUnitOfWork
from app.infrastructure.storage.oss import OSS

logger = logging.getLogger(__name__)


class OSSFileStorage(FileStorage):
    """基于阿里云 OSS 的文件存储实现。"""

    def __init__(
            self,
            oss: OSS,
            uow_factory: Callable[[], IUnitOfWork],
    ) -> None:
        self.oss = oss
        self._uow_factory = uow_factory
        self._uow = uow_factory()

    async def upload_file(self, upload_file: UploadFile) -> File:
        """上传文件到阿里云 OSS 并记录文件元信息。"""
        try:
            file_id = str(uuid.uuid4())
            _, file_extension = os.path.splitext(upload_file.filename)
            if not file_extension:
                file_extension = ""

            date_path = datetime.now().strftime("%Y/%m/%d")
            object_key = f"{date_path}/{file_id}{file_extension}"

            await run_in_threadpool(
                self.oss.bucket.put_object,
                object_key,
                upload_file.file,
            )
            logger.info(f"文件上传成功: {upload_file.filename} (ID: {file_id})")

            file = File(
                id=file_id,
                filename=upload_file.filename,
                key=object_key,
                extension=file_extension,
                mime_type=upload_file.content_type or "",
                size=upload_file.size,
            )
            async with self._uow:
                await self._uow.file.save(file)

            return file
        except Exception as e:
            logger.error(f"上传文件[{upload_file.filename}]失败: {str(e)}")
            raise

    async def download_file(self, file_id: str) -> Tuple[BinaryIO, File]:
        """根据文件 id 下载 OSS 对象。"""
        try:
            async with self._uow:
                file = await self._uow.file.get_by_id(file_id)
            if not file:
                raise ValueError(f"该文件不存在, 文件id: {file_id}")

            response = await run_in_threadpool(
                self.oss.bucket.get_object,
                file.key,
            )
            return response, file
        except Exception as e:
            logger.error(f"下载文件[{file_id}]失败: {str(e)}")
            raise

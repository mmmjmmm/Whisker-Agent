#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/5/17 10:54
@Author  : thezehui@gmail.com
@File    : dependencies.py
"""
import logging

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.agent_service import AgentService
from app.application.services.app_config_service import AppConfigService
from app.application.services.file_service import FileService
from app.application.services.session_service import SessionService
from app.application.services.skill_service import SkillService
from app.application.services.status_service import StatusService
from app.application.services.trace_service import TraceService
from app.domain.services.skills.parser import SkillParser
from app.domain.services.skills.registry import SkillRegistry
from app.infrastructure.external.file_storage.oss_file_storage import OSSFileStorage
from app.infrastructure.external.health_checker.postgres_health_checker import PostgresHealthChecker
from app.infrastructure.external.health_checker.redis_health_checker import RedisHealthChecker
from app.infrastructure.external.health_checker.oss_health_checker import OSSHealthChecker
from app.infrastructure.external.json_parser.repair_json_parser import RepairJSONParser
from app.infrastructure.external.llm.openai_llm import OpenAILLM
from app.infrastructure.external.sandbox.docker_sandbox import DockerSandbox
from app.infrastructure.external.search.bing_search import BingSearchEngine
from app.infrastructure.external.skill_bundle_storage.oss_skill_bundle_storage import (
    OSSSkillBundleStorage,
)
from app.infrastructure.external.task.redis_stream_task import RedisStreamTask
from app.infrastructure.repositories.file_app_config_repository import FileAppConfigRepository
from app.infrastructure.storage.oss import OSS, get_oss
from app.infrastructure.storage.postgres import get_db_session, get_uow
from app.infrastructure.storage.redis import RedisClient, get_redis
from core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def get_app_config_service() -> AppConfigService:
    """获取应用配置服务"""
    # 1.获取数据仓库并打印日志
    logger.info("加载获取AppConfigService")
    file_app_config_repository = FileAppConfigRepository(settings.app_config_filepath)

    # 2.实例化AppConfigService
    return AppConfigService(app_config_repository=file_app_config_repository)


def get_status_service(
        db_session: AsyncSession = Depends(get_db_session),
        redis_client: RedisClient = Depends(get_redis),
        oss: OSS = Depends(get_oss),
) -> StatusService:
    """获取状态服务"""
    # 1.初始化postgres、redis和oss健康检查
    postgres_checker = PostgresHealthChecker(db_session)
    redis_checker = RedisHealthChecker(redis_client)
    oss_checker = OSSHealthChecker(oss)

    # 2.创建服务并返回
    logger.info("加载获取StatusService")
    return StatusService(checkers=[postgres_checker, redis_checker, oss_checker])


def get_file_service(
        oss: OSS = Depends(get_oss)
) -> FileService:
    # 1.初始化文件仓库和文件存储桶
    file_storage = OSSFileStorage(
        oss=oss,
        uow_factory=get_uow,
    )

    # 2.构建服务并返回
    return FileService(
        uow_factory=get_uow,
        file_storage=file_storage,
    )


def get_session_service() -> SessionService:
    return SessionService(uow_factory=get_uow, sandbox_cls=DockerSandbox)


def get_trace_service() -> TraceService:
    return TraceService(uow_factory=get_uow)


def get_skill_registry(
        oss: OSS = Depends(get_oss),
) -> SkillRegistry:
    return SkillRegistry(
        uow_factory=get_uow,
        bundle_storage=OSSSkillBundleStorage(oss),
        parser=SkillParser(),
    )


def get_skill_service(
        registry: SkillRegistry = Depends(get_skill_registry),
) -> SkillService:
    return SkillService(registry)


def get_agent_service(
        oss: OSS = Depends(get_oss),
        skill_registry: SkillRegistry = Depends(get_skill_registry),
) -> AgentService:
    # 1.获取应用配置信息(读取配置需要实时获取,所以不配置缓存)
    app_config_repository = FileAppConfigRepository(config_path=settings.app_config_filepath)
    app_config = app_config_repository.load()

    # 2.构建依赖实例
    llm = OpenAILLM(app_config.llm_config)
    file_storage = OSSFileStorage(
        oss=oss,
        uow_factory=get_uow,
    )

    # 3.实例Agent服务并返回
    return AgentService(
        uow_factory=get_uow,
        llm=llm,
        agent_config=app_config.agent_config,
        mcp_config=app_config.mcp_config,
        a2a_config=app_config.a2a_config,
        sandbox_cls=DockerSandbox,
        task_cls=RedisStreamTask,
        json_parser=RepairJSONParser(),
        search_engine=BingSearchEngine(),
        file_storage=file_storage,
        skill_registry=skill_registry,
    )

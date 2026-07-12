#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/5/14 10:44
@Author  : thezehui@gmail.com
@File    : config.py
"""
from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """WhiskerAgent后端中控配置信息，从.env或者环境变量中加载数据"""

    # 项目基础配置
    env: str = "development"
    log_level: str = "INFO"
    app_config_filepath: str = "config.yaml"

    # 数据库相关配置
    sqlalchemy_database_uri: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/manus"

    # Redis缓存配置
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str | None = None

    # 阿里云OSS对象存储配置
    oss_access_key_id: str = ""
    oss_access_key_secret: str = ""
    oss_endpoint: str = ""
    oss_scheme: str = "https"
    oss_bucket: str = ""
    oss_public_base_url: str = ""

    # Sandbox配置
    sandbox_address: Optional[str] = None
    sandbox_image: Optional[str] = None
    sandbox_name_prefix: Optional[str] = None
    sandbox_ttl_minutes: Optional[int] = 60
    sandbox_network: Optional[str] = None
    sandbox_chrome_args: Optional[str] = ""
    sandbox_https_proxy: Optional[str] = None
    sandbox_http_proxy: Optional[str] = None
    sandbox_no_proxy: Optional[str] = None

    # 使用pydantic v2的写法来完成环境变量信息的告知
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache()
def get_settings() -> Settings:
    """获取当前WhiskerAgent项目的配置信息，并对内容进行缓存，避免重复读取"""
    settings = Settings()
    return settings

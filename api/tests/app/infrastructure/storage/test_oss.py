#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
import importlib

import pytest

from core.config import get_settings


def _load_oss_class():
    oss_module = importlib.import_module("app.infrastructure.storage.oss")
    return oss_module.OSS


def test_oss_init_skips_when_required_config_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """OSS is optional at API startup; missing credentials should not crash the app."""
    monkeypatch.setenv("OSS_ACCESS_KEY_ID", "")
    monkeypatch.setenv("OSS_ACCESS_KEY_SECRET", "")
    monkeypatch.setenv("OSS_ENDPOINT", "")
    monkeypatch.setenv("OSS_BUCKET", "")
    get_settings.cache_clear()

    try:
        oss_cls = _load_oss_class()
    except ModuleNotFoundError:
        pytest.fail("OSS storage client is not implemented")

    oss = oss_cls()

    asyncio.run(oss.init())

    with pytest.raises(RuntimeError):
        _ = oss.bucket


def test_oss_init_checks_bucket_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OSS_ACCESS_KEY_ID", "test-id")
    monkeypatch.setenv("OSS_ACCESS_KEY_SECRET", "test-secret")
    monkeypatch.setenv("OSS_ENDPOINT", "oss-cn-hangzhou.aliyuncs.com")
    monkeypatch.setenv("OSS_BUCKET", "demo-bucket")
    get_settings.cache_clear()

    oss_module = importlib.import_module("app.infrastructure.storage.oss")
    checked = {"value": False}

    class FakeBucket:
        def get_bucket_info(self):
            checked["value"] = True
            return object()

    monkeypatch.setattr(oss_module.oss2, "Auth", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(oss_module.oss2, "Bucket", lambda *_args, **_kwargs: FakeBucket())

    oss = oss_module.OSS()

    asyncio.run(oss.init())

    assert checked["value"] is True


def test_oss_health_checker_reports_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    try:
        checker_module = importlib.import_module("app.infrastructure.external.health_checker.oss_health_checker")
    except ModuleNotFoundError:
        pytest.fail("OSS health checker is not implemented")

    class HealthyOSS:
        async def check(self):
            return None

    result = asyncio.run(checker_module.OSSHealthChecker(HealthyOSS()).check())

    assert result.service == "oss"
    assert result.status == "ok"


def test_oss_public_url_uses_configured_public_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OSS_ACCESS_KEY_ID", "test-id")
    monkeypatch.setenv("OSS_ACCESS_KEY_SECRET", "test-secret")
    monkeypatch.setenv("OSS_ENDPOINT", "oss-cn-hangzhou.aliyuncs.com")
    monkeypatch.setenv("OSS_BUCKET", "demo-bucket")
    monkeypatch.setenv("OSS_PUBLIC_BASE_URL", "https://static.example.com/assets/")
    get_settings.cache_clear()

    oss = _load_oss_class()()

    assert oss.public_url("/2026/07/08/demo.png") == "https://static.example.com/assets/2026/07/08/demo.png"


def test_oss_public_url_builds_bucket_endpoint_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OSS_ACCESS_KEY_ID", "test-id")
    monkeypatch.setenv("OSS_ACCESS_KEY_SECRET", "test-secret")
    monkeypatch.setenv("OSS_ENDPOINT", "https://oss-cn-hangzhou.aliyuncs.com")
    monkeypatch.setenv("OSS_SCHEME", "https")
    monkeypatch.setenv("OSS_BUCKET", "demo-bucket")
    monkeypatch.setenv("OSS_PUBLIC_BASE_URL", "")
    get_settings.cache_clear()

    oss = _load_oss_class()()

    assert oss.public_url("2026/07/08/demo.png") == (
        "https://demo-bucket.oss-cn-hangzhou.aliyuncs.com/2026/07/08/demo.png"
    )

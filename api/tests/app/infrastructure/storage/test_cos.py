#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio

import pytest

from app.infrastructure.storage.cos import Cos
from core.config import get_settings


def test_cos_init_skips_when_required_config_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """COS is optional at API startup; missing credentials should not crash the app."""
    monkeypatch.setenv("COS_REGION", "")
    monkeypatch.setenv("COS_SECRET_ID", "")
    monkeypatch.setenv("COS_SECRET_KEY", "")
    monkeypatch.setenv("COS_BUCKET", "")
    get_settings.cache_clear()

    cos = Cos()

    asyncio.run(cos.init())

    with pytest.raises(RuntimeError):
        _ = cos.client

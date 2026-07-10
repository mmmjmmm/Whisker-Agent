import asyncio

import pytest


@pytest.mark.asyncio
async def test_asyncio_plugin_runs_coroutines() -> None:
    await asyncio.sleep(0)
    assert True

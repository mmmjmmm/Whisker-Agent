import asyncio

from app.infrastructure.external.task.redis_stream_task import RedisStreamTask


class BlockingRunner:
    def __init__(self):
        self.started = asyncio.Event()

    async def invoke(self, task):
        self.started.set()
        await asyncio.Event().wait()

    async def destroy(self):
        pass

    async def on_done(self, task):
        pass


def test_cancel_keeps_task_registered_until_runner_cleanup_finishes():
    async def scenario():
        runner = BlockingRunner()
        task = RedisStreamTask(runner)
        await task.invoke()
        await runner.started.wait()

        task.cancel()

        assert RedisStreamTask.get(task.id) is task
        await task.wait()
        assert RedisStreamTask.get(task.id) is None

    asyncio.run(scenario())

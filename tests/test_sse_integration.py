import asyncio
import os
import pytest
import redis
from app.services.sse_manager import SSEManager

pytestmark=pytest.mark.skipif(os.getenv('GRYADKA_INTEGRATION')!='1',reason='requires isolated Redis container')


def test_sse_fanout_delivers_once_and_can_restart():
    async def scenario():
        manager=SSEManager(redis_channel='gryadka:fixture:sse')
        manager.configure_redis(redis.Redis(host='redis',decode_responses=True))
        for _ in range(2):
            await manager.start()
            queue=await manager.subscribe('fixture')
            await asyncio.sleep(0.1)
            await manager.publish({'event':'fixture'})
            result=await asyncio.wait_for(queue.get(),2)
            assert result['event']=='fixture'
            await asyncio.sleep(0.1)
            assert queue.empty()
            await manager.stop()
        assert not manager._redis_listener_started
    asyncio.run(scenario())

"""Kafka read-only monitor endpoint."""
import asyncio
from fastapi import APIRouter
from app.services.monitor_runtime import read_cached_target

router=APIRouter(prefix='/api/kafka',tags=['kafka'])


@router.get('/{name}')
async def get_kafka_target(name:str):
    return await asyncio.to_thread(read_cached_target,'kafka',name)

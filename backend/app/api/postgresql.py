"""PostgreSQL monitor reads and separately authorized HBA metadata."""
import asyncio
import logging

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.auth import require_permission
from app.audit import audit_log
from app.services.monitor_runtime import get_configured_target, read_cached_target, read_cached_databases_overview
from app.services.postgresql_collector import PostgresqlCollector

router = APIRouter(prefix='/api/postgresql', tags=['postgresql'])
logger = logging.getLogger(__name__)


@router.get('/databases-overview')
async def get_postgresql_databases_overview():
    return await asyncio.to_thread(read_cached_databases_overview, 'postgresql')


@router.get('/{name}')
async def get_postgresql_target(name: str):
    return await asyncio.to_thread(read_cached_target, 'postgresql', name)


async def _read_catalog(name, method, *args):
    target = get_configured_target('postgresql', name)
    try:
        return await asyncio.to_thread(method, target, *args)
    except ValueError:
        raise HTTPException(404, 'База не найдена в каталоге сервера') from None
    except psycopg.Error as exc:
        logger.warning('PostgreSQL catalog unavailable: target=%s error_type=%s', name, type(exc).__name__)
        if isinstance(exc, psycopg.errors.InsufficientPrivilege) or getattr(exc, 'sqlstate', None) in {'28000', '28P01', '42501'}:
            raise HTTPException(403, 'Мониторинговой учетке недоступен этот каталог. Права автоматически не расширяются.') from None
        raise HTTPException(503, 'Каталог недоступен. Проверьте допуск учетной записи к выбранной базе.') from None


@router.get('/{name}/catalog')
async def get_postgresql_catalog(name: str, database: str | None = Query(default=None, max_length=128)):
    return await _read_catalog(name, PostgresqlCollector().collect_catalog, database)


@router.get('/{name}/roles')
async def get_postgresql_roles(name: str):
    return await _read_catalog(name, PostgresqlCollector().collect_roles)


@router.get('/{name}/hba')
async def get_postgresql_hba(name: str, request: Request, user: dict = Depends(require_permission('postgresql.hba.read'))):
    target = get_configured_target('postgresql', name)
    try:
        rules = await asyncio.to_thread(PostgresqlCollector().collect_hba_rules, target)
    except psycopg.errors.InsufficientPrivilege:
        raise HTTPException(403, 'Мониторинговой учетке PostgreSQL недоступно чтение HBA.') from None
    except psycopg.Error as exc:
        logger.warning('HBA read failed: target=%s error_type=%s', name, type(exc).__name__)
        raise HTTPException(503, 'Данные HBA недоступны. Проверьте monitoring helper и допуск учетной записи.') from None
    audit_log('postgresql_hba_read', user=user, request=request, target=name)
    return {'nodes': rules, 'source': 'current_file_contents'}

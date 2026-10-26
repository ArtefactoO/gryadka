from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from redis.exceptions import NoPermissionError

from app.main import app
from app.api.auth import get_current_user


@pytest.mark.parametrize('method,path,operation,body', [
    ('get', '/api/redis/fixture/keys?pattern=*&db=0', 'scan_keys', None),
    ('get', '/api/redis/fixture/keys/example/value?db=0', 'get_key_value', None),
    ('delete', '/api/redis/fixture/keys', 'delete_keys', {'keys': ['example'], 'db': 0}),
])
def test_target_acl_denial_is_actionable_and_never_leaks_raw_redis_error(method, path, operation, body):
    app.dependency_overrides[get_current_user] = lambda: {'sub': 'fixture', 'permissions': ['*']}
    try:
        with patch('app.api.redis.redis_service.' + operation, new_callable=AsyncMock,
                   side_effect=NoPermissionError('fixture-private-redis-error')) as run:
            kwargs = {'json': body} if body is not None else {}
            response = TestClient(app).request(method, path, headers={'Authorization': 'Bearer fixture'}, **kwargs)
            assert response.status_code == 403, response.text
            assert 'учетн' in response.json()['detail'].lower()
            assert 'fixture-private' not in response.text
            run.assert_awaited_once()
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_scan_keeps_db_cursor_and_pattern_when_authorized():
    app.dependency_overrides[get_current_user] = lambda: {'sub': 'fixture', 'permissions': ['redis.read', 'redis.keys.read']}
    try:
        with patch('app.api.redis.redis_service.scan_keys', new_callable=AsyncMock,
                   return_value=(24, [{'key': 'jobs:1', 'type': 'hash', 'ttl': 60}])) as scan:
            response = TestClient(app).get('/api/redis/fixture/keys?pattern=jobs:*&db=3&cursor=7&count=20',
                                          headers={'Authorization': 'Bearer fixture'})
            assert response.status_code == 200, response.text
            assert response.json()['cursor'] == 24
            assert scan.await_args.args == ('fixture', 'jobs:*', 7, 20, 3)
    finally:
        app.dependency_overrides.pop(get_current_user, None)

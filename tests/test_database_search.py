from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.api.auth import get_current_user
from app.services.monitor_runtime import read_cached_databases_overview


def test_postgresql_database_index_requires_login():
    assert TestClient(app).get('/api/postgresql/databases-overview').status_code == 401


def test_overview_reads_only_configured_cache_entries_and_deduplicates_replica_names():
    config = SimpleNamespace(get_targets_by_type=lambda engine: [SimpleNamespace(name='pg-a'), SimpleNamespace(name='pg-b')])
    cache = MagicMock()
    cache.get_json.side_effect = [
        {'nodes': [{'databases': [{'name': 'orders'}, {'name': 'analytics'}]}, {'databases': [{'name': 'orders'}]}]},
        None,
    ]
    with patch('app.services.monitor_runtime.get_app_config', return_value=config), patch('app.services.monitor_runtime.get_monitor_cache', return_value=cache):
        assert read_cached_databases_overview('postgresql') == {'pg-a': ['analytics', 'orders'], 'pg-b': []}
    assert [call.args[0] for call in cache.get_json.call_args_list] == ['postgresql:target:pg-a', 'postgresql:target:pg-b']


def test_overview_route_precedes_dynamic_target_route_and_returns_only_names():
    app.dependency_overrides[get_current_user] = lambda: {'sub': 'viewer', 'permissions': ['postgresql.read']}
    try:
        with patch('app.api.postgresql.read_cached_databases_overview', return_value={'fixture': ['orders']}):
            response = TestClient(app).get('/api/postgresql/databases-overview', headers={'Authorization': 'Bearer fixture'})
        assert response.status_code == 200
        assert response.json() == {'fixture': ['orders']}
    finally:
        app.dependency_overrides.pop(get_current_user, None)

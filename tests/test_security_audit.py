"""Security boundary probes use only synthetic identities and loopback fixtures."""
import threading
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.error import HTTPError
from urllib.request import Request

import pytest
from fastapi.testclient import TestClient

from app.auth import create_access_token
from app.main import app
from app.services.http_client import open_http


MUTATIONS = [(method.upper(), path) for path, operations in app.openapi()['paths'].items()
             for method in operations if method in {'post', 'put', 'patch', 'delete'}
             if not path.startswith('/api/auth/')]


@pytest.mark.parametrize('method,template', MUTATIONS)
@pytest.mark.parametrize('identity', ['anonymous', 'viewer'])
def test_every_monitor_mutation_denies_anonymous_and_viewer(method, template, identity):
    import re
    path = re.sub(r'\{[^}]+\}', 'fixture', template)
    headers = {} if identity == 'anonymous' else {'Authorization': 'Bearer ' + create_access_token(
        {'sub': 'viewer', 'auth_provider': 'local', 'roles': ['admin'], 'permissions': ['*']})}
    client = TestClient(app, raise_server_exceptions=False)
    response = client.request(method, path, headers=headers, json={'name': 'fixture', 'keys': ['fixture']})
    assert response.status_code == (401 if identity == 'anonymous' else 403), response.text


def test_cookie_mutation_cannot_skip_origin_check_with_non_bearer_header():
    client = TestClient(app)
    client.cookies.set('gryadka_auth', create_access_token({'sub': 'admin', 'auth_provider': 'local'}))
    with patch('app.api.redis.redis_service.delete_keys', new_callable=AsyncMock, return_value=0) as delete:
        response = client.request('DELETE', '/api/redis/local-redis/keys', json={'keys': ['fixture']},
                                  headers={'Origin': 'https://untrusted.example', 'Authorization': 'Basic fixture'})
    assert response.status_code == 403
    delete.assert_not_called()


def test_collector_redirect_cannot_send_credentials_to_another_endpoint():
    received = []
    class Destination(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append(self.headers.get('Authorization'))
            self.send_response(200); self.end_headers(); self.wfile.write(b'{}')
        def log_message(self, *args): pass
    destination = ThreadingHTTPServer(('127.0.0.1', 0), Destination)
    class Redirect(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header('Location', f'http://127.0.0.1:{destination.server_port}/other-service')
            self.end_headers()
        def log_message(self, *args): pass
    redirect = ThreadingHTTPServer(('127.0.0.1', 0), Redirect)
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in [destination, redirect]]
    for thread in threads: thread.start()
    try:
        with open_http(Request(f'http://127.0.0.1:{destination.server_port}/metrics', headers={'Authorization': 'Basic fixture-first'}), timeout=2) as response:
            assert response.read() == b'{}'
        with open_http(f'http://127.0.0.1:{destination.server_port}/metrics', timeout=2) as response:
            assert response.read() == b'{}'
        assert received == ['Basic fixture-first', None]
        received.clear()
        with pytest.raises(HTTPError):
            open_http(Request(f'http://127.0.0.1:{redirect.server_port}/metrics', headers={'Authorization': 'Basic fixture-only'}), timeout=2)
        assert received == []
    finally:
        for server in [redirect, destination]: server.shutdown(); server.server_close()


def test_shared_http_transport_reuses_default_without_poisoning_tls_overrides():
    from app.services.http_client import _default_opener
    _default_opener.cache_clear()
    default, custom = MagicMock(), MagicMock()
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    tls.check_hostname = False
    tls.verify_mode = ssl.CERT_NONE
    try:
        with patch('app.services.http_client.build_opener', side_effect=[default, custom]) as build:
            first = Request('http://fixture.invalid/metrics', headers={'Authorization': 'Basic fixture-first'})
            second = Request('https://fixture.invalid/metrics')
            open_http(first, timeout=2)
            open_http('https://custom.invalid/metrics', context=tls, timeout=3)
            open_http(second, timeout=2)
            assert build.call_count == 2
            default_tls = build.call_args_list[0].args[1]._context
            assert default_tls is not tls and default_tls.verify_mode == ssl.CERT_REQUIRED
            assert build.call_args_list[1].args[1]._context is tls
            assert default.open.call_args_list[0].args[0] is first
            assert default.open.call_args_list[1].args[0] is second
            assert second.get_header('Authorization') is None
            assert custom.open.call_count == 1
    finally:
        _default_opener.cache_clear()


def test_mongodb_cache_miss_cannot_trigger_collection_from_viewer_get():
    client = TestClient(app)
    token = create_access_token({'sub': 'viewer', 'auth_provider': 'local'})
    with patch('app.api.clusters.cache.get_cluster_data', return_value=None), patch(
        'app.api.clusters.collector.collect_cluster_data', return_value=None) as collect:
        response = client.get('/api/clusters/local-mongo', headers={'Authorization': 'Bearer ' + token})
    assert response.status_code == 503
    collect.assert_not_called()


def test_mongodb_stepdown_failure_does_not_expose_remote_credentials(caplog):
    token = create_access_token({'sub': 'admin', 'auth_provider': 'local'})
    with patch('app.api.clusters.collector.step_down_primary', side_effect=RuntimeError('password=fixture-sensitive-value')):
        response = TestClient(app).post('/api/clusters/local-mongo/stepdown', json={'stepdown_seconds': 60},
                                        headers={'Authorization': 'Bearer ' + token})
    assert response.status_code == 500
    assert 'fixture-sensitive-value' not in response.text + caplog.text

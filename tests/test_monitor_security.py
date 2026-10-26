from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import psycopg
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app.api.auth import _materialize_current_user
from app.auth import create_access_token
from app.config import TargetConfig
from app.services.monitor_runtime import collect_target_batch, read_cached_target
from app.services.postgresql_collector import PostgresqlCollector, connection_kwargs

client=TestClient(app)

@pytest.mark.parametrize('path',['/api/targets','/api/clusters','/api/rabbitmq/local-rabbit','/api/redis/local-redis','/api/postgresql/test','/api/postgresql/test/hba','/api/stats'])
def test_anonymous_reads_are_closed(path):
    assert client.get(path).status_code==401

@pytest.mark.parametrize('method,path,body',[
    ('delete','/api/redis/local-redis/keys',None),
    ('post','/api/rabbitmq/local-rabbit/queues/purge',{'vhost':'/','queue':'fixture'}),
    ('post','/api/rabbitmq/local-rabbit/queues/delete',{'vhost':'/','queue':'fixture'}),
    ('post','/api/clusters/local-mongo/stepdown',{}),
])
def test_viewer_cannot_mutate(method,path,body):
    token=create_access_token({'sub':'viewer','auth_provider':'local','permissions':['*']})
    response=client.request(method,path,json=body,headers={'Authorization':f'Bearer {token}'})
    assert response.status_code==403,response.text


def test_viewer_cannot_read_hba():
    token=create_access_token({'sub':'viewer','auth_provider':'local','permissions':['*']})
    assert client.get('/api/postgresql/test/hba',headers={'Authorization':f'Bearer {token}'}).status_code==403


def test_local_roles_are_reloaded_and_removed_account_is_denied():
    config=SimpleNamespace(users=[{'username':'changed','roles':['viewer']}])
    with patch('app.api.auth.get_app_config',return_value=config):
        user=_materialize_current_user({'sub':'changed','auth_provider':'local','permissions':['*']})
        assert '*' not in user['permissions']
        assert 'postgresql.read' in user['permissions']
        with pytest.raises(HTTPException) as error:
            _materialize_current_user({'sub':'removed','auth_provider':'local','permissions':['*']})
        assert error.value.status_code==401


def test_postgresql_connections_force_read_only_and_timeouts():
    target=TargetConfig('test',[{'host':'database:5432'}],username='monitor',password='fixture-password')
    kwargs=connection_kwargs(target,target.nodes[0])
    assert 'default_transaction_read_only=on' in kwargs['options']
    assert 'statement_timeout=3000' in kwargs['options']
    assert kwargs['connect_timeout']==5
    assert kwargs['host']=='database'
    with pytest.raises(ValueError):
        connection_kwargs(target,{'host':'username:password@database:5432'})


def test_hba_sanitization_excludes_secret_options_and_error_text():
    row={'line_number':1,'auth_method':'ldap','options':['ldapbindpasswd=fixture-sensitive-value'],'error':'fixture-sensitive-value','invalid':True}
    connection=MagicMock();connection.execute.return_value.fetchall.return_value=[row]
    with patch('app.services.postgresql_collector.psycopg.connect') as connect:
        connect.return_value.__enter__.return_value=connection
        target=TargetConfig('test',[{'host':'database:5432'}])
        payload=PostgresqlCollector().collect_hba_rules(target)
    assert 'fixture-sensitive-value' not in str(payload)
    assert payload[0]['rules']==[{'line_number':1,'auth_method':'ldap','invalid':True}]


def test_database_failure_is_down_and_does_not_expose_credentials(caplog):
    target=TargetConfig('test',[{'host':'database:5432'}])
    config=SimpleNamespace(postgresql_targets=[target])
    with patch('app.services.postgresql_collector.get_app_config',return_value=config),patch('app.services.postgresql_collector.psycopg.connect',side_effect=psycopg.OperationalError('password=fixture-sensitive-value')):
        payload=PostgresqlCollector().collect_target_data('test')
    assert payload['overview']['status']=='DOWN'
    assert 'fixture-sensitive-value' not in str(payload)+caplog.text


def test_batch_isolates_failures_and_notifies_only_successful_cache_writes():
    targets=[SimpleNamespace(name=name) for name in ['good','down','uncached']]
    cached=[];notified=[]
    def collect(name):
        if name=='down':raise RuntimeError('fixture')
        return name,{'name':name}
    def cache(name,payload):
        if name=='uncached':raise RuntimeError('cache unavailable')
        cached.append(name)
    assert collect_target_batch(targets,collect,cache,notified.extend,'postgresql')==['good']
    assert cached==['good']
    assert notified==['postgresql:good']


def test_cache_unavailable_returns_503_without_sync_collection():
    with patch('app.services.monitor_runtime.get_configured_target'),patch('app.services.monitor_runtime.get_monitor_cache') as cache:
        cache.return_value.get_json.return_value=None
        with pytest.raises(HTTPException) as error:read_cached_target('postgresql','test')
    assert error.value.status_code==503


def test_oidc_unknown_group_is_denied_and_group_mapping_does_not_infer_admin():
    from app.services.oidc_service import user_from_oidc_claims
    config=SimpleNamespace(auth_config={'role_mappings':[{'group':'gryadka-viewers','roles':['viewer']}]})
    with patch('app.authorization.get_app_config',return_value=config):
        with pytest.raises(HTTPException) as error:
            user_from_oidc_claims({'sub':'fixture','groups':['unrelated']})
        assert error.value.status_code==403
        user=user_from_oidc_claims({'sub':'fixture','groups':['gryadka-viewers']})
    assert user['roles']==['viewer']
    assert '*' not in user['permissions']
    assert user['sub']=='oidc:fixture'


def test_oidc_identity_claims_cannot_inject_roles():
    from app.services.oidc_service import user_from_oidc_claims
    with pytest.raises(HTTPException):user_from_oidc_claims({'sub':'fixture','groups':'gryadka-admins'})


@pytest.mark.parametrize('groups', [[], ['unrelated'], ['gryadka-admins'], ['grpArgoCDAdminsTest']])
def test_oidc_authenticated_viewer_never_inherits_privileged_roles(groups):
    from app.authorization import DEFAULT_VIEWER_PERMISSIONS
    from app.services.oidc_service import user_from_oidc_claims
    config=SimpleNamespace(auth_config={
        'oidc':{'access_mode':'authenticated_viewer'},
        'role_mappings':[{'group':g,'roles':['admin']} for g in groups],
        'roles':{'viewer':['*']},
    })
    with patch('app.authorization.get_app_config',return_value=config):
        user=user_from_oidc_claims({'sub':'admin','groups':groups,'roles':['admin'],'permissions':['*']})
    assert user['sub']=='oidc:admin'
    assert user['roles']==['viewer']
    assert set(user['permissions'])==DEFAULT_VIEWER_PERMISSIONS


@pytest.mark.parametrize('method,path,body',[
    ('get','/api/postgresql/test/hba',None),
    ('get','/api/redis/local-redis/keys',None),
    ('get','/api/clusters/local-mongo/users',None),
    ('delete','/api/redis/local-redis/keys',{'keys':['fixture'],'db':0}),
    ('post','/api/rabbitmq/local-rabbit/queues/purge',{'vhost':'/','name':'fixture'}),
    ('post','/api/rabbitmq/local-rabbit/queues/delete',{'vhost':'/','name':'fixture'}),
    ('post','/api/clusters/local-mongo/stepdown',{'stepdown_seconds':60}),
])
def test_existing_privileged_oidc_session_is_read_only_in_viewer_mode(method,path,body):
    config=SimpleNamespace(auth_config={'oidc':{'access_mode':'authenticated_viewer'}})
    token=create_access_token({'sub':'oidc:fixture','auth_provider':'authentik',
        'groups':['gryadka-admins'],'roles':['admin'],'permissions':['*']})
    with patch('app.authorization.get_app_config',return_value=config):
        response=client.request(method,path,json=body,headers={'Authorization':f'Bearer {token}'})
    assert response.status_code==403


def test_oidc_viewer_mode_preserves_local_admin_and_rejects_unknown_mode():
    from app.authorization import build_user_payload
    from app.services.oidc_service import user_from_oidc_claims
    config=SimpleNamespace(auth_config={'oidc':{'access_mode':'authenticated_viewer'}},
        users=[{'username':'admin','roles':['admin']}])
    with patch('app.authorization.get_app_config',return_value=config), patch('app.api.auth.get_app_config',return_value=config):
        user=_materialize_current_user({'sub':'admin','auth_provider':'local'})
        assert user['roles']==['admin'] and user['permissions']==['*']
        assert build_user_payload(username='local-viewer',auth_provider='local',roles=['viewer'])['roles']==['viewer']
    config.auth_config={'oidc':{'access_mode':'unknown'},'role_mappings':[{'group':'admins','roles':['admin']}]}
    with patch('app.authorization.get_app_config',return_value=config), pytest.raises(HTTPException) as error:
        user_from_oidc_claims({'sub':'fixture','groups':['admins']})
    assert error.value.status_code==403


def test_oidc_jwt_keeps_groups_for_current_mapping_resolution():
    from app.auth import verify_token
    token=create_access_token({'sub':'oidc:fixture','auth_provider':'authentik','groups':['gryadka-viewers'],'permissions':['*']})
    config=SimpleNamespace(auth_config={'role_mappings':[{'group':'gryadka-viewers','roles':['viewer']}]})
    with patch('app.authorization.get_app_config',return_value=config):
        user=_materialize_current_user(verify_token(token))
    assert '*' not in user['permissions']
    assert 'postgresql.read' in user['permissions']


def test_cookie_mutations_reject_untrusted_origin():
    token=create_access_token({'sub':'admin','auth_provider':'local'})
    with TestClient(app) as origin_client:
        origin_client.cookies.set('gryadka_auth',token)
        response=origin_client.delete('/api/redis/local-redis/keys',headers={'Origin':'https://untrusted.example'})
    assert response.status_code==403


def test_rollout_namespace_cannot_escape_configuration():
    from app.api.argo_rollout import _resolve_namespace
    target=TargetConfig('fixture',[],options={'namespace':'allowed'})
    config=SimpleNamespace(get_targets_by_type=lambda _: [target])
    with patch('app.api.argo_rollout.get_app_config',return_value=config):
        assert _resolve_namespace('fixture',None)=='allowed'
        with pytest.raises(HTTPException) as error:_resolve_namespace('fixture','argocd')
    assert error.value.status_code==403


@pytest.mark.parametrize('node,expected',[
    ({'host':'redis:6379'},('redis',6379)),
    ({'host':'redis','port':6380},('redis',6380)),
    ('[::1]:5432',('::1',5432)),
    ({'host':'redis'},('redis',6379)),
])
def test_shared_endpoint_parser(node,expected):
    from app.services.endpoints import parse_host_port
    assert parse_host_port(node,6379)==expected


def test_target_passwords_cannot_be_loaded_from_configmap():
    from app.config import AppConfig
    with pytest.raises(ValueError):AppConfig._resolve_credentials('redis','fixture',None,'unsafe-inline-password')


def test_empty_monitor_configuration_does_not_enable_all_queues():
    from app.services import celery_app as module
    with patch('app.services.celery_app.get_app_config',return_value=SimpleNamespace(get_enabled_monitor_types=lambda: [])):
        assert module._resolve_enabled_monitor_types()==[]


def test_lock_acquisition_error_is_preserved_instead_of_suppressed():
    from app.services.tasks import _collector_lock
    with patch('app.services.tasks._get_system_cache') as cache:
        cache.return_value.redis_client.set.side_effect=RuntimeError('fixture-lock-error')
        with pytest.raises(RuntimeError,match='fixture-lock-error'):
            with _collector_lock('fixture'):pass


@pytest.mark.parametrize('url',['file:///etc/passwd','ftp://fixture/file','https://user:password@fixture/path'])
def test_shared_http_transport_rejects_non_http_and_embedded_credentials(url):
    from app.services.http_client import open_http
    with pytest.raises(ValueError):open_http(url)


def test_https_rabbitmq_target_cannot_downgrade_to_http():
    from app.services.rabbitmq_collector import RabbitMQCollector
    urls=RabbitMQCollector()._build_base_urls('fixture',{'scheme':'https'})
    assert urls
    assert all(url.startswith('https://') for url in urls)


def test_config_rejects_nested_node_passwords_and_auth_hashes():
    from app.config import reject_inline_secrets
    with pytest.raises(ValueError):reject_inline_secrets({'mongodb':[{'nodes':[{'password':'fixture-sensitive'}]}]})
    with pytest.raises(ValueError):reject_inline_secrets({'users':[{'password_hash':'fixture-sensitive'}]})
    reject_inline_secrets({'credentialsSecretName':'fixture-secret','oidc':{'issuer':'https://fixture.invalid/'}})


def test_common_log_redaction_covers_environment_credentials_nested_payloads_and_uris(monkeypatch):
    import logging,json
    from app.logging_setup import JsonLogFormatter
    monkeypatch.setenv('MONITOR_FIXTURE_PASSWORD','fixture-sensitive-password')
    record=logging.LogRecord('fixture',logging.ERROR,'fixture.py',1,
        'failed redis://user:fixture-sensitive-password@fixture:6379 token=%s',("{'access_token': 'fixture-dynamic-token'}",),None)
    record.details={'password':'fixture-sensitive-password','count':2}
    rendered=JsonLogFormatter('fixture').format(record)
    assert 'fixture-sensitive-password' not in rendered
    assert 'fixture-dynamic-token' not in rendered
    assert json.loads(rendered)['details']['count']==2


@pytest.mark.parametrize('key',['saslPassword','sasl_plain_password'])
def test_kafka_transport_cannot_take_inline_passwords(key):
    from app.config import reject_inline_secrets
    with pytest.raises(ValueError):reject_inline_secrets({'options':{key:'fixture-sensitive-value'}})

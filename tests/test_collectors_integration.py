import os
from unittest.mock import patch
from types import SimpleNamespace

import pytest
import psycopg
from app.config import get_app_config
from app.services.postgresql_collector import PostgresqlCollector, connection_kwargs
from app.services.tasks import _collect_all_clusters_sync, _collect_all_rabbitmq_sync, _collect_all_redis_sync, _collect_all_postgresql_sync
from app.services.monitor_runtime import read_cached_target
from app.main import app
from fastapi.testclient import TestClient

pytestmark=pytest.mark.skipif(os.getenv('GRYADKA_INTEGRATION')!='1',reason='requires isolated database containers')


def test_all_four_collectors_against_real_local_databases():
    assert _collect_all_clusters_sync()==['local-mongo']
    assert _collect_all_rabbitmq_sync()==['local-rabbit']
    assert _collect_all_redis_sync()==['local-redis']
    assert _collect_all_postgresql_sync()==['test']
    payload=read_cached_target('postgresql','test')
    assert payload['overview']['status']=='UP'
    node=payload['nodes'][0]
    assert node['monitoring_role']=='gryadka_monitor'
    assert node['databases']
    assert node['max_connections']>0
    assert node['activity']['waiting_for_lock']>=0


def test_real_postgresql_role_is_non_superuser_and_session_read_only():
    target=get_app_config().postgresql_targets[0]
    with psycopg.connect(**connection_kwargs(target,target.nodes[0])) as connection:
        assert connection.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']=='on'
        assert connection.execute('SELECT rolsuper FROM pg_roles WHERE rolname=current_user').fetchone()['rolsuper'] is False
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            connection.execute('CREATE TABLE must_never_exist (id int)')


def test_hba_reads_work_for_authorized_operator_and_not_for_viewer():
    with TestClient(app) as client:
        viewer=client.post('/api/auth/login',json={'username':'viewer','password':'fixture-viewer-local-only'}).json()
        assert client.get('/api/postgresql/test/hba',headers={'Authorization':'Bearer '+viewer['access_token']}).status_code==403
        operator=client.post('/api/auth/login',json={'username':'operator','password':'fixture-operator-local-only'}).json()
        response=client.get('/api/postgresql/test/hba',headers={'Authorization':'Bearer '+operator['access_token']})
        assert response.status_code==200,response.text
        assert response.json()['nodes'][0]['rules']
        assert 'options' not in response.text
        assert 'fixture-monitor-local-only' not in response.text


def test_privileged_operations_work_only_on_disposable_local_objects():
    import redis
    import urllib.request
    import base64
    # The test object is explicitly disposable; no existing application data is touched.
    redis.Redis(host='redis').set('gryadka:fixture:delete','fixture-value')
    credential=base64.b64encode(b'fixture_admin:fixture-admin-local-only').decode()
    request=urllib.request.Request('http://rabbitmq:15672/api/queues/%2F/gryadka-fixture-delete',data=b'{"durable":false}',method='PUT',headers={'Authorization':'Basic '+credential,'Content-Type':'application/json'})
    urllib.request.urlopen(request).close()
    with TestClient(app) as client:
        user=client.post('/api/auth/login',json={'username':'operator','password':'fixture-operator-local-only'}).json()
        headers={'Authorization':'Bearer '+user['access_token']}
        response=client.request('DELETE','/api/redis/local-redis/keys',json={'keys':['gryadka:fixture:delete'],'db':0},headers=headers)
        assert response.status_code==200,response.text
        assert redis.Redis(host='redis').exists('gryadka:fixture:delete')==0
        with patch('app.api.rabbitmq.celery_app.send_task', return_value=SimpleNamespace(id='fixture-refresh')):
            response=client.post('/api/rabbitmq/local-rabbit/queues/delete',json={'vhost':'/','name':'gryadka-fixture-delete'},headers=headers)
        assert response.status_code==200,response.text

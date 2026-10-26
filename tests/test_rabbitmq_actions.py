from unittest.mock import AsyncMock, patch
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.api.auth import get_current_user
from app.authorization import DEFAULT_ROLE_PERMISSIONS
from app.config import TargetConfig
from app.services.rabbitmq_collector import RabbitMQCollector


@pytest.mark.parametrize('allowed,action,expected',[
    ('rabbitmq.messages.purge','purge',200),('rabbitmq.messages.purge','delete',403),
    ('rabbitmq.queues.delete','purge',403),('rabbitmq.queues.delete','delete',200),
    ('rabbitmq.queues.write','purge',403),('rabbitmq.queues.write','delete',403),
])
def test_queue_operations_have_independent_permissions(allowed,action,expected):
    user={'sub':'fixture','username':'fixture','permissions':['rabbitmq.read',allowed]}
    app.dependency_overrides[get_current_user]=lambda:user
    reply={'status':'queued_refresh','action':action,'target_name':'fixture','vhost':'/','queue':'jobs','refresh_task_id':'fixture-task'}
    try:
        with patch('app.api.rabbitmq._run_queue_action',new_callable=AsyncMock,return_value=reply) as run:
            response=TestClient(app).post('/api/rabbitmq/fixture/queues/'+action,json={'name':'jobs','vhost':'/'},headers={'Authorization':'Bearer fixture'})
            assert response.status_code==expected,response.text
            if expected==200:assert run.await_args.args[2]==action
            else:run.assert_not_called()
    finally:app.dependency_overrides.pop(get_current_user,None)


def test_rabbitmq_operator_role_keeps_both_actions_without_broad_write_alias():
    permissions=DEFAULT_ROLE_PERMISSIONS['rabbitmq-rw']
    assert {'rabbitmq.messages.purge','rabbitmq.queues.delete'}<=permissions
    assert 'rabbitmq.queues.write' not in permissions


@pytest.mark.parametrize('action,suffix',[('purge','/contents'),('delete','')])
def test_action_paths_preserve_exact_queue_and_vhost_identifiers(action,suffix):
    collector=RabbitMQCollector();target=TargetConfig('fixture',[{'host':'http://localhost:15672'}])
    with patch.object(collector,'_get_target_config',return_value=target),patch.object(collector,'_request_empty',return_value=204) as request:
        collector.manage_queue('fixture',' app ',' jobs ',action)
    assert request.call_args.args[1]=='/api/queues/%20app%20/%20jobs%20'+suffix
    assert request.call_args.kwargs['method']=='DELETE'

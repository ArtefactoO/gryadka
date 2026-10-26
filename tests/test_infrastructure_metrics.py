from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import pytest

from app.config import TargetConfig
from app.services.infrastructure_metrics import KubernetesMetricsService, enrich_node_resources, _cache
from app.services.node_exporter_metrics import NodeExporterMetricsService
from app.services.victoriametrics_client import MetricsQueryError


@pytest.fixture(autouse=True)
def clear_metric_cache():
    _cache.clear()


def vector(pod, container, value):
    return {'metric': {'pod': pod, 'container': container}, 'value': [1, str(value)]}


def test_kubernetes_dns_never_uses_vm_metrics():
    target=TargetConfig('mongo',[{'host':'mongo.test.svc.cluster.local:27017'}])
    nodes=[{'name':'mongo.test.svc.cluster.local:27017','node_exporter':{'matched':True}}]
    config=SimpleNamespace(victoria_metrics_config={'baseUrl':'http://fixture'})
    with patch('app.services.infrastructure_metrics.get_app_config',return_value=config),patch('app.services.infrastructure_metrics.KubernetesMetricsService') as kubernetes,patch('app.services.infrastructure_metrics.NodeExporterMetricsService') as vm:
        assert enrich_node_resources(nodes,target)[0]['infrastructure']['kind']=='kubernetes'
        assert 'node_exporter' not in nodes[0]
        vm.from_config.assert_not_called()
        assert kubernetes.return_value.enrich.call_args.args[-1]=='test'


def test_explicit_none_does_not_initialize_network_clients():
    target=TargetConfig('disabled',[{'host':'service:123'}],infrastructure={'kind':'none'})
    with patch('app.services.infrastructure_metrics.VictoriaMetricsClient') as metrics:
        rows=enrich_node_resources([{'name':'service:123'}],target)
    assert rows[0]['infrastructure']=={'kind':'none','state':'disabled'}
    metrics.assert_not_called()


def test_pod_metrics_do_not_invent_limits_and_do_not_mix_similar_services():
    client=MagicMock(base_url='http://fixture')
    def query(promql):
        assert 'namespace="test"' in promql
        if 'cpu_usage_seconds_total' in promql:
            assert 'container_last_seen' in promql
            return [vector('mongo-0','mongo',0.02),vector('mongo-mocks-0','mongo',1)]
        if 'working_set' in promql:
            return [vector('mongo-0','mongo',1024),vector('mongo-mocks-0','mongo',99999)]
        if 'memory_limit_bytes' in promql:
            return [vector('mongo-0','mongo',2**63)]
        if 'cpu_quota' in promql:
            return [vector('mongo-0','mongo',-1)]
        return []
    client.query.side_effect=query
    nodes=[{'name':'service.test.svc.cluster.local','infrastructure':{'kind':'kubernetes','state':'unavailable'}}]
    service=KubernetesMetricsService(client)
    service.enrich(nodes,{'podPattern':'mongo-[0-9]+','container':'mongo'},'test')
    result=nodes[0]['infrastructure']
    assert result['state']=='available' and len(result['pods'])==1
    assert result['resources']['cpu_usage_cores']==0.02
    assert result['resources']['memory_usage_bytes']==1024
    assert result['resources']['memory_limit_bytes'] is None
    assert result['resources']['cpu_limit_cores'] is None
    assert result['resources']['cpu_request_cores'] is None


def test_unmapped_service_is_explicitly_unavailable_not_guessed():
    client=MagicMock(base_url='http://fixture');client.query.return_value=[vector('unrelated-0','worker',1)]
    nodes=[{'name':'service.test.svc.cluster.local','infrastructure':{'kind':'kubernetes','state':'unavailable'}}]
    KubernetesMetricsService(client).enrich(nodes,{},'test')
    assert nodes[0]['infrastructure']['state']=='unavailable'
    assert 'resources' not in nodes[0]['infrastructure']


def test_namespace_is_validated_before_querying():
    client=MagicMock(base_url='http://fixture')
    with pytest.raises(ValueError):KubernetesMetricsService(client).collect('test"} or vector(1)')
    client.query.assert_not_called()


def test_vm_strict_matching_cannot_match_short_name_of_another_domain():
    service=NodeExporterMetricsService(MagicMock())
    assert service._resolve_instance(['db.test.example'],['db:9100'],{},strict=True)==(None,None)
    assert service._resolve_instance(['10.1.1.1'],['10.1.1.1:9100'],{},strict=True)==('10.1.1.1:9100','instance')


def test_metric_outage_does_not_mark_database_down():
    target=TargetConfig('test',[{'host':'service.test.svc.cluster.local'}])
    config=SimpleNamespace(victoria_metrics_config={'baseUrl':'http://fixture'})
    with patch('app.services.infrastructure_metrics.get_app_config',return_value=config),patch('app.services.infrastructure_metrics.KubernetesMetricsService.enrich',side_effect=TimeoutError):
        node=enrich_node_resources([{'name':'service','status':'UP'}],target)[0]
    assert node['status']=='UP' and node['infrastructure']['state']=='unavailable'



def test_vm_metric_failure_drops_old_resource_provenance():
    target=TargetConfig('vm',[{'host':'new-host:5432'}],infrastructure={'kind':'vm'})
    config=SimpleNamespace(victoria_metrics_config={'baseUrl':'http://fixture'})
    node={'name':'new-host:5432','node_exporter':{'matched':True,'matched_value':'old-host:9100'}}
    with patch('app.services.infrastructure_metrics.get_app_config',return_value=config),patch('app.services.infrastructure_metrics.NodeExporterMetricsService.from_config',side_effect=TimeoutError):
        result=enrich_node_resources([node],target)[0]
    assert 'node_exporter' not in result
    assert result['infrastructure']['state']=='unavailable'


def test_configured_metrics_url_enables_host_collection_without_a_second_hidden_flag():
    config = SimpleNamespace(get_victoria_metrics_config=lambda: {'baseUrl': 'http://fixture'})
    with patch('app.services.node_exporter_metrics.get_app_config', return_value=config):
        assert NodeExporterMetricsService.from_config().client.base_url == 'http://fixture'


def test_explicit_disabled_resources_and_docker_never_query_host_metrics():
    for hint, settings, expected in [
        ({'kind': 'vm'}, {'baseUrl': 'http://fixture', 'enabled': False}, 'collection_disabled'),
        ({'kind': 'docker'}, {'baseUrl': 'http://fixture'}, 'docker_not_configured'),
        ({'kind': 'vm'}, {}, 'source_not_configured'),
    ]:
        config = SimpleNamespace(victoria_metrics_config=settings)
        target = TargetConfig('fixture', [{'host': 'db:5432'}], infrastructure=hint)
        with patch('app.services.infrastructure_metrics.get_app_config', return_value=config), patch('app.services.infrastructure_metrics.NodeExporterMetricsService.from_config') as host:
            result = enrich_node_resources([{'name': 'db:5432', 'status': 'UP'}], target)[0]
        assert result['infrastructure']['reason_code'] == expected
        assert result['status'] == 'UP'
        host.assert_not_called()


@pytest.mark.parametrize('error,code', [(TimeoutError(), 'source_unavailable'), (MetricsQueryError(), 'query_failed')])
def test_source_outage_and_rejected_query_are_not_reported_as_absent_series(error, code):
    target = TargetConfig('fixture', [{'host': 'db:5432'}], infrastructure={'kind': 'vm'})
    config = SimpleNamespace(victoria_metrics_config={'baseUrl': 'http://fixture'})
    with patch('app.services.infrastructure_metrics.get_app_config', return_value=config), patch('app.services.infrastructure_metrics.NodeExporterMetricsService.from_config') as source:
        source.return_value.enrich_nodes.side_effect = error
        result = enrich_node_resources([{'name': 'db:5432', 'status': 'UP'}], target)[0]
    assert result['infrastructure']['reason_code'] == code


def test_reachable_source_distinguishes_no_host_series_from_no_exact_host_match():
    service = NodeExporterMetricsService('http://fixture')
    empty = {field: {} for field in ['cpu', 'cpu_cores', 'memory_used', 'memory_total', 'memory_available', 'disks', 'up', 'nodename']}
    for vectors, expected in [(empty, 'series_missing'), ({**empty, 'cpu': {'other-host:9100': 10}}, 'host_unmatched')]:
        nodes = [{'name': 'db:5432', 'infrastructure': {'kind': 'vm', 'state': 'unavailable'}}]
        with patch.object(service, '_collect_vectors', return_value=vectors):
            service.enrich_nodes(nodes, lambda node: ['db'], strict=True, diagnostics=True)
        assert nodes[0]['infrastructure']['reason_code'] == expected
        assert 'node_exporter' not in nodes[0]


def test_unavailable_postgres_identity_never_uses_the_proxy_as_a_resource_host():
    target = TargetConfig('fixture', [{'host': 'proxy:5432'}], infrastructure={'kind': 'vm'})
    config = SimpleNamespace(victoria_metrics_config={'baseUrl': 'http://fixture'})
    node = {'name': 'proxy:5432', 'status': 'DOWN', 'server_host': None}
    with patch('app.services.infrastructure_metrics.get_app_config', return_value=config), patch('app.services.infrastructure_metrics.NodeExporterMetricsService.from_config') as host:
        result = enrich_node_resources([node], target)[0]
        host.assert_not_called()
    assert result['infrastructure']['reason_code'] == 'host_unknown'
    assert 'node_exporter' not in result

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.config import TargetConfig
from app.services.kafka_version import enrich_kafka_versions, image_version


def fixture():
    target = TargetConfig('fixture', [{'host': 'kafka-0.kafka.test.svc.cluster.local:9092'}],
                          infrastructure={'kind': 'kubernetes', 'namespace': 'test', 'container': 'broker'})
    nodes = [{'id': i, 'host': f'kafka-{i}.kafka.test.svc.cluster.local', 'port': 9092} for i in range(2)]
    config = SimpleNamespace(victoria_metrics_config={'baseUrl': 'http://metrics.invalid', 'kubernetesLabels': {'cluster': 'test'}})
    return target, nodes, config


def metric(pod, **labels):
    return {'metric': {'namespace': 'test', 'cluster': 'test', 'pod': pod, **labels}, 'value': [0, '1']}


@pytest.mark.parametrize('image,expected', [
    ('registry.invalid:5000/team/kafka:5.7.2-custom-build', '5.7.2-custom-build'),
    ('team/kafka:v9.2.1-rc1', '9.2.1-rc1'),
    ('team/kafka:latest', None), ('team/kafka@sha256:abc', None),
    ('team/zookeeper:3.9.1', None), ('team/kafka:unknown', None),
])
def test_image_tag_is_explicit_and_never_guessed(image, expected):
    assert image_version(image) == expected


def test_runtime_build_takes_precedence_without_querying_images():
    target, nodes, config = fixture()
    builds = [metric(f'kafka-{i}', version='5.7.2') for i in range(2)]
    with patch('app.services.kafka_version.get_app_config', return_value=config), patch(
            'app.services.kafka_version.VictoriaMetricsClient') as client:
        client.return_value.query.return_value = builds
        summary = enrich_kafka_versions(nodes, target)
    assert summary == {'value': '5.7.2', 'versions': ['5.7.2'], 'source': 'app_info', 'complete': True}
    assert client.return_value.query.call_count == 1


def test_image_fallback_matches_cluster_namespace_pod_and_container():
    target, nodes, config = fixture()
    images = [metric(f'kafka-{i}', container='broker', image='team/kafka:5.7.2-build') for i in range(2)]
    images += [metric('kafka-0', container='sidecar', image='team/kafka:1.0.0'),
               metric('kafka-3', container='broker', image='team/kafka:2.0.0'),
               metric('kafka-0', namespace='other', container='broker', image='team/kafka:3.0.0'),
               metric('kafka-0', cluster='production', container='broker', image='team/kafka:4.0.0')]
    with patch('app.services.kafka_version.get_app_config', return_value=config), patch(
            'app.services.kafka_version.VictoriaMetricsClient') as client:
        client.return_value.query.side_effect = [[], images]
        summary = enrich_kafka_versions(nodes, target)
    assert summary['value'] == '5.7.2-build' and summary['source'] == 'container_image'
    queries = [call.args[0] for call in client.return_value.query.call_args_list]
    assert all('namespace="test"' in q and 'cluster="test"' in q and 'pod=~' in q for q in queries)
    assert 'time() - 120' in queries[1]


def test_rolling_upgrade_and_partial_data_never_claim_one_cluster_version():
    target, nodes, config = fixture()
    with patch('app.services.kafka_version.get_app_config', return_value=config), patch(
            'app.services.kafka_version.VictoriaMetricsClient') as client:
        client.return_value.query.side_effect = [[metric('kafka-0', version='5.7.2'), metric('kafka-1', version='5.8.0')]]
        mixed = enrich_kafka_versions(nodes, target)
        assert mixed['value'] is None and mixed['complete'] and mixed['versions'] == ['5.7.2', '5.8.0']
        client.return_value.query.side_effect = [[metric('kafka-0', version='5.7.2')], []]
        partial = enrich_kafka_versions(nodes, target)
    assert partial['value'] is None and not partial['complete'] and partial['versions'] == ['5.7.2']


def test_conflicting_scrapes_and_image_versions_stay_unknown():
    target, nodes, config = fixture()
    with patch('app.services.kafka_version.get_app_config', return_value=config), patch(
            'app.services.kafka_version.VictoriaMetricsClient') as client:
        client.return_value.query.side_effect = [
            [metric('kafka-0', version='5.7.2'), metric('kafka-0', version='5.8.0')],
            [metric('kafka-1', container='broker', image=f'team/kafka:{v}') for v in ['5.7.2', '5.8.0']]]
        summary = enrich_kafka_versions(nodes, target)
    assert summary['versions'] == [] and not summary['complete']
    assert all(n['version_info']['value'] is None for n in nodes)


def test_metrics_failure_preserves_known_build_and_never_changes_health(caplog):
    target, nodes, config = fixture()
    nodes[0]['status'] = 'UP'
    with patch('app.services.kafka_version.get_app_config', return_value=config), patch(
            'app.services.kafka_version.VictoriaMetricsClient') as client:
        client.return_value.query.side_effect = [[metric('kafka-0', version='5.7.2')], RuntimeError('fixture-sensitive-value')]
        summary = enrich_kafka_versions(nodes, target)
    assert nodes[0]['status'] == 'UP' and nodes[0]['version_info']['value'] == '5.7.2'
    assert summary['value'] is None and not summary['complete']
    assert 'fixture-sensitive-value' not in caplog.text + str(nodes)


@pytest.mark.parametrize('metrics', [{}, {'enabled': False, 'baseUrl': 'http://metrics.invalid'}])
def test_no_source_means_no_network_or_version_guess(metrics):
    target, nodes, _ = fixture()
    with patch('app.services.kafka_version.get_app_config', return_value=SimpleNamespace(victoria_metrics_config=metrics)), patch(
            'app.services.kafka_version.VictoriaMetricsClient') as client:
        summary = enrich_kafka_versions(nodes, target)
    client.assert_not_called()
    assert not summary['complete'] and summary['versions'] == []

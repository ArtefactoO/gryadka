from types import SimpleNamespace
from unittest.mock import patch
import pytest
from kafka import TopicPartition
from app.config import TargetConfig
from app.services.kafka_collector import client_options,topic_summary,KafkaCollector
from app.services.kafka_protocol import group_lag, KafkaOffsetReader


def test_kafka_ssl_sasl_uses_configured_transport_and_no_plaintext_fallback():
    target=TargetConfig('fixture',[{'host':'broker:9093'}],username='monitor',password='fixture',options={'securityProtocol':'SASL_SSL','saslMechanism':'SCRAM-SHA-512'})
    options=client_options(target)
    assert options['security_protocol']=='SASL_SSL'
    assert options['ssl_check_hostname'] is True
    assert options['sasl_mechanism']=='SCRAM-SHA-512'
    with pytest.raises(ValueError):client_options(TargetConfig('fixture',[],options={'securityProtocol':'BAD'}))


def test_missing_offsets_are_unknown_not_zero_lag():
    partition=TopicPartition('fixture',0)
    row=group_lag('fixture',{partition:SimpleNamespace(offset=-1)},{partition:20})
    assert row['lag'] is None
    assert row['status']=='UNKNOWN'
    assert group_lag('fixture',{partition:SimpleNamespace(offset=10)},{partition:20})['lag']==10


def test_isr_and_missing_leader_contract():
    parts,urp,offline=topic_summary([{'topic':'fixture','partitions':[{'partition':0,'leader':-1,'replicas':[1,2,3],'isr':[1,2]}]}])
    assert len(parts)==1 and urp==1 and offline==1


def test_unconfigured_kafka_never_initializes_network_clients():
    with patch('app.services.kafka_collector.get_app_config',return_value=SimpleNamespace(kafka_targets=[])),patch('app.services.kafka_collector.KafkaAdminClient') as admin:
        assert KafkaCollector().collect_target_data('fixture') is None
        admin.assert_not_called()


def test_consumer_only_reads_end_offsets_and_never_joins_or_commits():
    target=TargetConfig('fixture',[{'host':'broker:9092'}]);partition=TopicPartition('fixture',0)
    with patch('app.services.kafka_collector.get_app_config',return_value=SimpleNamespace(kafka_targets=[target])),patch('app.services.kafka_collector.KafkaAdminClient') as admin,patch('app.services.kafka_collector.KafkaConsumer',autospec=True) as consumer:
        admin.return_value.describe_cluster.return_value={'brokers':[{'node_id':1,'host':'broker','port':9092}],'controller_id':1}
        admin.return_value.describe_topics.return_value=[]
        admin.return_value.list_consumer_groups.return_value=[('fixture','consumer')]
        admin.return_value.list_consumer_group_offsets.return_value={partition:SimpleNamespace(offset=5)}
        consumer.return_value.topics.return_value={'fixture'}
        consumer.return_value.partitions_for_topic.return_value={0}
        consumer.return_value.end_offsets.return_value={partition:9}
        payload=KafkaCollector().collect_target_data('fixture')
        assert payload['overview']['total_lag']==4
        assert consumer.call_args.kwargs['group_id'] is None
        assert consumer.call_args.kwargs['enable_auto_commit'] is False
        for method in ['poll','subscribe','assign','commit']:
            getattr(consumer.return_value,method).assert_not_called()


@pytest.mark.parametrize('cluster,topics', [
    ({'brokers':[]}, []),
    ({'brokers':[{'node_id':1,'host':'broker','port':9092}]}, [{'topic':'restricted','error_code':29,'partitions':[]}]),
])
def test_incomplete_metadata_is_never_healthy(cluster,topics):
    target=TargetConfig('fixture',[{'host':'broker:9092'}])
    with patch('app.services.kafka_collector.get_app_config',return_value=SimpleNamespace(kafka_targets=[target])),patch('app.services.kafka_collector.KafkaAdminClient') as admin,patch('app.services.kafka_collector.KafkaConsumer'):
        admin.return_value.describe_cluster.return_value=cluster
        admin.return_value.describe_topics.return_value=topics
        admin.return_value.list_consumer_groups.return_value=[]
        payload=KafkaCollector().collect_target_data('fixture')
        assert payload['overview']['status']=='DEGRADED'
        assert payload['overview']['metadata_complete'] is False


def test_offset_reader_resolves_metadata_once_and_preserves_deleted_topics():
    from unittest.mock import MagicMock
    consumer=MagicMock();consumer.topics.return_value={'live'}
    consumer.partitions_for_topic.return_value={0}
    live=TopicPartition('live',0);deleted=TopicPartition('deleted',0)
    consumer.end_offsets.return_value={live:20}
    reader=KafkaOffsetReader(consumer)
    row=reader.read('one',{live:SimpleNamespace(offset=10),deleted:SimpleNamespace(offset=5)})
    assert row['lag'] is None and row['status']=='UNKNOWN'
    consumer.end_offsets.assert_called_once_with([live])
    assert row['partitions'][1]['end_offset'] is None
    assert reader.read('two',{live:SimpleNamespace(offset=15)})['lag']==5
    consumer.topics.assert_called_once()
    for method in ['poll','subscribe','assign','commit']:
        getattr(consumer,method).assert_not_called()


def test_shared_transport_preserves_explicit_tls_for_redpanda_and_bootstrap_hosts():
    from app.services.kafka_protocol import client_options as shared_options
    target=TargetConfig('fixture',[],username='monitor',password='fixture',options={
        'bootstrapServers':['broker:9093'],'securityProtocol':'SASL_SSL','saslMechanism':'SCRAM-SHA-256','sslCaFile':'/trusted/ca.pem'})
    options=shared_options(target,default_sasl_protocol='SASL_PLAINTEXT',default_sasl_mechanism='PLAIN')
    assert options['security_protocol']=='SASL_SSL' and options['ssl_check_hostname']
    assert options['ssl_cafile']=='/trusted/ca.pem' and options['bootstrap_servers']==['broker:9093']


@pytest.mark.skipif(not __import__('os').environ.get('GRYADKA_REDPANDA_COMPAT_HOST'),reason='Requires isolated seeded Redpanda fixture')
def test_kafka_and_redpanda_collectors_share_real_protocol_offsets():
    import os
    from app.services.redpanda_collector import RedpandaCollector
    target=TargetConfig('compatibility',[{'host':os.environ['GRYADKA_REDPANDA_COMPAT_HOST']}],options={'securityProtocol':'PLAINTEXT'})
    config=SimpleNamespace(kafka_targets=[target],get_redpanda_target_by_name=lambda _:target)
    with patch('app.services.kafka_collector.get_app_config',return_value=config):
        kafka=KafkaCollector().collect_target_data(target.name)
    collector=RedpandaCollector()
    try:
        with patch('app.services.redpanda_collector.get_app_config',return_value=config):
            redpanda=collector.collect_target_data(target.name)
        assert kafka['overview']['total_lag']==redpanda['overview']['total_lag']==3
        assert kafka['overview']['lag_complete'] and redpanda['overview']['lag_complete']
    finally:collector._invalidate_admin(target.name)

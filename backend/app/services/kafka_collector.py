"""Kafka metadata and offsets monitoring. No subscriptions, commits or records."""
import logging
from datetime import datetime, timezone
from kafka import KafkaAdminClient, KafkaConsumer
from app.config import get_app_config
from app.services.infrastructure_metrics import enrich_node_resources
from app.services.kafka_protocol import KafkaOffsetReader, client_options
from app.services.kafka_version import enrich_kafka_versions

logger=logging.getLogger(__name__)


def topic_summary(topics):
    partitions=[]
    for topic in topics:
        for part in topic.get('partitions',[]):
            partitions.append({'topic':topic['topic'],'partition':part['partition'],'leader':part['leader'],
                               'replicas':part.get('replicas',[]),'isr':part.get('isr',[])})
    return partitions, sum(len(p['isr'])<len(p['replicas']) for p in partitions),sum(p['leader']<0 for p in partitions)



class KafkaCollector:
    def collect_target_data(self,name):
        config=get_app_config()
        target=next((t for t in config.kafka_targets if t.name==name),None) if config else None
        if target is None:return None
        admin=None;consumer=None
        try:
            options=client_options(target)
            admin=KafkaAdminClient(**options)
            cluster=admin.describe_cluster()
            topics=admin.describe_topics()
            partitions,urp,offline=topic_summary(topics)
            groups_raw=[g[0] for g in admin.list_consumer_groups() if g[1] in {'','consumer'}]
            limit=max(1,min(int(target.options.get('maxGroups',1000)),10000))
            consumer=KafkaConsumer(**options,group_id=None,enable_auto_commit=False,consumer_timeout_ms=1000)
            reader=KafkaOffsetReader(consumer)
            groups=[]
            for group_id in groups_raw[:limit]:
                try:
                    offsets=admin.list_consumer_group_offsets(group_id)
                    groups.append(reader.read(group_id,offsets))
                except Exception as exc:
                    logger.warning('Kafka group metadata unavailable: target=%s error_type=%s',name,type(exc).__name__)
                    groups.append({'group_id':group_id,'lag':None,'partitions':[],'status':'UNAVAILABLE'})
            brokers=[{'id':b['node_id'],'host':b['host'],'port':b['port'],'controller':b['node_id']==cluster.get('controller_id')} for b in cluster.get('brokers',[])]
            brokers=enrich_node_resources(brokers,target)
            version_info=enrich_kafka_versions(brokers,target)
            metadata_complete=bool(brokers) and all(not t.get('error_code',0) and all(not p.get('error_code',0) for p in t.get('partitions',[])) for t in topics)
            complete=len(groups_raw)<=limit and all(g['lag'] is not None for g in groups)
            status='DEGRADED' if urp or offline or not complete or not metadata_complete else 'UP'
            return {'name':name,'overview':{'status':status,'cluster_id':cluster.get('cluster_id'),'brokers':len(brokers),
                    'metadata_complete':metadata_complete,'topics':len(topics),'partitions':len(partitions),'under_replicated':urp,'offline_partitions':offline,
                    'consumer_groups':len(groups_raw),'groups_collected':len(groups),
                    'total_lag':sum(g['lag'] for g in groups) if complete else None,'lag_complete':complete},
                    'version_info':version_info,'nodes':brokers,'topics':topics,'partitions':partitions,'consumer_groups':groups,
                    'last_update':datetime.now(timezone.utc).isoformat()}
        except Exception as exc:
            logger.warning('Kafka collection failed: target=%s error_type=%s',name,type(exc).__name__)
            return {'name':name,'overview':{'status':'DOWN','total_lag':None,'lag_complete':False},'nodes':[],
                    'topics':[],'partitions':[],'consumer_groups':[],'error':'Метаданные Kafka недоступны.',
                    'last_update':datetime.now(timezone.utc).isoformat()}
        finally:
            for client in [consumer,admin]:
                if client is not None:
                    try:client.close()
                    except Exception as exc:logger.warning("Kafka client close failed: error_type=%s",type(exc).__name__)

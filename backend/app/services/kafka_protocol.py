"""Kafka protocol transport and offset metadata shared by Kafka and Redpanda."""
from app.services.endpoints import parse_host_port

def client_options(target, *, default_sasl_protocol="SASL_SSL", default_sasl_mechanism="SCRAM-SHA-512"):
    options=target.options
    protocol=str(options.get('securityProtocol',default_sasl_protocol if target.username else 'PLAINTEXT')).upper()
    if protocol not in {'PLAINTEXT','SSL','SASL_PLAINTEXT','SASL_SSL'}:
        raise ValueError('Unsupported Kafka security protocol')
    servers=[]
    nodes = options.get('bootstrapServers') or options.get('bootstrap_servers') or target.nodes
    if isinstance(nodes, str):
        nodes = [value.strip() for value in nodes.split(',') if value.strip()]
    for node in nodes:
        host,port=parse_host_port(node if isinstance(node, dict) else {'host':node},int(options.get('port',9092)))
        servers.append(f'[{host}]:{port}' if ':' in host else f'{host}:{port}')
    result={'bootstrap_servers':servers,'client_id':f'gryadka-{target.name}',
            'request_timeout_ms':max(1000, min(30000, int(float(options.get('timeoutS',10))*1000))),'api_version_auto_timeout_ms':5000,'security_protocol':protocol}
    if protocol.startswith('SASL'):
        if not target.username or not target.password:raise ValueError('Kafka SASL credentials are required')
        result.update(sasl_mechanism=options.get('saslMechanism', default_sasl_mechanism),
                      sasl_plain_username=target.username,sasl_plain_password=target.password)
    if protocol.endswith('SSL'):
        result['ssl_check_hostname']=True
        if options.get('sslCaFile'):result['ssl_cafile']=options['sslCaFile']
    return result




def group_lag(group_id, offsets, end_offsets):
    rows = []
    for partition, metadata in offsets.items():
        committed = getattr(metadata, 'offset', None)
        end = end_offsets.get(partition)
        lag = max(0, end - committed) if committed is not None and committed >= 0 and end is not None else None
        rows.append({'topic': partition.topic, 'partition': partition.partition,
                     'committed_offset': committed, 'end_offset': end, 'lag': lag})
    complete = all(row['lag'] is not None for row in rows)
    return {'group_id': group_id, 'lag': sum(row['lag'] for row in rows) if complete else None,
            'partitions': rows, 'status': 'READY' if complete else 'UNKNOWN'}


class KafkaOffsetReader:
    """Load current topic metadata before ListOffsets; retained/deleted topics stay unknown."""
    def __init__(self, consumer):
        self.consumer = consumer
        self.topics = None
        self.partitions = {}

    def read(self, group_id, offsets):
        if not offsets:
            return group_lag(group_id, offsets, {})
        if self.topics is None:
            self.topics = set(self.consumer.topics())
        available = []
        for partition in offsets:
            if partition.topic not in self.topics:
                continue
            if partition.topic not in self.partitions:
                self.partitions[partition.topic] = self.consumer.partitions_for_topic(partition.topic) or set()
            if partition.partition in self.partitions[partition.topic]:
                available.append(partition)
        ends = self.consumer.end_offsets(available) if available else {}
        return group_lag(group_id, offsets, ends)

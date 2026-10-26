"""Read service resources from existing metrics. Never inspect the host of a pod."""
import json
import logging
import math
import re
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit

from app.config import get_app_config
from app.services.node_exporter_metrics import NodeExporterMetricsService
from app.services.victoriametrics_client import VictoriaMetricsClient, MetricsQueryError

logger = logging.getLogger(__name__)
_cache = {}


def _host(value):
    value = str(value or '').split('@')[-1]
    return urlsplit(value if '://' in value else '//' + value).hostname or ''


def _namespace(host):
    labels = host.split('.')
    if 'svc' in labels:
        index = labels.index('svc')
        if index >= 2:
            return labels[index - 1]
    return None


def _sum_known(values):
    return sum(values) if values and all(value is not None for value in values) else None


class KubernetesMetricsService:
    """cAdvisor usage, with optional kube-state-metrics requests. No Kubernetes API token."""
    def __init__(self, client, labels=None):
        self.client = client
        self.labels = labels or {}

    def collect(self, namespace):
        if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?', namespace):
            raise ValueError('Invalid metrics namespace')
        matchers = {**self.labels, 'namespace': namespace}
        if any(not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_]*', key) for key in matchers):
            raise ValueError('Invalid metrics label')
        scope = ','.join(f'{key}={json.dumps(str(value))}' for key, value in matchers.items())
        cache_key = (self.client.base_url, scope)
        cached = _cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < 15:
            return cached[1]
        containers = scope + ',container!="",container!="POD",image!=""'
        live = f'container_last_seen{{{containers}}} > time() - 120'
        join = ' and on(namespace,pod,container,id) '
        queries = {
            'cpu_usage_cores': f'max by(pod,container) (rate(container_cpu_usage_seconds_total{{{containers}}}[2m]){join}({live}))',
            'memory_usage_bytes': f'max by(pod,container) (container_memory_working_set_bytes{{{containers}}}{join}({live}))',
            'cpu_limit_cores': f'max by(pod,container) ((container_spec_cpu_quota{{{containers}}} / container_spec_cpu_period{{{containers}}}){join}({live}))',
            'memory_limit_bytes': f'max by(pod,container) (container_spec_memory_limit_bytes{{{containers}}}{join}({live}))',
            'cpu_request_cores': f'max by(pod,container) (kube_pod_container_resource_requests{{{scope},resource="cpu"}})',
            'memory_request_bytes': f'max by(pod,container) (kube_pod_container_resource_requests{{{scope},resource="memory"}})',
        }
        pods = {}
        for field, query in queries.items():
            for row in self.client.query(query):
                labels = row.get('metric', {})
                pod, container = labels.get('pod'), labels.get('container')
                if not pod or not container:
                    continue
                try:
                    value = float(row['value'][1])
                except (ValueError, TypeError, KeyError, IndexError):
                    continue
                if not math.isfinite(value) or value < 0:
                    continue
                if field.endswith('limit_bytes') and (value == 0 or value >= 2**60):
                    continue  # cAdvisor's unlimited sentinel is not a memory limit.
                if field == 'cpu_limit_cores' and value <= 0:
                    continue
                item = pods.setdefault(pod, {}).setdefault(container, {'name': container})
                item[field] = value
        _cache[cache_key] = (time.monotonic(), pods)
        return pods

    def enrich(self, nodes, hint, namespace):
        pods = self.collect(namespace)
        pattern = hint.get('podPattern')
        if pattern and len(pattern) > 200:
            raise ValueError('Pod pattern too long')
        for node in nodes:
            info = node['infrastructure']
            info['namespace'] = namespace
            host_pod = _host(node.get('host') or node.get('name')).split('.')[0]
            if hint.get('pod'):
                selected = [hint['pod']] if hint['pod'] in pods else []
            elif host_pod in pods:
                selected = [host_pod]
            elif pattern:
                selected = [pod for pod in pods if re.fullmatch(pattern, pod)]
            else:
                selected = []
            pod_rows = []
            for pod in sorted(selected):
                containers = [value for name, value in pods[pod].items()
                              if not hint.get('container') or name == hint['container']]
                if not containers or not any(c.get('cpu_usage_cores') is not None or
                                             c.get('memory_usage_bytes') is not None for c in containers):
                    continue
                row = {'namespace': namespace, 'pod': pod, 'containers': containers}
                for field in ('cpu_usage_cores', 'cpu_request_cores', 'cpu_limit_cores',
                              'memory_usage_bytes', 'memory_request_bytes', 'memory_limit_bytes'):
                    row[field] = _sum_known([c.get(field) for c in containers])
                pod_rows.append(row)
            if not pod_rows:
                info.update(reason_code='pod_unmatched', reason='VictoriaMetrics отвечает, но метрики выбранного pod/container не найдены. Проверьте namespace, сопоставление и сбор cAdvisor.')
                continue
            aggregate = {'namespace': namespace, 'pod': ', '.join(p['pod'] for p in pod_rows)}
            for field in ('cpu_usage_cores', 'cpu_request_cores', 'cpu_limit_cores',
                          'memory_usage_bytes', 'memory_request_bytes', 'memory_limit_bytes'):
                aggregate[field] = _sum_known([p.get(field) for p in pod_rows])
            info.update(state='available', source='kubernetes', pods=pod_rows, resources=aggregate)


def enrich_node_resources(nodes, target):
    """Auto-detect service DNS; explicit Helm hints resolve proxies and Service → pods."""
    hint = getattr(target, 'infrastructure', {}) or {}
    kind = hint.get('kind', 'auto')
    if kind not in {'auto', 'kubernetes', 'vm', 'host', 'docker', 'none'}:
        raise ValueError('Unsupported infrastructure kind')
    target_hosts = [_host(n.get('host') if isinstance(n, dict) else n) for n in target.nodes]
    target_hosts += [_host(n.get('host') or n.get('name')) for n in nodes]
    namespace = hint.get('namespace') or next((_namespace(h) for h in target_hosts if _namespace(h)), None)
    inferred_kubernetes = kind == 'kubernetes' or (kind == 'auto' and namespace is not None)
    for node in nodes:
        node['infrastructure'] = {'kind': kind if kind in {'none', 'vm', 'host', 'docker'} else 'kubernetes' if inferred_kubernetes else 'unknown',
                                  'state': 'disabled' if kind == 'none' else 'unavailable'}
        node.pop('node_exporter', None)
    if kind == 'none' or not nodes:
        return nodes
    def unavailable(code, reason):
        for node in nodes:
            if node['infrastructure']['state'] != 'available':
                node['infrastructure'].update(reason_code=code, reason=reason)
    if kind == 'docker':
        unavailable('docker_not_configured', 'Сбор ресурсов Docker-контейнера не настроен. Метрики хоста не подменяют ресурсы контейнера.')
        return nodes
    config = get_app_config()
    metrics = getattr(config, 'victoria_metrics_config', {}) if config else {}
    base_url = metrics.get('baseUrl') or metrics.get('base_url') or metrics.get('url')
    if metrics.get('enabled') is False:
        unavailable('collection_disabled', 'Сбор ресурсов отключен в конфигурации VictoriaMetrics.')
        return nodes
    if not base_url:
        unavailable('source_not_configured', 'Источник VictoriaMetrics не настроен.')
        return nodes
    try:
        if inferred_kubernetes:
            if not namespace:
                unavailable('namespace_missing', 'Укажите Kubernetes namespace в конфигурации target.')
            else:
                KubernetesMetricsService(VictoriaMetricsClient(base_url), metrics.get('kubernetesLabels')).enrich(nodes, hint, namespace)
        else:
            def candidates(node):
                if hint.get('host'):
                    return [hint['host']]
                if 'server_host' in node:
                    return [node['server_host']] if node['server_host'] else []
                return [_host(node.get('host') or node.get('name'))]
            known_hosts = []
            for node in nodes:
                if candidates(node):
                    known_hosts.append(node)
                else:
                    node['infrastructure'].update(reason_code='host_unknown',
                        reason='Адрес сервера пока не получен из PostgreSQL. Метрики VIP или прокси не используются вместо ресурсов БД.')
            if not known_hosts:
                return nodes
            service = NodeExporterMetricsService.from_config()
            if service:
                service.enrich_nodes(known_hosts, candidates, strict=True, diagnostics=True)
            else:
                unavailable('source_not_configured', 'Источник node exporter не настроен.')
            for node in nodes:
                if node.get('node_exporter', {}).get('matched'):
                    node['infrastructure'].update(kind=kind if kind in {'vm', 'host'} else 'host', state='available', source='node_exporter')
    except MetricsQueryError:
        unavailable('query_failed', 'VictoriaMetrics отвечает, но отклонила запрос метрик. Проверьте настройки источника.')
    except HTTPError as exc:
        unavailable('source_auth_denied' if exc.code in {401, 403} else 'query_failed',
                    'VictoriaMetrics отклонила запрос. Проверьте доступ и настройки источника.')
    except Exception as exc:
        unavailable('source_unavailable', 'VictoriaMetrics недоступна или не отвечает. Проверьте соединение с источником метрик.')
        logger.debug('Infrastructure metrics unavailable: target=%s error_type=%s', target.name, type(exc).__name__)
    return nodes

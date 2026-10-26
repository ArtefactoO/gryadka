"""Broker build metadata from existing metrics; never infer a release from ApiVersions."""
import json
import logging
import re
from urllib.parse import urlsplit

from app.config import get_app_config
from app.services.victoriametrics_client import VictoriaMetricsClient

logger = logging.getLogger(__name__)
VERSION = re.compile(r'v?(\d+\.\d+\.\d+)(?:[-+][A-Za-z0-9_.-]+)?')


def image_version(image):
    # Digests and floating tags carry no release. Do not infer vendor release numbers.
    reference = str(image or '').split('@')[0].rsplit('/', 1)[-1]
    name, separator, tag = reference.partition(':')
    if not separator or 'kafka' not in name.lower() or not VERSION.fullmatch(tag):
        return None
    return tag.removeprefix('v')


def _scope(labels):
    if any(not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_]*', key) for key in labels):
        raise ValueError('Invalid metrics label')
    return ','.join(f'{key}={json.dumps(str(value))}' for key, value in labels.items())


def _matches(row, labels, pod):
    metric = row.get('metric', {})
    return all(str(metric.get(k)) == str(v) for k, v in labels.items()) and metric.get('pod') == pod


def enrich_kafka_versions(nodes, target):
    """Match exact broker pods, namespace and cluster. Failure never changes Kafka health."""
    reason = 'Версия недоступна: метрики сборки брокера или образа контейнера не найдены.'
    for node in nodes:
        node['version_info'] = {'value': None, 'source': None, 'reason': reason}
    config = get_app_config()
    metrics = getattr(config, 'victoria_metrics_config', {}) or {}
    base = metrics.get('baseUrl') or metrics.get('base_url') or metrics.get('url')
    hint = getattr(target, 'infrastructure', {}) or {}
    if not nodes or not base or metrics.get('enabled') is False or hint.get('kind') in {'none', 'docker', 'vm', 'host'}:
        return version_summary(nodes)
    try:
        pod_nodes = {}
        namespaces = set()
        for node in nodes:
            host = urlsplit('//' + str(node['host'])).hostname or ''
            parts = host.split('.')
            namespace = hint.get('namespace') or node.get('infrastructure', {}).get('namespace')
            if not namespace and 'svc' in parts and parts.index('svc') >= 2:
                namespace = parts[parts.index('svc') - 1]
            if not namespace:
                continue
            namespaces.add(namespace)
            pod_nodes[parts[0]] = node
        if len(namespaces) != 1 or len(pod_nodes) != len(nodes):
            return version_summary(nodes)
        namespace = namespaces.pop()
        labels = {**(metrics.get('kubernetesLabels') or {}), 'namespace': namespace}
        scope = _scope(labels)
        pods = json.dumps('|'.join(re.escape(pod) for pod in pod_nodes))
        client = VictoriaMetricsClient(base)
        builds = client.query(f'kafka_server_app_info{{{scope},pod=~{pods},version!=""}} > 0')
        for pod, node in pod_nodes.items():
            versions = {str(row['metric']['version']) for row in builds if _matches(row, labels, pod)
                        and VERSION.fullmatch(str(row['metric'].get('version', '')))}
            if len(versions) == 1:
                node['version_info'] = {'value': versions.pop(), 'source': 'app_info'}
                continue
            if versions:
                node['version_info']['reason'] = 'Метрики сборки содержат разные версии одного брокера. Проверьте scrape labels.'
                continue
        missing = {pod: node for pod, node in pod_nodes.items()
                   if not node['version_info']['value'] and node['version_info']['reason'] == reason}
        images = client.query(f'container_last_seen{{{scope},pod=~{pods},container!="",container!="POD",image!=""}} > time() - 120') if missing else []
        for pod, node in missing.items():
            candidates = {row['metric']['image'] for row in images if _matches(row, labels, pod)
                          and (not hint.get('container') or row['metric'].get('container') == hint['container'])
                          and image_version(row['metric'].get('image'))}
            if len(candidates) == 1:
                image = candidates.pop()
                node['version_info'] = {'value': image_version(image), 'source': 'container_image', 'image': image}
            elif candidates:
                node['version_info']['reason'] = 'Найдено несколько образов брокера. Однозначно определить версию нельзя.'
    except Exception as exc:
        logger.warning('Kafka version metrics unavailable: target=%s error_type=%s', target.name, type(exc).__name__)
        for node in nodes:
            if not node['version_info']['value']:
                node['version_info']['reason'] = 'Источник метрик версии недоступен или отклонил запрос. Статус Kafka проверяется отдельно.'
    return version_summary(nodes)


def version_summary(nodes):
    infos = [node['version_info'] for node in nodes]
    versions = sorted({info['value'] for info in infos if info['value']})
    sources = {info['source'] for info in infos if info['source']}
    complete = bool(infos) and all(info['value'] for info in infos)
    source = next(iter(sources)) if len(sources) == 1 else None
    return {'value': versions[0] if complete and len(versions) == 1 and source else None,
            'versions': versions, 'source': source, 'complete': complete}

from __future__ import annotations

import ipaddress
import logging
from typing import Any, List, Optional

from app.config import get_app_config

logger = logging.getLogger(__name__)


def _to_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return str(value)


def _load_networks(configured: Any, config_name: str) -> List[ipaddress._BaseNetwork]:
    networks: List[ipaddress._BaseNetwork] = []
    for cidr in configured or []:
        text = _to_text(cidr)
        if not text:
            continue
        try:
            networks.append(ipaddress.ip_network(text, strict=False))
        except ValueError:
            logger.warning("Invalid registry %s entry %r, skipping", config_name, cidr)
    return networks


def _is_disallowed_private_ip(ip_obj: ipaddress._BaseAddress) -> bool:
    return (
        ip_obj.is_private
        or ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_multicast
        or ip_obj.is_unspecified
        or ip_obj.is_reserved
    )


def classify_registry_address(value: Any, *, source_node: Optional[dict] = None) -> Optional[str]:
    text = _to_text(value)
    if not text:
        return None

    current_config = get_app_config()
    view_configs = current_config.get_addresses_view_configs() if current_config else []

    try:
        ip_obj = ipaddress.ip_address(text)
    except ValueError:
        ip_obj = None

    try:
        network_obj = ipaddress.ip_network(text, strict=False) if ip_obj is None else None
    except ValueError:
        network_obj = None

    for view in view_configs:
        view_name = _to_text(view.get("name"))
        if not view_name:
            continue
        include_networks = _load_networks(view.get("includeIpCidrs"), "includeIpCidrs")
        exclude_networks = _load_networks(view.get("excludeIpCidrs"), "excludeIpCidrs")
        exclude_private_ips = bool(view.get("excludePrivateIps", False))
        exclude_internal_lbs = bool(view.get("excludeInternalLoadBalancers", False))

        if exclude_internal_lbs and isinstance(source_node, dict):
            annotations = source_node.get("annotations")
            if isinstance(annotations, dict):
                lb_type = _to_text(annotations.get("yandex.cloud/load-balancer-type"))
                if lb_type and lb_type.lower() == "internal":
                    continue

        if ip_obj is not None:
            if include_networks:
                if any(ip_obj in network for network in include_networks):
                    return view_name
                continue
            if any(ip_obj in network for network in exclude_networks):
                continue
            if exclude_private_ips and _is_disallowed_private_ip(ip_obj):
                continue
            if view_name == "external" or exclude_networks or exclude_private_ips or exclude_internal_lbs:
                return view_name
            continue

        if network_obj is None:
            continue
        if include_networks:
            if any(network_obj.overlaps(network) for network in include_networks):
                return view_name
            continue
        if any(network_obj.overlaps(network) for network in exclude_networks):
            continue
        if exclude_private_ips and _is_disallowed_private_ip(network_obj.network_address):
            continue
        if view_name == "external" or exclude_networks or exclude_private_ips or exclude_internal_lbs:
            return view_name

    return None

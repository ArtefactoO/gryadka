"""
Service for collecting HAProxy statistics from CSV stats endpoint.
"""
import csv
import io
import logging
import os
import socket
import ssl
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlparse
from urllib.request import Request
from app.services.http_client import open_http as urlopen
from urllib.error import HTTPError, URLError

from app.config import get_app_config
from app.monitor_logging import log_collector_progress, log_monitor_request

logger = logging.getLogger(__name__)


class HAProxyCollector:
    """Collect HAProxy statistics from CSV stats endpoint."""

    IMAGE_TAG_CACHE_TTL_S = 600  # 10 minutes

    def __init__(self) -> None:
        # In-memory per-worker cache for pod image tags.
        self._pod_image_tag_cache: Dict[tuple[str, str], Dict[str, Any]] = {}

    def _extract_image_tag(self, image: str) -> Optional[str]:
        """Extract container tag from image reference."""
        if not image:
            return None
        # Trim digest part if present: repo/name:tag@sha256:...
        image_no_digest = image.split("@", 1)[0]
        last_segment = image_no_digest.rsplit("/", 1)[-1]
        if ":" in last_segment:
            return last_segment.rsplit(":", 1)[-1]
        return None

    def _get_cached_pod_image_tag(self, namespace: str, pod_name: str) -> Optional[str]:
        cache_key = (namespace, pod_name)
        cached = self._pod_image_tag_cache.get(cache_key)
        if not cached:
            return None
        if float(cached.get("expires_at", 0)) <= time.time():
            self._pod_image_tag_cache.pop(cache_key, None)
            return None
        return str(cached.get("tag") or "") or None

    def _set_cached_pod_image_tag(self, namespace: str, pod_name: str, tag: str) -> None:
        self._pod_image_tag_cache[(namespace, pod_name)] = {
            "tag": tag,
            "expires_at": time.time() + self.IMAGE_TAG_CACHE_TTL_S,
        }

    def _fetch_pod_image_tag(
        self,
        base: str,
        token: str,
        ca_path: str,
        namespace: str,
        pod_name: str,
        timeout_s: float,
    ) -> Optional[str]:
        """Fetch pod image tag from Kubernetes API with in-memory cache."""
        cached = self._get_cached_pod_image_tag(namespace, pod_name)
        if cached:
            return cached

        try:
            url = f"{base}/api/v1/namespaces/{namespace}/pods/{pod_name}"
            pod_payload = self._request_json_k8s(url, token, ca_path, timeout_s)
            containers = ((pod_payload.get("spec") or {}).get("containers") or [])
            image = containers[0].get("image") if containers else None
            image_tag = self._extract_image_tag(str(image or ""))
            if image_tag:
                self._set_cached_pod_image_tag(namespace, pod_name, image_tag)
            return image_tag
        except Exception as e:
            logger.warning("Could not fetch image for pod %s: %s", pod_name, e)
            return None

    def _request_csv(
        self,
        base_url: str,
        path: str = "/stats?stats;csv",
        timeout_s: float = 5.0,
        verify_tls: bool = True,
    ) -> List[Dict[str, Any]]:
        """Fetch CSV stats from HAProxy."""
        url = f"{base_url.rstrip('/')}{path}"
        request = Request(url)
        request.add_header("Accept", "text/csv")

        ssl_context = None
        if url.startswith("https://") and not verify_tls:
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

        try:
            with urlopen(request, timeout=timeout_s, context=ssl_context) as response:
                body = response.read().decode("utf-8")
                # Parse CSV - header is in comment line starting with # pxname,svname,...
                lines = body.split('\n')

                # Find header in comment line
                header_line = None
                for line in lines:
                    line = line.strip()
                    if line.startswith('#') and 'pxname' in line:
                        # Extract header from comment: "# pxname,svname,..." -> "pxname,svname,..."
                        header_line = line.lstrip('#').strip()
                        break

                if not header_line:
                    logger.warning("No header line found in HAProxy CSV response")
                    return []

                # Find where data starts (first non-comment, non-empty line after header)
                data_start = 0
                for i, line in enumerate(lines):
                    line = line.strip()
                    if line and not line.startswith('#'):
                        data_start = i
                        break

                # Parse CSV with proper header
                reader = csv.DictReader(io.StringIO('\n'.join(lines[data_start:])))
                # Set fieldnames from the header line we found
                reader.fieldnames = [field.strip() for field in header_line.split(',')]

                rows = []
                for row in reader:
                    # Skip empty rows
                    if row and any(value for value in row.values()):
                        rows.append(row)
                return rows
        except HTTPError as e:
            logger.error("HAProxy HTTP error for %s: %s", url, e)
            raise
        except URLError as e:
            # Expected during rollout when pod is terminating or not ready
            log_monitor_request(logger, "haproxy", "fetch_stats_csv_failed", endpoint=url, error=str(e))
            raise
        except Exception as e:
            logger.error("HAProxy request error for %s: %s", url, e)
            raise

    def _parse_stats_row(self, row: Dict[str, str]) -> Optional[Dict[str, Any]]:
        """Parse a single CSV row into structured data."""
        pxname = row.get("pxname", "").strip()
        svname = row.get("svname", "").strip()
        status = row.get("status", "").strip()

        if not pxname:
            return None

        # Skip FRONTEND, BACKEND, and other special entries
        if svname in ("FRONTEND", "BACKEND", ""):
            return None

        return {
            "proxy_name": pxname,
            "server_name": svname,
            "status": status,
            "weight": self._to_int(row.get("weight")),
            "act": self._to_int(row.get("act")),
            "bck": self._to_int(row.get("bck")),
            "scur": self._to_int(row.get("scur")),  # current sessions
            "smax": self._to_int(row.get("smax")),  # max sessions
            "slim": self._to_int(row.get("slim")),  # configured sessions limit
            "stot": self._to_int(row.get("stot")),  # total sessions
            "bin": self._to_int(row.get("bin")),  # bytes in
            "bout": self._to_int(row.get("bout")),  # bytes out
            "check_status": row.get("check_status", "").strip(),
            "check_code": self._to_int(row.get("check_code")),
            "downtime": self._to_int(row.get("downtime")),
            "lastchg": self._to_int(row.get("lastchg")),
        }

    def _to_int(self, value: Any) -> Optional[int]:
        """Safely convert value to int."""
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (ValueError, TypeError):
            return None

    def _extract_sessions_breakdown(self, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Extract sessions by HAProxy row type (FRONTEND/BACKEND) from raw CSV."""
        def _is_internal_service_proxy(proxy_name: str) -> bool:
            normalized = proxy_name.strip().lower()
            if not normalized:
                return False
            # Exclude self-observability proxies from load calculations.
            return ("stats" in normalized) or ("metrics" in normalized)

        # Collect pseudo rows (FRONTEND/BACKEND) grouped by proxy name.
        proxy_rows: Dict[str, Dict[str, Dict[str, int]]] = {}
        for row in rows:
            pxname = str(row.get("pxname", "")).strip()
            if not pxname:
                continue
            if _is_internal_service_proxy(pxname):
                continue
            svname = str(row.get("svname", "")).strip().upper()
            if svname not in ("FRONTEND", "BACKEND"):
                continue
            scur = self._to_int(row.get("scur"))
            slim = self._to_int(row.get("slim"))
            if scur is None and slim is None:
                continue
            item: Dict[str, int] = {}
            if scur is not None:
                item["scur"] = scur
            if slim is not None:
                item["slim"] = slim
            proxy_rows.setdefault(pxname, {})[svname] = item

        backend_by_proxy: Dict[str, Dict[str, int]] = {}

        frontend_sessions = 0
        frontend_sessions_limit = 0
        backend_sessions = 0
        has_frontend = False
        has_frontend_limit = False
        has_backend = False

        # Determine frontend strictly by type (svname=FRONTEND), no name hardcoding.
        # If multiple frontend rows exist, select representative by max current sessions
        # to avoid double-counting shared traffic exposed via multiple listeners.
        selected_frontend_metric: Dict[str, int] = {}
        selected_frontend_scur = -1

        for pxname, rows_by_kind in proxy_rows.items():
            frontend_metric = rows_by_kind.get("FRONTEND")
            backend_metric = rows_by_kind.get("BACKEND")

            if frontend_metric:
                scur_value = frontend_metric.get("scur", -1)
                if scur_value > selected_frontend_scur:
                    selected_frontend_scur = scur_value
                    selected_frontend_metric = frontend_metric

            if backend_metric and "scur" in backend_metric:
                has_backend = True
                backend_sessions += backend_metric["scur"]
                backend_by_proxy.setdefault(pxname, {})["sessions_current"] = backend_metric["scur"]

        if "scur" in selected_frontend_metric:
            has_frontend = True
            frontend_sessions = selected_frontend_metric["scur"]
        if "slim" in selected_frontend_metric:
            has_frontend_limit = True
            frontend_sessions_limit = selected_frontend_metric["slim"]

        result: Dict[str, Any] = {"backend_by_proxy": backend_by_proxy}
        if has_frontend:
            result["frontend_sessions"] = frontend_sessions
        if has_frontend_limit:
            result["frontend_sessions_limit"] = frontend_sessions_limit
        if has_backend:
            result["backend_sessions"] = backend_sessions
        return result

    def _apply_backend_metrics_to_proxies(
        self,
        proxies_list: List[Dict[str, Any]],
        backend_by_proxy: Dict[str, Dict[str, int]],
    ) -> None:
        """Apply BACKEND-row session metrics to proxy list entries in-place."""
        for proxy in proxies_list:
            metrics = backend_by_proxy.get(str(proxy.get("name", "")))
            if not metrics:
                continue
            if "sessions_current" in metrics:
                proxy["sessions_current"] = metrics["sessions_current"]

    def _resolve_headless_service(self, service_url: str) -> List[str]:
        """Resolve headless service DNS to get all pod IPs, return URLs for each."""
        if not service_url.strip():
            return []
        if not service_url.startswith(('http://', 'https://')):
            service_url = f"http://{service_url}"
        parsed = urlparse(service_url)
        host = parsed.hostname or parsed.path.split(":")[0]
        port = parsed.port or 8404
        scheme = parsed.scheme or "http"
        try:
            _, _, ip_list = socket.gethostbyname_ex(host)
        except socket.gaierror as e:
            logger.warning("DNS resolution failed for %s: %s", host, e)
            return []
        if not ip_list:
            return []
        # Build URL for each pod IP
        return [f"{scheme}://{ip}:{port}" for ip in sorted(ip_list)]

    def _request_json_k8s(
        self,
        url: str,
        token: str,
        ca_cert_path: str,
        timeout_s: float,
    ) -> Any:
        """Fetch JSON from Kubernetes API using in-cluster service account."""
        request = Request(url)
        request.add_header("Authorization", f"Bearer {token}")
        request.add_header("Accept", "application/json")

        ssl_context = ssl.create_default_context(cafile=ca_cert_path)
        with urlopen(request, timeout=timeout_s, context=ssl_context) as response:
            body = response.read().decode("utf-8")
            import json
            return json.loads(body)

    def _build_k8s_context(
        self,
        k8s_config: Dict[str, Any],
        timeout_s: float,
    ) -> Optional[Dict[str, Any]]:
        """Build Kubernetes API context from config and environment."""
        token_path = "/var/run/secrets/kubernetes.io/serviceaccount/token"
        ca_path = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
        api_host = os.environ.get("KUBERNETES_SERVICE_HOST")
        api_port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")

        if not api_host:
            logger.warning("KUBERNETES_SERVICE_HOST is not set")
            return None
        if not os.path.exists(token_path) or not os.path.exists(ca_path):
            logger.warning("Service account token/cert files not found")
            return None

        try:
            with open(token_path, "r", encoding="utf-8") as f:
                token = f.read().strip()
        except Exception as e:
            logger.warning("Could not read service account token: %s", e)
            return None

        base = f"https://{api_host}:{api_port}"

        return {
            "base": base,
            "token": token,
            "ca_cert_path": ca_path,
            "timeout_s": timeout_s,
        }

    def _request_configmap(
        self,
        namespace: str,
        name: str,
        base: str,
        token: str,
        ca_cert_path: str,
        timeout_s: float,
    ) -> Optional[str]:
        """Fetch ConfigMap from Kubernetes API and return haproxy.cfg content."""
        try:
            url = f"{base}/api/v1/namespaces/{namespace}/configmaps/{name}"
            payload = self._request_json_k8s(url, token, ca_cert_path, timeout_s)
            data = payload.get("data") or {}
            return str(data.get("haproxy.cfg") or "")
        except Exception as e:
            log_monitor_request(
                logger,
                "haproxy",
                "get_configmap_failed",
                endpoint=f"{namespace}/{name}",
                error=str(e),
            )
            return None

    def _parse_haproxy_config(self, config_content: str) -> Dict[str, int]:
        """Parse haproxy.cfg and extract frontend->backend port mappings.

        Looks for patterns like:
          frontend {name}-frontend
            bind :{port}
            default_backend {name}-backend

         Returns: {backend_name: frontend_port}
        """
        backend_ports: Dict[str, int] = {}
        if not config_content:
            return backend_ports

        lines = config_content.split('\n')
        current_frontend: Optional[str] = None
        current_port: Optional[int] = None

        for line in lines:
            line_stripped = line.strip().lower()

            # Detect frontend block
            if line_stripped.startswith('frontend '):
                parts = line.strip().split()
                current_frontend = parts[1] if len(parts) > 1 else None
                current_port = None
                continue

            # Extract bind port from frontend block
            if current_frontend and line_stripped.startswith('bind '):
                bind_parts = line.strip().split()
                if len(bind_parts) >= 2:
                    bind_addr = bind_parts[1]
                    # Extract port from :PORT or *:PORT or 0.0.0.0:PORT
                    if ':' in bind_addr:
                        port_str = bind_addr.rsplit(':', 1)[-1]
                        current_port = self._to_int(port_str)
                continue

            # Extract default_backend and map to port
            if current_frontend and line_stripped.startswith('default_backend '):
                parts = line.strip().split()
                backend_name = parts[1] if len(parts) > 1 else None
                if backend_name and current_port is not None:
                    backend_ports[backend_name] = current_port
                current_frontend = None
                current_port = None

        return backend_ports

    def _get_proxy_ports_from_configmap(
        self,
        target_name: str,
        namespace: str,
        service_name: str,
        kctx: Dict[str, str],
    ) -> Dict[str, int]:
        """Get backend->port mapping from HAProxy ConfigMap with Redis caching (TTL 6 min).

        Redis key: haproxy:ports:{target_name}
        Empty results are NOT cached (will retry on next call).
        """
        import json
        from app.services.cache import CacheService
        from app.config import settings

        redis_key = f"haproxy:ports:{target_name}"
        cache = CacheService(
            db=settings.redis_haproxy_db if settings.redis_haproxy_db is not None else settings.redis_db
        )

        # 1. Try to get from Redis cache
        try:
            cached_data = cache.redis_client.get(redis_key)
            if cached_data:
                log_collector_progress(logger, "haproxy", target_name, "configmap_cache_hit")
                return json.loads(cached_data)
        except Exception as e:
            log_collector_progress(logger, "haproxy", target_name, "configmap_cache_read_failed", error=str(e))

        # 2. Cache miss - fetch from Kubernetes API
        log_monitor_request(logger, "haproxy", "get_configmap", endpoint=f"{namespace}/{service_name}")
        base = kctx.get("base", "")
        token = kctx.get("token", "")
        ca_path = kctx.get("ca_cert_path", "")
        timeout_s_val = float(kctx.get("timeout_s", 5.0) or 5.0)

        config_content = self._request_configmap(
            namespace, service_name, base, token, ca_path, float(timeout_s_val)
        )

        if not config_content:
            logger.warning("[HAProxy ConfigMap] ConfigMap not found for %s/%s", namespace, service_name)
            # Empty result - do NOT cache, will retry next time
            return {}

        # 3. Parse config
        backend_ports = self._parse_haproxy_config(config_content)
        log_collector_progress(logger, "haproxy", target_name, "configmap_ports_parsed", count=len(backend_ports))

        # 4. Cache only non-empty results (TTL 6 minutes = 360 seconds)
        if backend_ports:
            try:
                cache.redis_client.setex(redis_key, 360, json.dumps(backend_ports))
                log_collector_progress(logger, "haproxy", target_name, "configmap_cache_success", count=len(backend_ports), ttl_s=360)
            except Exception as e:
                log_collector_progress(logger, "haproxy", target_name, "configmap_cache_write_failed", error=str(e))

        return backend_ports

    def _host_matches_tls(self, host: str, tls_hosts: set[str]) -> bool:
        host_l = host.lower()
        for tls_host in tls_hosts:
            tls_l = tls_host.lower()
            if tls_l == host_l:
                return True
            if tls_l.startswith("*.") and host_l.endswith(tls_l[1:]):
                return True
        return False

    def _build_ingress_url(self, host: str, path: Optional[str], tls_hosts: set[str]) -> str:
        scheme = "https" if self._host_matches_tls(host, tls_hosts) else "http"
        normalized_path = path or "/"
        if not normalized_path.startswith("/"):
            normalized_path = f"/{normalized_path}"
        return f"{scheme}://{host}{normalized_path}"

    def _get_service_port_refs(
        self,
        namespace: str,
        service_name: str,
        kctx: Dict[str, str],
    ) -> Optional[Dict[str, set[str]]]:
        """Fetch service ports so Ingress ownership can be verified by backend service and port."""
        base = kctx.get("base", "")
        token = kctx.get("token", "")
        ca_path = kctx.get("ca_cert_path", "")
        timeout_s = float(kctx.get("timeout_s", 5.0) or 5.0)
        url = f"{base}/api/v1/namespaces/{namespace}/services/{service_name}"

        try:
            payload = self._request_json_k8s(url, token, ca_path, timeout_s)
        except Exception as e:
            logger.debug("Could not fetch Service %s/%s for Ingress validation: %s", namespace, service_name, e)
            return None

        ports = ((payload.get("spec") or {}).get("ports") or [])
        names = {
            str(port.get("name") or "").strip()
            for port in ports
            if str(port.get("name") or "").strip()
        }
        numbers = {
            str(port.get("port") or "").strip()
            for port in ports
            if str(port.get("port") or "").strip()
        }
        if not names and not numbers:
            logger.debug("Service %s/%s has no ports for Ingress validation", namespace, service_name)
            return None

        return {"names": names, "numbers": numbers}

    def _ingress_backend_port_matches(
        self,
        backend_service: Dict[str, Any],
        service_ports: Dict[str, set[str]],
    ) -> tuple[bool, Optional[str]]:
        port_ref = backend_service.get("port") or {}
        port_name = str(port_ref.get("name") or "").strip()
        port_number = str(port_ref.get("number") or "").strip()

        if port_name:
            return port_name in service_ports["names"], port_name
        if port_number:
            return port_number in service_ports["numbers"], port_number
        return False, None

    def _discover_ingresses_for_service(
        self,
        namespace: str,
        service_name: str,
        kctx: Dict[str, str],
    ) -> List[Dict[str, str]]:
        """List Ingress URLs whose backend service is exactly this HAProxy service."""
        if not namespace or not service_name:
            return []

        base = kctx.get("base", "")
        token = kctx.get("token", "")
        ca_path = kctx.get("ca_cert_path", "")
        timeout_s = float(kctx.get("timeout_s", 5.0) or 5.0)
        service_ports = self._get_service_port_refs(namespace, service_name, kctx)
        if service_ports is None:
            return []

        url = f"{base}/apis/networking.k8s.io/v1/namespaces/{namespace}/ingresses"

        try:
            payload = self._request_json_k8s(url, token, ca_path, timeout_s)
        except Exception as e:
            logger.debug("Could not discover Ingress objects for %s/%s: %s", namespace, service_name, e)
            return []

        result: List[Dict[str, str]] = []
        seen_urls: set[str] = set()

        for item in payload.get("items", []):
            metadata = item.get("metadata") or {}
            spec = item.get("spec") or {}
            ingress_name = str(metadata.get("name") or "").strip()
            ingress_namespace = str(metadata.get("namespace") or namespace).strip() or namespace
            if ingress_namespace != namespace:
                continue
            tls_hosts = {
                str(host).strip()
                for tls in (spec.get("tls") or [])
                for host in (tls.get("hosts") or [])
                if str(host).strip()
            }

            for rule in spec.get("rules") or []:
                host = str(rule.get("host") or "").strip()
                if not host:
                    continue
                http = rule.get("http") or {}
                for path_item in http.get("paths") or []:
                    backend_service = (((path_item.get("backend") or {}).get("service")) or {})
                    backend_service_name = str(backend_service.get("name") or "").strip()
                    if backend_service_name != service_name:
                        continue
                    port_matches, service_port = self._ingress_backend_port_matches(backend_service, service_ports)
                    if not port_matches:
                        continue
                    path = str(path_item.get("path") or "/").strip() or "/"
                    path_type = str(path_item.get("pathType") or "").strip() or None
                    ingress_url = self._build_ingress_url(host, path, tls_hosts)
                    if ingress_url in seen_urls:
                        continue
                    seen_urls.add(ingress_url)
                    result.append(
                        {
                            "name": ingress_name,
                            "namespace": ingress_namespace,
                            "host": host,
                            "path": path,
                            "path_type": path_type,
                            "url": ingress_url,
                            "service_name": backend_service_name,
                            "service_port": service_port,
                        }
                    )

            default_service = (((spec.get("defaultBackend") or {}).get("service")) or {})
            if str(default_service.get("name") or "").strip() == service_name:
                port_matches, service_port = self._ingress_backend_port_matches(default_service, service_ports)
                if not port_matches:
                    continue
                for tls_host in sorted(host for host in tls_hosts if not host.startswith("*.")):
                    ingress_url = self._build_ingress_url(tls_host, "/", tls_hosts)
                    if ingress_url in seen_urls:
                        continue
                    seen_urls.add(ingress_url)
                    result.append(
                        {
                            "name": ingress_name,
                            "namespace": ingress_namespace,
                            "host": tls_host,
                            "path": "/",
                            "path_type": None,
                            "url": ingress_url,
                            "service_name": service_name,
                            "service_port": service_port,
                        }
                    )

        return sorted(result, key=lambda item: (item.get("host", ""), item.get("path", ""), item.get("name", "")))

    def _resolve_service_stats_port(
        self,
        base: str,
        token: str,
        ca_cert_path: str,
        namespace: str,
        service_name: str,
        timeout_s: float,
    ) -> Optional[int]:
        """Resolve HAProxy stats port from Kubernetes Service.

        If a port named 'stats' or 'stat' exists, prefer it.
        """
        try:
            url = f"{base}/api/v1/namespaces/{namespace}/services/{service_name}"
            payload = self._request_json_k8s(url, token, ca_cert_path, timeout_s)
            ports = (((payload.get("spec") or {}).get("ports")) or [])
            if not ports:
                return None

            for p in ports:
                name = str(p.get("name") or "").strip().lower()
                if name in {"stats", "stat"}:
                    port_value = self._to_int(p.get("port"))
                    if port_value:
                        return port_value
            return None
        except Exception as e:
            logger.warning(
                "Could not resolve stats port from service %s/%s: %s",
                namespace,
                service_name,
                e,
            )
            return None

    def _discover_pods_via_kubernetes_api(
        self,
        k8s_config: Dict[str, Any],
        timeout_s: float,
    ) -> List[Dict[str, str]]:
        """Discover pod endpoints for HAProxy service from Kubernetes API.

        Returns list of {"url": "...", "label": "...", "version"?: "..."}.
        Label prefers pod name when available.
        """
        namespace = str(k8s_config.get("namespace") or "").strip()
        service_name = str(k8s_config.get("serviceName") or "").strip()
        if not namespace or not service_name:
            logger.warning("HAProxy kubernetes discovery requires namespace and serviceName")
            return []

        scheme = str(k8s_config.get("scheme") or "http").strip()
        configured_port = self._to_int(k8s_config.get("port"))
        prefer_endpointslice = bool(k8s_config.get("preferEndpointSlice", True))

        token_path = "/var/run/secrets/kubernetes.io/serviceaccount/token"
        ca_path = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
        api_host = os.environ.get("KUBERNETES_SERVICE_HOST")
        api_port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
        if not api_host:
            logger.warning("KUBERNETES_SERVICE_HOST is not set; cannot use Kubernetes API discovery")
            return []
        if not os.path.exists(token_path) or not os.path.exists(ca_path):
            logger.warning("Service account token/cert files not found; cannot use Kubernetes API discovery")
            return []

        try:
            with open(token_path, "r", encoding="utf-8") as f:
                token = f.read().strip()
        except Exception as e:
            logger.warning("Could not read service account token: %s", e)
            return []

        base = f"https://{api_host}:{api_port}"
        port = configured_port
        if port is None:
            # If port is not configured explicitly, auto-discover from service port named
            # "stats" (preferred) or "stat" (legacy naming).
            port = self._resolve_service_stats_port(
                base=base,
                token=token,
                ca_cert_path=ca_path,
                namespace=namespace,
                service_name=service_name,
                timeout_s=timeout_s,
            )
        if port is None:
            logger.warning(
                "Port is not configured and stats port was not found for %s/%s; fallback to 8404",
                namespace,
                service_name,
            )
            port = 8404

        discovered: List[Dict[str, Any]] = []
        seen_addresses = set()

        def _add_endpoint(address: str, pod_name: Optional[str]) -> None:
            if not address or address in seen_addresses:
                return
            seen_addresses.add(address)
            discovered.append(
                {
                    "url": f"{scheme}://{address}:{port}",
                    "label": pod_name or address,
                    "pod_name": pod_name,
                }
            )

        # Try EndpointSlice first (better metadata and modern API).
        if prefer_endpointslice:
            try:
                selector = quote(f"kubernetes.io/service-name={service_name}", safe="")
                url = (
                    f"{base}/apis/discovery.k8s.io/v1/namespaces/{namespace}/endpointslices"
                    f"?labelSelector={selector}"
                )
                payload = self._request_json_k8s(url, token, ca_path, timeout_s)
                for item in payload.get("items", []):
                    for endpoint in item.get("endpoints", []):
                        pod_name = ((endpoint.get("targetRef") or {}).get("name"))
                        for address in endpoint.get("addresses", []):
                            _add_endpoint(address, pod_name)
            except Exception as e:
                logger.warning("EndpointSlice discovery failed for %s/%s: %s", namespace, service_name, e)

        # Fallback to Endpoints if EndpointSlice yielded nothing.
        if not discovered:
            try:
                url = f"{base}/api/v1/namespaces/{namespace}/endpoints/{service_name}"
                payload = self._request_json_k8s(url, token, ca_path, timeout_s)
                for subset in payload.get("subsets", []):
                    for addr in subset.get("addresses", []):
                        pod_name = ((addr.get("targetRef") or {}).get("name")) or addr.get("hostname")
                        _add_endpoint(addr.get("ip", ""), pod_name)
            except Exception as e:
                logger.warning("Endpoints discovery failed for %s/%s: %s", namespace, service_name, e)

        # Enrich endpoints with image tag (used as quick "version" in UI).
        # We fetch from one pod: Deployment pods should run the same image.
        sample_pod_name = None
        for endpoint in discovered:
            pod_name = str(endpoint.get("pod_name") or "").strip()
            if pod_name:
                sample_pod_name = pod_name
                break

        shared_tag = None
        if sample_pod_name:
            shared_tag = self._fetch_pod_image_tag(
                base=base,
                token=token,
                ca_path=ca_path,
                namespace=namespace,
                pod_name=sample_pod_name,
                timeout_s=timeout_s,
            )
        if shared_tag:
            for endpoint in discovered:
                endpoint["version"] = shared_tag

        for endpoint in discovered:
            endpoint.pop("pod_name", None)

        return discovered

    def collect_target_data(self, target_name: str) -> Optional[Dict[str, Any]]:
        """Collect HAProxy statistics for a target.

        Supports multiple collection modes:
        1. Single URL (baseUrl) - for single instance or ingress
        2. Multiple pods (pods list) - for Kubernetes pods via headless service
        3. Kubernetes API (kubernetes config) - auto-discovery via K8s API
        """
        current_config = get_app_config()
        if not current_config:
            logger.error("App config not initialized")
            return None

        target_config = current_config.get_haproxy_target_by_name(target_name)
        if not target_config:
            logger.warning("HAProxy target %s not found in config", target_name)
            return None

        options = target_config.options or {}
        timeout_s = float(options.get("timeoutS", 5.0) or 5.0)
        verify_tls = options.get("verifyTls", True) if "verifyTls" in options else True
        ingresses: List[Dict[str, str]] = []

        def _with_ingresses(payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
            if payload is not None and ingresses:
                payload["ingresses"] = ingresses
            return payload

        # Check for multiple pods configuration (explicit list).
        # Supports items as string URL or dict {url, label}.
        pods = options.get("pods")
        if pods and isinstance(pods, list):
            return _with_ingresses(self._collect_from_multiple_pods(target_name, pods, timeout_s, verify_tls))

        # Check for Kubernetes API configuration (pod names support).
        k8s_config = options.get("kubernetes")
        if k8s_config and k8s_config.get("enabled"):
            namespace = str(k8s_config.get("namespace") or "").strip()
            service_name = str(k8s_config.get("serviceName") or "").strip()

            # Build Kubernetes context for API calls
            kctx = self._build_k8s_context(k8s_config, timeout_s)

            # Get proxy ports from ConfigMap
            proxy_ports: Dict[str, int] = {}
            if namespace and service_name and kctx:
                log_collector_progress(logger, "haproxy", target_name, "configmap_ports_fetch", namespace=namespace, service_name=service_name)
                proxy_ports = self._get_proxy_ports_from_configmap(
                    target_name, namespace, service_name, kctx
                )
                log_collector_progress(logger, "haproxy", target_name, "configmap_ports_loaded", count=len(proxy_ports))
                ingresses = self._discover_ingresses_for_service(namespace, service_name, kctx)
                log_collector_progress(logger, "haproxy", target_name, "ingresses_verified", count=len(ingresses))
            else:
                logger.warning("[HAProxy ConfigMap] Missing params for target %s: namespace=%s, service_name=%s",
                              target_name, namespace, service_name)

            pod_endpoints = self._discover_pods_via_kubernetes_api(k8s_config, timeout_s)
            if pod_endpoints:
                return _with_ingresses(
                    self._collect_from_multiple_pods(
                        target_name, pod_endpoints, timeout_s, verify_tls, proxy_ports=proxy_ports
                    )
                )
            logger.warning("Kubernetes API discovery returned 0 endpoints for HAProxy target %s", target_name)

        # Check for headless service: resolve DNS to get all pod IPs
        service_url = options.get("serviceUrl") or options.get("headlessService")
        if service_url:
            pod_urls = self._resolve_headless_service(service_url)
            if pod_urls:
                return _with_ingresses(self._collect_from_multiple_pods(target_name, pod_urls, timeout_s, verify_tls))
            logger.warning("Headless service %s resolved to 0 endpoints", service_url)

        # Default: single URL (ingress or single instance)
        base_url = options.get("baseUrl") or options.get("url")
        if not base_url:
            logger.error("HAProxy target %s: baseUrl not configured", target_name)
            return None

        try:
            rows = self._request_csv(base_url, timeout_s=timeout_s, verify_tls=verify_tls)

            # Group by proxy name
            proxies: Dict[str, List[Dict[str, Any]]] = {}
            for row in rows:
                parsed = self._parse_stats_row(row)
                if parsed:
                    pxname = parsed["proxy_name"]
                    if pxname not in proxies:
                        proxies[pxname] = []
                    proxies[pxname].append(parsed)

            # Ports are only available via ConfigMap (K8s mode), not in single URL mode
            overview, proxies_list = self._build_overview_and_proxies(proxies, proxy_ports={})
            sessions_breakdown = self._extract_sessions_breakdown(rows)
            backend_by_proxy = sessions_breakdown.pop("backend_by_proxy", {})
            self._apply_backend_metrics_to_proxies(proxies_list, backend_by_proxy)
            if "backend_sessions" not in sessions_breakdown and overview.get("total_sessions") is not None:
                sessions_breakdown["backend_sessions"] = overview["total_sessions"]
            if "frontend_sessions" in sessions_breakdown:
                # Common sessions for pod/overview should come from FRONTEND rows.
                sessions_breakdown["total_sessions"] = sessions_breakdown["frontend_sessions"]
            overview.update(sessions_breakdown)
            from datetime import datetime, timezone
            pod_label = base_url.split("://")[1].split("/")[0] if "://" in base_url else base_url
            return _with_ingresses({
                "name": target_name,
                "overview": overview,
                "proxies": proxies_list,
                "pods": [{
                    "url": base_url,
                    "label": pod_label,
                    "overview": overview,
                    "proxies": proxies_list,
                }],
                "last_update": datetime.now(timezone.utc).isoformat(),
            })

        except Exception as e:
            logger.error("Error collecting HAProxy data for target %s: %s", target_name, e)
            return None

    def _build_overview_and_proxies(
        self,
        proxies: Dict[str, List[Dict[str, Any]]],
        version: Optional[str] = None,
        proxy_ports: Optional[Dict[str, int]] = None,
    ) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """Build overview and proxies list from grouped proxy data."""
        total_servers = sum(len(servers) for servers in proxies.values())
        total_up = 0
        total_down = 0
        total_sessions = 0
        for servers in proxies.values():
            for server in servers:
                if server["status"] == "UP":
                    total_up += 1
                elif server["status"] in ("DOWN", "MAINT"):
                    total_down += 1
                # Real-time sessions: use current active sessions (scur).
                if server.get("scur") is not None:
                    total_sessions += server["scur"]

        overview = {
            "total_proxies": len(proxies),
            "total_servers": total_servers,
            "servers_up": total_up,
            "servers_down": total_down,
            "total_sessions": total_sessions,
        }
        if version:
            overview["version"] = version

        ports = proxy_ports or {}

        proxies_list = []
        for pxname, servers in proxies.items():
            servers_up = sum(1 for s in servers if s["status"] == "UP")
            servers_down = sum(1 for s in servers if s["status"] in ("DOWN", "MAINT"))
            proxy_item: Dict[str, Any] = {
                "name": pxname,
                "total_servers": len(servers),
                "servers_up": servers_up,
                "servers_down": servers_down,
                "servers": servers,
            }
            # Assign port from ConfigMap mapping (direct lookup, no heuristics)
            if pxname in ports:
                proxy_item["port"] = ports[pxname]
            proxies_list.append(proxy_item)
        return overview, proxies_list

    def _collect_from_multiple_pods(
        self,
        target_name: str,
        pods: List[Any],
        timeout_s: float,
        verify_tls: bool,
        proxy_ports: Optional[Dict[str, int]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Collect HAProxy statistics from multiple pods.
        Returns aggregated summary (overview, proxies) plus per-pod breakdown (pods).
        """
        pods_data: List[Dict[str, Any]] = []
        all_proxies: Dict[str, List[Dict[str, Any]]] = {}
        pod_versions = set()
        frontend_sessions_value: Optional[int] = None
        frontend_sessions_limit_value: Optional[int] = None
        backend_sessions_sum = 0
        has_backend_sessions = False
        aggregated_backend_by_proxy: Dict[str, Dict[str, int]] = {}
        # Use proxy_ports from ConfigMap (passed as parameter), not from CSV stats
        effective_proxy_ports = proxy_ports or {}

        pod_url: Optional[str] = None
        for pod_entry in pods:
            try:
                if isinstance(pod_entry, dict):
                    pod_url = str(pod_entry.get("url") or "").strip()
                    pod_label = str(pod_entry.get("label") or "").strip()
                    pod_version = str(pod_entry.get("version") or "").strip()
                else:
                    pod_url = str(pod_entry).strip()
                    pod_label = ""
                    pod_version = ""

                if not pod_url:
                    continue
                if not pod_url.startswith(('http://', 'https://')):
                    pod_url = f"http://{pod_url}"
                if not pod_label:
                    pod_label = pod_url.split("://")[1].split(":")[0]

                rows = self._request_csv(pod_url, timeout_s=timeout_s, verify_tls=verify_tls)
                proxies: Dict[str, List[Dict[str, Any]]] = {}

                for row in rows:
                    parsed = self._parse_stats_row(row)
                    if parsed:
                        pxname = parsed["proxy_name"]
                        if pxname not in proxies:
                            proxies[pxname] = []
                        proxies[pxname].append(parsed)
                        if pxname not in all_proxies:
                            all_proxies[pxname] = []
                        parsed_aggregated = {**parsed}
                        parsed_aggregated["server_name"] = f"{parsed['server_name']}@{pod_label}"
                        all_proxies[pxname].append(parsed_aggregated)

                # Use proxy_ports from ConfigMap (passed as parameter)
                pod_overview, pod_proxies = self._build_overview_and_proxies(
                    proxies, version=pod_version or None, proxy_ports=effective_proxy_ports
                )
                sessions_breakdown = self._extract_sessions_breakdown(rows)
                backend_by_proxy = sessions_breakdown.pop("backend_by_proxy", {})
                self._apply_backend_metrics_to_proxies(pod_proxies, backend_by_proxy)
                for pxname, metrics in backend_by_proxy.items():
                    agg = aggregated_backend_by_proxy.setdefault(pxname, {})
                    if "sessions_current" in metrics:
                        agg["sessions_current"] = agg.get("sessions_current", 0) + metrics["sessions_current"]
                if "backend_sessions" not in sessions_breakdown and pod_overview.get("total_sessions") is not None:
                    sessions_breakdown["backend_sessions"] = pod_overview["total_sessions"]
                if "frontend_sessions" in sessions_breakdown:
                    sessions_breakdown["total_sessions"] = sessions_breakdown["frontend_sessions"]
                pod_overview.update(sessions_breakdown)
                if "frontend_sessions" in sessions_breakdown:
                    # FRONTEND sessions are cluster-wide indicator; do not sum across pods.
                    # Keep the first observed value.
                    if frontend_sessions_value is None:
                        frontend_sessions_value = sessions_breakdown["frontend_sessions"]
                if "frontend_sessions_limit" in sessions_breakdown:
                    if frontend_sessions_limit_value is None:
                        frontend_sessions_limit_value = sessions_breakdown["frontend_sessions_limit"]
                if "backend_sessions" in sessions_breakdown:
                    has_backend_sessions = True
                    backend_sessions_sum += sessions_breakdown["backend_sessions"]
                if pod_version:
                    pod_versions.add(pod_version)
                pods_data.append({
                    "url": pod_url,
                    "label": pod_label,
                    "overview": pod_overview,
                    "proxies": pod_proxies,
                })
            except Exception as e:
                # Expected during rollout when one pod is terminating or not ready
                log_monitor_request(logger, "haproxy", "fetch_pod_stats_failed", endpoint=pod_url, error=str(e))
                continue

        if not pods_data:
            logger.error("No HAProxy data collected from any pod for target %s", target_name)
            return None

        cluster_version = None
        if len(pod_versions) == 1:
            cluster_version = next(iter(pod_versions))
        elif len(pod_versions) > 1:
            cluster_version = f"mixed: {', '.join(sorted(pod_versions))}"

        overview, proxies_list = self._build_overview_and_proxies(
            all_proxies, version=cluster_version, proxy_ports=effective_proxy_ports
        )
        self._apply_backend_metrics_to_proxies(proxies_list, aggregated_backend_by_proxy)
        if frontend_sessions_value is not None:
            overview["frontend_sessions"] = frontend_sessions_value
            overview["total_sessions"] = frontend_sessions_value
        if frontend_sessions_limit_value is not None:
            overview["frontend_sessions_limit"] = frontend_sessions_limit_value
        if has_backend_sessions:
            overview["backend_sessions"] = backend_sessions_sum
        elif overview.get("total_sessions") is not None:
            overview["backend_sessions"] = overview["total_sessions"]
        from datetime import datetime, timezone
        return {
            "name": target_name,
            "overview": overview,
            "proxies": proxies_list,
            "pods": pods_data,
            "last_update": datetime.now(timezone.utc).isoformat(),
        }

"""
Small VictoriaMetrics HTTP client for Prometheus-compatible instant queries.
"""
import json
from typing import Any, Dict, List
from urllib.parse import urlencode
from urllib.request import Request
from app.services.http_client import open_http as urlopen



class MetricsQueryError(RuntimeError):
    """The configured metrics backend responded but rejected the query."""


class VictoriaMetricsClient:
    """Query VictoriaMetrics through the Prometheus-compatible API."""

    def __init__(self, base_url: str, timeout_s: float = 3.0):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def _query_url(self) -> str:
        if self.base_url.endswith("/api/v1"):
            return f"{self.base_url}/query"
        return f"{self.base_url}/api/v1/query"

    def query(self, promql: str) -> List[Dict[str, Any]]:
        params = urlencode({"query": promql})
        url = f"{self._query_url()}?{params}"
        request = Request(url, headers={"Accept": "application/json"})

        with urlopen(request, timeout=self.timeout_s) as response:
            payload = json.loads(response.read().decode("utf-8"))

        if payload.get("status") != "success":
            raise MetricsQueryError('VictoriaMetrics query failed')

        data = payload.get("data") or {}
        if data.get("resultType") != "vector":
            return []
        result = data.get("result")
        return result if isinstance(result, list) else []

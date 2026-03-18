"""
HTTP client 侧指标封装。

统一为 Prometheus/VM、Grafana、Telegram、下游 Webhook 等 HTTP 请求打指标：
- webhook_alerts_http_client_requests_total{target,method,status}
- webhook_alerts_http_client_request_duration_seconds{target,method,status}
"""
from __future__ import annotations

import time
from typing import Any, Dict

import requests

from .metrics import HttpClientRequestDuration, HttpClientRequestsTotal


def request_with_metrics(
    session: requests.Session,
    method: str,
    url: str,
    *,
    target: str,
    **kwargs: Any,
) -> requests.Response:
    """
    统一封装 requests 请求并打 http_client 指标。

    Args:
        session: 复用的 requests.Session
        method: HTTP 方法，如 GET/POST
        url: 请求 URL
        target: 逻辑目标（prometheus/victoriametrics/grafana/telegram/webhook 等）
        **kwargs: 透传给 session.request
    """
    start = time.perf_counter()
    status = "error"
    try:
        resp = session.request(method=method, url=url, **kwargs)
        status = str(resp.status_code)
        resp.raise_for_status()
        return resp
    finally:
        elapsed = time.perf_counter() - start
        try:
            HttpClientRequestsTotal.labels(
                target=target,
                method=method.upper(),
                status=status,
            ).inc()
            HttpClientRequestDuration.labels(
                target=target,
                method=method.upper(),
                status=status,
            ).observe(elapsed)
        except Exception:
            # 指标异常不影响主流程
            pass


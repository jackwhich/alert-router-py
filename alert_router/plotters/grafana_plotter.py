"""
Grafana 趋势图生成模块

支持两种方式生成图片：
1. 如果 Grafana 使用 Prometheus 数据源，从 generatorURL 提取查询表达式并调用 Prometheus API
2. 使用 Grafana 渲染服务（需要配置 grafana_url 和渲染服务）
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, Optional
from urllib.parse import parse_qs, urlparse

import requests

from ..core.http_metrics import request_with_metrics
from ..core.logging_config import get_logger
from .base import render_matplotlib_png

logger = get_logger("alert-router")


def generate_plot_from_grafana_generator_url(
    generator_url: str,
    *,
    grafana_url: Optional[str] = None,
    grafana_api_token: Optional[str] = None,
    prometheus_url: Optional[str] = None,
    proxies: Optional[Dict[str, str]] = None,
    lookback_minutes: int = 15,
    step: str = "30s",
    timeout_seconds: int = 8,
    max_series: int = 8,
    alertname: Optional[str] = None,
    alert_time: Optional[str] = None,
) -> Optional[bytes]:
    """
    根据 Grafana generatorURL 生成趋势图。
    """
    if not generator_url:
        return None

    effective_grafana_url = grafana_url
    if not effective_grafana_url:
        try:
            parsed = urlparse(generator_url)
            if parsed.scheme and parsed.netloc:
                effective_grafana_url = f"{parsed.scheme}://{parsed.netloc}"
                logger.debug(f"从 generatorURL 提取 Grafana URL: {effective_grafana_url}")
        except Exception:
            pass
    logger.info(
        "Grafana 图片生成配置: generatorURL=%s, grafana_url=%s, effective_grafana_url=%s, grafana_token_set=%s",
        generator_url,
        grafana_url,
        effective_grafana_url,
        bool(grafana_api_token),
    )

    if effective_grafana_url:
        result = _generate_from_grafana_renderer(
            generator_url=generator_url,
            grafana_url=effective_grafana_url,
            grafana_api_token=grafana_api_token,
            proxies=proxies,
            timeout_seconds=timeout_seconds,
            alertname=alertname,
            alert_time=alert_time,
        )
        if result:
            return result

    if prometheus_url:
        result = _generate_from_prometheus_query(
            generator_url=generator_url,
            prometheus_url=prometheus_url,
            proxies=proxies,
            lookback_minutes=lookback_minutes,
            step=step,
            timeout_seconds=timeout_seconds,
            max_series=max_series,
            alertname=alertname,
            alert_time=alert_time,
        )
        if result:
            return result

        if effective_grafana_url:
            result = _generate_from_grafana_alert_rule(
                generator_url=generator_url,
                grafana_url=effective_grafana_url,
                grafana_api_token=grafana_api_token,
                prometheus_url=prometheus_url,
                proxies=proxies,
                lookback_minutes=lookback_minutes,
                step=step,
                timeout_seconds=timeout_seconds,
                max_series=max_series,
                alertname=alertname,
                alert_time=alert_time,
            )
            if result:
                return result

    logger.info("Grafana 图片生成：未配置 grafana_url（或无法从 generatorURL 提取），跳过出图")
    return None


def _query_prometheus_range(
    prometheus_url: str,
    expr: str,
    *,
    lookback_minutes: int,
    step: str,
    timeout_seconds: int,
    proxies: Optional[Dict[str, str]],
    max_series: int,
) -> Optional[list]:
    parsed_prometheus = urlparse(prometheus_url)
    if not parsed_prometheus.scheme or not parsed_prometheus.netloc:
        logger.warning("配置的 prometheus_url 非法: %s", prometheus_url)
        return None
    prometheus_base = f"{parsed_prometheus.scheme}://{parsed_prometheus.netloc}"
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=max(1, lookback_minutes))
    prometheus_api = f"{prometheus_base}/api/v1/query_range"
    params = {
        "query": expr,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "step": step,
    }
    session = requests.Session()
    response = request_with_metrics(
        session,
        "GET",
        prometheus_api,
        target="prometheus",
        params=params,
        timeout=timeout_seconds,
        proxies=proxies,
    )
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data", {})
    result = data.get("result", [])
    if payload.get("status") != "success" or not isinstance(result, list) or not result:
        logger.info("Prometheus query_range 无可绘制数据，跳过出图")
        return None
    if max_series and len(result) > max_series:
        result = result[:max_series]
    return result


def _plot_grafana_result(
    result: list,
    alertname: Optional[str],
    alert_time: Optional[str],
) -> Optional[bytes]:
    png = render_matplotlib_png(
        result,
        alertname=alertname,
        alert_time=alert_time,
        default_title="Grafana Alert Trend",
    )
    if png is None:
        logger.info("Prometheus query_range 结果无法解析为曲线，跳过出图")
    return png


def _generate_from_prometheus_query(
    generator_url: str,
    prometheus_url: str,
    proxies: Optional[Dict[str, str]] = None,
    lookback_minutes: int = 15,
    step: str = "30s",
    timeout_seconds: int = 8,
    max_series: int = 8,
    alertname: Optional[str] = None,
    alert_time: Optional[str] = None,
) -> Optional[bytes]:
    """从 Grafana generatorURL 提取 Prometheus 查询表达式并生成图片。"""
    try:
        parsed = urlparse(generator_url)
        q = parse_qs(parsed.query)
        expr = (q.get("query") or q.get("expr") or q.get("g0.expr") or [None])[0]
        if not expr:
            logger.debug("Grafana generatorURL 中未找到查询表达式，尝试使用 Grafana 渲染服务")
            return None
        logger.debug("从 Grafana generatorURL 提取查询，请求 Prometheus query_range")
        result = _query_prometheus_range(
            prometheus_url,
            expr,
            lookback_minutes=lookback_minutes,
            step=step,
            timeout_seconds=timeout_seconds,
            proxies=proxies,
            max_series=max_series,
        )
        if not result:
            return None
        return _plot_grafana_result(result, alertname, alert_time)
    except requests.RequestException as exc:
        logger.warning("Grafana 出图请求 Prometheus API 失败: %s", exc)
        return None
    except Exception as exc:
        logger.warning("Grafana 出图异常: %s", exc)
        return None


def _build_grafana_headers(grafana_api_token: Optional[str]) -> Optional[Dict[str, str]]:
    if not grafana_api_token:
        return None
    return {"Authorization": f"Bearer {grafana_api_token}"}


def _extract_alert_rule_uid(generator_url: str) -> Optional[str]:
    """从 Grafana generatorURL 中提取告警规则 UID。"""
    try:
        parsed = urlparse(generator_url)
        path = parsed.path.strip("/")
        parts = path.split("/")
        if len(parts) >= 3 and parts[0] == "alerting":
            if parts[1] == "grafana" and len(parts) >= 3:
                uid = parts[2]
                if uid and uid != "view":
                    return uid
            elif len(parts) >= 2:
                uid = parts[1]
                if uid and uid != "view":
                    return uid
    except Exception as exc:
        logger.debug(f"从 generatorURL 提取告警规则 UID 失败: {exc}")
    return None


def _generate_from_grafana_alert_rule(
    generator_url: str,
    grafana_url: str,
    grafana_api_token: Optional[str] = None,
    prometheus_url: Optional[str] = None,
    proxies: Optional[Dict[str, str]] = None,
    lookback_minutes: int = 15,
    step: str = "30s",
    timeout_seconds: int = 8,
    max_series: int = 8,
    alertname: Optional[str] = None,
    alert_time: Optional[str] = None,
) -> Optional[bytes]:
    """从 Grafana 告警规则 API 获取查询表达式并生成图片。"""
    try:
        rule_uid = _extract_alert_rule_uid(generator_url)
        if not rule_uid:
            logger.debug("无法从 generatorURL 提取告警规则 UID")
            return None

        parsed_grafana = urlparse(grafana_url)
        if not parsed_grafana.scheme or not parsed_grafana.netloc:
            logger.warning("配置的 grafana_url 非法: %s", grafana_url)
            return None
        grafana_base = f"{parsed_grafana.scheme}://{parsed_grafana.netloc}"
        api_url = f"{grafana_base}/api/alerting/rule/{rule_uid}"
        logger.debug(f"从 Grafana API 获取告警规则详情: {api_url}")
        headers = _build_grafana_headers(grafana_api_token)
        session = requests.Session()
        response = request_with_metrics(
            session,
            "GET",
            api_url,
            target="grafana",
            timeout=timeout_seconds,
            proxies=proxies,
            headers=headers,
        )
        if response.status_code == 404:
            logger.debug("Grafana 9+ API 不存在，尝试 Grafana 8.x API")
            return None

        response.raise_for_status()
        rule_data = response.json()
        queries = rule_data.get("data", {}).get("queries", [])
        if not queries:
            logger.debug("告警规则中未找到查询表达式")
            return None

        prometheus_query = None
        for query in queries:
            expr = query.get("expr") or query.get("model", {}).get("expr")
            if expr:
                prometheus_query = expr
                break
        if not prometheus_query:
            logger.debug("告警规则中未找到 Prometheus 查询表达式")
            return None
        if not prometheus_url:
            logger.debug("未配置 prometheus_url，无法查询 Prometheus 数据")
            return None

        logger.debug(
            "从 Grafana 告警规则提取查询，请求 Prometheus query_range: query=%s",
            prometheus_query[:100] if len(prometheus_query) > 100 else prometheus_query,
        )
        result = _query_prometheus_range(
            prometheus_url,
            prometheus_query,
            lookback_minutes=lookback_minutes,
            step=step,
            timeout_seconds=timeout_seconds,
            proxies=proxies,
            max_series=max_series,
        )
        if not result:
            return None
        return _plot_grafana_result(result, alertname, alert_time)
    except requests.RequestException as exc:
        logger.debug(f"Grafana 出图请求 API 失败: {exc}")
        return None
    except Exception as exc:
        logger.debug(f"Grafana 出图异常: {exc}")
        return None


def _generate_from_grafana_renderer(
    generator_url: str,
    grafana_url: str,
    grafana_api_token: Optional[str] = None,
    proxies: Optional[Dict[str, str]] = None,
    timeout_seconds: int = 8,
    alertname: Optional[str] = None,
    alert_time: Optional[str] = None,
) -> Optional[bytes]:
    """使用 Grafana 渲染服务生成图片。"""
    try:
        rule_uid = _extract_alert_rule_uid(generator_url)
        if not rule_uid:
            logger.debug("无法从 generatorURL 提取告警规则 UID，无法使用渲染服务")
            return None

        parsed_grafana = urlparse(grafana_url)
        if not parsed_grafana.scheme or not parsed_grafana.netloc:
            logger.warning("配置的 grafana_url 非法: %s", grafana_url)
            return None
        grafana_base = f"{parsed_grafana.scheme}://{parsed_grafana.netloc}"

        api_url = f"{grafana_base}/api/alerting/rule/{rule_uid}"
        logger.debug(f"从 Grafana API 获取告警规则详情: {api_url}")
        headers = _build_grafana_headers(grafana_api_token)
        session = requests.Session()
        response = request_with_metrics(
            session,
            "GET",
            api_url,
            target="grafana",
            timeout=timeout_seconds,
            proxies=proxies,
            headers=headers,
        )

        if response.status_code == 404:
            logger.debug("告警规则不存在或 Grafana 版本不支持该 API")
            return None

        response.raise_for_status()
        rule_data = response.json()

        dashboard_uid = None
        panel_id = None
        rule_spec = rule_data.get("data", {}).get("rule", {}) or rule_data.get("rule", {}) or rule_data
        dashboard_uid = (
            rule_spec.get("dashboardUid")
            or rule_spec.get("dashboard_uid")
            or rule_spec.get("dashboardUID")
            or None
        )
        panel_id = (
            rule_spec.get("panelId")
            or rule_spec.get("panel_id")
            or rule_spec.get("panelID")
            or None
        )

        if not dashboard_uid or not panel_id:
            queries = rule_spec.get("data", {}).get("queries", []) or rule_spec.get("queries", [])
            for query in queries:
                if isinstance(query, dict):
                    dashboard_uid = dashboard_uid or query.get("dashboardUid") or query.get("dashboard_uid")
                    panel_id = panel_id or query.get("panelId") or query.get("panel_id")
                    if dashboard_uid and panel_id:
                        break

        if not dashboard_uid or not panel_id:
            logger.debug(
                f"告警规则中未找到 dashboard/panel 信息 (dashboard_uid={dashboard_uid}, panel_id={panel_id})"
            )
            return None

        render_url = f"{grafana_base}/render/d-solo/{dashboard_uid}/{panel_id}"
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(minutes=15)
        params = {
            "from": str(int(start_time.timestamp() * 1000)),
            "to": str(int(end_time.timestamp() * 1000)),
            "width": 1000,
            "height": 500,
            "theme": "light",
        }

        logger.debug(f"使用 Grafana 渲染服务生成图片: {render_url}")
        session = requests.Session()
        response = request_with_metrics(
            session,
            "GET",
            render_url,
            target="grafana",
            params=params,
            timeout=timeout_seconds + 5,
            proxies=proxies,
            headers=headers,
        )

        if response.status_code == 404:
            logger.debug("Grafana 渲染服务未启用或 dashboard/panel 不存在")
            return None

        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "")
        if "image" in content_type.lower():
            return response.content
        logger.debug(f"Grafana 渲染服务返回非图片内容: {content_type}")
        return None

    except requests.RequestException as exc:
        logger.debug(f"Grafana 渲染服务请求失败: {exc}")
        return None
    except Exception as exc:
        logger.debug(f"Grafana 渲染服务异常: {exc}")
        return None

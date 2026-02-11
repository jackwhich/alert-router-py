"""
Grafana 趋势图生成模块

支持两种方式生成图片：
1. 如果 Grafana 使用 Prometheus 数据源，从 generatorURL 提取查询表达式并调用 Prometheus API
2. 使用 Grafana 渲染服务（需要配置 grafana_url 和渲染服务）
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Dict, Optional
from urllib.parse import parse_qs, urlparse

import matplotlib
import requests

from .logging_config import get_logger

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

logger = get_logger("alert-router")


def _build_series_label(metric: Dict[str, str]) -> str:
    """从 Prometheus metric 标签构造曲线名称。"""
    if not isinstance(metric, dict):
        return "series"
    pairs = []
    for k in sorted(metric.keys()):
        if k == "__name__":
            continue
        pairs.append(f"{k}={metric[k]}")
    label = ", ".join(pairs) if pairs else metric.get("__name__", "series")
    if len(label) > 90:
        return label[:87] + "..."
    return label


def generate_plot_from_grafana_generator_url(
    generator_url: str,
    *,
    grafana_url: Optional[str] = None,
    prometheus_url: Optional[str] = None,
    proxies: Optional[Dict[str, str]] = None,
    lookback_minutes: int = 15,
    step: str = "30s",
    timeout_seconds: int = 8,
    max_series: int = 8,
) -> Optional[bytes]:
    """
    根据 Grafana generatorURL 生成趋势图。
    
    优先尝试从 generatorURL 提取 Prometheus 查询表达式（如果 Grafana 使用 Prometheus 数据源），
    否则尝试使用 Grafana 渲染服务。

    返回:
        PNG 二进制内容；无法生成时返回 None。
    """
    if not generator_url:
        return None

    # 方法1：如果配置了 prometheus_url，尝试从 generatorURL 提取查询表达式
    if prometheus_url:
        return _generate_from_prometheus_query(
            generator_url=generator_url,
            prometheus_url=prometheus_url,
            proxies=proxies,
            lookback_minutes=lookback_minutes,
            step=step,
            timeout_seconds=timeout_seconds,
            max_series=max_series,
        )

    # 方法2：使用 Grafana 渲染服务（如果配置了 grafana_url）
    if grafana_url:
        return _generate_from_grafana_renderer(
            generator_url=generator_url,
            grafana_url=grafana_url,
            proxies=proxies,
            timeout_seconds=timeout_seconds,
        )

    logger.info("Grafana 图片生成：未配置 prometheus_url 或 grafana_url，跳过出图")
    return None


def _generate_from_prometheus_query(
    generator_url: str,
    prometheus_url: str,
    proxies: Optional[Dict[str, str]] = None,
    lookback_minutes: int = 15,
    step: str = "30s",
    timeout_seconds: int = 8,
    max_series: int = 8,
) -> Optional[bytes]:
    """从 Grafana generatorURL 提取 Prometheus 查询表达式并生成图片。"""
    try:
        # 尝试从 Grafana generatorURL 中提取查询表达式
        # Grafana 的 generatorURL 格式可能是：http://grafana:3000/alerting/...?query=...
        parsed = urlparse(generator_url)
        q = parse_qs(parsed.query)
        
        # 尝试多种可能的查询参数名
        expr = (
            q.get("query") or q.get("expr") or q.get("g0.expr") or [None]
        )[0]
        
        if not expr:
            logger.debug("Grafana generatorURL 中未找到查询表达式，尝试使用 Grafana 渲染服务")
            return None

        # 使用配置的 Prometheus URL
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

        logger.debug(
            "从 Grafana generatorURL 提取查询，请求 Prometheus query_range: api=%s",
            prometheus_api,
        )
        response = requests.get(
            prometheus_api,
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

        fig, ax = plt.subplots(figsize=(10, 4.5), dpi=120)
        plotted = 0
        for series in result:
            if plotted >= max_series:
                break
            values = series.get("values") or []
            if not values:
                continue
            xs = []
            ys = []
            for item in values:
                if not isinstance(item, (list, tuple)) or len(item) < 2:
                    continue
                try:
                    ts = float(item[0])
                    val = float(item[1])
                except (TypeError, ValueError):
                    continue
                xs.append(datetime.fromtimestamp(ts, tz=timezone.utc))
                ys.append(val)
            if not xs:
                continue
            label = _build_series_label(series.get("metric") or {})
            ax.plot(xs, ys, linewidth=1.6, label=label)
            plotted += 1

        if plotted == 0:
            plt.close(fig)
            logger.info("Prometheus query_range 结果无法解析为曲线，跳过出图")
            return None

        ax.set_title("Grafana Alert Trend")
        ax.set_xlabel("Time (UTC)")
        ax.set_ylabel("Value")
        ax.grid(True, linestyle="--", alpha=0.35)
        ax.legend(loc="best", fontsize=8)
        fig.autofmt_xdate()
        fig.tight_layout()

        buffer = BytesIO()
        fig.savefig(buffer, format="png")
        plt.close(fig)
        return buffer.getvalue()
    except requests.RequestException as exc:
        logger.warning("Grafana 出图请求 Prometheus API 失败: %s", exc)
        return None
    except Exception as exc:
        logger.warning("Grafana 出图异常: %s", exc)
        return None


def _generate_from_grafana_renderer(
    generator_url: str,
    grafana_url: str,
    proxies: Optional[Dict[str, str]] = None,
    timeout_seconds: int = 8,
) -> Optional[bytes]:
    """使用 Grafana 渲染服务生成图片。"""
    # Grafana 渲染 API 格式：/render/d-solo/{dashboard_uid}/{panel_id}?...
    # 或者使用 /api/render 端点（需要配置渲染服务）
    
    # 尝试从 generatorURL 构建渲染 URL
    parsed = urlparse(generator_url)
    
    # 如果 generatorURL 已经是 Grafana 的 alerting 页面，尝试转换为渲染 URL
    # 这需要根据实际的 Grafana 配置来调整
    
    # 简化方案：直接使用 generatorURL 作为渲染目标（如果 Grafana 支持）
    # 实际使用时可能需要根据 Grafana 版本和配置调整
    
    logger.info("Grafana 渲染服务功能待实现，当前跳过")
    return None

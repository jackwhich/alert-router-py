"""
Prometheus 趋势图生成模块

基于 Alertmanager webhook 中的 generatorURL（g0.expr）调用 Prometheus query_range，
生成 PNG 趋势图，供 Telegram sendPhoto 使用。
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


def generate_plot_from_generator_url(
    generator_url: str,
    *,
    prometheus_url: Optional[str] = None,
    proxies: Optional[Dict[str, str]] = None,
    lookback_minutes: int = 15,
    step: str = "30s",
    timeout_seconds: int = 8,
    max_series: int = 8,
) -> Optional[bytes]:
    """
    根据 generatorURL 生成 Prometheus 趋势图。

    返回:
        PNG 二进制内容；无法生成时返回 None。
    """
    if not generator_url:
        return None

    try:
        # 解析查询表达式
        q = parse_qs(urlparse(generator_url).query)
        expr = (q.get("g0.expr") or [None])[0]
        if not expr:
            logger.info("generatorURL 不含 g0.expr，跳过出图")
            return None

        # 确定 Prometheus API 地址：优先使用配置的 prometheus_url，否则从 generatorURL 解析
        if prometheus_url:
            # 使用配置的 Prometheus URL
            parsed_prometheus = urlparse(prometheus_url)
            if not parsed_prometheus.scheme or not parsed_prometheus.netloc:
                logger.warning("配置的 prometheus_url 非法，跳过出图: %s", prometheus_url)
                return None
            prometheus_base = f"{parsed_prometheus.scheme}://{parsed_prometheus.netloc}"
        else:
            # 从 generatorURL 解析
            parsed = urlparse(generator_url)
            if not parsed.scheme or not parsed.netloc:
                logger.warning("generatorURL 非法，跳过出图: %s", generator_url)
                return None
            prometheus_base = f"{parsed.scheme}://{parsed.netloc}"

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
            "请求 Prometheus query_range 生成趋势图: api=%s, step=%s, lookback=%sm",
            prometheus_api,
            step,
            lookback_minutes,
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

        ax.set_title("Prometheus Alert Trend")
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
        logger.warning("Prometheus 出图请求失败，跳过图片发送: %s", exc)
        return None
    except Exception as exc:
        logger.warning("Prometheus 出图异常，跳过图片发送: %s", exc)
        return None

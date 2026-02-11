"""
Grafana 趋势图生成模块

支持两种方式生成图片：
1. 如果 Grafana 使用 Prometheus 数据源，从 generatorURL 提取查询表达式并调用 Prometheus API
2. 使用 Grafana 渲染服务（需要配置 grafana_url 和渲染服务）
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from io import BytesIO
from typing import Dict, Optional
from urllib.parse import parse_qs, urlparse

import matplotlib
import matplotlib.dates as mdates
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
    alertname: Optional[str] = None,
    alert_time: Optional[str] = None,
) -> Optional[bytes]:
    """
    根据 Grafana generatorURL 生成趋势图。
    
    支持多种方式：
    1. 如果配置了 prometheus_url，尝试从 generatorURL 提取查询表达式
    2. 如果配置了 grafana_url，尝试从 generatorURL 提取告警规则 UID，调用 Grafana API 获取查询表达式
    3. 使用 Grafana 渲染服务（如果配置了 grafana_url）

    返回:
        PNG 二进制内容；无法生成时返回 None。
    """
    if not generator_url:
        return None

    # 方法1：尝试从 generatorURL 提取 Grafana URL（如果未配置）
    effective_grafana_url = grafana_url
    if not effective_grafana_url:
        try:
            parsed = urlparse(generator_url)
            if parsed.scheme and parsed.netloc:
                effective_grafana_url = f"{parsed.scheme}://{parsed.netloc}"
                logger.debug(f"从 generatorURL 提取 Grafana URL: {effective_grafana_url}")
        except Exception:
            pass
    
    # 方法2：如果配置了 grafana_url 或从 generatorURL 提取到了，优先使用 Grafana 渲染服务
    if effective_grafana_url:
        # 优先尝试使用 Grafana 渲染服务（不依赖 Prometheus）
        result = _generate_from_grafana_renderer(
            generator_url=generator_url,
            grafana_url=effective_grafana_url,
            proxies=proxies,
            timeout_seconds=timeout_seconds,
            alertname=alertname,
            alert_time=alert_time,
        )
        if result:
            return result
    
    # 方法3：如果配置了 prometheus_url，尝试从 generatorURL 提取查询表达式（向后兼容）
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
        
        # 如果直接提取失败，尝试从 Grafana API 获取告警规则详情
        if effective_grafana_url:
            result = _generate_from_grafana_alert_rule(
                generator_url=generator_url,
                grafana_url=effective_grafana_url,
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

        # 创建图表，使用更大的尺寸和更高的DPI以获得更好的质量
        fig, ax = plt.subplots(figsize=(12, 6), dpi=150)
        
        # 设置中文字体支持
        import platform
        if platform.system() == 'Darwin':  # macOS
            plt.rcParams['font.sans-serif'] = ['Arial Unicode MS', 'PingFang SC', 'STHeiti', 'Arial']
        elif platform.system() == 'Linux':
            plt.rcParams['font.sans-serif'] = ['WenQuanYi Micro Hei', 'DejaVu Sans', 'Liberation Sans']
        else:  # Windows
            plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'Arial']
        plt.rcParams['axes.unicode_minus'] = False
        
        plotted = 0
        # 使用更鲜艳、对比度更好的颜色方案
        colors = ['#e74c3c', '#3498db', '#2ecc71', '#f39c12', '#9b59b6', '#1abc9c', '#e67e22', '#34495e']
        # 如果数据系列超过预设颜色数量，使用调色板生成更多颜色
        if len(result) > len(colors):
            colors = plt.cm.Set2(range(len(result)))
        
        for idx, series in enumerate(result):
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
                # 将 UTC 时间转换为 UTC+8
                utc_time = datetime.fromtimestamp(ts, tz=timezone.utc)
                utc8_time = utc_time.astimezone(ZoneInfo("Asia/Shanghai"))
                xs.append(utc8_time)
                ys.append(val)
            if not xs:
                continue
            
            # 确保数据点按时间排序
            sorted_pairs = sorted(zip(xs, ys), key=lambda x: x[0])
            xs, ys = zip(*sorted_pairs) if sorted_pairs else ([], [])
            
            if not xs:
                continue
            
            label = _build_series_label(series.get("metric") or {})
            ax.plot(xs, ys, linewidth=2.5, label=label, color=colors[idx % len(colors)], marker='o', markersize=4, alpha=0.9)
            plotted += 1

        if plotted == 0:
            plt.close(fig)
            logger.info("Prometheus query_range 结果无法解析为曲线，跳过出图")
            return None

        # 优化图表样式 - 使用实际的 alertname 作为标题（深色主题）
        chart_title = alertname if alertname else "Grafana Alert Trend"
        ax.set_title(chart_title, fontsize=18, fontweight='bold', pad=25, color='#e0e0e0')
        
        # X轴标签显示告警时间（UTC+8，到秒）
        if alert_time:
            try:
                # 解析告警时间并转换为 UTC+8
                from dateutil import parser
                alert_dt = parser.parse(alert_time)
                if alert_dt.tzinfo is None:
                    # 如果没有时区信息，假设是 UTC
                    alert_dt = alert_dt.replace(tzinfo=timezone.utc)
                # 转换为 UTC+8
                alert_dt_utc8 = alert_dt.astimezone(ZoneInfo("Asia/Shanghai"))
                # 格式化为字符串：年-月-日 时:分:秒
                xlabel_text = alert_dt_utc8.strftime('%Y-%m-%d %H:%M:%S')
            except Exception:
                # 如果解析失败，使用默认标签
                xlabel_text = "Time (UTC+8)"
        else:
            xlabel_text = "Time (UTC+8)"
        
        ax.set_xlabel(xlabel_text, fontsize=13, color='#e0e0e0', fontweight='normal')
        
        # Y轴不显示标签，保持简洁
        ax.set_ylabel("", fontsize=0)
        
        # 优化Y轴数值格式 - 使用K格式（类似Grafana）
        def format_y_value(x, p):
            if abs(x) >= 1000:
                return f'{x/1000:.2f} K'.rstrip('0').rstrip('.')
            elif x == int(x):
                return f'{int(x)}'
            else:
                return f'{x:.1f}'
        ax.yaxis.set_major_formatter(plt.FuncFormatter(format_y_value))
        ax.tick_params(axis='y', labelsize=11, colors='#e0e0e0', width=1)
        ax.tick_params(axis='x', labelsize=10, colors='#e0e0e0', width=1)
        
        # 改进网格样式 - 更明显的网格线（深色主题）
        ax.grid(True, linestyle="--", alpha=0.3, linewidth=0.8, color='#666666')
        ax.set_axisbelow(True)
        
        # 设置坐标轴颜色（深色主题）
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.spines['left'].set_color('#666666')
        ax.spines['bottom'].set_color('#666666')
        ax.spines['left'].set_linewidth(1.5)
        ax.spines['bottom'].set_linewidth(1.5)
        
        # 优化图例显示 - 放在右侧（类似Grafana）
        if plotted > 0:
            legend = ax.legend(
                loc="center left",  # 放在右侧
                bbox_to_anchor=(1.02, 0.5),  # 稍微偏移到图表外
                fontsize=11,  # 字体大小
                framealpha=0.9,  # 透明度
                fancybox=True,
                shadow=False,
                edgecolor='#666666',
                facecolor='#2b2b2b',  # 深色背景
                borderpad=0.8,
                labelspacing=0.6,
                handlelength=1.5,
                handletextpad=0.5
            )
            # 设置图例文字颜色（浅色，适合深色背景）
            for text in legend.get_texts():
                text.set_color('#e0e0e0')
                text.set_fontweight('normal')
        
        # 优化时间轴显示 - 只显示时间（时:分:秒），不显示日期
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S', tz=ZoneInfo("Asia/Shanghai")))
        # 根据时间范围自动调整刻度间隔
        if len(xs) > 0:
            time_span = (max(xs) - min(xs)).total_seconds()
            if time_span <= 300:  # 5分钟以内，每30秒一个刻度
                ax.xaxis.set_major_locator(mdates.SecondLocator(interval=30))
            elif time_span <= 900:  # 15分钟以内，每1分钟一个刻度
                ax.xaxis.set_major_locator(mdates.MinuteLocator(interval=1))
            elif time_span <= 3600:  # 1小时以内，每5分钟一个刻度
                ax.xaxis.set_major_locator(mdates.MinuteLocator(interval=5))
            else:  # 超过1小时，每15分钟一个刻度
                ax.xaxis.set_major_locator(mdates.MinuteLocator(interval=15))
        fig.autofmt_xdate(rotation=45)
        
        # 优化背景色 - 使用深色主题（类似Grafana）
        fig.patch.set_facecolor('#1e1e1e')  # 深色背景
        ax.set_facecolor('#2b2b2b')  # 图表区域深灰色
        
        # 确保图表紧凑但不会裁剪内容
        fig.tight_layout(pad=3.0)

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


def _extract_alert_rule_uid(generator_url: str) -> Optional[str]:
    """从 Grafana generatorURL 中提取告警规则 UID。
    
    支持的格式：
    - http://grafana:3000/alerting/grafana/{uid}/view
    - http://grafana:3000/alerting/{uid}/view
    """
    try:
        parsed = urlparse(generator_url)
        path = parsed.path.strip("/")
        
        # 匹配格式：alerting/grafana/{uid}/view 或 alerting/{uid}/view
        parts = path.split("/")
        if len(parts) >= 3 and parts[0] == "alerting":
            # 格式：alerting/grafana/{uid}/view
            if parts[1] == "grafana" and len(parts) >= 3:
                uid = parts[2]
                if uid and uid != "view":
                    return uid
            # 格式：alerting/{uid}/view
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
        # 从 generatorURL 提取告警规则 UID
        rule_uid = _extract_alert_rule_uid(generator_url)
        if not rule_uid:
            logger.debug("无法从 generatorURL 提取告警规则 UID")
            return None
        
        # 解析 Grafana URL
        parsed_grafana = urlparse(grafana_url)
        if not parsed_grafana.scheme or not parsed_grafana.netloc:
            logger.warning("配置的 grafana_url 非法: %s", grafana_url)
            return None
        grafana_base = f"{parsed_grafana.scheme}://{parsed_grafana.netloc}"
        
        # 调用 Grafana API 获取告警规则详情
        # Grafana 9+ 使用 /api/alerting/rule/{uid}
        # Grafana 8.x 使用 /api/ruler/grafana/api/v1/rules/{namespace}/{group}/{rule}
        api_url = f"{grafana_base}/api/alerting/rule/{rule_uid}"
        
        logger.debug(f"从 Grafana API 获取告警规则详情: {api_url}")
        response = requests.get(
            api_url,
            timeout=timeout_seconds,
            proxies=proxies,
        )
        
        # 如果 404，可能是 Grafana 8.x，尝试其他 API
        if response.status_code == 404:
            logger.debug("Grafana 9+ API 不存在，尝试 Grafana 8.x API")
            # 对于 Grafana 8.x，需要先列出所有规则，然后查找匹配的
            # 这里简化处理，如果 404 就返回 None
            return None
        
        response.raise_for_status()
        rule_data = response.json()
        
        # 提取查询表达式
        # Grafana 告警规则可能包含多个查询（data、condition 等）
        queries = rule_data.get("data", {}).get("queries", [])
        if not queries:
            logger.debug("告警规则中未找到查询表达式")
            return None
        
        # 查找 Prometheus 数据源的查询
        prometheus_query = None
        for query in queries:
            datasource_uid = query.get("datasourceUid") or query.get("datasource", {}).get("uid")
            # 如果查询中包含 expr 字段，说明可能是 Prometheus 查询
            expr = query.get("expr") or query.get("model", {}).get("expr")
            if expr:
                prometheus_query = expr
                break
        
        if not prometheus_query:
            logger.debug("告警规则中未找到 Prometheus 查询表达式")
            return None
        
        # 如果配置了 prometheus_url，使用它；否则尝试从 Grafana URL 推断
        if not prometheus_url:
            # 尝试从 Grafana 数据源配置推断 Prometheus URL
            # 这里简化处理，如果没有配置 prometheus_url 就返回 None
            logger.debug("未配置 prometheus_url，无法查询 Prometheus 数据")
            return None
        
        # 使用 Prometheus API 查询数据并生成图片
        parsed_prometheus = urlparse(prometheus_url)
        if not parsed_prometheus.scheme or not parsed_prometheus.netloc:
            logger.warning("配置的 prometheus_url 非法: %s", prometheus_url)
            return None
        prometheus_base = f"{parsed_prometheus.scheme}://{parsed_prometheus.netloc}"
        
        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=max(1, lookback_minutes))
        prometheus_api = f"{prometheus_base}/api/v1/query_range"
        params = {
            "query": prometheus_query,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "step": step,
        }
        
        logger.debug(
            "从 Grafana 告警规则提取查询，请求 Prometheus query_range: api=%s, query=%s",
            prometheus_api,
            prometheus_query[:100] if len(prometheus_query) > 100 else prometheus_query,
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
        
        # 生成图片
        # 创建图表，使用更大的尺寸和更高的DPI以获得更好的质量
        fig, ax = plt.subplots(figsize=(12, 6), dpi=150)
        
        # 设置中文字体支持
        import platform
        if platform.system() == 'Darwin':  # macOS
            plt.rcParams['font.sans-serif'] = ['Arial Unicode MS', 'PingFang SC', 'STHeiti', 'Arial']
        elif platform.system() == 'Linux':
            plt.rcParams['font.sans-serif'] = ['WenQuanYi Micro Hei', 'DejaVu Sans', 'Liberation Sans']
        else:  # Windows
            plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'Arial']
        plt.rcParams['axes.unicode_minus'] = False
        
        plotted = 0
        # 使用更鲜艳、对比度更好的颜色方案
        colors = ['#e74c3c', '#3498db', '#2ecc71', '#f39c12', '#9b59b6', '#1abc9c', '#e67e22', '#34495e']
        # 如果数据系列超过预设颜色数量，使用调色板生成更多颜色
        if len(result) > len(colors):
            colors = plt.cm.Set2(range(len(result)))
        
        for idx, series in enumerate(result):
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
                # 将 UTC 时间转换为 UTC+8
                utc_time = datetime.fromtimestamp(ts, tz=timezone.utc)
                utc8_time = utc_time.astimezone(ZoneInfo("Asia/Shanghai"))
                xs.append(utc8_time)
                ys.append(val)
            if not xs:
                continue
            
            # 确保数据点按时间排序
            sorted_pairs = sorted(zip(xs, ys), key=lambda x: x[0])
            xs, ys = zip(*sorted_pairs) if sorted_pairs else ([], [])
            
            if not xs:
                continue
            
            label = _build_series_label(series.get("metric") or {})
            ax.plot(xs, ys, linewidth=2.5, label=label, color=colors[idx % len(colors)], marker='o', markersize=4, alpha=0.9)
            plotted += 1
        
        if plotted == 0:
            plt.close(fig)
            logger.info("Prometheus query_range 结果无法解析为曲线，跳过出图")
            return None
        
        # 优化图表样式 - 使用实际的 alertname 作为标题（深色主题）
        chart_title = alertname if alertname else "Grafana Alert Trend"
        ax.set_title(chart_title, fontsize=18, fontweight='bold', pad=25, color='#e0e0e0')
        
        # X轴标签显示告警时间（UTC+8，到秒）
        if alert_time:
            try:
                # 解析告警时间并转换为 UTC+8
                from dateutil import parser
                alert_dt = parser.parse(alert_time)
                if alert_dt.tzinfo is None:
                    # 如果没有时区信息，假设是 UTC
                    alert_dt = alert_dt.replace(tzinfo=timezone.utc)
                # 转换为 UTC+8
                alert_dt_utc8 = alert_dt.astimezone(ZoneInfo("Asia/Shanghai"))
                # 格式化为字符串：年-月-日 时:分:秒
                xlabel_text = alert_dt_utc8.strftime('%Y-%m-%d %H:%M:%S')
            except Exception:
                # 如果解析失败，使用默认标签
                xlabel_text = "Time (UTC+8)"
        else:
            xlabel_text = "Time (UTC+8)"
        
        ax.set_xlabel(xlabel_text, fontsize=13, color='#e0e0e0', fontweight='normal')
        
        # Y轴不显示标签，保持简洁
        ax.set_ylabel("", fontsize=0)
        
        # 优化Y轴数值格式 - 使用K格式（类似Grafana）
        def format_y_value(x, p):
            if abs(x) >= 1000:
                return f'{x/1000:.2f} K'.rstrip('0').rstrip('.')
            elif x == int(x):
                return f'{int(x)}'
            else:
                return f'{x:.1f}'
        ax.yaxis.set_major_formatter(plt.FuncFormatter(format_y_value))
        ax.tick_params(axis='y', labelsize=11, colors='#e0e0e0', width=1)
        ax.tick_params(axis='x', labelsize=10, colors='#e0e0e0', width=1)
        
        # 改进网格样式 - 更明显的网格线（深色主题）
        ax.grid(True, linestyle="--", alpha=0.3, linewidth=0.8, color='#666666')
        ax.set_axisbelow(True)
        
        # 设置坐标轴颜色（深色主题）
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.spines['left'].set_color('#666666')
        ax.spines['bottom'].set_color('#666666')
        ax.spines['left'].set_linewidth(1.5)
        ax.spines['bottom'].set_linewidth(1.5)
        
        # 优化图例显示 - 放在右侧（类似Grafana）
        if plotted > 0:
            legend = ax.legend(
                loc="center left",  # 放在右侧
                bbox_to_anchor=(1.02, 0.5),  # 稍微偏移到图表外
                fontsize=11,  # 字体大小
                framealpha=0.9,  # 透明度
                fancybox=True,
                shadow=False,
                edgecolor='#666666',
                facecolor='#2b2b2b',  # 深色背景
                borderpad=0.8,
                labelspacing=0.6,
                handlelength=1.5,
                handletextpad=0.5
            )
            # 设置图例文字颜色（浅色，适合深色背景）
            for text in legend.get_texts():
                text.set_color('#e0e0e0')
                text.set_fontweight('normal')
        
        # 优化时间轴显示 - 只显示时间（时:分:秒），不显示日期
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S', tz=ZoneInfo("Asia/Shanghai")))
        # 根据时间范围自动调整刻度间隔
        if len(xs) > 0:
            time_span = (max(xs) - min(xs)).total_seconds()
            if time_span <= 300:  # 5分钟以内，每30秒一个刻度
                ax.xaxis.set_major_locator(mdates.SecondLocator(interval=30))
            elif time_span <= 900:  # 15分钟以内，每1分钟一个刻度
                ax.xaxis.set_major_locator(mdates.MinuteLocator(interval=1))
            elif time_span <= 3600:  # 1小时以内，每5分钟一个刻度
                ax.xaxis.set_major_locator(mdates.MinuteLocator(interval=5))
            else:  # 超过1小时，每15分钟一个刻度
                ax.xaxis.set_major_locator(mdates.MinuteLocator(interval=15))
        fig.autofmt_xdate(rotation=45)
        
        # 优化背景色 - 使用深色主题（类似Grafana）
        fig.patch.set_facecolor('#1e1e1e')  # 深色背景
        ax.set_facecolor('#2b2b2b')  # 图表区域深灰色
        
        # 确保图表紧凑但不会裁剪内容
        fig.tight_layout(pad=3.0)
        
        buffer = BytesIO()
        fig.savefig(buffer, format="png")
        plt.close(fig)
        return buffer.getvalue()
    except requests.RequestException as exc:
        logger.debug(f"Grafana 出图请求 API 失败: {exc}")
        return None
    except Exception as exc:
        logger.debug(f"Grafana 出图异常: {exc}")
        return None


def _generate_from_grafana_renderer(
    generator_url: str,
    grafana_url: str,
    proxies: Optional[Dict[str, str]] = None,
    timeout_seconds: int = 8,
    alertname: Optional[str] = None,
    alert_time: Optional[str] = None,
) -> Optional[bytes]:
    """使用 Grafana 渲染服务生成图片。"""
    try:
        # 从 generatorURL 提取告警规则 UID
        rule_uid = _extract_alert_rule_uid(generator_url)
        if not rule_uid:
            logger.debug("无法从 generatorURL 提取告警规则 UID，无法使用渲染服务")
            return None
        
        # 解析 Grafana URL
        parsed_grafana = urlparse(grafana_url)
        if not parsed_grafana.scheme or not parsed_grafana.netloc:
            logger.warning("配置的 grafana_url 非法: %s", grafana_url)
            return None
        grafana_base = f"{parsed_grafana.scheme}://{parsed_grafana.netloc}"
        
        # 步骤1：获取告警规则详情，查找关联的 dashboard 和 panel
        api_url = f"{grafana_base}/api/alerting/rule/{rule_uid}"
        logger.debug(f"从 Grafana API 获取告警规则详情: {api_url}")
        response = requests.get(
            api_url,
            timeout=timeout_seconds,
            proxies=proxies,
        )
        
        if response.status_code == 404:
            logger.debug("告警规则不存在或 Grafana 版本不支持该 API")
            return None
        
        response.raise_for_status()
        rule_data = response.json()
        
        # 从告警规则中提取 dashboard 和 panel 信息
        # Grafana 告警规则可能包含 dashboardUid 和 panelId
        dashboard_uid = None
        panel_id = None
        
        # 尝试多种可能的字段名
        rule_spec = rule_data.get("data", {}).get("rule", {}) or rule_data.get("rule", {}) or rule_data
        dashboard_uid = (
            rule_spec.get("dashboardUid") or 
            rule_spec.get("dashboard_uid") or
            rule_spec.get("dashboardUID") or
            None
        )
        panel_id = (
            rule_spec.get("panelId") or
            rule_spec.get("panel_id") or
            rule_spec.get("panelID") or
            None
        )
        
        # 如果规则中没有 dashboard/panel 信息，尝试从查询中查找
        if not dashboard_uid or not panel_id:
            queries = rule_spec.get("data", {}).get("queries", []) or rule_spec.get("queries", [])
            for query in queries:
                if isinstance(query, dict):
                    dashboard_uid = dashboard_uid or query.get("dashboardUid") or query.get("dashboard_uid")
                    panel_id = panel_id or query.get("panelId") or query.get("panel_id")
                    if dashboard_uid and panel_id:
                        break
        
        if not dashboard_uid or not panel_id:
            logger.debug(f"告警规则中未找到 dashboard/panel 信息 (dashboard_uid={dashboard_uid}, panel_id={panel_id})")
            return None
        
        # 步骤2：使用 Grafana 渲染服务生成图片
        # 方法1：使用 /render/d-solo/{dashboard_uid}/{panel_id}
        render_url = f"{grafana_base}/render/d-solo/{dashboard_uid}/{panel_id}"
        
        # 构建查询参数：设置时间范围（最近15分钟）
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(minutes=15)
        
        params = {
            "from": str(int(start_time.timestamp() * 1000)),  # Grafana 使用毫秒时间戳
            "to": str(int(end_time.timestamp() * 1000)),
            "width": 1000,
            "height": 500,
            "theme": "light",
        }
        
        logger.debug(f"使用 Grafana 渲染服务生成图片: {render_url}")
        response = requests.get(
            render_url,
            params=params,
            timeout=timeout_seconds + 5,  # 渲染可能需要更长时间
            proxies=proxies,
        )
        
        if response.status_code == 404:
            logger.debug("Grafana 渲染服务未启用或 dashboard/panel 不存在")
            return None
        
        response.raise_for_status()
        
        # 检查响应内容类型
        content_type = response.headers.get("Content-Type", "")
        if "image" in content_type.lower():
            return response.content
        else:
            logger.debug(f"Grafana 渲染服务返回非图片内容: {content_type}")
            return None
            
    except requests.RequestException as exc:
        logger.debug(f"Grafana 渲染服务请求失败: {exc}")
        return None
    except Exception as exc:
        logger.debug(f"Grafana 渲染服务异常: {exc}")
        return None

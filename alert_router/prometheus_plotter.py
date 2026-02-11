"""
Prometheus 趋势图生成模块

基于 Alertmanager webhook 中的 generatorURL（g0.expr）调用 Prometheus query_range，
生成 PNG 趋势图，供 Telegram sendPhoto 使用。
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
    if not metric:
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
    alertname: Optional[str] = None,
    alert_time: Optional[str] = None,
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
        # 使用 Set2 或 Set3 调色板，颜色更鲜艳
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
            # 使用模运算确保颜色索引不越界
            color = colors[idx % len(colors)]
            ax.plot(xs, ys, linewidth=2.5, label=label, color=color, marker='o', markersize=4, alpha=0.9)
            plotted += 1

        if plotted == 0:
            plt.close(fig)
            logger.info("Prometheus query_range 结果无法解析为曲线，跳过出图")
            return None

        # 优化图表样式 - 使用实际的 alertname 作为标题（深色主题）
        chart_title = alertname if alertname else "Prometheus Alert Trend"
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
        logger.warning("Prometheus 出图请求失败，跳过图片发送: %s", exc)
        return None
    except Exception as exc:
        logger.warning("Prometheus 出图异常，跳过图片发送: %s", exc)
        return None

"""
Prometheus 趋势图生成模块

基于 Alertmanager webhook 中的 generatorURL（g0.expr）调用 Prometheus query_range，
生成 PNG 趋势图，供 Telegram sendPhoto 使用。

支持两种绘图引擎：
1. Plotly（推荐）- 更美观的图表，支持渐变、阴影等现代视觉效果
2. Matplotlib（备选）- 传统绘图库，兼容性好
"""
from __future__ import annotations

import platform
import subprocess
import warnings
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from io import BytesIO
from typing import Dict, List, Optional
from urllib.parse import parse_qs, urlparse

import matplotlib
import matplotlib.dates as mdates
import requests

from ..core.logging_config import get_logger

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager as fm  # noqa: E402

logger = get_logger("alert-router")

# 各平台优先使用的中文字体（按顺序尝试，第一个可用即用）
_CJK_FONT_CANDIDATES: Dict[str, List[str]] = {
    "Darwin": ["PingFang SC", "STHeiti", "Arial Unicode MS", "Heiti SC", "Arial"],
    "Linux": [
        "WenQuanYi Micro Hei",
        "WenQuanYi Zen Hei",
        "Noto Sans CJK SC",
        "Noto Sans SC",
        "Droid Sans Fallback",
        "AR PL UMing CN",
        "DejaVu Sans",
    ],
    "Windows": ["Microsoft YaHei", "SimHei", "DengXian", "Arial"],
}

# 缓存检测到的中文字体名，供 matplotlib 与 Plotly 共用
_cjk_font_family_cache: Optional[str] = None


def _get_cjk_font_family() -> Optional[str]:
    """返回当前系统可用的中文字体 family 名称（供 matplotlib / Plotly 使用）。"""
    global _cjk_font_family_cache
    # 已计算过：None=未计算，''=无可用字体，非空=字体名
    if _cjk_font_family_cache is not None:
        return _cjk_font_family_cache if _cjk_font_family_cache else None
    system = platform.system()
    candidates: List[str] = list(_CJK_FONT_CANDIDATES.get(system, _CJK_FONT_CANDIDATES["Linux"]))
    # Linux 下用 fc-list 发现系统已安装的中文语言字体，优先使用
    if system == "Linux":
        try:
            out = subprocess.run(
                ["fc-list", "-f", "%{family}\n", ":lang=zh"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if out.returncode == 0 and out.stdout:
                for line in out.stdout.strip().splitlines():
                    name = line.strip().split(",")[0].strip()  # 取第一个 family
                    if name and name not in candidates:
                        candidates.insert(0, name)
        except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
            pass
    chosen: Optional[str] = None
    for name in candidates:
        if not name or name == "DejaVu Sans":
            continue
        try:
            path = fm.findfont(fm.FontProperties(family=name), fallback_to_default=False)
            if path and "DejaVu" not in path:
                chosen = name
                break
        except Exception:
            continue

    # Linux：matplotlib 字体缓存可能未包含新安装的字体，用 fc-list 取路径后按文件加载
    if not chosen and system == "Linux":
        try:
            out = subprocess.run(
                ["fc-list", "-f", "%{file}\n", ":lang=zh"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if out.returncode == 0 and out.stdout:
                paths = [p.strip() for p in out.stdout.strip().splitlines() if p.strip()]
                font_manager = getattr(fm, "fontManager", None)
                if font_manager is not None and hasattr(font_manager, "addfont"):
                    for font_path in paths:
                        if "wqy" in font_path.lower() or "noto" in font_path.lower() or "cjk" in font_path.lower():
                            try:
                                font_manager.addfont(font_path)
                                for f in font_manager.ttflist:
                                    if getattr(f, "fname", None) == font_path:
                                        chosen = getattr(f, "name", None) or ""
                                        if chosen:
                                            break
                                if chosen:
                                    break
                            except Exception:
                                continue
        except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
            pass

    if chosen:
        _cjk_font_family_cache = chosen
        return chosen
    _cjk_font_family_cache = ""  # 标记已计算且无可用字体
    if not getattr(_get_cjk_font_family, "_warned", False):
        _get_cjk_font_family._warned = True
        logger.warning(
            "未检测到中文字体，趋势图中文可能显示为方框。"
            "Linux 可安装: apt-get install fonts-wqy-microhei 或 fonts-noto-cjk；"
            "若已安装仍报错可尝试: rm -rf ~/.cache/matplotlib 后重启进程"
        )
    return None


def _setup_matplotlib_cjk_font() -> None:
    """设置 matplotlib 使用支持中文的字体，避免 'Glyph missing from font' 警告与方框。"""
    chosen = _get_cjk_font_family()
    if chosen:
        plt.rcParams["font.sans-serif"] = [chosen]
    else:
        system = platform.system()
        candidates = _CJK_FONT_CANDIDATES.get(system, _CJK_FONT_CANDIDATES["Linux"])
        plt.rcParams["font.sans-serif"] = candidates
    plt.rcParams["axes.unicode_minus"] = False

# 尝试导入 Plotly（可选，如果未安装则使用 matplotlib）
try:
    import plotly.graph_objects as go
    import plotly.io as pio
    PLOTLY_AVAILABLE = True
except ImportError:
    PLOTLY_AVAILABLE = False
    logger.info("Plotly 未安装，将使用 matplotlib 生成图表。要使用更美观的图表，请安装: pip install plotly kaleido")


# 告警里仅用于路由/展示、通常不出现在 metric 里的标签，过滤曲线时不参与匹配
_ALERT_ONLY_LABELS = {"alertname", "severity", "cluster", "_source", "_receiver"}

# 图例标签白名单：仅在此列表中的 label 会显示在图例中，便于统一控制（可在 config 中覆盖）
# 与 config 默认一致，含 nginx status、server_name 等，避免未加载 config 时图例缺项
DEFAULT_LEGEND_LABEL_WHITELIST = (
    "pod", "container", "device", "mountpoint", "fstype", "instance", "node",
    "topic", "consumergroup", "name", "address",
    "group", "broker", "brokerIP", "cluster", "env",
    "service_name", "endpoint", "application",
    "jenkins_job", "build_number",
    "server_name", "status", "uri", "request_uri", "remote_addr", "url",
    "namespace", "alertmanager", "remote_name", "controller", "resource",
    "service", "kubernetes_namespace",
)


def _filter_result_by_alert_labels(
    result: List[dict],
    alert_labels: Optional[Dict[str, str]],
) -> List[dict]:
    """
    按告警 labels 过滤 query_range 返回的曲线，只保留与当前告警目标一致的 series。
    例如告警是 device=/dev/sdb1, mountpoint=/data，则图里只画该磁盘的曲线，而不是所有 tmpfs 等。
    """
    if not alert_labels or not result:
        return result
    # 只拿会出现在 metric 里的标签做匹配（值为字符串；合并告警时可能为列表则跳过）
    match_labels = {
        k: v for k, v in alert_labels.items()
        if k not in _ALERT_ONLY_LABELS and v and isinstance(v, str)
    }
    if not match_labels:
        return result
    filtered = [
        s for s in result
        if isinstance(s.get("metric"), dict)
        and all(s["metric"].get(k) == v for k, v in match_labels.items())
    ]
    if filtered:
        logger.debug(
            "按告警 labels 过滤曲线: 共 %s 条 -> 匹配 %s 条 (labels: %s)",
            len(result), len(filtered), list(match_labels.keys()),
        )
        return filtered
    return result


def _build_series_label(
    metric: Dict[str, str],
    legend_label_whitelist: Optional[List[str]] = None,
) -> str:
    """
    从 Prometheus metric 标签构造曲线名称。
    仅显示白名单中的标签；白名单为空或未传时使用默认白名单（便于统一控制）。
    """
    if not metric:
        return "series"
    whitelist = legend_label_whitelist or list(DEFAULT_LEGEND_LABEL_WHITELIST)
    allow = set(whitelist) if whitelist else set()
    pairs = [f"{k}={metric[k]}" for k in sorted(metric.keys()) if k in allow and k != "__name__"]
    if pairs:
        label = ", ".join(pairs)
    else:
        # 白名单未命中时兜底：显示所有非 __name__ 的标签（避免漏掉 status 等）
        fallback = [f"{k}={v}" for k, v in sorted(metric.items()) if k != "__name__"]
        label = ", ".join(fallback) if fallback else metric.get("__name__", "series")
    if len(label) > 90:
        return label[:87] + "..."
    return label


def _generate_plot_with_plotly(
    result: list,
    alertname: Optional[str] = None,
    alert_time: Optional[str] = None,
    legend_label_whitelist: Optional[List[str]] = None,
) -> Optional[bytes]:
    """
    使用 Plotly 生成美观的图表（推荐）
    
    优势：
    - 更现代的视觉效果（渐变、阴影、平滑曲线）
    - 更好的颜色方案和样式
    - 更清晰的图例和标签
    """
    if not PLOTLY_AVAILABLE:
        return None
    
    try:
        fig = go.Figure()
        
        # 使用更美观的颜色方案（现代渐变色）
        colors = [
            '#FF6B6B',  # 珊瑚红
            '#4ECDC4',  # 青绿色
            '#45B7D1',  # 天蓝色
            '#FFA07A',  # 浅橙红
            '#98D8C8',  # 薄荷绿
            '#F7DC6F',  # 金黄色
            '#BB8FCE',  # 淡紫色
            '#85C1E2',  # 浅蓝色
        ]
        
        plotted = 0
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
            
            label = _build_series_label(
                series.get("metric") or {},
                legend_label_whitelist=legend_label_whitelist,
            )
            color = colors[idx % len(colors)]
            
            # 将十六进制颜色转换为 rgba 格式（用于填充）
            def hex_to_rgba(hex_color, alpha=0.2):
                hex_color = hex_color.lstrip('#')
                r = int(hex_color[0:2], 16)
                g = int(hex_color[2:4], 16)
                b = int(hex_color[4:6], 16)
                return f'rgba({r}, {g}, {b}, {alpha})'
            
            # 添加填充区域（渐变效果）
            fig.add_trace(go.Scatter(
                x=list(xs),
                y=list(ys),
                mode='lines+markers',
                name=label,
                line=dict(
                    color=color,
                    width=3,
                    shape='spline',  # 平滑曲线
                ),
                marker=dict(
                    size=6,
                    color=color,
                    line=dict(width=1, color='white'),
                ),
                fill='tonexty' if idx > 0 else 'tozeroy',
                fillcolor=hex_to_rgba(color, 0.2),
                hovertemplate=f'<b>{label}</b><br>时间: %{{x}}<br>值: %{{y}}<extra></extra>',
            ))
            plotted += 1
        
        if plotted == 0:
            return None
        
        # 设置标题
        chart_title = alertname if alertname else "Prometheus Alert Trend"
        
        # X轴标签
        if alert_time:
            try:
                from dateutil import parser
                alert_dt = parser.parse(alert_time)
                if alert_dt.tzinfo is None:
                    alert_dt = alert_dt.replace(tzinfo=timezone.utc)
                alert_dt_utc8 = alert_dt.astimezone(ZoneInfo("Asia/Shanghai"))
                xlabel_text = alert_dt_utc8.strftime('%Y-%m-%d %H:%M:%S')
            except Exception:
                xlabel_text = "Time (UTC+8)"
        else:
            xlabel_text = "Time (UTC+8)"
        
        # 使用检测到的中文字体，避免标题/图例中文显示为方框
        plot_font_family = _get_cjk_font_family() or "Arial, sans-serif"
        # 更新布局 - 使用深色主题，更现代
        fig.update_layout(
            title=dict(
                text=chart_title,
                font=dict(size=24, color='#ffffff', family=plot_font_family),
                x=0.5,
                xanchor='center',
            ),
            xaxis=dict(
                title=dict(text=xlabel_text, font=dict(size=14, color='#ffffff')),
                tickfont=dict(size=11, color='#ffffff', family=plot_font_family),
                gridcolor='rgba(255, 255, 255, 0.2)',
                gridwidth=1,
                showgrid=True,
                zeroline=False,
            ),
            yaxis=dict(
                title="",
                tickfont=dict(size=12, color='#ffffff', family=plot_font_family),
                gridcolor='rgba(255, 255, 255, 0.2)',
                gridwidth=1,
                showgrid=True,
                zeroline=False,
            ),
            plot_bgcolor='#0a0a0f',
            paper_bgcolor='#0a0a0f',
            font=dict(family=plot_font_family),
            legend=dict(
                bgcolor='rgba(26, 26, 46, 0.95)',
                bordercolor='rgba(255, 255, 255, 0.3)',
                borderwidth=1,
                font=dict(size=12, color='#ffffff', family=plot_font_family),
                x=1.02,
                y=0.5,
                xanchor='left',
                yanchor='middle',
            ),
            margin=dict(l=60, r=200, t=80, b=60),
            width=1400,
            height=700,
            hovermode='x unified',
        )
        
        # 导出为 PNG
        buffer = BytesIO()
        try:
            # 使用 write_image 方法（更可靠）
            import tempfile
            import os
            with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp_file:
                tmp_path = tmp_file.name
            try:
                fig.write_image(tmp_path, width=1400, height=700, scale=2)
                with open(tmp_path, 'rb') as f:
                    img_bytes = f.read()
                buffer.write(img_bytes)
                os.unlink(tmp_path)  # 删除临时文件
            except Exception as e:
                # 如果 write_image 失败，尝试 to_image
                logger.debug(f"write_image 失败，尝试 to_image: {e}")
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                img_bytes = pio.to_image(fig, format='png', width=1400, height=700, scale=2)
                buffer.write(img_bytes)
        except Exception as e:
            logger.warning(f"Plotly 图片导出失败: {e}")
            raise
        return buffer.getvalue()
        
    except Exception as exc:
        logger.warning("Plotly 出图异常，回退到 matplotlib: %s", exc)
        return None


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
    use_plotly: bool = True,  # 默认使用 Plotly
    alert_labels: Optional[Dict[str, str]] = None,  # 告警 labels，用于只画与当前告警匹配的曲线
    legend_label_whitelist: Optional[List[str]] = None,  # 图例中只显示这些 label，不配置则用默认白名单
) -> Optional[bytes]:
    """
    根据 generatorURL 生成 Prometheus 趋势图。

    若提供 alert_labels，会先按 instance/device/mountpoint 等过滤曲线，使图与告警文案一致。
    legend_label_whitelist 控制图例中显示哪些标签（白名单），便于统一控制。

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

        # 时间范围：有 alert_time 时以告警时刻为基准，保证图里包含告警时段的数据；否则用当前时间往前推
        now_utc = datetime.now(timezone.utc)
        lb = max(1, lookback_minutes)
        if alert_time:
            try:
                from dateutil import parser as dateutil_parser
                alert_dt = dateutil_parser.parse(alert_time)
                if alert_dt.tzinfo is None:
                    alert_dt = alert_dt.replace(tzinfo=timezone.utc)
                # 以告警时刻为 end，往前推 lookback，这样趋势图能覆盖告警前后
                end = alert_dt + timedelta(minutes=2)
                if end > now_utc:
                    end = now_utc
                start = end - timedelta(minutes=lb)
            except Exception:
                end = now_utc
                start = end - timedelta(minutes=lb)
        else:
            end = now_utc
            start = end - timedelta(minutes=lb)
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

        # 按告警 labels 过滤，只画与当前告警目标一致的曲线（如图只显示 /dev/sdb1 /data 而非全部 tmpfs）
        result = _filter_result_by_alert_labels(result, alert_labels)
        if result and len(result) > max_series:
            result = result[:max_series]

        # 优先使用 Plotly 生成更美观的图表
        if use_plotly and PLOTLY_AVAILABLE:
            plotly_result = _generate_plot_with_plotly(
                result, alertname, alert_time,
                legend_label_whitelist=legend_label_whitelist,
            )
            if plotly_result:
                return plotly_result
            logger.info("Plotly 出图失败，回退到 matplotlib")

        # 使用 Matplotlib 生成图表（备选方案）
        _setup_matplotlib_cjk_font()
        # 无 CJK 字体时抑制 "Glyph xxx missing from font" 刷屏（savefig 时触发）
        warnings.filterwarnings(
            "ignore",
            message=".*Glyph.*missing from font",
            category=UserWarning,
            module="matplotlib",
        )
        # 创建图表 - 使用更大的尺寸
        fig, ax = plt.subplots(figsize=(14, 7), dpi=150)
        
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
            
            label = _build_series_label(
                series.get("metric") or {},
                legend_label_whitelist=legend_label_whitelist,
            )
            # 使用模运算确保颜色索引不越界
            color = colors[idx % len(colors)]
            # 绘制数据线 - 更粗更明显
            ax.plot(xs, ys, linewidth=3.0, label=label, color=color, marker='o', markersize=4, alpha=0.95, zorder=5-idx)
            plotted += 1

        if plotted == 0:
            plt.close(fig)
            logger.info("Prometheus query_range 结果无法解析为曲线，跳过出图")
            return None

        # 优化图表样式 - 使用实际的 alertname 作为标题
        chart_title = alertname if alertname else "Prometheus Alert Trend"
        ax.set_title(chart_title, fontsize=20, fontweight='bold', pad=30, color='#ffffff')
        
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
        
        ax.set_xlabel(xlabel_text, fontsize=14, color='#ffffff', fontweight='normal')
        
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
        ax.tick_params(axis='y', labelsize=12, colors='#ffffff', width=1)
        ax.tick_params(axis='x', labelsize=11, colors='#ffffff', width=1)
        
        # 改进网格样式 - 更明显
        ax.grid(True, linestyle="--", alpha=0.4, linewidth=1.0, color='#ffffff')
        ax.set_axisbelow(True)
        
        # 设置坐标轴颜色
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.spines['left'].set_color('#ffffff')
        ax.spines['bottom'].set_color('#ffffff')
        ax.spines['left'].set_linewidth(2)
        ax.spines['bottom'].set_linewidth(2)
        
        # 优化图例显示 - 放在右侧（单条曲线也显示，便于看到 status=500 等）
        legend_obj = None
        if plotted > 0:
            legend_obj = ax.legend(
                loc="center left",
                bbox_to_anchor=(1.02, 0.5),
                fontsize=12,
                framealpha=0.95,
                fancybox=True,
                shadow=False,
                edgecolor='#ffffff',
                facecolor='#1a1a2e',
                borderpad=1.0,
                labelspacing=0.8,
                handlelength=2.0,
                handletextpad=0.8,
            )
            for text in legend_obj.get_texts():
                text.set_color('#ffffff')
                text.set_fontweight('normal')

        # 确保图例在 savefig(bbox_inches='tight') 时不被裁掉
        extra_artists = [legend_obj] if legend_obj is not None else []

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
        
        # 优化背景 - 使用现代渐变背景，更有视觉冲击力
        # 使用深色到浅色的渐变背景
        fig.patch.set_facecolor('#0a0a0f')  # 深黑色背景
        
        # 图表区域使用深色渐变背景
        ax.set_facecolor('#151520')  # 深灰蓝色
        
        # 创建垂直渐变效果（从下到上）
        import numpy as np
        y_min, y_max = ax.get_ylim()
        x_min, x_max = ax.get_xlim()
        
        # 创建渐变网格
        y_vals = np.linspace(y_min, y_max, 100)
        x_vals = np.linspace(x_min, x_max, 100)
        X, Y = np.meshgrid(x_vals, y_vals)
        
        # 创建渐变（从深到浅）
        Z = np.linspace(0, 1, len(y_vals)).reshape(-1, 1)
        Z = np.tile(Z, (1, len(x_vals)))
        
        # 使用自定义颜色映射（深蓝到浅蓝）
        from matplotlib.colors import LinearSegmentedColormap
        colors_gradient = ['#0a0a0f', '#1a1a2e', '#2a2a3e']
        n_bins = 256
        cmap = LinearSegmentedColormap.from_list('custom', colors_gradient, N=n_bins)
        
        ax.imshow(Z, extent=[x_min, x_max, y_min, y_max], 
                  aspect='auto', cmap=cmap, alpha=0.3, zorder=0, origin='lower')
        
        # 预留右侧约 22% 给图例，避免 tight_layout 把图例挤出画布导致不显示
        fig.tight_layout(pad=3.5, rect=[0, 0, 0.78, 1])

        buffer = BytesIO()
        fig.savefig(
            buffer, format='png', dpi=150, facecolor='#0a0a0f', edgecolor='none',
            bbox_inches='tight', bbox_extra_artists=extra_artists,
        )
        plt.close(fig)
        return buffer.getvalue()
    except requests.RequestException as exc:
        logger.warning("Prometheus 出图请求失败，跳过图片发送: %s", exc)
        return None
    except Exception as exc:
        logger.warning("Prometheus 出图异常，跳过图片发送: %s", exc)
        return None

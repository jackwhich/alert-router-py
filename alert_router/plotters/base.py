"""
公共绘图工具模块

prometheus_plotter / grafana_plotter 共用：字体、序列解析、matplotlib 出图。
"""
from __future__ import annotations

import platform
import subprocess
from datetime import datetime, timezone
from io import BytesIO
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import matplotlib
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib import font_manager as fm

from ..core.logging_config import get_logger

matplotlib.use("Agg")

logger = get_logger("alert-router")

# 尝试导入 Plotly（可选）
try:
    import plotly.graph_objects as go
    import plotly.io as pio
    PLOTLY_AVAILABLE = True
except ImportError:
    PLOTLY_AVAILABLE = False
    go = None
    pio = None

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

_cjk_font_family_cache: Optional[str] = None


def get_cjk_font_family() -> Optional[str]:
    """返回当前系统可用的中文字体 family 名称（供 matplotlib / Plotly 使用）。"""
    global _cjk_font_family_cache
    if _cjk_font_family_cache is not None:
        return _cjk_font_family_cache if _cjk_font_family_cache else None
    system = platform.system()
    candidates: List[str] = list(_CJK_FONT_CANDIDATES.get(system, _CJK_FONT_CANDIDATES["Linux"]))
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
                    name = line.strip().split(",")[0].strip()
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
    _cjk_font_family_cache = ""
    if not getattr(get_cjk_font_family, "_warned", False):
        get_cjk_font_family._warned = True
        logger.warning(
            "未检测到中文字体，趋势图中文可能显示为方框。"
            "Linux 可安装: apt-get install fonts-wqy-microhei 或 fonts-noto-cjk；"
            "若已安装仍报错可尝试: rm -rf ~/.cache/matplotlib 后重启进程"
        )
    return None


def setup_matplotlib_cjk_font() -> None:
    """设置 matplotlib 使用支持中文的字体。"""
    chosen = get_cjk_font_family()
    if chosen:
        plt.rcParams["font.sans-serif"] = [chosen]
    else:
        system = platform.system()
        candidates = _CJK_FONT_CANDIDATES.get(system, _CJK_FONT_CANDIDATES["Linux"])
        plt.rcParams["font.sans-serif"] = candidates
    plt.rcParams["axes.unicode_minus"] = False


def setup_chinese_fonts():
    """兼容旧名：设置中文字体支持。"""
    setup_matplotlib_cjk_font()


def build_series_label(
    metric: Dict[str, str],
    legend_label_whitelist: Optional[List[str]] = None,
) -> str:
    """
    从 Prometheus metric 标签构造曲线名称。
    传入 whitelist 时仅显示白名单标签；未传则排除内部标签。
    """
    if not metric:
        return "series"
    if legend_label_whitelist:
        allow = set(legend_label_whitelist)
        pairs = [f"{k}={metric[k]}" for k in sorted(metric.keys()) if k in allow and k != "__name__"]
        if pairs:
            label = ", ".join(pairs)
        else:
            fallback = [f"{k}={v}" for k, v in sorted(metric.items()) if k != "__name__"]
            label = ", ".join(fallback) if fallback else metric.get("__name__", "series")
    else:
        exclude_keys = {"__name__", "replica", "prometheus", "job", "instance", "namespace"}
        pairs = []
        for k in sorted(metric.keys()):
            if k in exclude_keys:
                continue
            pairs.append(f"{k}={metric[k]}")
        label = ", ".join(pairs) if pairs else metric.get("__name__", "series")
    if len(label) > 90:
        return label[:87] + "..."
    return label


def legend_line_with_alert_value(label: str, ys: List[float]) -> str:
    """图例仅展示当前告警值，避免过宽挤压绘图区。"""
    if not ys:
        return label
    return f"{label}\n告警值 {ys[-1]:.1f}"


def parse_time_series_data(result: List[Dict]) -> List[Tuple[List[datetime], List[float], Dict]]:
    """解析 Prometheus query_range 结果为 (时间列表, 值列表, metric)。"""
    parsed_data = []

    for series in result:
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
            utc_time = datetime.fromtimestamp(ts, tz=timezone.utc)
            utc8_time = utc_time.astimezone(ZoneInfo("Asia/Shanghai"))
            xs.append(utc8_time)
            ys.append(val)

        if not xs:
            continue

        sorted_pairs = sorted(zip(xs, ys), key=lambda x: x[0])
        xs, ys = zip(*sorted_pairs) if sorted_pairs else ([], [])
        if not xs:
            continue

        parsed_data.append((list(xs), list(ys), series.get("metric") or {}))

    return parsed_data


def format_alert_time(alert_time: Optional[str]) -> str:
    """格式化告警时间为 UTC+8 时间字符串。"""
    if not alert_time:
        return "Time (UTC+8)"

    try:
        from dateutil import parser
        alert_dt = parser.parse(alert_time)
        if alert_dt.tzinfo is None:
            alert_dt = alert_dt.replace(tzinfo=timezone.utc)
        alert_dt_utc8 = alert_dt.astimezone(ZoneInfo("Asia/Shanghai"))
        return alert_dt_utc8.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "Time (UTC+8)"


def format_y_value(x: float, p: int) -> str:
    """格式化 Y 轴数值（K 格式）。"""
    if abs(x) >= 1000:
        return f"{x/1000:.2f} K".rstrip("0").rstrip(".")
    if x == int(x):
        return f"{int(x)}"
    return f"{x:.1f}"


def get_color_palette(count: int) -> List:
    base_colors = ["#e74c3c", "#3498db", "#2ecc71", "#f39c12", "#9b59b6", "#1abc9c", "#e67e22", "#34495e"]
    if count <= len(base_colors):
        return base_colors[:count]
    return plt.cm.Set2(range(count))


def _configure_time_axis(ax, xs: List[datetime]) -> None:
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S", tz=ZoneInfo("Asia/Shanghai")))
    if not xs:
        return
    time_span = (max(xs) - min(xs)).total_seconds()
    if time_span <= 300:
        ax.xaxis.set_major_locator(mdates.SecondLocator(interval=30))
    elif time_span <= 900:
        ax.xaxis.set_major_locator(mdates.MinuteLocator(interval=1))
    elif time_span <= 3600:
        ax.xaxis.set_major_locator(mdates.MinuteLocator(interval=5))
    else:
        ax.xaxis.set_major_locator(mdates.MinuteLocator(interval=15))
    ax.get_figure().autofmt_xdate(rotation=45)


def apply_dark_theme(fig, ax):
    """应用深色主题样式。"""
    fig.patch.set_facecolor("#0a0a0f")
    ax.set_facecolor("#151520")
    try:
        import numpy as np
        from matplotlib.colors import LinearSegmentedColormap

        y_min, y_max = ax.get_ylim()
        x_min, x_max = ax.get_xlim()
        y_vals = np.linspace(y_min, y_max, 100)
        x_vals = np.linspace(x_min, x_max, 100)
        Z = np.linspace(0, 1, len(y_vals)).reshape(-1, 1)
        Z = np.tile(Z, (1, len(x_vals)))
        cmap = LinearSegmentedColormap.from_list("custom", ["#0a0a0f", "#1a1a2e", "#2a2a3e"], N=256)
        ax.imshow(
            Z,
            extent=[x_min, x_max, y_min, y_max],
            aspect="auto",
            cmap=cmap,
            alpha=0.3,
            zorder=0,
            origin="lower",
        )
    except ImportError:
        pass


def render_matplotlib_png(
    result: list,
    *,
    alertname: Optional[str] = None,
    alert_time: Optional[str] = None,
    legend_label_whitelist: Optional[List[str]] = None,
    default_title: str = "Alert Trend",
    legend_with_alert_value: bool = False,
    percent_ylabel: bool = False,
) -> Optional[bytes]:
    """从 query_range result 生成 PNG。prometheus / grafana 出图共用此入口。"""
    setup_matplotlib_cjk_font()
    parsed = parse_time_series_data(result)
    if not parsed:
        return None

    fig, ax = plt.subplots(figsize=(14, 7), dpi=150)
    colors = get_color_palette(len(parsed))
    time_axis_xs: List[datetime] = []
    plotted = 0

    for idx, (xs, ys, metric) in enumerate(parsed):
        label = build_series_label(metric, legend_label_whitelist=legend_label_whitelist)
        if not label or not str(label).strip():
            label = metric.get("__name__", f"series_{idx}")
        legend_label = legend_line_with_alert_value(label, ys) if legend_with_alert_value else label
        color = colors[idx % len(colors)]
        ax.plot(
            xs,
            ys,
            linewidth=3.0,
            label=legend_label,
            color=color,
            marker="o",
            markersize=4,
            alpha=0.95,
            zorder=5 - idx,
        )
        time_axis_xs.extend(xs)
        plotted += 1

    if plotted == 0:
        plt.close(fig)
        return None

    chart_title = alertname if alertname else default_title
    fig.suptitle(chart_title, fontsize=20, fontweight="bold", color="#ffffff", y=0.98, x=0.5, ha="center")
    ax.set_xlabel(format_alert_time(alert_time), fontsize=14, color="#ffffff", fontweight="normal")
    ax.set_ylabel("使用率 (%)" if percent_ylabel else "", fontsize=12 if percent_ylabel else 0, color="#ffffff")

    if percent_ylabel:
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f"{x:.1f}%"))
    else:
        ax.yaxis.set_major_formatter(plt.FuncFormatter(format_y_value))
    ax.tick_params(axis="y", labelsize=12, colors="#ffffff", width=1)
    ax.tick_params(axis="x", labelsize=11, colors="#ffffff", width=1)
    ax.grid(True, linestyle="--", alpha=0.4, linewidth=1.0, color="#ffffff")
    ax.set_axisbelow(True)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#ffffff")
    ax.spines["bottom"].set_color("#ffffff")
    ax.spines["left"].set_linewidth(2)
    ax.spines["bottom"].set_linewidth(2)

    legend_obj = ax.legend(
        loc="upper left",
        bbox_to_anchor=(1.02, 1.0),
        fontsize=10,
        frameon=False,
        labelspacing=0.8,
        handlelength=1.5,
        handletextpad=0.5,
    )
    for text in legend_obj.get_texts():
        text.set_color("#ffffff")
        text.set_fontweight("normal")

    _configure_time_axis(ax, time_axis_xs)
    apply_dark_theme(fig, ax)
    fig.subplots_adjust(left=0.08, right=0.80, top=0.90, bottom=0.15)

    buffer = BytesIO()
    fig.savefig(
        buffer,
        format="png",
        dpi=150,
        facecolor="#0a0a0f",
        edgecolor="none",
        bbox_inches="tight",
        bbox_extra_artists=[legend_obj],
    )
    plt.close(fig)
    return buffer.getvalue()

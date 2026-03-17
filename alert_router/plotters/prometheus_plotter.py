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
import re
import subprocess
import warnings
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from io import BytesIO
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, unquote, urlencode, urlparse

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


def _normalize_query_for_plot(expr: str) -> str:
    """
    将告警表达式转换为更适合出图的查询表达式。

    典型告警规则会写成 `metric_expr > 30` 或 `metric_expr >= 0.8`，
    这种表达式在 query_range 下会返回 0/1 布尔值（满足阈值为 1），图上会显示 0-1 而非真实指标（如 727）。
    因此剥离末尾的标量比较条件，直接绘制原始 metric_expr，Y 轴才显示真实计数值。
    """
    if not expr:
        return expr
    normalized = expr.strip()
    # 从字符串末尾匹配：可选的空白 + 比较符 + 可选的 bool + 数字 + 结尾空白
    # 用后缀匹配避免 base 内含有 =、> 等字符（如 status=~"4.*.*"）时误匹配
    suffix_pattern = re.compile(
        r"\s*(?:>=|<=|==|!=|>|<)\s*(?:bool\s+)?(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*$"
    )
    match = suffix_pattern.search(normalized)
    if match:
        base_expr = normalized[: match.start()].strip()
        if base_expr:
            return base_expr
    return normalized


def _full_decode_expr(raw: str) -> str:
    """对 expr 做完整 URL 解码（与 Go url.Query().Get 行为一致，支持多重编码）。"""
    if not raw:
        return raw
    s = raw.strip()
    while True:
        decoded = unquote(s)
        if decoded == s:
            break
        s = decoded
    return s


def _parse_expr_from_generator_url(generator_url: str) -> Optional[str]:
    """从 generatorURL 取 g0.expr 并完整 decode。"""
    if not generator_url:
        return None
    q = parse_qs(urlparse(generator_url).query)
    raw = (q.get("g0.expr") or [None])[0]
    if raw is None:
        return None
    expr = _full_decode_expr(raw if isinstance(raw, str) else str(raw))
    return (expr.strip() or None) if expr else None


def _is_datasource_victoriametrics(generator_url: str) -> bool:
    """根据 generatorURL 判断是否来自 VictoriaMetrics（vmalert / vmselect / 带 /select/ 的 VM 集群）。"""
    if not generator_url:
        return False
    url_lower = generator_url.lower()
    return (
        "victoriametrics" in url_lower
        or "vmselect" in url_lower
        or "vmalert" in url_lower
        or "/select/" in generator_url
    )


def _alert_labels_all_scalar(alert_labels: Optional[Dict[str, Any]]) -> bool:
    """用于注入/收窄的 label 是否全为标量（无列表），合并告警时为 False。"""
    if not alert_labels:
        return True
    for k, v in alert_labels.items():
        if k in _ALERT_ONLY_LABELS or v is None or v == "":
            continue
        if isinstance(v, list):
            return False
    return True


def _inject_alert_labels_into_expr(
    expr: str,
    alert_labels: Optional[Dict[str, Any]],
) -> str:
    """
    把告警的 label 条件注入到查询表达式里，让 VM/Prometheus API 只返回「当前告警」对应的 series，
    而不是该查询下的全部 series（避免「把当前时间所有的都罗列出来」）。
    只注入 selector 里尚未存在的 label；仅标量值会注入（合并告警的列表值不注入）。
    """
    if not expr or not alert_labels:
        return expr
    match_labels = {
        k: v for k, v in alert_labels.items()
        if k not in _ALERT_ONLY_LABELS and v and isinstance(v, str)
    }
    if not match_labels:
        return expr
    # 找第一个 selector { ... } 的起止位置（按括号匹配，忽略字符串内的花括号较复杂，先按简单匹配）
    start = expr.find("{")
    if start == -1:
        return expr
    depth = 1
    i = start + 1
    while i < len(expr) and depth > 0:
        if expr[i] == "{":
            depth += 1
        elif expr[i] == "}":
            depth -= 1
        i += 1
    if depth != 0:
        return expr
    end = i - 1
    selector_inner = expr[start + 1 : end]
    # 解析已有 label 名（key=value 或 key=~"regex"）
    existing_keys = set(re.findall(r"(\w+)\s*[=~]", selector_inner))
    to_add = {k: v for k, v in match_labels.items() if k not in existing_keys}
    if not to_add:
        return expr
    # Prometheus 里 label 值需转义 \ 和 "
    def escape_val(v: str) -> str:
        return v.replace("\\", "\\\\").replace('"', '\\"')
    extra = "," + ",".join(f'{k}="{escape_val(v)}"' for k, v in sorted(to_add.items()))
    new_expr = expr[:end] + extra + expr[end:]
    logger.info(
        "已按告警 label 收窄查询，VM 只返回当前告警的 series：注入 %s",
        list(to_add.keys()),
    )
    return new_expr


def _filter_result_by_alert_labels(
    result: List[dict],
    alert_labels: Optional[Dict[str, Any]],
) -> List[dict]:
    """
    按告警 labels 过滤 query_range 返回的曲线，只保留与当前告警目标一致的 series。
    - 单条告警：label 值为标量，保留 metric 完全匹配的 series。
    - 合并告警：label 值可为列表（如 server_name: ["a","b"], status: ["403","404"]），
      保留 metric 的每个 key 在对应列表或等于标量的 series（一张图多条曲线，每条对应一实体）。
    """
    if not alert_labels or not result:
        return result
    # 只拿会出现在 metric 里的标签做匹配；排除仅用于路由的标签
    match_labels: Dict[str, Any] = {
        k: v for k, v in alert_labels.items()
        if k not in _ALERT_ONLY_LABELS and v is not None and v != ""
    }
    if not match_labels:
        return result

    def _series_matches(metric: dict) -> bool:
        for k, v in match_labels.items():
            mv = metric.get(k)
            if isinstance(v, list):
                if not v:
                    continue  # 空列表不参与匹配
                if mv not in v:
                    return False
            else:
                if mv != v:
                    return False
        return True

    filtered = [
        s for s in result
        if isinstance(s.get("metric"), dict) and _series_matches(s["metric"])
    ]
    if filtered:
        logger.debug(
            "按告警 labels 过滤曲线: 共 %s 条 -> 匹配 %s 条 (labels: %s)",
            len(result), len(filtered), list(match_labels.keys()),
        )
        return filtered
    return result


def _legend_line_with_alert_value(label: str, ys: List[float]) -> str:
    """图例仅展示当前告警值，避免过宽挤压绘图区。"""
    if not ys:
        return label
    alert_value = ys[-1]
    return f"{label}\n告警值 {alert_value:.1f}"


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
        legend_labels: List[str] = []
        all_timestamps: List[datetime] = []  # 用于红线/阴影和 x 范围
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
            legend_label = _legend_line_with_alert_value(label, list(ys))
            color = colors[idx % len(colors)]
            
            # 将十六进制颜色转换为 rgba 格式（用于填充）
            def hex_to_rgba(hex_color, alpha=0.2):
                hex_color = hex_color.lstrip('#')
                r = int(hex_color[0:2], 16)
                g = int(hex_color[2:4], 16)
                b = int(hex_color[4:6], 16)
                return f'rgba({r}, {g}, {b}, {alpha})'
            
            # 添加填充区域（渐变效果），图例含均值/最大/最小
            fig.add_trace(go.Scatter(
                x=list(xs),
                y=list(ys),
                mode='lines+markers',
                name=legend_label,
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
            legend_labels.append(legend_label)
            all_timestamps.extend(xs)
            plotted += 1
        
        if plotted == 0:
            return None
        
        # 设置标题与 Y 轴单位（借鉴参考图：使用率类注明 %）
        chart_title = alertname if alertname else "Prometheus Alert Trend"
        _an = (alertname or "").upper()
        yaxis_title = "使用率 (%)" if ("使用率" in (alertname or "")) or ("CPU" in _an) else ""
        
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
        
        # 图例：垂直排列在右侧，无边框，紧凑
        fig.update_layout(
            title=dict(
                text=chart_title,
                font=dict(size=24, color='#ffffff', family=plot_font_family),
                x=0.5,
                xanchor='center',
                pad=dict(t=40),
            ),
            xaxis=dict(
                domain=[0, 0.82],  # 主图占 82% 宽度
                title=dict(text=xlabel_text, font=dict(size=14, color='#ffffff')),
                tickfont=dict(size=11, color='#ffffff', family=plot_font_family),
                gridcolor='rgba(255, 255, 255, 0.2)',
                gridwidth=1,
                showgrid=True,
                zeroline=False,
            ),
            yaxis=dict(
                title=dict(text=yaxis_title, font=dict(size=12, color='#ffffff', family=plot_font_family)),
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
                orientation="v",  # 垂直排列
                bgcolor='rgba(0,0,0,0)',  # 透明背景
                bordercolor='rgba(0,0,0,0)',  # 无边框
                font=dict(size=11, color='#ffffff', family=plot_font_family),
                x=0.83,  # 紧挨主图右侧
                y=1.0,   # 顶部对齐
                xanchor='left',
                yanchor='top',
                traceorder="normal",
            ),
            margin=dict(l=60, r=20, t=80, b=60),  # 右侧 margin 减小，空间留给图例
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


def _generate_plot_with_matplotlib(
    result: list,
    alertname: Optional[str] = None,
    alert_time: Optional[str] = None,
    legend_label_whitelist: Optional[List[str]] = None,
) -> Optional[bytes]:
    """
    使用 Matplotlib 从已解析的 result 生成趋势图（含红线、图例在右）。
    供 generate_plot_from_generator_url 与 generate_plot_from_result 复用。
    """
    import numpy as np
    from matplotlib.colors import LinearSegmentedColormap

    fig, ax = plt.subplots(figsize=(14, 7), dpi=150)
    plotted = 0
    legend_labels: List[str] = []
    time_axis_xs: List[datetime] = []
    colors = ['#e74c3c', '#3498db', '#2ecc71', '#f39c12', '#9b59b6', '#1abc9c', '#e67e22', '#34495e']
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
        label = _build_series_label(
            series.get("metric") or {},
            legend_label_whitelist=legend_label_whitelist,
        )
        if not label or not label.strip():
            label = series.get("metric", {}).get("__name__", f"series_{idx}")
        legend_label = _legend_line_with_alert_value(label, list(ys))
        legend_labels.append(legend_label)
        color = colors[idx % len(colors)]
        ax.plot(xs, ys, linewidth=3.0, label=legend_label, color=color, marker='o', markersize=4, alpha=0.95, zorder=5 - idx)
        time_axis_xs = list(xs)
        plotted += 1

    if plotted == 0:
        plt.close(fig)
        return None

    # 借鉴参考图：使用率类显示 Y 轴单位
    _an = (alertname or "").upper()
    show_pct = ("使用率" in (alertname or "")) or ("CPU" in _an)

    chart_title = alertname if alertname else "Prometheus Alert Trend"
    # 标题按整张图居中，而不是按左侧坐标轴区域居中
    fig.suptitle(chart_title, fontsize=20, fontweight='bold', color='#ffffff', y=0.98, x=0.5, ha='center')

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
    ax.set_xlabel(xlabel_text, fontsize=14, color='#ffffff', fontweight='normal')
    ax.set_ylabel("使用率 (%)" if show_pct else "", fontsize=12 if show_pct else 0, color='#ffffff')

    def format_y_value(x, p):
        if abs(x) >= 1000:
            return f'{x/1000:.2f} K'.rstrip('0').rstrip('.')
        elif x == int(x):
            return f'{int(x)}'
        return f'{x:.1f}'

    if show_pct:
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'{x:.1f}%'))
    else:
        ax.yaxis.set_major_formatter(plt.FuncFormatter(format_y_value))
    ax.tick_params(axis='y', labelsize=12, colors='#ffffff', width=1)
    ax.tick_params(axis='x', labelsize=11, colors='#ffffff', width=1)
    ax.grid(True, linestyle="--", alpha=0.4, linewidth=1.0, color='#ffffff')
    ax.set_axisbelow(True)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['left'].set_color('#ffffff')
    ax.spines['bottom'].set_color('#ffffff')
    ax.spines['left'].set_linewidth(2)
    ax.spines['bottom'].set_linewidth(2)

    # 图例布局优化：垂直排列在右侧，无边框
    legend_obj = None
    if plotted > 0:
        legend_obj = ax.legend(
            loc="upper left",
            bbox_to_anchor=(1.02, 1.0),  # 紧挨主图右侧，顶部对齐
            fontsize=10,
            frameon=False,  # 去掉图例边框
            labelspacing=0.8,
            handlelength=1.5,
            handletextpad=0.5,
        )
        for text in legend_obj.get_texts():
            text.set_color('#ffffff')
            text.set_fontweight('normal')

    ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S', tz=ZoneInfo("Asia/Shanghai")))
    if time_axis_xs:
        time_span = (max(time_axis_xs) - min(time_axis_xs)).total_seconds()
        if time_span <= 300:
            ax.xaxis.set_major_locator(mdates.SecondLocator(interval=30))
        elif time_span <= 900:
            ax.xaxis.set_major_locator(mdates.MinuteLocator(interval=1))
        elif time_span <= 3600:
            ax.xaxis.set_major_locator(mdates.MinuteLocator(interval=5))
        else:
            ax.xaxis.set_major_locator(mdates.MinuteLocator(interval=15))
    fig.autofmt_xdate(rotation=45)

    fig.patch.set_facecolor('#0a0a0f')
    ax.set_facecolor('#151520')
    y_min, y_max = ax.get_ylim()
    x_min, x_max = ax.get_xlim()
    y_vals = np.linspace(y_min, y_max, 100)
    x_vals = np.linspace(x_min, x_max, 100)
    Z = np.linspace(0, 1, len(y_vals)).reshape(-1, 1)
    Z = np.tile(Z, (1, len(x_vals)))
    cmap = LinearSegmentedColormap.from_list('custom', ['#0a0a0f', '#1a1a2e', '#2a2a3e'], N=256)
    ax.imshow(Z, extent=[x_min, x_max, y_min, y_max], aspect='auto', cmap=cmap, alpha=0.3, zorder=0, origin='lower')

    # 主图占 80% 宽度，右侧留给垂直图例
    fig.subplots_adjust(left=0.08, right=0.80, top=0.90, bottom=0.15)
    
    # 确保图例不被裁剪
    extra_artists = [legend_obj] if legend_obj else []
    
    buffer = BytesIO()
    fig.savefig(
        buffer, format='png', dpi=150, facecolor='#0a0a0f', edgecolor='none',
        bbox_inches='tight', bbox_extra_artists=extra_artists,
    )
    plt.close(fig)
    return buffer.getvalue()


def generate_plot_from_result(
    result: list,
    *,
    alertname: Optional[str] = None,
    alert_time: Optional[str] = None,
    use_plotly: bool = True,
    legend_label_whitelist: Optional[List[str]] = None,
) -> Optional[bytes]:
    """
    从已解析的 Prometheus query_range result 直接生成趋势图（不发起 HTTP 请求）。
    用于本地测试或已有 result 数据的场景。含告警时刻红线，图例在红线右侧。
    """
    if not result:
        return None
    if use_plotly and PLOTLY_AVAILABLE:
        png = _generate_plot_with_plotly(
            result, alertname, alert_time,
            legend_label_whitelist=legend_label_whitelist,
        )
        if png:
            return png
    _setup_matplotlib_cjk_font()
    warnings.filterwarnings(
        "ignore",
        message=".*Glyph.*missing from font",
        category=UserWarning,
        module="matplotlib",
    )
    return _generate_plot_with_matplotlib(result, alertname, alert_time, legend_label_whitelist)


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
    alert_labels: Optional[Dict[str, Any]] = None,  # 告警 labels，支持标量或列表（合并告警）
    legend_label_whitelist: Optional[List[str]] = None,  # 图例中只显示这些 label，不配置则用默认白名单
    datasource_type: Optional[str] = None,  # "prometheus" | "victoriametrics" | None/"auto" 按 URL 推断
    inject_labels: Optional[bool] = None,  # 仅 Prometheus 时生效：是否向 expr 注入 label 收窄查询
) -> Optional[bytes]:
    """
    根据 generatorURL 生成 Prometheus/VM 趋势图（与 Go 取数方式一致）。

    - 表达式来源：从 generatorURL 的 g0.expr 取，并做完整 URL 解码（支持多重编码）。
    - 请求方式：POST + application/x-www-form-urlencoded 调用 query_range（与 Go 一致），避免 GET 对长 expr 的编码差异。
    - datasource_type 为 victoriametrics（或 auto 推断为 VM）且 alert_labels 全标量时，会向表达式注入
      label 再请求；合并告警（labels 含列表）时不注入，请求后按多值过滤。
    - datasource_type 为 prometheus 时默认不注入，可通过 inject_labels=True 启用。
    - alert_labels 支持列表值，过滤时保留 metric 在对应列表内的 series（一图多曲线）。
    """
    if not generator_url:
        return None

    try:
        # 解析查询表达式：从 g0.expr 取并完整 URL decode
        expr = _parse_expr_from_generator_url(generator_url)
        if expr:
            logger.info("从 generatorURL 解析出的 g0.expr（已完整 decode）: %s", expr[:300] + ("..." if len(expr) > 300 else ""))
        if not expr:
            logger.info(
                "generatorURL 不含 g0.expr，跳过出图；generatorURL=%s，已配置 prometheus_url=%s",
                generator_url[:200] + ("..." if len(generator_url) > 200 else ""),
                bool(prometheus_url),
            )
            if prometheus_url:
                logger.info(
                    "已配置 prometheus_url 但因无法从 generatorURL 获取查询表达式（vmalert 链接通常无 g0.expr），无法请求该地址出图。"
                    "可配置 vmalert 的 -external.alert.source 将 expr 写入链接。"
                )
            return None

        # 确定 Prometheus API 地址：优先使用配置的 prometheus_url，否则从 generatorURL 解析
        if prometheus_url:
            # 使用配置的 Prometheus URL（config 中的 prometheus_image.prometheus_url）
            # 保留 path：VictoriaMetrics vmselect 需用 /select/0/prometheus，否则会 400
            parsed_prometheus = urlparse(prometheus_url)
            if not parsed_prometheus.scheme or not parsed_prometheus.netloc:
                logger.warning("配置的 prometheus_url 非法，跳过出图: %s", prometheus_url)
                return None
            base_path = (parsed_prometheus.path or "").rstrip("/") or ""
            prometheus_base = f"{parsed_prometheus.scheme}://{parsed_prometheus.netloc}{base_path}"
            logger.info("使用 config 中的 prometheus_url 请求趋势图: %s", prometheus_base)
        else:
            # 从 generatorURL 解析
            parsed = urlparse(generator_url)
            if not parsed.scheme or not parsed.netloc:
                logger.warning("generatorURL 非法，跳过出图: %s", generator_url)
                return None
            prometheus_base = f"{parsed.scheme}://{parsed.netloc}"

        # 时间范围：始终以出图时的当前时间为右端，使图表右边缘对齐「现在」，中间数据不会挤在右边
        now_utc = datetime.now(timezone.utc)
        lb = max(1, lookback_minutes)
        end = now_utc
        start = end - timedelta(minutes=lb)
        prometheus_api = f"{prometheus_base}/api/v1/query_range"
        plot_expr = _normalize_query_for_plot(expr)
        if plot_expr != expr:
            logger.info("检测到阈值比较表达式，已转换为绘图表达式: %s -> %s", expr[:200], plot_expr[:200])

        # 解析数据源：None/"auto" 时根据 generatorURL 推断
        effective_ds = (datasource_type or "auto").strip().lower()
        if effective_ds == "auto":
            effective_ds = "victoriametrics" if _is_datasource_victoriametrics(generator_url) else "prometheus"
            logger.debug("datasource 自动推断为: %s", effective_ds)
        # VM：仅当 alert_labels 全为标量时注入；合并告警（含列表）不注入，靠请求后多值过滤
        # Prometheus：仅当 inject_labels 为 True 且全标量时注入
        should_inject = (
            alert_labels
            and _alert_labels_all_scalar(alert_labels)
            and (
                (effective_ds == "victoriametrics")
                or (effective_ds == "prometheus" and inject_labels is True)
            )
        )
        if should_inject:
            plot_expr = _inject_alert_labels_into_expr(plot_expr, alert_labels)

        # 与 Go 一致：POST + application/x-www-form-urlencoded，避免 GET 对长 expr 的编码差异
        params = {
            "query": plot_expr,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "step": step,
        }

        full_uri = f"{prometheus_api}?{urlencode(params)}"
        logger.info("获取趋势图请求 URI: %s", full_uri)
        # 输出可直接复制到终端运行的 curl（仅对 value 内双引号做 shell 转义，用 %s 避免 repr 导致不能直接用）
        _q_escaped = plot_expr.replace("\\", "\\\\").replace('"', '\\"')
        _curl_cmd = (
            f'curl -S -G '
            f'--data-urlencode "query={_q_escaped}" '
            f'--data-urlencode "start={start.isoformat()}" '
            f'--data-urlencode "end={end.isoformat()}" '
            f'--data-urlencode "step={step}" '
            f'"{prometheus_api}"'
        )
        logger.info("curl 本地验证: %s", _curl_cmd)
        logger.debug(
            "请求 Prometheus query_range 生成趋势图: api=%s, step=%s, lookback=%sm",
            prometheus_api,
            step,
            lookback_minutes,
        )
        response = requests.post(
            prometheus_api,
            data=params,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=timeout_seconds,
            proxies=proxies,
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data", {})
        result = data.get("result", [])
        
        # DEBUG: 打印查询结果中的标签，辅助排查为什么 status 不显示
        if result:
            first_metric = result[0].get("metric", {})
            logger.debug("[图表调试] Prometheus 返回了 %s 条曲线", len(result))
            logger.debug("[图表调试] 第一条曲线的原始标签: %s", first_metric)
            logger.debug("[图表调试] 使用的图例白名单: %s", legend_label_whitelist)

        # Decode 调试：打印每条 series 的原始值与解析后的值（首、尾各一档），便于核对与告警是否一致
        if result:
            for idx, series in enumerate(result):
                metric = series.get("metric") or {}
                values = series.get("values") or []
                if not values:
                    logger.info("[decode] series[%s] metric=%s values=[]", idx, metric)
                    continue
                for label, (item, point_name) in [
                    ("first", (values[0], "首点")),
                    ("last", (values[-1], "尾点")),
                ]:
                    if not isinstance(item, (list, tuple)) or len(item) < 2:
                        logger.info(
                            "[decode] series[%s] metric=%s %s: 无效 item (非 list 或 len<2) raw=%s",
                            idx, metric, point_name, item,
                        )
                        continue
                    raw_ts, raw_val = item[0], item[1]
                    type_val = type(raw_val).__name__
                    try:
                        decoded_ts = float(raw_ts)
                        decoded_val = float(raw_val)
                    except (TypeError, ValueError) as e:
                        logger.info(
                            "[decode] series[%s] metric=%s %s: 解析异常 %s raw_ts=%s raw_val=%s type_val=%s",
                            idx, metric, point_name, e, raw_ts, raw_val, type_val,
                        )
                        continue
                    logger.info(
                        "[decode] series[%s] metric=%s %s: raw_ts=%s raw_val=%s (type=%s) -> decoded_ts=%.0f decoded_val=%s",
                        idx, metric, point_name, raw_ts, raw_val, type_val, decoded_ts, decoded_val,
                    )

        if payload.get("status") != "success" or not isinstance(result, list) or not result:
            logger.info("Prometheus query_range 无可绘制数据，跳过出图")
            return None

        # 校验：若图里最大值很小（如 0–5），而告警应为计数类（如当前值：727），说明表达式可能未剥离比较符，query_range 返回的是 0/1
        try:
            max_val = None
            for s in result:
                for item in (s.get("values") or []):
                    if isinstance(item, (list, tuple)) and len(item) >= 2:
                        try:
                            v = float(item[1])
                            max_val = v if max_val is None else max(max_val, v)
                        except (TypeError, ValueError):
                            pass
            if max_val is not None and max_val < 20:
                logger.warning(
                    "图表数值偏小（最大值 %.1f）：若告警为计数类（如「当前值：727」）而图只显示 0–4，"
                    "说明请求的表达式可能仍含比较符(>=200 等)，导致返回 0/1 而非真实计数。请核对上文的「转换为绘图表达式」与请求 query。",
                    max_val,
                )
        except Exception:
            pass

        # 按告警 labels 过滤，只画与当前告警目标一致的曲线（如图只显示 /dev/sdb1 /data 而非全部 tmpfs）
        if alert_labels:
            logger.debug("[图表调试] 正在按告警标签过滤: %s", alert_labels)
        
        result = _filter_result_by_alert_labels(result, alert_labels)
        
        if result:
            logger.debug("[图表调试] 过滤后剩余 %s 条曲线", len(result))
            if result:
                 logger.debug("[图表调试] 过滤后第一条曲线标签: %s", result[0].get("metric", {}))

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
        warnings.filterwarnings(
            "ignore",
            message=".*Glyph.*missing from font",
            category=UserWarning,
            module="matplotlib",
        )
        png = _generate_plot_with_matplotlib(
            result, alertname, alert_time,
            legend_label_whitelist=legend_label_whitelist,
        )
        if png is not None:
            return png
        logger.info("Prometheus query_range 结果无法解析为曲线，跳过出图")
        return None
    except requests.RequestException as exc:
        logger.warning("Prometheus 出图请求失败，跳过图片发送: %s", exc)
        return None
    except Exception as exc:
        logger.warning("Prometheus 出图异常，跳过图片发送: %s", exc)
        return None

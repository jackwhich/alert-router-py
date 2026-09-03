"""
Prometheus 趋势图生成模块

基于 Alertmanager webhook 中的 generatorURL（g0.expr）调用 Prometheus query_range，
生成 PNG 趋势图，供 Telegram sendPhoto 使用。

支持两种绘图引擎：
1. Plotly（推荐）- 更美观的图表，支持渐变、阴影等现代视觉效果
2. Matplotlib（备选）- 传统绘图库，兼容性好
"""
from __future__ import annotations

import re
import warnings
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, unquote, urlencode, urlparse

import requests

from ..core.logging_config import get_logger
from ..core.http_metrics import request_with_metrics
from ..core.metrics import (
    ImageGenerateFailuresTotal,
    PrometheusRequestDuration,
    PrometheusRequestsByDatasourceTotal,
    PrometheusRequestsTotal,
)
from .base import (
    DEFAULT_LEGEND_LABEL_WHITELIST,
    PLOTLY_AVAILABLE,
    build_series_label,
    format_alert_time,
    get_cjk_font_family,
    legend_line_with_alert_value,
    parse_time_series_data,
    render_matplotlib_png,
)

logger = get_logger("alert-router")

try:
    import plotly.graph_objects as go
    import plotly.io as pio
except ImportError:
    go = None
    pio = None


# 告警里仅用于路由/展示、通常不出现在 metric 里的标签，过滤曲线时不参与匹配
_ALERT_ONLY_LABELS = {"alertname", "severity", "cluster", "_source", "_receiver"}


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


def _shell_escape_for_double_quoted(s: str) -> str:
    """对双引号内的 shell 字符串做一层转义；已存在的 \\\" 不再重复转义，避免日志里出现 \\\\\\\"。"""
    out: List[str] = []
    i = 0
    while i < len(s):
        if i + 1 < len(s) and s[i] == "\\" and s[i + 1] == '"':
            out.append('\\"')
            i += 2
        elif s[i] == "\\":
            out.append("\\\\")
            i += 1
        elif s[i] == '"':
            out.append('\\"')
            i += 1
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def _shell_escape_for_single_quoted(s: str) -> str:
    """单引号 shell 片段内只需转义单引号：' -> '\\''（结束引号+反斜杠引号+开始引号）。双引号无需转义，从源头避免 \\\"。"""
    return s.replace("'", "'\\''")


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
        all_timestamps: List[datetime] = []
        whitelist = legend_label_whitelist or list(DEFAULT_LEGEND_LABEL_WHITELIST)

        def hex_to_rgba(hex_color, alpha=0.2):
            hex_color = hex_color.lstrip('#')
            r = int(hex_color[0:2], 16)
            g = int(hex_color[2:4], 16)
            b = int(hex_color[4:6], 16)
            return f'rgba({r}, {g}, {b}, {alpha})'

        for idx, (xs, ys, metric) in enumerate(parse_time_series_data(result)):
            label = build_series_label(metric, legend_label_whitelist=whitelist)
            legend_label = legend_line_with_alert_value(label, ys)
            color = colors[idx % len(colors)]
            fig.add_trace(go.Scatter(
                x=list(xs),
                y=list(ys),
                mode='lines+markers',
                name=legend_label,
                line=dict(
                    color=color,
                    width=3,
                    shape='spline',
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
        
        chart_title = alertname if alertname else "Prometheus Alert Trend"
        _an = (alertname or "").upper()
        yaxis_title = "使用率 (%)" if ("使用率" in (alertname or "")) or ("CPU" in _an) else ""
        xlabel_text = format_alert_time(alert_time)
        plot_font_family = get_cjk_font_family() or "Arial, sans-serif"
        
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
    """Matplotlib 出图，委托给 plotters.base.render_matplotlib_png。"""
    warnings.filterwarnings(
        "ignore",
        message=".*Glyph.*missing from font",
        category=UserWarning,
        module="matplotlib",
    )
    _an = (alertname or "").upper()
    return render_matplotlib_png(
        result,
        alertname=alertname,
        alert_time=alert_time,
        legend_label_whitelist=legend_label_whitelist or list(DEFAULT_LEGEND_LABEL_WHITELIST),
        default_title="Prometheus Alert Trend",
        legend_with_alert_value=True,
        percent_ylabel=("使用率" in (alertname or "")) or ("CPU" in _an),
    )


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
            logger.debug("从 generatorURL 解析出的 g0.expr（已完整 decode）: %s", expr)
        if not expr:
            logger.info(
                "generatorURL 不含 g0.expr，跳过出图；generatorURL=%s，已配置 prometheus_url=%s",
                generator_url,
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
            logger.debug("使用 config 中的 prometheus_url 请求趋势图: %s", prometheus_base)
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
        # Decode 出的表达式 1:1 作为绘图 query，不做任何改写（不剥离 >=、>、< 等比较符）
        plot_expr = expr
        logger.debug("绘图请求使用的表达式（与告警表达式一致）: %s", plot_expr)

        # 解析数据源：None/"auto" 时根据 generatorURL 推断
        effective_ds = (datasource_type or "auto").strip().lower()
        if effective_ds == "auto":
            effective_ds = "victoriametrics" if _is_datasource_victoriametrics(generator_url) else "prometheus"
            logger.debug("datasource 自动推断为: %s", effective_ds)
        ds_label = effective_ds if effective_ds in ("prometheus", "victoriametrics") else "unknown"
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
        logger.debug("获取趋势图请求 URI: %s", full_uri)
        # 拼 curl 时用「展示用」表达式：若 plot_expr 里已有 \"（来自 generatorURL 等），先还原为 "，否则复制到终端会多出反斜杠
        _q_for_curl = plot_expr.replace('\\"', '"')
        _q_escaped = _shell_escape_for_single_quoted(_q_for_curl)
        _curl_cmd = (
            "curl -S -G "
            f"--data-urlencode 'query={_q_escaped}' "
            f"--data-urlencode 'start={start.isoformat()}' "
            f"--data-urlencode 'end={end.isoformat()}' "
            f"--data-urlencode 'step={step}' "
            f"'{prometheus_api}'"
        )
        logger.debug("curl 本地验证: %s", _curl_cmd)
        logger.debug(
            "请求 Prometheus query_range 生成趋势图: api=%s, step=%s, lookback=%sm",
            prometheus_api,
            step,
            lookback_minutes,
        )
        # 记录 Prometheus 请求耗时与结果状态
        import time as _time

        t0 = _time.perf_counter()
        metric_status = "ok"
        try:
            session = requests.Session()
            response = request_with_metrics(
                session,
                "POST",
                prometheus_api,
                target=ds_label,
                data=params,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=timeout_seconds,
                proxies=proxies,
            )
            payload = response.json()
        except requests.RequestException as req_exc:
            metric_status = "error"
            # 按请求失败原因记录出图失败（网络/超时/HTTP 等）
            reason = "network"
            if isinstance(req_exc, requests.exceptions.Timeout):
                reason = "timeout"
            elif isinstance(req_exc, requests.exceptions.HTTPError):
                reason = "http_error"
            try:
                ImageGenerateFailuresTotal.labels(source="prometheus", reason=reason).inc()
            except Exception:
                pass
            raise
        finally:
            try:
                elapsed = _time.perf_counter() - t0
                PrometheusRequestDuration.observe(elapsed)
                PrometheusRequestsTotal.labels(status=metric_status).inc()
                PrometheusRequestsByDatasourceTotal.labels(
                    status=metric_status,
                    datasource=ds_label,
                ).inc()
            except Exception:
                pass
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
                    logger.debug("[decode] series[%s] metric=%s values=[]", idx, metric)
                    continue
                for label, (item, point_name) in [
                    ("first", (values[0], "首点")),
                    ("last", (values[-1], "尾点")),
                ]:
                    if not isinstance(item, (list, tuple)) or len(item) < 2:
                        logger.debug(
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
                        logger.debug(
                            "[decode] series[%s] metric=%s %s: 解析异常 %s raw_ts=%s raw_val=%s type_val=%s",
                            idx, metric, point_name, e, raw_ts, raw_val, type_val,
                        )
                        continue
                    logger.debug(
                        "[decode] series[%s] metric=%s %s: raw_ts=%s raw_val=%s (type=%s) -> decoded_ts=%.0f decoded_val=%s",
                        idx, metric, point_name, raw_ts, raw_val, type_val, decoded_ts, decoded_val,
                    )

        if payload.get("status") != "success" or not isinstance(result, list) or not result:
            logger.info("Prometheus query_range 无可绘制数据，跳过出图")
            try:
                ImageGenerateFailuresTotal.labels(source="prometheus", reason="no_data").inc()
            except Exception:
                pass
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
                    "请优先核对告警规则是否使用了比较表达式 + bool（该写法会返回 0/1），"
                    "并确认上文记录的 query 与告警表达式一致。",
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

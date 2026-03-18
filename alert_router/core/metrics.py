"""
Prometheus 指标定义模块

对齐 alert-router-go/internal/metrics/metrics.go 中的指标，统一前缀 webhook_alerts_，
并在此基础上扩展更细粒度的观测指标。
"""
from prometheus_client import Counter, Histogram


_PREFIX = "webhook_alerts_"


# === 与 Go 对齐的基础指标 ===

AlertsReceivedTotal = Counter(
    _PREFIX + "alerts_received_total",
    "按来源与状态统计的接收告警数",
    labelnames=("source", "status"),
)

AlertsRoutedTotal = Counter(
    _PREFIX + "alerts_routed_total",
    "被路由到各渠道的告警次数",
    labelnames=("channel",),
)

AlertsSentTotal = Counter(
    _PREFIX + "alerts_sent_total",
    "按渠道与结果统计的发送次数（success/failure/skipped）",
    labelnames=("channel", "status"),
)

AlertsSendFailuresTotal = Counter(
    _PREFIX + "alerts_send_failures_total",
    "按渠道与原因统计的发送失败次数",
    labelnames=("channel", "reason"),
)

AlertsDedupSkippedTotal = Counter(
    _PREFIX + "alerts_dedup_skipped_total",
    "去重跳过次数（jenkins/grafana）",
    labelnames=("type",),
)

ImageGeneratedTotal = Counter(
    _PREFIX + "image_generated_total",
    "按来源与状态统计的出图次数",
    labelnames=("source", "status"),
)

PrometheusRequestsTotal = Counter(
    _PREFIX + "prometheus_requests_total",
    "请求 Prometheus/VM query_range 次数",
    labelnames=("status",),
)

PrometheusRequestDuration = Histogram(
    _PREFIX + "prometheus_request_duration_seconds",
    "query_range 请求耗时",
)

WebhookRequestsTotal = Counter(
    _PREFIX + "webhook_requests_total",
    "Webhook 请求次数",
    labelnames=("status",),
)

WebhookRequestDuration = Histogram(
    _PREFIX + "webhook_request_duration_seconds",
    "单次 Webhook 处理耗时",
)


# === 扩展指标：解析失败、错误细分、耗时、数据源等 ===

AlertsParseFailuresTotal = Counter(
    _PREFIX + "alerts_parse_failures_total",
    "告警解析失败次数（按来源与原因统计）",
    labelnames=("source", "reason"),
)

AlertsReceivedByNameTotal = Counter(
    _PREFIX + "alerts_received_by_name_total",
    "按来源、告警名称与状态统计的接收告警数",
    labelnames=("source", "alertname", "status"),
)

AlertsReceivedBySeverityTotal = Counter(
    _PREFIX + "alerts_received_by_severity_total",
    "按来源、严重级别与状态统计的接收告警数",
    labelnames=("source", "severity", "status"),
)

WebhookErrorsTotal = Counter(
    _PREFIX + "webhook_errors_total",
    "Webhook 入口错误次数（按错误类型统计）",
    labelnames=("type",),
)

ChannelSendDuration = Histogram(
    _PREFIX + "channel_send_duration_seconds",
    "按渠道与类型统计的发送耗时",
    labelnames=("channel", "type"),
)

ChannelHttpFailuresTotal = Counter(
    _PREFIX + "channel_http_failures_total",
    "按渠道与 HTTP 状态码统计的下游 HTTP 失败次数",
    labelnames=("channel", "code"),
)

PrometheusRequestsByDatasourceTotal = Counter(
    _PREFIX + "prometheus_requests_by_ds_total",
    "按数据源与状态统计的 Prometheus/VM 请求次数",
    labelnames=("status", "datasource"),
)

ImageGenerateFailuresTotal = Counter(
    _PREFIX + "image_generate_failures_total",
    "按来源与失败原因统计的趋势图生成失败次数",
    labelnames=("source", "reason"),
)

AlertsSentByNameTotal = Counter(
    _PREFIX + "alerts_sent_by_name_total",
    "按渠道、告警名称、状态与结果统计的发送次数",
    labelnames=("channel", "alertname", "status", "result"),
)

AlertsSentBySeverityTotal = Counter(
    _PREFIX + "alerts_sent_by_severity_total",
    "按渠道、严重级别与结果统计的发送次数",
    labelnames=("channel", "severity", "result"),
)


def inc_alerts_sent(channel: str, status: str) -> None:
    """记录一次发送结果（success/failure/skipped）。"""
    AlertsSentTotal.labels(channel=channel, status=status).inc()


def inc_alerts_send_failure(channel: str, reason: str) -> None:
    """记录一次发送失败及原因（timeout/http_error/network/unknown 等）。"""
    AlertsSendFailuresTotal.labels(channel=channel, reason=reason).inc()


def inc_alerts_parse_failure(source: str, reason: str) -> None:
    """记录一次告警解析失败。"""
    AlertsParseFailuresTotal.labels(source=source or "unknown", reason=reason).inc()


def inc_webhook_error(err_type: str) -> None:
    """记录一次 Webhook 入口错误。"""
    WebhookErrorsTotal.labels(type=err_type).inc()


def inc_alerts_received_by_name(source: str, alertname: str, status: str) -> None:
    AlertsReceivedByNameTotal.labels(
        source=source or "unknown",
        alertname=alertname or "unknown",
        status=status or "unknown",
    ).inc()


def inc_alerts_received_by_severity(source: str, severity: str, status: str) -> None:
    AlertsReceivedBySeverityTotal.labels(
        source=source or "unknown",
        severity=severity or "unknown",
        status=status or "unknown",
    ).inc()


def inc_alerts_sent_by_name(
    channel: str,
    alertname: str,
    status: str,
    result: str,
) -> None:
    AlertsSentByNameTotal.labels(
        channel=channel,
        alertname=alertname or "unknown",
        status=status or "unknown",
        result=result or "unknown",
    ).inc()


def inc_alerts_sent_by_severity(
    channel: str,
    severity: str,
    result: str,
) -> None:
    AlertsSentBySeverityTotal.labels(
        channel=channel,
        severity=severity or "unknown",
        result=result or "unknown",
    ).inc()


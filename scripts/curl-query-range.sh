#!/usr/bin/env bash
# Prometheus query_range 本地验证脚本
# 用于与 Python 出图逻辑对比：原始 JSON 中的 values 与 decode 后的值是否一致。
#
# 使用前请替换:
#   PROMETHEUS_URL - 你的 Prometheus 或 VictoriaMetrics 地址（不含 /api/v1/query_range）
#   QUERY         - 与告警一致的 Prometheus 表达式（如 nginx 5xx 计数）
#
# 示例（nginx 状态码 5xx，按 server_name、status 分组）:
#   export PROMETHEUS_URL="http://localhost:9090"
#   export QUERY='sum(increase(nginx_http_requests_total{status=~"5.."}[1m])) by (server_name, status)'
#
# 时间范围默认：过去 15 分钟，step=30s（与出图一致）。

set -e
PROMETHEUS_URL="${PROMETHEUS_URL:-http://localhost:9090}"
STEP="${STEP:-30s}"
LOOKBACK_MINUTES="${LOOKBACK_MINUTES:-15}"

# 若未传 QUERY，使用示例表达式（请按实际指标名修改）
if [ -z "${QUERY}" ]; then
  echo "未设置 QUERY，使用示例表达式（请按实际修改）"
  QUERY='sum(increase(nginx_http_requests_total{status=~"5.."}[1m])) by (server_name, status)'
fi

END=$(date -u +%s)
START=$((${END} - ${LOOKBACK_MINUTES} * 60))
API="${PROMETHEUS_URL%/}/api/v1/query_range"

echo "请求: ${API}"
echo "query: ${QUERY}"
echo "start: ${START} end: ${END} step: ${STEP}"
echo "---"

curl -sS -G \
  --data-urlencode "query=${QUERY}" \
  --data-urlencode "start=${START}" \
  --data-urlencode "end=${END}" \
  --data-urlencode "step=${STEP}" \
  "${API}" | python3 -m json.tool

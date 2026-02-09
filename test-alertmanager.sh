#!/bin/bash

# 测试发送告警到 Alertmanager
# 使用方法：./test-alertmanager.sh [firing|resolved]
# 默认发送 firing 告警

ALERTMANAGER_URL="${ALERTMANAGER_URL:-http://localhost:9093}"  # Alertmanager API 地址
STATUS="${1:-firing}"  # 告警状态：firing 或 resolved

# 获取当前时间（UTC）
CURRENT_TIME=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

if [ "$STATUS" == "resolved" ]; then
    # Resolved 告警：endsAt 设置为当前时间
    ENDS_AT="$CURRENT_TIME"
    # startsAt 设置为 5 分钟前
    STARTS_AT=$(date -u -v-5M +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || date -u -d "5 minutes ago" +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || date -u +"%Y-%m-%dT%H:%M:%SZ")
else
    # Firing 告警
    STARTS_AT="$CURRENT_TIME"
    ENDS_AT="0001-01-01T00:00:00Z"
fi

echo "发送 $STATUS 告警到 Alertmanager: $ALERTMANAGER_URL"
echo "告警名称: NodeFilesystemSpaceFillingUp"
echo "开始时间: $STARTS_AT"
echo "结束时间: $ENDS_AT"
echo ""

# 使用 Alertmanager API 发送告警
curl -X POST "$ALERTMANAGER_URL/api/v2/alerts" \
  -H "Content-Type: application/json" \
  -d "[
  {
    \"labels\": {
      \"alertname\": \"NodeFilesystemSpaceFillingUp\",
      \"cluster\": \"prod\",
      \"device\": \"/dev/sdb1\",
      \"fstype\": \"xfs\",
      \"instance\": \"10.8.64.91:9100\",
      \"mountpoint\": \"/data\",
      \"replica\": \"prom-03\",
      \"severity\": \"warning\"
    },
    \"annotations\": {
      \"summary\": \"磁盘空间将在 24 小时内耗尽\",
      \"description\": \"实例 10.8.64.91:9100 的 /data 分区使用率超过 80%\"
    },
    \"startsAt\": \"$STARTS_AT\",
    \"endsAt\": \"$ENDS_AT\",
    \"generatorURL\": \"http://prometheus:9090/graph?g0.expr=node_filesystem_avail_bytes\"
  }
]"

echo ""
echo ""
echo "告警已发送！"
echo "查看 Alertmanager UI: $ALERTMANAGER_URL"
echo "查看告警列表: $ALERTMANAGER_URL/#/alerts"

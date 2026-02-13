#!/bin/bash
# 触发 Prometheus 告警 Webhook，用于测试 alert-router（路由、Telegram、趋势图、中文显示）
#
# 用法:
#   ./test-trigger.sh              # 默认 POST 到 http://127.0.0.1:8080/webhook
#   WEBHOOK_URL=http://10.8.64.101:9600/webhook ./test-trigger.sh
#
# 环境变量:
#   WEBHOOK_URL  可选，默认 http://127.0.0.1:8080/webhook

set -e
BASE="${WEBHOOK_URL:-http://127.0.0.1:9600/webhook}"

echo "POST -> $BASE"
echo ""

curl -s -X POST "$BASE" \
  -H "Content-Type: application/json" \
  -d '{
  "receiver": "Prometheus",
  "status": "firing",
  "alerts": [
    {
      "status": "firing",
      "labels": {
        "alertname": "容器CPU使用使用率超过阈值大于30%.在1分钟内持续飙高",
        "cluster": "prod",
        "namespace": "ebpay",
        "pod": "prod-merchant-service-5c7b7fb4b6-kcjr5",
        "replica": "prom-02",
        "severity": "警告"
      },
      "annotations": {
        "description": "Pod ebpay/prod-merchant-service-5c7b7fb4b6-kcjr5 的 CPU 使用率在过去 1 分钟内持续超过 30%。\n当前值：61.11%\n",
        "summary": "CPU使用率已经超过30%, 并且在过去1分钟内持续飙升。 当前值: 61.11%\n"
      },
      "startsAt": "2026-02-13T02:21:37.34Z",
      "endsAt": "0001-01-01T00:00:00Z",
      "generatorURL": "http://prometheus:9090/graph?g0.expr=up%7Bjob%3D%22prometheus%22%7D&g0.tab=1",
      "fingerprint": "214b755dd06aba03"
    }
  ],
  "groupLabels": { "alertname": "容器CPU使用使用率超过阈值大于30%.在1分钟内持续飙高" },
  "commonLabels": {
    "alertname": "容器CPU使用使用率超过阈值大于30%.在1分钟内持续飙高",
    "cluster": "prod",
    "namespace": "ebpay",
    "severity": "警告"
  },
  "commonAnnotations": {},
  "externalURL": "http://prometheus:9093",
  "version": "4",
  "groupKey": "{}/{}:{alertname=\"容器CPU使用使用率超过阈值大于30%.在1分钟内持续飙高\"}",
  "truncatedAlerts": 0
}'

echo ""
echo "Done. 检查 Telegram 与 alert-router 日志。"

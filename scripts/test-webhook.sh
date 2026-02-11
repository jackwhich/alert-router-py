#!/bin/bash

# 测试告警 webhook 的 curl 命令
# 根据日志数据构造的 Prometheus Alertmanager 格式

curl -X POST http://10.8.64.101:9600/webhook \
  -H "Content-Type: application/json" \
  -d '{
  "version": "4",
  "groupKey": "{}:{alertname=\"域名证书即将过期\"}",
  "status": "firing",
  "receiver": "webhook",
  "groupLabels": {
    "alertname": "域名证书即将过期"
  },
  "commonLabels": {
    "alertname": "域名证书即将过期",
    "cluster": "prod",
    "instance": "https://pro-app-ebpay-s3.ebpay01.net",
    "replica": "prom-03",
    "severity": "warning"
  },
  "commonAnnotations": {
    "summary": "域名证书30天后过期 (instance https://pro-app-ebpay-s3.ebpay01.net)"
  },
  "externalURL": "http://prometheus:9090",
  "alerts": [
    {
      "status": "firing",
      "labels": {
        "alertname": "域名证书即将过期",
        "cluster": "prod",
        "instance": "https://pro-app-ebpay-s3.ebpay01.net",
        "replica": "prom-03",
        "severity": "warning"
      },
      "annotations": {
        "summary": "域名证书30天后过期 (instance https://pro-app-ebpay-s3.ebpay01.net)"
      },
      "startsAt": "2026-02-08T06:06:07Z",
      "endsAt": "0001-01-01T00:00:00Z",
      "generatorURL": "http://prometheus:9090/graph?g0.expr=up%7Bjob%3D%22prometheus%22%7D"
    }
  ]
}'


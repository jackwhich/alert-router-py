# 数据源说明：Prometheus vs Grafana Webhook 负载结构

本服务根据 **HTTP 请求体的 JSON 顶层字段** 区分数据源，再决定用哪个解析器并写入 `_source`，路由据此分发。  
这里明确写出两种数据源的典型形态和区分依据。

---

## 1. Prometheus Alertmanager 数据源（长什么样）

**来源**：Prometheus 生态的 [Alertmanager](https://prometheus.io/docs/alerting/latest/alertmanager/) 发出的 Webhook。

**顶层特征**：

| 字段 | 说明 |
|------|------|
| `version` | 固定为 **`"4"`**（Alertmanager 当前 webhook 版本） |
| `groupKey` | 告警分组键，如 `"{}:{alertname=\"HighCPU\"}"` |
| `receiver` | 接收器名称 |
| `status` | `"firing"` / `"resolved"` |
| `groupLabels` | 分组标签 |
| `commonLabels` / `commonAnnotations` | 组内公共标签/注解 |
| `externalURL` | Alertmanager 地址 |
| `alerts` | 告警数组 |

**没有**：`orgId`、`state`、`title`、`message`（这些是 Grafana 才有）。

**示例（精简）**：

```json
{
  "version": "4",
  "groupKey": "{}:{alertname=\"HighCPU\"}",
  "status": "firing",
  "receiver": "webhook",
  "groupLabels": {},
  "commonLabels": { "alertname": "HighCPU", "severity": "critical" },
  "commonAnnotations": {},
  "externalURL": "http://alertmanager:9093",
  "alerts": [
    {
      "status": "firing",
      "labels": { "alertname": "HighCPU", "instance": "host1" },
      "annotations": { "summary": "CPU high" },
      "startsAt": "2024-01-01T00:00:00Z",
      "endsAt": "0001-01-01T00:00:00Z",
      "generatorURL": "http://prometheus:9090/graph?g0.expr=..."
    }
  ]
}
```

**识别依据（代码里用到的）**：无 `orgId`，且 `version` 存在且不为 `"1"`，或 有 `groupKey` + `alerts`。

---

## 2. Grafana Unified Alerting 数据源（长什么样）

**来源**：Grafana [Unified Alerting](https://grafana.com/docs/grafana/latest/alerting/) 的 Webhook 联系点。

**顶层特征**：

| 字段 | 说明 |
|------|------|
| `version` | 当前为 **`"1"`**（与 Alertmanager 的 `"4"` 区分） |
| `orgId` | **Grafana 独有**，组织 ID（数字） |
| `state` | **Grafana 独有**，如 `"alerting"`、`"resolved"` |
| `title` | **Grafana 独有**，如 `"[FIRING:1] (TestAlert Grafana)"` |
| `message` | **Grafana 独有**，富文本告警内容 |
| `receiver` | 联系点名称（可为空字符串） |
| `status` | `"firing"` / `"resolved"` 等 |
| `groupKey` | 分组键（有，但和 Alertmanager 格式可同存） |
| `alerts` | 告警数组 |
| `groupLabels` / `commonLabels` / `commonAnnotations` / `externalURL` | 同 Prometheus 风格 |

**单条告警里** 还可能有：`fingerprint`、`silenceURL`、`dashboardURL`、`panelURL`、`valueString`、`values` 等。

**示例（精简）**：

```json
{
  "receiver": "",
  "status": "firing",
  "alerts": [
    {
      "status": "firing",
      "labels": { "alertname": "TestAlert", "instance": "Grafana" },
      "annotations": { "summary": "Notification test" },
      "startsAt": "2026-02-10T02:09:54.917072407+08:00",
      "endsAt": "0001-01-01T00:00:00Z",
      "fingerprint": "57c6d9296de2ad39",
      "silenceURL": "http://localhost:3000/alerting/silence/new?...",
      "valueString": "[ metric='foo' labels={instance=bar} value=10 ]"
    }
  ],
  "groupLabels": {},
  "commonLabels": { "alertname": "TestAlert", "instance": "Grafana" },
  "commonAnnotations": { "summary": "Notification test" },
  "externalURL": "http://localhost:3000/",
  "version": "1",
  "groupKey": "{alertname=\"TestAlert\", instance=\"Grafana\"}...",
  "truncatedAlerts": 0,
  "orgId": 1,
  "title": "[FIRING:1]  (TestAlert Grafana)",
  "state": "alerting",
  "message": "**Firing**\n\nValue: [no value]\nLabels:\n - alertname = TestAlert\n..."
}
```

**识别依据（代码里用到的）**：顶层存在 `orgId`，**或** `version == "1"` 且存在 `state` 或 `title`。

---

## 3. 区分小结（一眼能看出谁发的）

| 判断 | Prometheus Alertmanager | Grafana Unified Alerting |
|------|-------------------------|---------------------------|
| **version** | `"4"` | `"1"` |
| **orgId** | 无 | 有（数字） |
| **state** | 无 | 有（如 `alerting`） |
| **title** | 无 | 有（如 `[FIRING:1] ...`） |
| **groupKey + alerts** | 有 | 也有（不能单靠这个区分） |

代码里在 `adapters/alert_normalizer.py` 的 `identify_data_source(payload)` 中按上表规则做**数据源识别**，解析后由各 adapter 写入 `_source: "prometheus"` 或 `_source: "grafana"`，路由再根据 `_source` 等标签选择渠道。

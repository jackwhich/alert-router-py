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

代码里在 `alert_router/adapters/alert_normalizer.py` 的 `identify_data_source(payload)` 中按上表规则做**数据源识别**，解析后由各 adapter 写入 `_source: "prometheus"` 或 `_source: "grafana"`，路由再根据 `_source` 等标签选择渠道。

## 4. 数据源识别流程

```
Webhook 请求
    ↓
alert_normalizer.identify_data_source()
    ↓
    ├─ 有 orgId? → Grafana Unified Alerting
    ├─ version == "1" 且 (有 state 或 title)? → Grafana Unified Alerting
    ├─ version != "1" 且 有 groupKey + alerts? → Prometheus Alertmanager
    └─ 其他 → 单条告警格式或未知格式
    ↓
调用对应的 adapter.parse()
    ↓
写入 _source 标签
    ↓
路由模块根据 _source 等标签分发
```

## 5. 适配器模块说明

### Prometheus Adapter (`alert_router/adapters/prometheus_adapter.py`)

- **功能**: 解析 Prometheus Alertmanager webhook payload
- **特性**:
  - 支持告警合并（同组多条告警合并为一条）
  - 提取实体值（pod、instance、service_name 等）
  - 支持多实体类型汇总
- **输出**: 标准告警格式，包含 `_source: "prometheus"` 标签

### Grafana Adapter (`alert_router/adapters/grafana_adapter.py`)

- **功能**: 解析 Grafana Unified Alerting webhook payload
- **特性**:
  - 提取当前值（从 `values.B` 或 `valueString`）
  - 保留 Grafana 特有字段（fingerprint、silenceURL 等）
- **输出**: 标准告警格式，包含 `_source: "grafana"` 标签

### Alert Normalizer (`alert_router/adapters/alert_normalizer.py`)

- **功能**: 统一解析入口，自动识别数据源并调用对应适配器
- **流程**:
  1. 识别数据源类型
  2. 调用对应的 adapter.parse()
  3. 返回标准化的告警列表

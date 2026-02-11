# 告警模板样式展示

本文档展示告警模板渲染后的实际效果。

---

## 输入数据示例

假设收到以下告警数据：

```json
{
  "status": "firing",
  "labels": {
    "alertname": "HighCPU",
    "severity": "critical",
    "service_name": "gateway",
    "category": "性能告警"
  },
  "annotations": {
    "description": "CPU usage on server1 has been above 80% for more than 5 minutes. Current value: 85%",
    "mention": "@默认用户"
  },
  "startsAt": "2024-01-15 10:30:00",
  "endsAt": "",
  "generatorURL": "http://prometheus:9090/graph?g0.expr=cpu_usage"
}
```

---

## Telegram 模板渲染效果

### 模板文件：`templates/telegram.md.j2`

### 渲染后的消息（Markdown 格式）：

```
❌❌❌❌ 状态: 告警

时间: 2024-01-15 10:30:00

alertname: HighCPU
severity: critical
env: prod
cluster: k8s-prod-01

summary: CPU usage is above 80%

description: CPU usage on server1 has been above 80% for more than 5 minutes. Current value: 85%
```

### Telegram 实际显示效果：

```
❌❌❌❌ 状态: 告警

时间: 2024-01-15 10:30:00

alertname: HighCPU
severity: critical
env: prod
cluster: k8s-prod-01

summary: CPU usage is above 80%

description: CPU usage on server1 has been above 80% for more than 5 minutes. Current value: 85%
```

**说明**：
- 状态显示为中文（❌❌❌❌ 状态: 告警 / ✅✅✅✅ 状态: 恢复）
- 显示告警时间
- 自动遍历 labels 字段（排除 prometheus、id、image、uid、metrics_path、endpoint、job、service、name、_source）
- 显示 summary 和 description（如果有）
- 简洁清晰，适合移动端查看

---

## Slack 模板渲染效果

### 模板文件：`templates/slack.json.j2`

### 渲染后的 JSON（发送给 Slack）：

```json
{
  "text": "❌❌❌❌ 告警",
  "username": "平台健康度告警",
  "blocks": [
    {
      "type": "section",
      "text": {
        "type": "mrkdwn",
        "text": "❌❌❌❌ 告警\n*告警时间*: `2024-01-15 10:30:00`\n\n*告警项*: `HighCPU`\n*服务名称*: `gateway`\n*告警类别*: `性能告警`\n*等级*: `critical`\n\n*告警详情*：\nCPU usage on server1 has been above 80% for more than 5 minutes. Current value: 85%\n\n@默认用户"
      }
    },
    {
      "type": "actions",
      "elements": [
        {
          "type": "button",
          "text": {
            "type": "plain_text",
            "text": "View Dashboard"
          },
          "url": "http://prometheus:9090/graph?g0.expr=cpu_usage"
        }
      ]
    }
  ]
}
```

### Slack 实际显示效果：

```
┌─────────────────────────────────────────┐
│ ❌❌❌❌ 告警                             │
│ 平台健康度告警                           │
├─────────────────────────────────────────┤
│ 告警时间: 2024-01-15 10:30:00           │
│                                         │
│ 告警项: HighCPU                         │
│ 服务名称: gateway                        │
│ 告警类别: 性能告警                        │
│ 等级: critical                          │
│                                         │
│ 告警详情：                               │
│ CPU usage on server1 has been above     │
│ 80% for more than 5 minutes.            │
│ Current value: 85%                      │
│                                         │
│ @默认用户                                │
├─────────────────────────────────────────┤
│ [View Dashboard] ← 可点击按钮            │
└─────────────────────────────────────────┘
```

**说明**：
- 使用 Slack Block Kit 格式
- 状态显示为中文（❌❌❌❌ 告警 / ✅✅✅✅ 恢复）
- 显示告警时间（firing）或恢复时间（resolved）
- 字段包括：告警项、服务名称、告警类别、等级
- 支持告警详情（description）和 @mention
- 包含可点击的按钮链接（如果有 generatorURL）
- 支持 Markdown 格式

---

## 恢复告警示例

### 输入数据：

```json
{
  "status": "resolved",
  "labels": {
    "alertname": "HighCPU",
    "severity": "critical",
    "service_name": "gateway",
    "category": "性能告警"
  },
  "annotations": {
    "description": "CPU usage on server1 has returned to normal levels (45%)",
    "mention": "@默认用户"
  },
  "startsAt": "2024-01-15 10:30:00",
  "endsAt": "2024-01-15 10:35:00",
  "generatorURL": "http://prometheus:9090/graph?g0.expr=cpu_usage"
}
```

### Telegram 渲染效果：

```
✅✅✅✅ 状态: 恢复

时间: 2024-01-15 10:35:00

alertname: HighCPU
severity: critical
env: prod
cluster: k8s-prod-01

summary: CPU usage returned to normal

description: CPU usage on server1 has returned to normal levels (45%)
```

**说明**：
- ✅ 使用绿色对勾表示恢复（✅✅✅✅ 状态: 恢复）
- 显示恢复时间
- 字段格式与告警状态一致

### Slack 渲染效果：

```
┌─────────────────────────────────────────┐
│ ✅✅✅✅ 恢复                             │
│ 平台健康度告警                           │
├─────────────────────────────────────────┤
│ 恢复时间: 2024-01-15 10:35:00           │
│                                         │
│ 告警项: HighCPU                         │
│ 服务名称: gateway                        │
│ 告警类别: 性能告警                        │
│ 等级: critical                          │
│                                         │
│ 告警详情：                               │
│ CPU usage on server1 has returned to    │
│ normal levels (45%)                      │
│                                         │
│ @默认用户                                │
├─────────────────────────────────────────┤
│ [View Dashboard] ← 可点击按钮            │
└─────────────────────────────────────────┘
```

**说明**：
- ✅ 使用绿色对勾表示恢复（✅✅✅✅ 恢复）
- 显示恢复时间（resolved 状态显示恢复时间）
- 状态显示为"✅✅✅✅ 恢复"
- 字段格式与告警状态一致

---

## 模板变量说明

模板中可用的变量：

| 变量 | 说明 | 示例值 |
|------|------|--------|
| `title` | 告警标题 | `[ALERT] HighCPU` |
| `status` | 告警状态 | `firing` / `resolved` |
| `labels.*` | 所有 labels | `labels.severity` = `critical` |
| `annotations.*` | 所有 annotations | `annotations.summary` = `CPU usage is high` |
| `startsAt` | 告警开始时间 | `2024-01-15T10:30:00Z` |
| `endsAt` | 告警结束时间 | `2024-01-15T10:35:00Z` |
| `generatorURL` | 告警生成器链接 | `http://prometheus:9090/graph?...` |

### 常用 Jinja2 过滤器：

- `default('-')` - 如果值为空，显示默认值 `-`
- `| upper` - 转换为大写
- `| lower` - 转换为小写
- `| length` - 获取长度

---

## 自定义模板

你可以根据需要修改模板文件：

1. **修改 Telegram 模板**：编辑 `templates/prometheus_telegram.html.j2` 或 `templates/grafana_telegram.html.j2`
2. **修改 Slack 模板**：编辑 `templates/prometheus_slack.json.j2` 或 `templates/grafana_slack.json.j2`
3. **创建新模板**：创建新的 `.j2` 文件，在 `config.yaml` 中引用

**注意**：模板文件位于项目根目录的 `templates/` 目录，模板渲染逻辑在 `alert_router/templates/template_renderer.py` 中。

### 模板示例：更详细的 Telegram 模板

```jinja2
🚨 *{{ title }}*

*状态:* {{ "告警" if status == "firing" else "恢复" }}
*级别:* {{ labels.severity | default('unknown') | upper }}
*时间:* {{ startsAt }}

*环境信息:*
• 环境: {{ labels.env | default('-') }}
• 服务: {{ labels.service | default('-') }}
• 集群: {{ labels.cluster | default('-') }}
• 实例: {{ labels.instance | default('-') }}

*告警详情:*
{{ annotations.summary | default('无摘要') }}

{{ annotations.description | default('') }}

[查看详情]({{ generatorURL }})
```

---

## 测试模板

你可以使用以下 Python 代码测试模板渲染：

```python
from alert_router.templates import render

# 测试数据
ctx = {
    "title": "[ALERT] HighCPU",
    "status": "firing",
    "labels": {
        "alertname": "HighCPU",
        "severity": "critical",
        "env": "prod",
        "service": "gateway",
        "cluster": "k8s-prod-01"
    },
    "annotations": {
        "summary": "CPU usage is above 80%",
        "description": "CPU usage on server1 is 85%"
    },
    "startsAt": "2024-01-15 10:30:00",
    "endsAt": "",
    "generatorURL": "http://prometheus:9090/graph?g0.expr=cpu_usage"
}

# 渲染 Prometheus Telegram 模板
prometheus_telegram = render("prometheus_telegram.html.j2", ctx)
print("=== Prometheus Telegram 模板 ===")
print(prometheus_telegram)

# 渲染 Prometheus Slack 模板
prometheus_slack = render("prometheus_slack.json.j2", ctx)
print("\n=== Prometheus Slack 模板 ===")
print(prometheus_slack)
```

### 使用测试脚本

项目提供了测试脚本，可以直接测试 webhook：

```bash
# 测试 Prometheus Alertmanager webhook
./scripts/test-alertmanager.sh

# 测试通用 webhook
./scripts/test-webhook.sh
```

## 模板文件位置

所有模板文件位于项目根目录的 `templates/` 目录：

- `prometheus_telegram.html.j2` - Prometheus → Telegram HTML 模板
- `prometheus_slack.json.j2` - Prometheus → Slack JSON 模板
- `prometheus_telegram_jenkins.html.j2` - Jenkins 专用 Telegram 模板
- `grafana_telegram.html.j2` - Grafana → Telegram HTML 模板
- `grafana_slack.json.j2` - Grafana → Slack JSON 模板

模板渲染器位于 `alert_router/templates/template_renderer.py`，会自动处理：
- 时间转换（UTC → CST）
- URL 转链接（Telegram HTML）
- description 中的时间替换（Slack）

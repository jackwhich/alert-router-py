# 快速开始指南

本指南帮助你快速部署和配置 Alert Router 服务。

## 前置要求

- Python 3.9+ 
- pip 包管理器
- 配置文件访问权限

## 安装步骤

### 1. 克隆或下载项目

```bash
cd /path/to/alert-router-py
```

### 2. 安装依赖

```bash
pip3 install -r scripts/requirements.txt
```

### 3. 配置服务

编辑 `config.yaml` 文件，配置你的告警渠道：

```yaml
channels:
  prometheus_telegram_default:
    type: telegram
    enabled: true
    bot_token: "你的Bot Token"
    chat_id: "你的Chat ID"
    template: "prometheus_telegram.html.j2"
    image_enabled: true  # 启用图片生成
```

### 4. 启动服务

#### 方式一：使用启动脚本（推荐）

```bash
./scripts/start.sh start
```

#### 方式二：直接运行

```bash
python3 -m uvicorn app:app --host 0.0.0.0 --port 8080
```

### 5. 配置 Webhook

在 Prometheus Alertmanager 或 Grafana 中配置 Webhook URL：

```
http://<your-host>:8080/webhook
```

## 配置示例

### 基本配置

```yaml
server:
  host: 0.0.0.0
  port: 8080

logging:
  log_dir: "logs"
  log_file: "alert-router.log"
  level: "INFO"
  max_bytes: 10485760
  backup_count: 5

channels:
  # Telegram 渠道
  prometheus_telegram_default:
    type: telegram
    enabled: true
    bot_token: "123456:ABC-DEF..."
    chat_id: "-1001234567890"
    template: "prometheus_telegram.html.j2"
    image_enabled: true
    send_resolved: true

  # Slack 渠道
  prometheus_slack:
    type: slack
    enabled: true
    webhook_url: "https://hooks.slack.com/services/T000/B000/XXX"
    template: "prometheus_slack.json.j2"

routing:
  # 默认路由
  - match:
      _source: "prometheus"
    send_to: ["prometheus_telegram_default"]

  # Critical 告警路由
  - match:
      _source: "prometheus"
      severity: "critical|灾难"
    send_to: ["prometheus_telegram_default", "prometheus_slack"]
```

### 路由规则示例

#### 1. 按告警级别路由

```yaml
routing:
  - match:
      severity: "critical|灾难"
    send_to: ["critical_channel"]
  
  - match:
      severity: "warning"
    send_to: ["warning_channel"]
```

#### 2. 按服务名称路由

```yaml
routing:
  - match:
      service_name: "gateway"
    send_to: ["gateway_channel"]
  
  - match:
      service_name: ".*api.*"
    send_to: ["api_channel"]
```

#### 3. Jenkins 告警专用路由

```yaml
routing:
  - match:
      _source: "prometheus"
      _receiver: "prod_ebpay_jenkins_alarm"
      alertname: ".*Jenkins.*|.*jenkins.*"
    send_to: ["prometheus_telegram_jenkins"]

channels:
  prometheus_telegram_jenkins:
    type: telegram
    enabled: true
    bot_token: "123456:ABC-DEF..."
    chat_id: "-1001234567890"
    template: "prometheus_telegram_jenkins.html.j2"
    image_enabled: false  # Jenkins 告警不生成图片
    send_resolved: false  # Jenkins 告警不发送 resolved
```

## 功能特性配置

### 图片生成配置

```yaml
prometheus_image:
  enabled: true
  prometheus_url: "http://prometheus:9090"  # 可选，为空则从 generatorURL 解析
  use_proxy: false
  plot_engine: "plotly"  # 或 "matplotlib"
  lookback_minutes: 15
  step: "30s"
  timeout_seconds: 8
  max_series: 8

grafana_image:
  enabled: true
  grafana_url: "http://grafana:3000"  # 可选
  prometheus_url: "http://prometheus:9090"  # 可选
  use_proxy: false
  lookback_minutes: 15
  step: "30s"
  timeout_seconds: 8
  max_series: 8
```

### Jenkins 去重配置

```yaml
jenkins_dedup:
  enabled: true
  ttl_seconds: 900  # 15分钟内去重
  clear_on_resolved: true  # resolved 时清理缓存
```

### 代理配置

```yaml
# 全局代理开关
proxy_enabled: true

# 全局代理配置
proxy:
  http: "socks5://10.8.16.64:13080"
  https: "socks5://10.8.16.64:13080"

channels:
  prometheus_telegram_default:
    # 渠道级别代理（可选，覆盖全局配置）
    proxy_enabled: true
    proxy:
      http: "socks5://10.8.16.64:13080"
      https: "socks5://10.8.16.64:13080"
```

## 测试

### 测试 Webhook

使用提供的测试脚本：

```bash
# 测试 Prometheus Alertmanager webhook
./scripts/test-alertmanager.sh

# 测试通用 webhook
./scripts/test-webhook.sh
```

### 手动测试

使用 curl 发送测试请求：

```bash
curl -X POST http://localhost:8080/webhook \
  -H "Content-Type: application/json" \
  -d '{
    "version": "4",
    "status": "firing",
    "alerts": [{
      "status": "firing",
      "labels": {
        "alertname": "TestAlert",
        "severity": "warning"
      },
      "annotations": {
        "summary": "Test alert"
      },
      "startsAt": "2024-01-15T10:30:00Z"
    }]
  }'
```

## 常见问题

### 1. 服务启动失败

**问题**: 端口被占用

**解决**: 
```bash
# 检查端口占用
lsof -i :8080

# 修改 config.yaml 中的端口
server:
  port: 8081
```

### 2. Telegram 消息发送失败

**问题**: Bot Token 或 Chat ID 配置错误

**解决**:
- 检查 `bot_token` 是否正确
- 检查 `chat_id` 是否正确（Telegram 群组 ID 需要以 `-` 开头）
- 确认 Bot 已添加到群组

### 3. 图片生成失败

**问题**: Prometheus/Grafana 无法访问

**解决**:
- 检查 `prometheus_url` 或 `grafana_url` 配置
- 检查网络连接
- 如果使用代理，设置 `use_proxy: true`

### 4. 路由不匹配

**问题**: 告警没有发送到预期渠道

**解决**:
- 检查路由规则中的 `match` 条件
- 查看日志确认告警的 labels
- 使用正则表达式时注意转义字符

## 下一步

- 查看 [数据源格式说明](DATA_SOURCES.md) 了解 Prometheus 和 Grafana 的数据格式
- 查看 [模板示例](template-examples.md) 了解如何自定义模板
- 查看 [兼容性说明](COMPATIBILITY.md) 了解迁移指南

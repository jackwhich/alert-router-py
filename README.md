# Grafana / Prometheus 告警统一路由与模板化服务

本服务用于接收 **Prometheus Alertmanager** 和 **Grafana Unified Alerting** 的 Webhook，  
根据 **labels** 做路由分发，并对 **不同告警渠道（Telegram / Slack）** 进行统一模板化发送。

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置

编辑 `config.yaml`，配置你的 Telegram Bot Token、Chat ID 和 Slack Webhook URL：

```yaml
channels:
  tg_prometheus_critical:
    type: telegram
    enabled: true
    bot_token: "你的Bot Token"
    chat_id: "你的Chat ID"
    template: "telegram.md.j2"
```

### 3. 启动服务

```bash
uvicorn app:app --host 0.0.0.0 --port 8080
```

### 4. 配置 Webhook

在 Grafana 或 Prometheus Alertmanager 中配置 Webhook URL：

```
http://<your-host>:8080/webhook
```

## 目录结构

```
alert-router-py/
├── app.py                  # 主应用
├── alert_normalizer.py    # 统一解析入口（告警标准化）
├── prometheus_adapter.py  # Prometheus Alertmanager 适配器
├── grafana_adapter.py      # Grafana Unified Alerting 适配器
├── config.yaml            # 配置文件
├── requirements.txt       # Python 依赖
├── README.md             # 说明文档
└── templates/            # 模板目录
    ├── telegram.md.j2    # Telegram 模板
    └── slack.json.j2     # Slack 模板
```

## 功能特性

- ✅ 自动识别 Prometheus Alertmanager 和 Grafana Unified Alerting 格式
- ✅ 灵活的 YAML 配置路由规则
- ✅ 支持渠道开关控制（enabled）
- ✅ 按来源（Grafana/Prometheus）区分群组
- ✅ 按告警级别（severity）路由
- ✅ 模板化消息格式（Jinja2）
- ✅ 模块化设计，易于扩展

## 配置说明

详细配置说明请参考 `alert-router.md` 文档。

## 许可证

MIT License

# Grafana / Prometheus 告警统一路由与模板化服务

本服务用于接收 **Prometheus Alertmanager** 和 **Grafana Unified Alerting** 的 Webhook，  
根据 **labels** 做路由分发，并对 **不同告警渠道（Telegram / Slack）** 进行统一模板化发送。

## 环境要求

- **Python 3.9**（推荐）或 Python 3.8+
- pip 包管理器

## 快速开始

### 1. 检查 Python 版本

```bash
python3.9 --version
# 或
python3 --version  # 应该显示 3.9.x
```

### 2. 安装依赖

```bash
python3.9 -m pip install -r scprit/requirements.txt
# 或
pip3 install -r scprit/requirements.txt
```

### 2. 配置

直接编辑根目录下的 `config.yaml`（建议在本地环境中维护，并在生产环境通过配置管理下发）。  
也可以通过环境变量 `CONFIG_FILE` 指定配置文件路径。

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

#### 方式一：使用启动脚本（推荐）

```bash
# 启动服务
./scprit/start.sh start

# 停止服务（优雅关闭）
./scprit/start.sh stop

# 重启服务
./scprit/start.sh restart

# 查看状态
./scprit/start.sh status

# 查看日志
./scprit/start.sh logs
```

#### 方式二：使用 systemd（生产环境推荐）

```bash
# 1. 复制服务文件
sudo cp scprit/alert-router.service /etc/systemd/system/

# 2. 修改服务文件中的路径和用户
sudo vi /etc/systemd/system/alert-router.service

# 3. 重载 systemd 配置
sudo systemctl daemon-reload

# 4. 启动服务
sudo systemctl start alert-router

# 5. 设置开机自启
sudo systemctl enable alert-router

# 6. 查看状态
sudo systemctl status alert-router

# 7. 查看日志
sudo journalctl -u alert-router -f
```

#### 方式三：直接使用 uvicorn

```bash
python3.9 -m uvicorn app:app --host 0.0.0.0 --port 8080 --workers 4
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
├── adapters/               # 适配器目录
│   ├── alert_normalizer.py    # 统一解析入口（告警标准化）
│   ├── prometheus_adapter.py  # Prometheus Alertmanager 适配器
│   └── grafana_adapter.py     # Grafana Unified Alerting 适配器
├── alert_router/             # 核心模块目录
│   ├── config.py             # 配置加载模块
│   ├── logging_config.py     # 日志配置模块
│   ├── models.py             # 数据模型
│   ├── routing.py            # 路由匹配模块
│   ├── senders.py            # 消息发送模块
│   ├── template_renderer.py  # 模板渲染模块
│   └── utils.py              # 工具函数模块
├── config.yaml               # 本地配置文件
├── scprit/                   # 启动 & 测试脚本目录
│   ├── requirements.txt      # Python 依赖
│   ├── start.sh              # 启动脚本（支持优雅重启）
│   ├── test-alertmanager.sh  # 测试发送告警到 Alertmanager
│   ├── test-webhook.sh       # 测试 webhook 的示例脚本
│   └── alert-router.service  # systemd 服务文件
├── docs/                     # 文档目录
│   ├── COMPATIBILITY.md      # 新旧实现兼容性说明
│   └── template-examples.md  # 模板与配置示例
├── README.md                 # 说明文档
├── logs/                     # 日志目录（自动创建）
│   └── alert-router.log      # 日志文件
└── templates/                # 模板目录
    ├── grafana_slack.json.j2             # Grafana → Slack 模板
    ├── grafana_telegram.html.j2          # Grafana → Telegram 模板
    ├── prometheus_slack.json.j2          # Prometheus → Slack 模板
    ├── prometheus_telegram.html.j2       # Prometheus → Telegram 模板
    └── prometheus_telegram_jenkins.html.j2 # Jenkins 专用 Telegram 模板
```

## 功能特性

- ✅ 自动识别 Prometheus Alertmanager 和 Grafana Unified Alerting 格式
- ✅ 灵活的 YAML 配置路由规则
- ✅ 支持渠道开关控制（enabled）
- ✅ 按来源（Grafana/Prometheus）区分群组
- ✅ 按告警级别（severity）路由
- ✅ 模板化消息格式（Jinja2）
- ✅ 模块化设计，易于扩展
- ✅ 完善的日志系统（文件输出 + 日志轮转）
- ✅ 优雅关闭和重启支持

## 启动脚本说明

启动脚本 `scprit/start.sh` 支持以下命令：

- `start` - 启动服务
- `stop` - 停止服务（优雅关闭，等待最多 30 秒）
- `restart` - 重启服务
- `status` - 查看服务状态和进程信息
- `logs` - 实时查看日志
- `reload` - 重载配置（重启服务）

### 环境变量

可以通过环境变量自定义配置：

```bash
export PYTHON_CMD=python3.9  # Python 命令（默认: python3.9）
export HOST=0.0.0.0          # 监听地址
export PORT=8080             # 监听端口
export WORKERS=4             # 工作进程数
export TIMEOUT=30            # 超时时间（秒）

./scprit/start.sh start
```

### 优雅关闭

启动脚本支持优雅关闭：

1. 发送 `SIGTERM` 信号给主进程
2. uvicorn 会等待当前请求完成
3. 最多等待 30 秒
4. 如果超时，强制终止进程

## 日志配置

日志系统支持文件输出和日志轮转，配置在 `config.yaml` 中：

```yaml
logging:
  log_dir: "logs"              # 日志目录
  log_file: "alert-router.log" # 日志文件名
  level: "INFO"                # 日志级别
  max_bytes: 10485760          # 单个文件最大 10MB
  backup_count: 5              # 保留 5 个备份文件
```

日志文件会自动轮转，当日志文件达到 `max_bytes` 大小时，会自动创建新文件，并保留指定数量的备份文件。

日志格式示例：
```
2024-01-15 10:30:00 - alert-router - INFO - [app.py:273] - 收到告警请求: {...}
2024-01-15 10:30:01 - alert-router - INFO - [app.py:320] - 告警 HighCPU 已发送到渠道: tg_critical
2024-01-15 10:30:02 - alert-router - ERROR - [app.py:324] - 告警 HighCPU 发送到渠道 slack_main 失败: Connection timeout
```

## 配置说明

详细配置说明请参考 `docs/template-examples.md` 文档。

## 许可证

MIT License

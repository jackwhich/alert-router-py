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
python3.9 -m pip install -r scripts/requirements.txt
# 或
pip3 install -r scripts/requirements.txt
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
./scripts/start.sh start

# 停止服务（优雅关闭）
./scripts/start.sh stop

# 重启服务
./scripts/start.sh restart

# 查看状态
./scripts/start.sh status

# 查看日志
./scripts/start.sh logs
```

#### 方式二：使用 systemd（生产环境推荐）

```bash
# 1. 复制服务文件
sudo cp scripts/alert-router.service /etc/systemd/system/

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
├── app.py                          # 应用入口（HTTP 路由）
├── config.yaml                     # 配置文件
│
├── alert_router/                   # 核心模块包
│   ├── __init__.py                # 包初始化（向后兼容导出）
│   │
│   ├── core/                      # 核心功能模块
│   │   ├── __init__.py
│   │   ├── config.py             # 配置加载
│   │   ├── models.py             # 数据模型
│   │   ├── logging_config.py     # 日志配置
│   │   └── utils.py              # 工具函数
│   │
│   ├── adapters/                  # 数据源适配器
│   │   ├── __init__.py
│   │   ├── alert_normalizer.py   # 统一解析入口（告警标准化）
│   │   ├── grafana_adapter.py    # Grafana Unified Alerting 适配器
│   │   └── prometheus_adapter.py # Prometheus Alertmanager 适配器
│   │
│   ├── services/                  # 业务服务层
│   │   ├── __init__.py
│   │   ├── alert_service.py      # 告警处理服务（核心业务逻辑）
│   │   ├── image_service.py      # 图片生成服务
│   │   └── channel_filter.py     # 渠道过滤服务
│   │
│   ├── plotters/                  # 绘图模块
│   │   ├── __init__.py
│   │   ├── base.py               # 公共绘图工具
│   │   ├── prometheus_plotter.py # Prometheus 绘图器
│   │   └── grafana_plotter.py    # Grafana 绘图器
│   │
│   ├── routing/                   # 路由模块
│   │   ├── __init__.py
│   │   ├── routing.py            # 路由匹配逻辑
│   │   └── jenkins_dedup.py      # Jenkins 去重逻辑
│   │
│   ├── senders/                   # 消息发送模块
│   │   ├── __init__.py
│   │   └── senders.py            # 发送器实现（HTTP 连接池优化）
│   │
│   └── templates/                 # 模板渲染模块
│       ├── __init__.py
│       └── template_renderer.py  # 模板渲染器
│
├── templates/                      # Jinja2 模板文件
│   ├── grafana_slack.json.j2
│   ├── grafana_telegram.html.j2
│   ├── prometheus_slack.json.j2
│   ├── prometheus_telegram.html.j2
│   └── prometheus_telegram_jenkins.html.j2
│
├── scripts/                        # 脚本目录
│   ├── requirements.txt           # Python 依赖
│   ├── start.sh                   # 启动脚本（支持优雅重启）
│   ├── test-alertmanager.sh       # 测试发送告警到 Alertmanager
│   ├── test-webhook.sh            # 测试 webhook 的示例脚本
│   └── alert-router.service       # systemd 服务文件
│
├── docs/                           # 文档目录
│   ├── COMPATIBILITY.md           # 新旧实现兼容性说明
│   ├── DATA_SOURCES.md            # 数据源格式说明
│   └── template-examples.md       # 模板与配置示例
│
├── archive/                        # 归档目录（旧代码）
│   └── old_py/                     # 旧版本代码归档
│
├── logs/                           # 日志目录（自动创建）
│   └── alert-router.log           # 日志文件
│
└── README.md                       # 说明文档
```

### 架构说明

项目采用模块化设计，按功能划分为以下模块：

- **core/**: 核心功能模块（配置、模型、日志、工具函数）
- **adapters/**: 数据源适配器（支持 Prometheus 和 Grafana）
- **services/**: 业务服务层（告警处理、图片生成、渠道过滤）
- **plotters/**: 绘图模块（统一管理绘图相关代码）
- **routing/**: 路由模块（路由匹配、去重逻辑）
- **senders/**: 消息发送模块（HTTP 连接池优化）
- **templates/**: 模板渲染模块

这种模块化设计使得代码结构清晰，易于维护和扩展。

## 功能特性

### 核心功能
- ✅ 自动识别 Prometheus Alertmanager 和 Grafana Unified Alerting 格式
- ✅ 灵活的 YAML 配置路由规则
- ✅ 支持渠道开关控制（enabled）
- ✅ 按来源（Grafana/Prometheus）区分群组
- ✅ 按告警级别（severity）路由
- ✅ 模板化消息格式（Jinja2）
- ✅ 模块化设计，易于扩展
- ✅ 完善的日志系统（文件输出 + 日志轮转）
- ✅ 优雅关闭和重启支持

### 高级特性
- ✅ **图片生成**：支持 Prometheus 和 Grafana 告警趋势图生成（Plotly/Matplotlib）
- ✅ **Jenkins 去重**：智能去重 Jenkins 告警，避免重复通知
- ✅ **HTTP 连接池**：使用连接池复用连接，提升性能
- ✅ **服务层架构**：业务逻辑与 HTTP 层分离，便于测试和维护
- ✅ **渠道过滤**：统一的渠道过滤逻辑，支持图片渠道筛选
- ✅ **代理支持**：支持 HTTP/SOCKS5 代理，可配置全局或渠道级别代理

## 启动脚本说明

启动脚本 `scripts/start.sh` 支持以下命令：

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

./scripts/start.sh start
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

## 代码架构

### 架构设计

项目采用分层架构设计：

1. **HTTP 层** (`app.py`): 负责 HTTP 路由和请求处理
2. **服务层** (`alert_router/services/`): 封装业务逻辑
3. **适配器层** (`alert_router/adapters/`): 数据源适配和格式转换
4. **基础设施层** (`alert_router/core/`, `routing/`, `senders/`, `templates/`): 提供基础功能

### 性能优化

- **HTTP 连接池**: 使用 `requests.Session` 实现连接复用，减少连接建立开销
- **代码复用**: 提取公共代码，消除重复逻辑（约 300+ 行）
- **模块化设计**: 按功能划分模块，便于维护和扩展

### 代码质量

- ✅ **单一职责**: 每个模块职责明确
- ✅ **DRY 原则**: 消除重复代码
- ✅ **可维护性**: 代码结构清晰，便于理解和修改
- ✅ **可测试性**: 业务逻辑与 HTTP 层分离，便于单元测试

## 开发指南

### 导入模块

推荐使用新的模块化导入方式：

```python
# 核心模块
from alert_router.core import Channel, load_config, setup_logging

# 服务层
from alert_router.services import AlertService, ImageService

# 路由
from alert_router.routing import route, match

# 发送器
from alert_router.senders import send_telegram, send_webhook

# 绘图器
from alert_router.plotters import generate_plot_from_generator_url
```

### 向后兼容

为了保持向后兼容，仍然支持旧式导入：

```python
# 通过主包的 __init__.py 导出，仍然可以使用
from alert_router import Channel, load_config, AlertService
```

## 相关文档

- [快速开始](docs/QUICK_START.md) - 快速部署和配置指南
- [架构设计](docs/ARCHITECTURE.md) - 架构设计和模块说明
- [数据源格式](docs/DATA_SOURCES.md) - Prometheus 和 Grafana 数据源格式说明
- [模板示例](docs/template-examples.md) - 模板和配置示例
- [兼容性说明](docs/COMPATIBILITY.md) - 新旧代码兼容性对比

## 许可证

MIT License

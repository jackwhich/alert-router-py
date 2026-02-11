# 架构设计文档

本文档详细说明 Alert Router 的架构设计、模块划分和设计原则。

## 架构概览

Alert Router 采用分层架构设计，将 HTTP 层、业务逻辑层、适配器层和基础设施层清晰分离。

```
┌─────────────────────────────────────────┐
│           HTTP 层 (app.py)              │
│  FastAPI 路由、请求处理、生命周期管理    │
└─────────────────┬───────────────────────┘
                  │
┌─────────────────▼───────────────────────┐
│        服务层 (services/)                │
│  AlertService - 告警处理核心逻辑         │
│  ImageService - 图片生成服务             │
│  ChannelFilter - 渠道过滤服务           │
└─────┬───────────────┬───────────────────┘
      │               │
┌─────▼──────┐  ┌────▼────────────────────┐
│ 适配器层    │  │   基础设施层             │
│ (adapters/)│  │  routing/ - 路由匹配     │
│            │  │  senders/ - 消息发送     │
│ 数据源适配  │  │  templates/ - 模板渲染  │
│ 格式转换    │  │  plotters/ - 图片生成   │
└────────────┘  └─────────────────────────┘
```

## 模块说明

### 1. HTTP 层 (`app.py`)

**职责**:
- HTTP 路由定义
- 请求/响应处理
- 应用生命周期管理
- 错误处理

**设计原则**:
- 保持简洁，只负责 HTTP 相关逻辑
- 业务逻辑委托给服务层
- 使用 FastAPI 的异步特性

### 2. 核心模块 (`alert_router/core/`)

**包含模块**:
- `config.py`: 配置加载和验证
- `models.py`: 数据模型定义（Channel 等）
- `logging_config.py`: 日志配置
- `utils.py`: 工具函数（时间转换、URL 处理等）

**设计原则**:
- 提供基础功能，不包含业务逻辑
- 可被其他模块复用

### 3. 适配器层 (`alert_router/adapters/`)

**包含模块**:
- `alert_normalizer.py`: 统一解析入口，数据源识别
- `prometheus_adapter.py`: Prometheus Alertmanager 适配器
- `grafana_adapter.py`: Grafana Unified Alerting 适配器

**设计原则**:
- 采用适配器模式（Adapter Pattern）
- 将不同数据源格式转换为统一格式
- 每个适配器负责一种数据源的解析

**数据流**:
```
Webhook Payload
    ↓
identify_data_source()  # 识别数据源
    ↓
调用对应 adapter.parse()
    ↓
标准化告警格式（包含 _source 标签）
```

### 4. 服务层 (`alert_router/services/`)

**包含模块**:
- `alert_service.py`: 告警处理服务（核心业务逻辑）
- `image_service.py`: 图片生成服务
- `channel_filter.py`: 渠道过滤服务

**设计原则**:
- 封装业务逻辑，与 HTTP 层解耦
- 单一职责原则，每个服务负责一个领域
- 便于单元测试

**AlertService 流程**:
```
1. 接收告警列表
2. 遍历每条告警
   ├─ Jenkins 去重检查
   ├─ 路由匹配
   ├─ 生成图片（如果需要）
   └─ 发送到各个渠道
3. 返回处理结果
```

### 5. 路由模块 (`alert_router/routing/`)

**包含模块**:
- `routing.py`: 路由匹配逻辑
- `jenkins_dedup.py`: Jenkins 告警去重

**设计原则**:
- 支持灵活的匹配规则（精确匹配、正则匹配）
- 支持多规则叠加
- 去重逻辑独立模块化

### 6. 发送器模块 (`alert_router/senders/`)

**包含模块**:
- `senders.py`: Telegram 和 Webhook 发送实现

**设计原则**:
- 使用 HTTP 连接池提升性能
- 统一的错误处理
- 支持代理配置

**性能优化**:
- 使用 `requests.Session` 实现连接复用
- 连接池配置：`pool_connections=10`, `pool_maxsize=20`
- 自动重试机制

### 7. 绘图模块 (`alert_router/plotters/`)

**包含模块**:
- `base.py`: 公共绘图工具
- `prometheus_plotter.py`: Prometheus 绘图器
- `grafana_plotter.py`: Grafana 绘图器

**设计原则**:
- 提取公共代码，消除重复
- 支持多种绘图引擎（Plotly/Matplotlib）
- 统一的样式和主题

### 8. 模板渲染模块 (`alert_router/templates/`)

**包含模块**:
- `template_renderer.py`: Jinja2 模板渲染

**设计原则**:
- 使用 Jinja2 模板引擎
- 支持自定义过滤器
- 自动时间转换

## 数据流

### 告警处理流程

```
1. Webhook 请求到达
   ↓
2. app.py 接收请求，解析 JSON
   ↓
3. AlertService.process_webhook()
   ↓
4. alert_normalizer.normalize() - 识别数据源并解析
   ↓
5. 遍历每条告警
   ├─ Jenkins 去重检查
   ├─ routing.route() - 路由匹配
   ├─ ImageService.generate_image() - 生成图片（可选）
   └─ 发送到各个渠道
      ├─ template_renderer.render() - 渲染模板
      └─ senders.send_telegram/webhook() - 发送消息
   ↓
6. 返回处理结果
```

### 图片生成流程

```
1. 检查是否需要图片（source、image_enabled、渠道类型）
   ↓
2. ImageService.generate_image()
   ├─ Prometheus → prometheus_plotter.generate_plot_from_generator_url()
   └─ Grafana → grafana_plotter.generate_plot_from_grafana_generator_url()
   ↓
3. 提取查询表达式（从 generatorURL）
   ↓
4. 调用 Prometheus/Grafana API 获取数据
   ↓
5. 使用 Plotly/Matplotlib 生成图片
   ↓
6. 返回图片字节
```

## 设计原则

### 1. 单一职责原则 (SRP)

每个模块只负责一个明确的功能：
- `AlertService`: 告警处理
- `ImageService`: 图片生成
- `ChannelFilter`: 渠道过滤
- `Routing`: 路由匹配

### 2. 开闭原则 (OCP)

- 通过适配器模式支持新的数据源
- 通过模板系统支持新的消息格式
- 通过路由配置支持新的路由规则

### 3. 依赖倒置原则 (DIP)

- HTTP 层依赖服务层接口，不依赖具体实现
- 服务层依赖抽象，不依赖具体适配器

### 4. DRY 原则

- 提取公共代码到 `base.py`、`utils.py`
- 消除重复的绘图代码、渠道过滤代码

### 5. 关注点分离

- HTTP 层：只处理 HTTP 相关
- 服务层：只处理业务逻辑
- 适配器层：只处理数据格式转换
- 基础设施层：提供通用功能

## 性能优化

### 1. HTTP 连接池

- 使用 `requests.Session` 复用连接
- 减少连接建立开销
- 配置连接池大小和重试策略

### 2. 代码复用

- 提取公共代码，减少内存占用
- 统一的绘图样式，减少代码量

### 3. 异步处理（未来优化）

- 图片生成可以异步处理
- 消息发送可以批量处理

## 扩展性

### 添加新数据源

1. 在 `adapters/` 目录创建新的适配器
2. 实现 `detect()` 和 `parse()` 方法
3. 在 `alert_normalizer.py` 中注册

### 添加新渠道

1. 在 `senders/` 目录添加新的发送器
2. 在 `models.py` 中添加渠道类型
3. 在 `AlertService` 中集成

### 添加新模板

1. 在 `templates/` 目录创建 Jinja2 模板
2. 在 `config.yaml` 中配置模板路径
3. 渠道配置中引用模板

## 测试策略

### 单元测试

- 服务层：测试业务逻辑
- 适配器层：测试数据解析
- 路由层：测试匹配规则

### 集成测试

- 端到端测试：Webhook → 发送消息
- 性能测试：并发请求处理
- 兼容性测试：不同数据源格式

## 总结

Alert Router 采用模块化、分层架构设计，具有以下特点：

- ✅ **清晰的模块划分**：按功能组织代码
- ✅ **良好的可扩展性**：易于添加新功能
- ✅ **高性能**：连接池、代码复用等优化
- ✅ **易于维护**：单一职责、关注点分离
- ✅ **易于测试**：业务逻辑与 HTTP 层分离

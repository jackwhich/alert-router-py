# 新旧代码兼容性对比

## 功能对比

### ✅ 已实现的功能

1. **告警解析**
   - ✅ Prometheus Alertmanager 格式支持
   - ✅ Grafana Unified Alerting 格式支持
   - ✅ 单个告警格式支持

2. **路由功能**
   - ✅ 基于 labels 的路由匹配
   - ✅ 正则表达式匹配（支持 `.*pattern.*`, `pattern.*`, `.*pattern`, `Jenkins.*|jenkins.*`）
   - ✅ 精确匹配
   - ✅ 默认路由（兜底规则）

3. **渠道支持**
   - ✅ Telegram 发送
   - ✅ Slack 发送
   - ✅ Webhook 发送（通用）
   - ✅ 渠道开关（enabled）
   - ✅ **send_resolved 控制**（新增，对应 Alertmanager 的 send_resolved）

4. **时间处理**
   - ✅ UTC 转 CST（北京时间）
   - ✅ 多种时间格式支持
   - ✅ description 中的时间替换（Slack）

5. **代理支持**
   - ✅ 全局代理配置
   - ✅ 渠道级别代理配置
   - ✅ 代理开关控制
   - ✅ HTTP/HTTPS 代理
   - ✅ SOCKS 代理支持

6. **模板系统**
   - ✅ Jinja2 模板
   - ✅ Telegram Markdown 模板
   - ✅ Telegram HTML 模板
   - ✅ Slack JSON 模板
   - ✅ 状态判断（firing/resolved）

7. **日志系统**
   - ✅ 文件日志输出
   - ✅ 日志轮转
   - ✅ 控制台日志
   - ✅ 详细错误日志

8. **服务管理**
   - ✅ 优雅关闭
   - ✅ 多进程支持
   - ✅ 启动脚本
   - ✅ systemd 支持

### ⚠️ 需要注意的差异

1. **告警格式处理**
   - 旧代码：会提取 `values.B` 或 `valueString` 中的值作为"当前值"
   - 新代码：模板可以直接访问所有字段，包括 `values` 和 `valueString`
   - **建议**：在模板中使用 `{{ alert.values.B }}` 或通过 Jinja2 处理 `valueString`

2. **路由逻辑**
   - 旧代码：硬编码的条件判断（如 `if severity == '灾难'`）
   - 新代码：通过 YAML 配置路由规则，更灵活
   - **迁移**：将旧代码的条件判断转换为路由规则

3. **多 Webhook 支持**
   - 旧代码：Mango receiver 有多个 webhook URL
   - 新代码：需要创建多个 channel，每个对应一个 webhook URL
   - **示例**：见 `config-alertmanager-example.yaml`

4. **告警聚合**
   - 旧代码：`webhook-telegram.py` 中有告警聚合逻辑（合并相同 monitor_name 和 project）
   - 新代码：不处理聚合，每个告警独立处理
   - **说明**：聚合应该在 Alertmanager 层面处理（group_by）

### 🔄 迁移建议

#### 1. 路由规则迁移

**旧代码** (`webhook_nginx_8081.py`):
```python
if alert_dict.get('severity') == '灾难':
    send_to_telegram(alert_dict)
if 'environment' in alert_dict:
    send_to_slack(alert_dict)
```

**新配置** (`config.yaml`):
```yaml
routing:
  - match:
      severity: "灾难"
    send_to: ["tg_disaster"]
  
  - match:
      environment: ".*"  # 存在 environment 标签
    send_to: ["slack_main"]
```

#### 2. send_resolved 配置

**Alertmanager 配置**:
```yaml
- name: "prod_ebpay_jenkins_alarm"
  webhook_configs:
  - send_resolved: false
```

**alert-router-py 配置**:
```yaml
channels:
  prod_ebpay_jenkins_alarm:
    send_resolved: false  # 只发送 firing，不发送 resolved
```

#### 3. 多 Webhook 迁移

**Alertmanager** (Mango receiver 有 3 个 webhook):
```yaml
- name: mango
  webhook_configs:
  - url: 'http://10.8.64.57:8081/webhook/'
  - url: 'http://10.104.166.1:31833/api/v1/dc/'
  - url: 'http://10.108.222.114:31800/v1/prometheus/dc'
```

**alert-router-py** (需要创建 3 个 channel):
```yaml
channels:
  mango_webhook1:
    webhook_url: "http://10.8.64.57:8081/webhook/"
  mango_webhook2:
    webhook_url: "http://10.104.166.1:31833/api/v1/dc/"
  mango_webhook3:
    webhook_url: "http://10.108.222.114:31800/v1/prometheus/dc"

routing:
  - default: true
    send_to: ["mango_webhook1", "mango_webhook2", "mango_webhook3"]
```

## 兼容性总结

✅ **完全兼容**：
- 告警格式解析
- 时间转换
- 代理支持
- 模板渲染
- 路由匹配（包括正则）

✅ **新增功能**：
- send_resolved 控制
- 更灵活的路由配置
- 更好的错误处理
- 日志系统

⚠️ **需要调整**：
- 多 webhook 需要创建多个 channel
- 告警聚合逻辑需要在 Alertmanager 层面处理
- 模板中访问 values/valueString 的方式

## 测试建议

1. **功能测试**：
   - 测试 firing 告警发送
   - 测试 resolved 告警发送
   - 测试 send_resolved: false 的渠道
   - 测试路由规则匹配

2. **兼容性测试**：
   - 对比新旧代码的输出格式
   - 验证时间转换是否正确
   - 验证模板渲染是否一致

3. **性能测试**：
   - 并发请求测试
   - 大量告警处理测试

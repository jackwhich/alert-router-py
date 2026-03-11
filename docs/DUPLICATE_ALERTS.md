# 告警重复（同一条告警收到两条）排查与处理

## 为什么同一条告警会收到 2 条？

常见原因如下。

### 1. Grafana 侧重复投递（最常见）

- **原因**：Grafana 的「通知策略」里，同一条告警可能被多个「路由」命中，每个路由都配置了指向同一 webhook 的「联系点」，导致同一事件触发多次 HTTP 请求。
- **表现**：告警和恢复各收到 2 条内容完全一致的消息，时间戳相同或非常接近。
- **处理**：
  - 在 Grafana：**Alerting → Contact points** 中确认同一个 webhook URL 只在一个联系点中使用；**Alerting → Notification policies** 中检查是否有多个策略/路由都指向该联系点，且会匹配到同一条规则（如「台湾-EBPay-生产」下的 nginx 告警）。
  - 合并重复的路由或联系点，保证每条告警只命中一个「发送到该 webhook」的路径。
- **同时**：启用本项目的 **Grafana 去重**（见下），即使 Grafana 多发一次，也只会下发一条到群聊。

### 2. 同一 payload 中重复告警条目

- **原因**：极少数情况下，Grafana 在一次 webhook 的 `alerts` 数组里包含两条相同或高度相似的告警（相同 fingerprint 或相同关键 labels）。
- **处理**：启用 **Grafana 去重** 后，第二条会在发送前被过滤，只发第一条。

### 3. 多实例/多副本重复接收

- **原因**：若 alert-router 以多进程/多实例部署，且上游（Grafana/Alertmanager）对同一告警发了多份请求到不同实例，每个实例都会发一条。
- **处理**：Grafana/Alertmanager 的 webhook 只配置一个 URL，指向负载均衡后的单一入口；或保持单实例/单 worker 部署。当前 Grafana 去重为进程内缓存，多实例间不去重。

---

## Grafana 去重配置（推荐开启）

项目内置 **Grafana 告警去重**：同一告警（相同 fingerprint + 状态）在短时间窗口内只发送一次，可避免上述重复。

在 `config.yaml` 中：

```yaml
grafana_dedup:
  enabled: true
  ttl_seconds: 90        # 同一 fingerprint+状态 在 90 秒内只发一次
  clear_on_resolved: true # 恢复后清理 key，下次再 firing 会再发
```

- **enabled**：设为 `true` 启用，`false` 关闭。
- **ttl_seconds**：去重窗口（秒）。同一告警（相同 fingerprint + firing/resolved）在此时间内只发第一条，后续跳过。建议 60–120。
- **clear_on_resolved**：恢复（resolved）后是否从缓存中移除该 key，以便下次再触发 firing 时能再次发送。

启用后，若日志中出现「Grafana 去重：同一条告警在窗口内已发送过，跳过」，说明重复已被拦截。

---

## 小结

| 现象           | 优先排查                         | 建议配置           |
|----------------|----------------------------------|--------------------|
| 告警/恢复各 2 条 | Grafana 通知策略/联系点是否重复 | 开启 `grafana_dedup` |
| 仅偶发 2 条     | 同上 + 是否多实例/多 worker     | 同上 + 单入口/单实例 |

在 Grafana 侧收窄重复投递 + 在 alert-router 开启 Grafana 去重，可最大程度避免「同一条告警收到两条」的问题。

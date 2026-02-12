# Telegram 发送失败排查（含图片 400）

## 为什么图片发不出去 / 报 400 Bad Request？

可能原因如下，按常见程度排序。

### 1. 趋势图不是有效 PNG（sendPhoto 400）

- **原因**：Prometheus/Grafana 绘图超时、无数据或异常时，可能返回空字节或非 PNG 内容（如错误页 HTML），Telegram 会拒收并返回 400。
- **处理**：当前逻辑已做校验：仅当内容长度 ≥ 100 且文件头为 PNG 魔数时才走 `sendPhoto`，否则自动改为发文本。部署最新代码即可。
- **自查**：若仍先尝试发图再 400，请确认线上已拉取包含「PNG 魔数校验」的版本。

### 2. chat_id 或 Bot 权限（sendPhoto / sendMessage 都 400）

- **原因**：`chat_id` 填错、Bot 未加入该群/频道、或 Bot 被禁用，两种接口都会 400。
- **处理**：
  - 确认 `config.yaml` 里该渠道的 `chat_id` 为数字或 `-100xxxxxxxxxx` 形式（群组），或 `@channel`（频道）。
  - 确认 Bot 已加入目标群/频道，且未被移除或禁用。
- **自查**：在日志中查看是否有 **`Telegram API 响应说明:`**，后面会写具体原因（如 chat not found、chat_id invalid）。

### 3. 消息/caption 的 HTML 解析错误（400 + “can't parse entities”）

- **原因**：使用 `parse_mode=HTML` 时，若文案里出现未转义的 `<`、`>`、`&`，Telegram 会报 400。
- **处理**：当前逻辑在收到 400 且使用了 parse_mode 时，会自动用**纯文本**再发一次（去掉 parse_mode）。若重试成功，说明是 HTML 解析问题，后续可在模板里对变量做 HTML 转义（`&` → `&amp;`，`<` → `&lt;`，`>` → `&gt;`）。
- **自查**：日志里出现 **「尝试以纯文本重发（去掉 parse_mode）」** 且第二次发送成功，即属此类。

### 4. 部署的不是最新代码（看不到具体原因）

- 若日志里**没有**「Telegram API 响应说明」这一行，多半是未部署包含该逻辑的版本。
- **处理**：拉取最新代码并重启服务，再触发一次告警。新的日志会打出 Telegram 返回的 `description`，便于精确定位是上述哪一种。

---

## 建议排查顺序

1. **确认已部署最新代码**，再复现一次，看是否出现「Telegram API 响应说明」和「尝试以纯文本重发」。
2. 若有 **Telegram API 响应说明**，根据其内容判断：chat 相关 → 查 `chat_id` 与 Bot 权限；photo/file 相关 → 多为图片无效，当前版本会 fallback 文本；parse 相关 → 已自动重试纯文本。
3. 检查 `config.yaml` 中该渠道的 `bot_token`、`chat_id` 是否正确，且 Bot 仍在群/频道内。

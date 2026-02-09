import logging
import json
import requests
import re
from flask import Flask, request, jsonify
from datetime import datetime
from pytz import timezone, utc

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

status_dict = {
    "firing": {"状态": "❌❌❌❌ 告警"},
    "resolved": {"状态": "✅✅✅✅ 恢复"}
}

# 代理设置（如不需要可设置为 None）
proxies = { "http": "http://10.8.16.64:13080", "https": "http://10.8.16.64:13080"}

# 设置北京时间
beijing_tz = timezone("Asia/Shanghai")

# 时间转换函数（精确处理毫秒和时区）
def convert_to_beijing_time(utc_time_str):
    try:
        # 尝试解析 %Y-%m-%dT%H:%M:%S.%fZ 格式的时间（例如：2025-03-28T00:30:15.418Z）
        try:
            utc_time = datetime.strptime(utc_time_str, "%Y-%m-%dT%H:%M:%S.%fZ")
        except ValueError:
            # 如果解析失败，尝试解析 %Y-%m-%d %H:%M:%S.%f +0000 UTC 格式的时间（例如：2025-03-28 00:30:15.418 +0000 UTC）
            utc_time = datetime.strptime(utc_time_str, "%Y-%m-%d %H:%M:%S.%f +0000 UTC")

        # 设置为 UTC 时区
        utc_time = utc.localize(utc_time)
        
        # 将时间转换为北京时间（UTC+8）
        beijing_time = utc_time.astimezone(beijing_tz)
        
        # 返回格式化后的时间
        return beijing_time.strftime("%Y-%m-%d %H:%M:%S")
    except Exception as e:
        logging.error(f"时间转换失败: {e}")
        return "未知时间"

# 替换描述中的时间（严格匹配不破坏原有格式）
def replace_times_in_description(description):
    try:
        # 精确匹配时间部分的正则表达式
        time_pattern = r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} \+0000 UTC)"
        
        # 定义替换函数
        def replace_match(match):
            original_time = match.group(0)
            beijing_time = convert_to_beijing_time(original_time)
            return beijing_time
        
        # 使用正则替换所有匹配项
        updated_description = re.sub(time_pattern, replace_match, description)
        return updated_description
    except Exception as e:
        logging.error(f"替换时间失败: {e}")
        return description

def send_to_slack(content):
    logging.info(f"send_to_slack数据内容日志:\n{json.dumps(content, indent=2, ensure_ascii=False)}")
    request_header = {"Content-Type": "application/json; charset=UTF-8"}
    push_url = 'https://hooks.slack.com/services/YOUR_WORKSPACE/YOUR_CHANNEL/YOUR_TOKEN'  # 请替换为实际的 Slack Webhook URL

    status_key = content.get("status", "")
    status_info = status_dict.get(status_key, {"状态": "未知"})

    labels = content.get("labels", {})
    annotations = content.get("annotations", {})

    # 处理 description 并替换时间
    description = annotations.get("description", "")
    description = replace_times_in_description(description)  # 仅替换时间部分

    # 处理告警触发/恢复时间（startsAt/endsAt）
    starts_at = convert_to_beijing_time(content.get("startsAt", "未知时间"))
    ends_at = convert_to_beijing_time(content.get("endsAt", "未知恢复时间"))

    # 组装 Slack 消息
    text_lines = [
        f"{status_info.get('状态', '未知状态')}",
        f"*告警时间*: `{starts_at}`" if status_key == "firing" else f"*恢复时间*: `{ends_at}`",
        "",
        f"*告警项*: `{labels.get('alertname', '未知告警')}`",
        f"*服务名称*: `{labels.get('service_name', '未知应用')}`",
        f"*告警类别*: `{labels.get('category', '未知分类')}`",
#        f"*environment*: `{labels.get('environment', '未知环境')}`",
        f"*等级*: `{labels.get('severity', '未知级别')}`",
    ]

    # 添加告警详情（已替换时间）
    if description:
        text_lines.append("\n*告警详情*：")
        text_lines.append(description)

    # 添加 @mention
    mention = annotations.get("mention", "@默认用户")
    text_lines.append(f"\n{mention}")

    # 生成最终的 Slack 消息
    push_slack_data = {
        "text": "\n".join(text_lines),
        "username": "平台健康度告警"
    }

    logging.info(f"发送到 Slack 的数据:\n{json.dumps(push_slack_data, indent=2, ensure_ascii=False)}")

    try:
        res = requests.post(url=push_url, headers=request_header, data=json.dumps(push_slack_data), proxies=proxies)
        res.raise_for_status()
        logging.info(f"Slack返回响应:\n{res.text}")
    except requests.exceptions.RequestException as e:
        logging.error(f"Slack 发送请求失败: {e}")

@app.route("/webhook", methods=["POST"])
def alertmanager_webhook():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid JSON"}), 400

        logging.info(f"收到 Alertmanager 告警: {json.dumps(data, indent=2, ensure_ascii=False)}")
        
        for alert in data.get("alerts", []):
            send_to_slack(alert)  
        
        return jsonify({"status": "success"})
    except Exception as e:
        logging.error(f"处理 Webhook 失败: {e}")
        return jsonify({"error": "Internal Server Error"}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=9081, debug=True)
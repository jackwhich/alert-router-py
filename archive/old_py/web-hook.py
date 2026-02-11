#!/usr/bin/python
# -*- coding: utf-8 -*-

import json
import requests
import logging
from flask import request, Flask    #flask模块
from datetime import datetime,timezone,timedelta

TELEGRAM_CHAT_ID = "YOUR_CHAT_ID"  # 请替换为实际的 Telegram Chat ID
TELEGRAM_BOT_TOKEN = "YOUR_BOT_TOKEN:YOUR_BOT_SECRET"  # 请替换为实际的 Telegram Bot Token
TELEGRAM_PROXY_URL = "http://10.8.16.64:13080"
proxies = {"http": TELEGRAM_PROXY_URL, "https": TELEGRAM_PROXY_URL}

# 设置日志配置
logging.basicConfig(filename='/var/log/mango-hook.log', level=logging.INFO, format='%(asctime)s %(levelname)s:%(message)s')

#Flask通用配置
app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False

status_dict = {
    "firing": {"❌❌❌❌ 状态": "告警"},
    "resolved": {"✅✅✅✅  状态": "恢复"}
}

# 发送告警到 Telegram（支持代理）
def send_to_telegram(content):
    try:
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            logging.error("Telegram 配置缺失：请设置 TELEGRAM_BOT_TOKEN 与 TELEGRAM_CHAT_ID")
            return

        # 将 content 组织成文本（与 send_to_mango 相同风格）
        data = ""
        for k in content:
            data += f"{k}: {content.get(k)}\n"

        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        headers = {"Content-Type": "application/json; charset=UTF-8"}
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": data,
        }

        logging.info("发送 Telegram 数据：%s", json.dumps(payload, ensure_ascii=False))
        resp = requests.post(url, headers=headers, data=json.dumps(payload), timeout=15, proxies=proxies)

        try:
            resp_json = resp.json()
        except ValueError:
            logging.error("Telegram 返回非 JSON：%s", resp.text)
        else:
            if not resp.ok or not resp_json.get("ok", False):
                logging.error("Telegram 发送失败：HTTP %s, body=%s", resp.status_code, json.dumps(resp_json, ensure_ascii=False))
            else:
                logging.info("Telegram 返回：%s", json.dumps(resp_json, ensure_ascii=False))
    except Exception as e:
        logging.exception("发送 Telegram 失败：%s", e)


# 发送告警到Mango
def send_to_mango(content):
    logging.info(f"send_to_mango数据内容日志: {content}")
    data = ''
    for i in content:
        data += i + ': ' + content.get(i) + '\n'
    # 记录处理后的日志
    logging.info(f"处理日志信息: {data}")
    request_header = {"content-type": "application/json; charset=UTF-8","Authorization": "YOUR_MANGO_AUTH_TOKEN"}  # 请替换为实际的 Mango 认证 Token
    push_tx_data = {"targetname": "YOUR_TARGET_NAME","text": data,"chatType":"2","model": "1"}  # 请替换为实际的目标名称
    push_url = 'https://trobot.ymtio.com/api/robot/YOUR_USERCODE:YOUR_SECRET/sendmessage_v2'  # 请替换为实际的 Mango Webhook URL
    push_tx_data = json.dumps(push_tx_data)
    #logging.info(push_tx_data)
    #logger.debug('告警数据：{}'.format(push_tx_data))
    res= requests.post(url=push_url, headers=request_header, data=push_tx_data)
    #处理返回的数据
    try:
        json_data = res.json()
    except ValueError:
        logging.error('无法解析的响应数据: %s', res.text)
    else:
        logging.info('Mango数据返回响应：\n%s', json.dumps(json_data, indent=4, ensure_ascii=False))


@app.route('/webhook/', methods=['POST'])
def IssueCreate():
    data_dict = json.loads(request.data)
    logging.info(f"求数据解析为json: {data_dict}")
    try:
        alerts_l = data_dict['alerts']
        dict_last = {}
        for alert in alerts_l:
            dict_new = {}
            utc_dt = datetime.strptime(alert.get('startsAt'), '%Y-%m-%dT%H:%M:%S.%fZ')
            cst_time =utc_dt.astimezone(timezone(timedelta(hours=16))).strftime("%Y-%m-%d %H:%M:%S")

            alert_status = data_dict.get('status')
            dict_new.update(status_dict[alert_status])
            dict_new.update({"时间": cst_time})
            dict_new.update(**alert.get('labels'))
            dict_new["summary"] = "%s" % alert.get('annotations').get('summary')
            for pop_name in ["prometheus", "id", "image", "uid", "metrics_path", "endpoint", "job", "service", "name"]: 
                dict_new.pop(pop_name,100)

            for key1 in dict_new:
                if key1 in dict_last.keys():
                    if key1 != "时间" and key1 != "summary":
                        if dict_new.get(key1) not in dict_last.get(key1):
                                dict_last[key1] = dict_new.get(key1)+"\n\t"+dict_last.get(key1)
                else:
                    dict_last = dict_new
        logging.info(f"发送告警数据日志: {dict_last}")
        send_to_mango(dict_last)
        # 同步发送到 Telegram
        send_to_telegram(dict_last)

    except Exception as e:
        logging.error(e)

    return "OK"

if __name__ == '__main__':
    app.run(debug = False, host = '0.0.0.0', port = 8082)
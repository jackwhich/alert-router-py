#!/usr/bin/python
# -*- coding: utf-8 -*-

import json
import requests,logging
from flask import request, Flask    #flask模块
from datetime import datetime,timezone,timedelta

proxies = { "http": "http://10.8.16.64:13080", "https": "http://10.8.16.64:13080"}


# 设置日志配置
logging.basicConfig(filename='/var/log/mango-hook.log', level=logging.INFO, format='%(asctime)s %(levelname)s:%(message)s')

#Flask通用配置
app = Flask(__name__)
app.url_map.strict_slashes = False  # 允许 /webhook 与 /webhook/ 都匹配，避免 308
app.config['JSON_AS_ASCII'] = False

status_dict = {
    "firing": {"❌❌❌❌ 状态": "告警"},
    "resolved": {"✅✅✅✅  状态": "恢复"}
}

def SendMango2(content):
    data = {'text': ''}
    logging.info(f"SendMango数据内容日志: {content}")
    for i in content:
        data['text'] += i + ': ' + content.get(i) + '\n'
    logging.info("数据格式: %s, %s", data, str(type(data)))
    res = requests.get("http://dc-tw-ebpay-pro-alarm.zfit999.com/alarm/grafana/pushToSlack",params=data)
    logging.info("响应状态类型: %s, URL: %s", res.text, res.url)

# dc_telegram 告警发送
def send_to_telegram(content):
    logging.info("send_to_telegram 数据内容日志: %s", content)

    # 组装文本
    telegram_monitr = ""
    for key, value in content.items():
        telegram_monitr += f"{key}: {value}\n"

    chatid = 'YOUR_CHAT_ID'  # 请替换为实际的 Telegram Chat ID
    auth_token = 'YOUR_BOT_TOKEN:YOUR_BOT_SECRET'  # 请替换为实际的 Telegram Bot Token
    telegram_url = f'https://api.telegram.org/bot{auth_token}/sendMessage'
    payload = {"chat_id": chatid, "text": telegram_monitr}

    try:
        logging.info("Telegram 请求 URL: %s", telegram_url)
        logging.info("Telegram 请求 payload: %s", payload)
        logging.info("Telegram 代理: %s", proxies)

        res = requests.post(
            url=telegram_url,
            data=payload,          # Telegram 支持表单
            timeout=10,            # 加超时避免卡线程
            verify=False,
            proxies=proxies
        )

        logging.info("Telegram响应状态码: %s", res.status_code)
        logging.info("Telegram响应内容: %s", res.text)
    except Exception as e:
        logging.exception("发送 Telegram 失败: %s", e)

def SendMango(content):
    data = {'text': ''}
    logging.info(f"SendMango数据内容日志: {content}")
    for i in content:
        data['text'] += i + ': ' + content.get(i) + '\n'
    logging.info("数据格式: %s, %s", data, str(type(data)))
    res = requests.get("http://risk-manager.ebpay.org:30040/risk-manager/alarm",params=data)
    logging.info("响应状态类型: %s, URL: %s", res.text, res.url)


def Send_grafana(content):
    data = {'text': ''}
    logging.info(f"Send_grafana数据内容日志: {content}")
    for i in content:
        data['text'] += i + ': ' + content.get(i) + '\n'
    logging.info("数据格式: %s, %s", data, str(type(data)))
    res = requests.post("http://10.104.166.1:31833/api/v1/ebpay",data=data)
    logging.info("响应状态类型: %s, URL: %s", res.text, res.url)


# 发送告警到Mango
def send_to_mango(content):
    data = ''
    for i in content:
        data += i + ': ' + content.get(i) + '\n'
    request_header = {"content-type": "application/json; charset=UTF-8","Authorization": "YOUR_MANGO_AUTH_TOKEN"}  # 请替换为实际的 Mango 认证 Token
    push_tx_data = {"targetname": "YOUR_TARGET_NAME","text": data,"chatType":"2","model": "1"}  # 请替换为实际的目标名称
    push_url = 'https://trobot.ymtio.com/api/robot/YOUR_USERCODE:YOUR_SECRET/sendmessage_v2'  # 请替换为实际的 Mango Webhook URL
    push_tx_data = json.dumps(push_tx_data)
    #logger.debug('告警数据：{}'.format(push_tx_data))
    res= requests.post(url=push_url, headers=request_header, data=push_tx_data)
    #处理返回的数据
    try:
        json_data = res.json()
    except ValueError:
        logging.error('无法解析的响应数据: %s', res.text)
    else:
        logging.info('Mango数据返回响应: \n%s', json.dumps(json_data, indent=4, ensure_ascii=False))


@app.route('/webhook', methods=['POST'])
def IssueCreate():
    data_dict = request.get_json(force=True)
    #WriteLogFile(data_dict)
    #print (data_dict)
    try:
        alerts_l = data_dict['alerts']
        dict_last = {}
        for alert in alerts_l:
            dict_new = {}
            utc_dt = datetime.strptime(alert.get('startsAt'), '%Y-%m-%dT%H:%M:%S.%fZ')
            cst_time = utc_dt.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")

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
                        if dict_new.get(key1) not in dict_last.get(key1) and dict_last.get('monitor_name') == dict_new.get('monitor_name') and dict_last.get('project') == dict_new.get('project'):
                                dict_last[key1] = dict_new.get(key1)+"\n\t"+dict_last.get(key1)
                        elif dict_new.get(key1) not in dict_last.get(key1) and 'monitor_name' not in dict_last.keys():
                                dict_last[key1] = dict_new.get(key1)+"\n\t"+dict_last.get(key1)
                else:
                    dict_last = dict_new
        #print(dict_last)
        logging.info(f"发送告警数据日志: {dict_last}")
        # 先发 Telegram，便于快速确认是否进入到这里；其余通道失败也不影响 TG
        logging.info("准备发送 Telegram 告警(Pre): %s", dict_last)
        try:
            send_to_telegram(dict_last)
            logging.info("已调用 send_to_telegram()")
        except Exception as e:
            logging.exception("调用 send_to_telegram 失败: %s", e)
        #WriteLogFile(dict_last)
        if "task_summary" in dict_last:
            try:
                send_to_mango(dict_last)
            except Exception as e:
                logging.exception("send_to_mango 失败: %s", e)
            #send_to_telegram(dict_last)
        else:
            try:
                send_to_mango(dict_last)
            except Exception as e:
                logging.exception("send_to_mango 失败: %s", e)
            try:
                SendMango2(dict_last)
            except Exception as e:
                logging.exception("SendMango2 失败: %s", e)
            #send_to_telegram(dict_last)
        #SendMango(dict_last)

    except Exception as e:
        logging.exception("IssueCreate 处理异常: %s", e)

    return "OK"

if __name__ == '__main__':
    app.run(debug = False, host = '0.0.0.0', port = 8081)
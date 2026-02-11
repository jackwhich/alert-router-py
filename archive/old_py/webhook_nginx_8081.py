# 导入所需的库
import json
import logging
from datetime import datetime, timezone, timedelta
from dateutil import parser
from flask import Flask, request
import requests
import re

#定义状态字典，用于在警告消息中显示状态
status_dict = {
    "firing": {"❌❌❌❌ 状态": "告警"},
    "resolved": {"✅✅✅✅ 状态": "恢复"}
}

proxies = { "http": "http://10.8.16.64:13080", "https": "http://10.8.16.64:13080"}

# 配置日志
logging.basicConfig(filename='/tmp/mango.log', level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y/%m/%d %H:%M:%S')

# 初始化 Flask
app = Flask(__name__)
app.url_map.strict_slashes = False 
app.config['JSON_AS_ASCII'] = False

#定义函数来格式化警告消息
def format_alert(alert):
    dict_new = {}
    # 把开始时间从UTC转化为CST时间
    utc_dt = parser.parse(alert.get('startsAt'))
    cst_time = utc_dt.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
    #获取警告状态
    alert_status = alert.get('status')
    #更新警告状态
    dict_new.update(status_dict[alert_status])
    #增加时间信息
    dict_new.update({"时间": cst_time})
    #增加标签信息
    dict_new.update(alert.get('labels'))
    try:
    # 如果'B'在values字段中存在，则将当前值增加到警告值信息
        dict_new.update({"当前值": alert.get('values').get('B')})
    except AttributeError as e:
    #如果values字段获取不到，则在valueString字段中获取B对应的当前值并加入告警信息
        match = re.search(r"var='B' labels={.*?} value=(\d+)", alert.get('valueString'))
        if match:
            value = int(match.group(1))
            dict_new.update({"当前值": value})
        else:
            dict_new.update({"当前值": "获取当前值异常,请检查es是否故障"})
    return dict_new

#定义函数向告警接收端发送警告
def send_alert(alert_dict):
    #定义请求头部
    request_header = {
        "content-type": "application/json; charset=UTF-8",
        "Authorization": "YOUR_MANGO_AUTH_TOKEN"  # 请替换为实际的 Mango 认证 Token
    }
    #定义请求URL
#    push_url = 'https://trobot.ymtio.com/api/robot/usercod_60007578:56b1a6161d6c4b51aaa94ad4fb5b1398/sendmessage_v2'
    push_url = 'https://trobot.ymtio.com/api/robot/YOUR_USERCODE:YOUR_SECRET/sendmessage_v2'  # 请替换为实际的 Mango Webhook URL
    #定义要发送的数据
    push_tx_data = {
        "targetname": "YOUR_TARGET_NAME",  # 请替换为实际的目标名称
        "text": '\n'.join([f'{k}: {v}' for k, v in alert_dict.items()]),
        "chatType": "2",
        "model": "1"
    }
    #把数据转换为JSON格式
    push_tx_data = json.dumps(push_tx_data)
    #发送POST请求
    res = requests.post(url=push_url, headers=request_header, data=push_tx_data)
    #处理返回的数据
    try:
        json_data = res.json()
    except ValueError:
        logging.error('无法解析的响应数据: %s', res.text)
    else:
        logging.info('Mango数据返回响应：\n%s', json.dumps(json_data, indent=4, ensure_ascii=False))

# dc_telegram 告警发送
def send_to_telegram(alert_dict):
    logging.info(f"send_to_telegram数据内容日志: {alert_dict}")
    data = ''
    for i in alert_dict:
        data += i + ': ' + str(alert_dict.get(i)) + '\n'

    #chatid='-4162282768'
    chatid='YOUR_CHAT_ID'  # 请替换为实际的 Telegram Chat ID
    auth_token = 'YOUR_BOT_TOKEN:YOUR_BOT_SECRET'  # 请替换为实际的 Telegram Bot Token 
    telegram_url='https://api.telegram.org/bot' + auth_token + '/sendMessage'
    push_data = {
        "chat_id": chatid,
        "text": data  # 注意这里应该发送的是formatted data
    }

    requests.post(url=telegram_url, data=push_data, verify=False, proxies=proxies)

def send_to_telegram_v2(alert_dict):
    logging.info(f"send_to_telegram_v2数据内容日志: {alert_dict}")
    data = ''
    for i in alert_dict:
        data += i + ': ' + str(alert_dict.get(i)) + '\n'

    chatid = 'YOUR_CHAT_ID'  # 请替换为实际的 Telegram Chat ID
    auth_token = 'YOUR_BOT_TOKEN:YOUR_BOT_SECRET'  # 请替换为实际的 Telegram Bot Token
    telegram_url = 'https://api.telegram.org/bot' + auth_token + '/sendMessage'
    push_data = {
        "chat_id": chatid,
        "text": data
    }

    requests.post(url=telegram_url, data=push_data, verify=False, proxies=proxies)

#slack发送告警
#定义函数向告警向slakc发送警告
def send_to_slack(alert_dict):
    #定义请求头部
    request_header = {
        "content-type": "application/json; charset=UTF-8"
    }
    #定义请求URL
    push_url = 'https://hooks.slack.com/services/YOUR_WORKSPACE/YOUR_CHANNEL/YOUR_TOKEN'  # 请替换为实际的 Slack Webhook URL
    #定义要发送的数据
    push_slack_data = {
        "text": '\n'.join([f'{k}: {v}' for k, v in alert_dict.items()] + [f'<@U070YD6L19R>'])
    }
    #把数据转换为JSON格
    push_slack_data = json.dumps(push_slack_data)
    #发送POST请求
    res = requests.post(url=push_url, headers=request_header, data=push_slack_data, proxies=proxies)
    #处理返回的数据
    try:
        slack_json_data = res.json()
    except ValueError:
        logging.error('Slack无法解析的响应数据: %s', res.text)
    else:
        logging.info('Slack数据返回响应：\n%s', json.dumps(slack_json_data, indent=4, ensure_ascii=False))

# 判断是否为主程序，如果是则执行以下代码
if __name__ == '__main__':
    # Flask路由设置
    @app.route('/webhook/', methods=['POST'])
    def handle_webhook():
        #解析请求数据
        data_dict = json.loads(request.data)
        #记录数据字典
        logging.info("收到的数据字典: %s", data_dict)
        try:
            #从数据字典中获取警告信息
            alerts = data_dict['alerts']
            #处理每一个警告
            for alert in alerts:
                #格式化警告
                alert_dict = format_alert(alert)
                #发送警告
                send_alert(alert_dict)
                send_to_telegram_v2(alert_dict)
                #如果警告等级是灾难，才发送到telegram
                if alert_dict.get('severity') == '灾难':
                    send_to_telegram(alert_dict)
                # 只要 `environment` 变量存在，就发送到 Slack
                if 'environment' in alert_dict:
                    send_to_slack(alert_dict) 
        except KeyError as e:
            # 如果捕获到KeyError，则记录错误信息
            logging.error('数据格式错误: 没有 "alerts" 字段. 错误信息: %s', str(e))
        #返回确认信息
        return "OK"

    # 运行 Flask 服务器
    app.run(debug = False, host = '0.0.0.0', port = 8081)
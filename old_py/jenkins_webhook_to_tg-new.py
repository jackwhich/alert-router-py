#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import logging
import html
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import requests
from requests.auth import HTTPBasicAuth
from flask import Flask, request, jsonify

# ========= 写死的基础配置（按你要求） =========
TG_BOT_TOKEN = "YOUR_BOT_TOKEN:YOUR_BOT_SECRET"  # 请替换为实际的 Telegram Bot Token
TG_CHAT_ID = "YOUR_CHAT_ID"  # 请替换为实际的 Telegram Chat ID


# 只给 TG 用的代理
TG_PROXIES = {
    "http":  "http://10.8.16.64:13080",
    "https": "http://10.8.16.64:13080",
}

# Jenkins API 必须直连：不使用代理（并且不读取环境变量代理）
JENKINS_PROXIES = None  # 或 {}


# Jenkins 若需要 BasicAuth（不需要就保持 None）
JENKINS_USER = "YOUR_JENKINS_USER"  # 请替换为实际的 Jenkins 用户名
JENKINS_TOKEN = "YOUR_JENKINS_TOKEN"  # 请替换为实际的 Jenkins API Token

# 目标错误关键字：命中才发 TG
TARGET_ERROR = "Failed to execute goal org.simplify4u.plugins:pgpverify-maven-plugin"

# 回溯最近多少次 build（找 commit 对应的 build number）
RECENT_BUILDS_TO_SCAN = 60

# ========= 日志 =========
logging.basicConfig(
    filename="/tmp/jenkins_webhook_tg.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y/%m/%d %H:%M:%S",
)

app = Flask(__name__)


# ----------------- Telegram -----------------
def tg_send(text: str) -> Dict[str, Any]:
    """发送 HTML 消息到 Telegram（走代理）"""
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    data = {
        "chat_id": TG_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    r = requests.post(url, data=data, timeout=10, proxies=TG_PROXIES)
    r.raise_for_status()
    return r.json()


# ----------------- Jenkins HTTP -----------------
def jenkins_get(url: str) -> requests.Response:
    """
    Jenkins API 请求：强制直连，不走任何代理（也不读取环境变量 proxy）
    """
    auth = HTTPBasicAuth(JENKINS_USER, JENKINS_TOKEN) if (JENKINS_USER and JENKINS_TOKEN) else None
    s = requests.Session()
    s.trust_env = False  # ✅ 不读取 http_proxy/https_proxy
    return s.get(url, timeout=15, auth=auth)


def build_jenkins_job_url(jenkins_base: str, job_name: str) -> str:
    """
    folder job：
    uat/adminmanager-gpg -> http://host:8080/job/uat/job/adminmanager-gpg/
    """
    parts = [p for p in (job_name or "").strip("/").split("/") if p]
    path = "/".join([f"job/{p}" for p in parts])
    return f"{jenkins_base}/{path}/"


# ----------------- Helpers -----------------
def normalize_build_status(raw: Optional[str]) -> str:
    if not raw:
        return "UNKNOWN"
    s = str(raw).upper()
    if "SUCCESS" in s:
        return "SUCCESS"
    if "FAIL" in s:
        return "FAILURE"
    if "ABORT" in s or "CANCEL" in s:
        return "ABORTED"
    return s


def job_env_and_task(job_name: str) -> Tuple[str, str]:
    """
    根据 jenkins_job 判断环境与任务
    uat/adminmanager-gpg -> (UAT, adminmanager-gpg)
    """
    raw = (job_name or "").strip("/")
    if not raw:
        return ("未知", "未知")

    parts = raw.split("/", 1)
    prefix = parts[0]
    task = parts[1] if len(parts) > 1 else parts[0]

    if prefix == "uat":
        env = "EB预发"
    elif prefix == "pro":
        env = "EB生产"
    elif prefix == "jp-prod-gray-ebpay":
        env = "EB生产灰度"
    else:
        env = prefix

    return env, task


# ----------------- Jenkins Data Fetch -----------------
def get_build_commit(jenkins_base: str, job_name: str, build_no: int) -> Optional[str]:
    """取某次 build 的 check_commitID 参数值"""
    job_url = build_jenkins_job_url(jenkins_base, job_name)
    api = f"{job_url}{build_no}/api/json?tree=actions[parameters[name,value]]"
    r = jenkins_get(api)
    if r.status_code != 200:
        return None

    data = r.json()
    for a in (data.get("actions") or []):
        for p in (a.get("parameters") or []):
            if (p.get("name") or "").strip() == "check_commitID":
                v = str(p.get("value") or "").strip()
                return v or None
    return None


def get_build_branch(jenkins_base: str, job_name: str, build_no: int) -> Optional[str]:
    """
    从 Jenkins build API 的 BuildData 获取分支：
    - actions[] _class == hudson.plugins.git.util.BuildData
      - lastBuiltRevision.branch[].name -> origin/master-pgpverify-error
      - buildsByBranchName 的 key 也可作为 fallback
    返回时去掉 origin/ 前缀
    """
    job_url = build_jenkins_job_url(jenkins_base, job_name)
    api = (
        f"{job_url}{build_no}/api/json?"
        f"tree=actions[_class,lastBuiltRevision[branch[name]],buildsByBranchName]"
    )
    r = jenkins_get(api)
    if r.status_code != 200:
        return None

    data = r.json()
    actions = data.get("actions") or []

    def _strip_origin(s: str) -> str:
        s = (s or "").strip()
        return s[len("origin/"):] if s.startswith("origin/") else s

    for a in actions:
        if a.get("_class") != "hudson.plugins.git.util.BuildData":
            continue

        rev = a.get("lastBuiltRevision") or {}
        branches = rev.get("branch") or []
        for b in branches:
            name = (b.get("name") or "").strip()
            if name:
                return _strip_origin(name)

        bb = a.get("buildsByBranchName") or {}
        if isinstance(bb, dict) and bb:
            for k in bb.keys():
                if k:
                    return _strip_origin(str(k))

    return None


def find_build_number_by_commit(jenkins_base: str, job_name: str, commit: str) -> Optional[int]:
    """
    优先 lastBuild/lastFailedBuild，再扫最近 N 个 build
    对每个 build 调用 get_build_commit() 比对 check_commitID
    """
    commit = (commit or "").strip()
    if not commit:
        return None

    job_url = build_jenkins_job_url(jenkins_base, job_name)
    api = f"{job_url}api/json?tree=lastBuild[number],lastFailedBuild[number],builds[number]"
    r = jenkins_get(api)
    if r.status_code != 200:
        logging.warning("Jenkins job api 请求失败: %s code=%s", api, r.status_code)
        return None

    data = r.json()
    candidates: List[int] = []

    lb = (data.get("lastBuild") or {}).get("number")
    lfb = (data.get("lastFailedBuild") or {}).get("number")
    if isinstance(lb, int):
        candidates.append(lb)
    if isinstance(lfb, int) and lfb not in candidates:
        candidates.append(lfb)

    builds = data.get("builds") or []
    for b in builds[:RECENT_BUILDS_TO_SCAN]:
        n = b.get("number")
        if isinstance(n, int) and n not in candidates:
            candidates.append(n)

    for build_no in candidates:
        b_commit = get_build_commit(jenkins_base, job_name, build_no)
        if b_commit == commit:
            return build_no

    return None


def fetch_console_text(jenkins_base: str, job_name: str, build_no: int) -> Optional[str]:
    """拉 Jenkins consoleText"""
    job_url = build_jenkins_job_url(jenkins_base, job_name)
    log_url = f"{job_url}{build_no}/consoleText"
    r = jenkins_get(log_url)
    if r.status_code == 200:
        return r.text
    return None


# ----------------- Alertmanager Parse & Format -----------------
def parse_alertmanager_payload(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    alerts = payload.get("alerts") or []
    results: List[Dict[str, Any]] = []

    for alert in alerts:
        labels = alert.get("labels", {})
        annotations = alert.get("annotations", {})

        job_name = labels.get("jenkins_job", "未知任务")
        commit = labels.get("check_commitID", "N/A")
        branch = labels.get("gitBranch", "N/A")
        instance = labels.get("instance", "N/A")  # 10.8.68.57:8080

        # Jenkins base 用告警里的 instance
        jenkins_base = f"http://{instance}" if instance != "N/A" else ""

        build_status_raw = labels.get("alert_status") or labels.get("status") or labels.get("result")
        build_status = normalize_build_status(build_status_raw)

        am_status = alert.get("status", payload.get("status", "unknown"))

        detail_url = annotations.get("url") or alert.get("generatorURL") or (build_jenkins_job_url(jenkins_base, job_name) if jenkins_base else "")

        results.append({
            "job_name": job_name,
            "build_status": build_status,
            "am_status": am_status,
            "branch": branch,
            "commit": commit,
            "instance": instance,
            "jenkins_base": jenkins_base,
            "url": detail_url,
        })

    return results


def fmt_message(items: List[Dict[str, Any]]) -> str:
    icon = "🔴"
    title = "Jenkins 构建失败（PGP签名校验失败）"
    now = (datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    
    lines = [
        f"{icon} <b>{title}</b>",
        "<b>━━━━━━━━━━━━━━</b>",
    ]

    for idx, i in enumerate(items, 1):
        job_name_raw = i.get("job_name", "N/A")
        env, task = job_env_and_task(job_name_raw)

        lines.extend([
            f"<b>{idx}) 环境:</b> <code>{html.escape(env)}</code>",
            f"<b>任务:</b> <code>{html.escape(task)}</code>",
            f"<b>Jenkins Job:</b> <code>{html.escape(job_name_raw)}</code>",
            f"<b>状态:</b> <u>{html.escape(i.get('build_status','N/A'))}</u> <i>({html.escape(i.get('am_status','N/A'))})</i>",
            f"<b>分支:</b> {html.escape(i.get('branch', 'N/A'))}",
            f"<b>Commit:</b> <code>{html.escape(i.get('commit','N/A'))}</code>",
            f"<b>Build:</b> <code>{html.escape(str(i.get('build_number','N/A')))}</code>",
            f"<b>结论:</b> <b>PGP签名校验失败</b>",
            "<b>──────────────</b>",
        ])

    lines.append(f"<b>时间:</b> {(datetime.now()).strftime('%Y-%m-%d %H:%M:%S')}")
    return "\n".join(lines)


# ----------------- Flask Webhook -----------------
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        payload = request.get_json(force=True)
        logging.info("收到告警: %s", json.dumps(payload, ensure_ascii=False))

        items = parse_alertmanager_payload(payload)
        if not items:
            return jsonify({"status": "ignored", "reason": "no alerts"}), 200

        matched_items: List[Dict[str, Any]] = []

        for it in items:
            job_name = it.get("job_name")
            commit = it.get("commit")
            jenkins_base = it.get("jenkins_base")

            if not jenkins_base or not job_name or not commit or commit == "N/A":
                continue

            # 1) 定位 build
            build_no = find_build_number_by_commit(jenkins_base, job_name, commit)
            if build_no is None:
                logging.info("未找到 commit 对应 build，忽略: job=%s commit=%s", job_name, commit)
                continue

            # 2) 从 Jenkins API 获取真实分支（覆盖 UNDEFINED）
            real_branch = get_build_branch(jenkins_base, job_name, build_no)
            if real_branch:
                it["branch"] = real_branch

            # 3) 拉日志并检测关键字：命中才发 TG
            log_text = fetch_console_text(jenkins_base, job_name, build_no)
            if not log_text:
                logging.info("拉取 consoleText 失败，忽略: job=%s build=%s", job_name, build_no)
                continue

            if TARGET_ERROR in log_text:
                it["build_number"] = build_no
                matched_items.append(it)
            else:
                logging.info("未命中 pgpverify 关键字，忽略: job=%s build=%s commit=%s", job_name, build_no, commit)

        if not matched_items:
            return jsonify({"status": "ignored", "reason": "no pgpverify keyword matched"}), 200

        msg = fmt_message(matched_items)

        # TG 发送失败也不要返回 500，避免 Alertmanager 重试打爆
        try:
            tg_send(msg)
        except Exception:
            logging.exception("TG 发送失败")
            return jsonify({"status": "tg_failed"}), 200

        return jsonify({"status": "success", "alerts": len(matched_items)}), 200

    except Exception as e:
        logging.exception("处理失败")
        # 不让 Alertmanager 重试打爆：异常也返回 200
        return jsonify({"status": "ignored", "reason": f"exception: {type(e).__name__}", "message": str(e)}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8089)
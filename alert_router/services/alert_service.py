"""
告警处理服务层

将业务逻辑从 app.py 中分离出来，使 app.py 只负责 HTTP 路由和请求处理
"""
import logging
from typing import Dict, List, Optional

from requests.exceptions import HTTPError, RequestException

from ..routing.jenkins_dedup import should_skip_jenkins_firing
from ..routing.routing import route
from ..senders.senders import send_telegram, send_webhook
from ..templates.template_renderer import render
from .image_service import ImageService
from .channel_filter import ChannelFilter
from ..adapters.alert_normalizer import normalize

logger = logging.getLogger("alert-router")


class AlertService:
    """告警处理服务"""
    
    def __init__(self, config: Dict, channels: Dict):
        """
        初始化告警服务
        
        Args:
            config: 配置字典
            channels: 渠道字典
        """
        self.config = config
        self.channels = channels
        self.channel_filter = ChannelFilter(channels)
        # 传入 channel_filter 实例，避免 ImageService 重复创建
        self.image_service = ImageService(config, channels, channel_filter=self.channel_filter)
    
    def process_webhook(self, payload: dict) -> dict:
        """
        处理 webhook 请求
        
        Args:
            payload: Webhook 请求体
            
        Returns:
            处理结果字典
        """
        alerts = normalize(payload)
        if not alerts:
            logger.warning("无法解析告警数据格式")
            return {"ok": False, "error": "无法解析告警数据格式"}

        alert_summary = ", ".join(a.get("labels", {}).get("alertname", "?") for a in alerts)
        logger.info(f"收到告警请求: {len(alerts)} 条 [{alert_summary}]")

        results = []
        for alert in alerts:
            result = self._process_single_alert(alert)
            results.extend(result)

        return {"ok": True, "sent": results}
    
    def _process_single_alert(self, alert: dict) -> List[dict]:
        """
        处理单条告警
        
        Args:
            alert: 告警对象
            
        Returns:
            处理结果列表
        """
        labels = alert.get("labels", {})
        alertname = labels.get("alertname") or "Unknown"
        alert_status = alert.get("status", "firing")

        # Jenkins 告警去重
        if should_skip_jenkins_firing(alert, labels, alert_status, self.config):
            logger.info(f"告警 {alertname} 命中 Jenkins 去重窗口，跳过重复 firing 通知")
            return [{
                "alert": alertname,
                "skipped": "jenkins 去重窗口内重复 firing",
                "alert_status": alert_status,
            }]

        # 路由到渠道（使用原始 labels，并附带内部来源用于匹配）
        source = alert.get("_source") or labels.get("_source")
        receiver = alert.get("_receiver")
        match_labels = dict(labels)
        if source:
            match_labels["_source"] = source
        if receiver:
            match_labels["_receiver"] = receiver
        target_channels = route(match_labels, self.config)
        logger.info(f"告警 {alertname} 路由到渠道: {target_channels}")

        # 构建模板上下文
        ctx = self._build_template_context(alert, labels)

        # 生成图片（如果需要）；失败或异常时仍发告警，仅不发图
        image_bytes = None
        try:
            image_bytes = self.image_service.generate_image(
                source=source,
                alert=alert,
                alert_status=alert_status,
                target_channels=target_channels,
                alertname=alertname,
            )
        except Exception as img_err:
            logger.warning(
                f"告警 {alertname} 趋势图生成异常，将仅发送文本: {img_err}",
                exc_info=True,
            )

        # 发送到各个渠道（无图时自动走纯文本）
        results = []
        sent_channels = []
        
        for channel_name in target_channels:
            result = self._send_to_channel(
                channel_name=channel_name,
                alert=alert,
                alertname=alertname,
                alert_status=alert_status,
                ctx=ctx,
                image_bytes=image_bytes,
                source=source,
            )
            results.append(result)
            
            if result.get("status") == "sent":
                sent_channels.append(channel_name)

        if sent_channels:
            channels_str = ", ".join(sent_channels)
            logger.info(
                f"告警 {alertname} 已发送到 {len(sent_channels)} 个渠道: "
                f"{channels_str} (状态: {alert_status})"
            )

        return results
    
    def _build_template_context(self, alert: dict, labels: dict) -> dict:
        """
        构建模板渲染上下文
        
        Args:
            alert: 告警对象
            labels: 告警标签
            
        Returns:
            模板上下文字典
        """
        # 安全获取 title_prefix，如果不存在则使用默认值
        defaults = self.config.get("defaults", {})
        title_prefix = defaults.get("title_prefix", "[ALERT]")
        alertname = labels.get("alertname") or "Unknown"

        return {
            "title": f"{title_prefix} {alertname}".strip(),
            "status": alert.get("status", "unknown"),
            "labels": labels,
            "annotations": alert.get("annotations", {}),
            "startsAt": alert.get("startsAt", ""),
            "endsAt": alert.get("endsAt", ""),
            "generatorURL": alert.get("generatorURL", ""),
        }
    
    def _send_to_channel(
        self,
        channel_name: str,
        alert: dict,
        alertname: str,
        alert_status: str,
        ctx: dict,
        image_bytes: Optional[bytes],
        source: str,
    ) -> dict:
        """
        发送告警到指定渠道
        
        Args:
            channel_name: 渠道名称
            alert: 告警对象
            alertname: 告警名称
            alert_status: 告警状态
            ctx: 模板上下文
            image_bytes: 图片字节（可选）
            source: 告警来源
            
        Returns:
            发送结果字典
        """
        channel = self.channels.get(channel_name)
        if not channel:
            error_msg = f"渠道不存在: {channel_name}"
            logger.warning(f"告警 {alertname}: {error_msg}")
            return {"alert": alertname, "channel": channel_name, "error": error_msg}

        # 检查渠道是否启用
        if not channel.enabled:
            logger.debug(f"告警 {alertname} 跳过已禁用的渠道: {channel_name}")
            return {"alert": alertname, "channel": channel_name, "skipped": "渠道已禁用"}

        # 检查是否发送 resolved 状态
        if alert_status == "resolved" and not channel.send_resolved:
            logger.debug(
                f"告警 {alertname} 跳过 resolved 状态（渠道 {channel_name} 配置为不发送 resolved）"
            )
            return {"alert": alertname, "channel": channel_name, "skipped": "resolved 状态已禁用"}

        try:
            # 渲染模板
            body = render(channel.template, ctx)
            logger.info(f"发送到渠道 [{channel_name}] 的内容:\n{body}")

            # 发送消息
            if channel.type == "telegram":
                # 判断是否使用图片发送
                use_image = (
                    source == "prometheus"
                    and channel.image_enabled
                    and bool(image_bytes)
                )
                
                if use_image:
                    try:
                        send_telegram(channel, body, photo_bytes=image_bytes)
                        logger.info(f"告警 {alertname} 渠道 {channel_name} 图片发送成功")
                    except Exception as img_err:
                        logger.warning(
                            f"告警 {alertname} 渠道 {channel_name} 图片发送失败，"
                            f"自动回退文本发送: {img_err}"
                        )
                        try:
                            send_telegram(channel, body)
                            logger.info(f"告警 {alertname} 渠道 {channel_name} 文本回退发送成功")
                        except Exception as fallback_err:
                            logger.error(
                                f"告警 {alertname} 渠道 {channel_name} 文本回退发送失败: {fallback_err}",
                                exc_info=True,
                            )
                            raise
                else:
                    send_telegram(channel, body)
            else:
                send_webhook(channel, body)

            return {
                "alert": alertname,
                "channel": channel_name,
                "status": "sent",
                "alert_status": alert_status,
            }
        except RequestException as e:
            error_msg = str(e)
            # 404/401/410 为 Webhook URL 配置问题，不打印堆栈
            is_config_error = (
                isinstance(e, HTTPError)
                and e.response is not None
                and e.response.status_code in (401, 404, 410)
            )
            if is_config_error:
                logger.warning(
                    f"告警 {alertname} 发送到渠道 {channel_name} 失败: {error_msg} "
                    f"（请检查该渠道 Webhook URL 配置）"
                )
            else:
                logger.error(
                    f"告警 {alertname} 发送到渠道 {channel_name} 失败: {error_msg}",
                    exc_info=True,
                )
            return {"alert": alertname, "channel": channel_name, "error": error_msg}
        except Exception as e:
            error_msg = str(e)
            logger.critical(
                f"告警 {alertname} 发送到渠道 {channel_name} 发生未预期错误: {error_msg}",
                exc_info=True,
            )
            raise

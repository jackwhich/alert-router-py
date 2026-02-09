"""
模板渲染模块
"""
from typing import Dict, Any
from jinja2 import Environment, FileSystemLoader
from .utils import convert_to_cst, replace_times_in_description, url_to_link

env = Environment(
    loader=FileSystemLoader("templates"),
    trim_blocks=True,  # 移除模板标签后的第一个换行
    lstrip_blocks=True  # 移除模板标签前的空格
)

# 注册自定义过滤器
env.filters['url_to_link'] = url_to_link


def render(template: str, ctx: Dict[str, Any]) -> str:
    """
    渲染模板
    
    Args:
        template: 模板文件名
        ctx: 模板上下文
    
    Returns:
        str: 渲染后的文本
    """
    # 转换时间为 CST
    if ctx.get("startsAt"):
        ctx["startsAt"] = convert_to_cst(ctx["startsAt"])
    if ctx.get("endsAt"):
        ctx["endsAt"] = convert_to_cst(ctx["endsAt"])
    
    # 替换 description 中的时间（仅对 Slack 模板）
    if template.endswith(".json.j2") and ctx.get("annotations", {}).get("description"):
        ctx["annotations"]["description"] = replace_times_in_description(ctx["annotations"]["description"])
    
    return env.get_template(template).render(**ctx)

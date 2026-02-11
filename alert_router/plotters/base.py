"""
公共绘图工具模块

提取 prometheus_plotter 和 grafana_plotter 中的重复代码
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Dict, List, Tuple, Optional
import platform

import matplotlib
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

matplotlib.use("Agg")

# 尝试导入 Plotly（可选）
try:
    import plotly.graph_objects as go
    import plotly.io as pio
    PLOTLY_AVAILABLE = True
except ImportError:
    PLOTLY_AVAILABLE = False
    go = None
    pio = None


def setup_chinese_fonts():
    """设置中文字体支持"""
    if platform.system() == 'Darwin':  # macOS
        plt.rcParams['font.sans-serif'] = ['Arial Unicode MS', 'PingFang SC', 'STHeiti', 'Arial']
    elif platform.system() == 'Linux':
        plt.rcParams['font.sans-serif'] = ['WenQuanYi Micro Hei', 'DejaVu Sans', 'Liberation Sans']
    else:  # Windows
        plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'Arial']
    plt.rcParams['axes.unicode_minus'] = False


def build_series_label(metric: Dict[str, str]) -> str:
    """
    从 Prometheus metric 标签构造曲线名称
    
    Args:
        metric: Metric 标签字典
        
    Returns:
        曲线名称字符串
    """
    if not metric:
        return "series"
    pairs = []
    # 排除的标签：不需要在图例中显示
    exclude_keys = {"__name__", "replica", "prometheus", "job", "instance"}
    for k in sorted(metric.keys()):
        if k in exclude_keys:
            continue
        pairs.append(f"{k}={metric[k]}")
    label = ", ".join(pairs) if pairs else metric.get("__name__", "series")
    if len(label) > 90:
        return label[:87] + "..."
    return label


def parse_time_series_data(result: List[Dict]) -> List[Tuple[List[datetime], List[float], Dict]]:
    """
    解析时间序列数据
    
    Args:
        result: Prometheus query_range 返回的结果列表
        
    Returns:
        列表，每个元素为 (时间列表, 值列表, metric字典)
    """
    parsed_data = []
    
    for series in result:
        values = series.get("values") or []
        if not values:
            continue
        
        xs = []
        ys = []
        for item in values:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            try:
                ts = float(item[0])
                val = float(item[1])
            except (TypeError, ValueError):
                continue
            # 将 UTC 时间转换为 UTC+8
            utc_time = datetime.fromtimestamp(ts, tz=timezone.utc)
            utc8_time = utc_time.astimezone(ZoneInfo("Asia/Shanghai"))
            xs.append(utc8_time)
            ys.append(val)
        
        if not xs:
            continue
        
        # 确保数据点按时间排序
        sorted_pairs = sorted(zip(xs, ys), key=lambda x: x[0])
        xs, ys = zip(*sorted_pairs) if sorted_pairs else ([], [])
        
        if not xs:
            continue
        
        parsed_data.append((list(xs), list(ys), series.get("metric") or {}))
    
    return parsed_data


def format_alert_time(alert_time: Optional[str]) -> str:
    """
    格式化告警时间为 UTC+8 时间字符串
    
    Args:
        alert_time: 告警时间字符串
        
    Returns:
        格式化后的时间字符串
    """
    if not alert_time:
        return "Time (UTC+8)"
    
    try:
        from dateutil import parser
        alert_dt = parser.parse(alert_time)
        if alert_dt.tzinfo is None:
            alert_dt = alert_dt.replace(tzinfo=timezone.utc)
        alert_dt_utc8 = alert_dt.astimezone(ZoneInfo("Asia/Shanghai"))
        return alert_dt_utc8.strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        return "Time (UTC+8)"


def format_y_value(x: float, p: int) -> str:
    """
    格式化 Y 轴数值（使用 K 格式，类似 Grafana）
    
    Args:
        x: 数值
        p: 位置（matplotlib 格式化器参数）
        
    Returns:
        格式化后的字符串
    """
    if abs(x) >= 1000:
        return f'{x/1000:.2f} K'.rstrip('0').rstrip('.')
    elif x == int(x):
        return f'{int(x)}'
    else:
        return f'{x:.1f}'


def configure_matplotlib_axes(
    ax,
    chart_title: str,
    xlabel_text: str,
    xs: List[datetime],
    plotted_count: int,
):
    """
    配置 matplotlib 坐标轴样式
    
    Args:
        ax: matplotlib 坐标轴对象
        chart_title: 图表标题
        xlabel_text: X 轴标签文本
        xs: X 轴时间数据
        plotted_count: 已绘制的曲线数量
    """
    # 设置标题
    ax.set_title(chart_title, fontsize=20, fontweight='bold', pad=30, color='#ffffff')
    
    # 设置 X 轴标签
    ax.set_xlabel(xlabel_text, fontsize=14, color='#ffffff', fontweight='normal')
    
    # Y 轴不显示标签，保持简洁
    ax.set_ylabel("", fontsize=0)
    
    # 优化 Y 轴数值格式
    ax.yaxis.set_major_formatter(plt.FuncFormatter(format_y_value))
    ax.tick_params(axis='y', labelsize=12, colors='#ffffff', width=1)
    ax.tick_params(axis='x', labelsize=11, colors='#ffffff', width=1)
    
    # 改进网格样式
    ax.grid(True, linestyle="--", alpha=0.4, linewidth=1.0, color='#ffffff')
    ax.set_axisbelow(True)
    
    # 设置坐标轴颜色
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['left'].set_color('#ffffff')
    ax.spines['bottom'].set_color('#ffffff')
    ax.spines['left'].set_linewidth(2)
    ax.spines['bottom'].set_linewidth(2)
    
    # 优化图例显示
    if plotted_count > 0:
        legend = ax.legend(
            loc="center left",
            bbox_to_anchor=(1.02, 0.5),
            fontsize=12,
            framealpha=0.95,
            fancybox=True,
            shadow=False,
            edgecolor='#ffffff',
            facecolor='#1a1a2e',
            borderpad=1.0,
            labelspacing=0.8,
            handlelength=2.0,
            handletextpad=0.8
        )
        for text in legend.get_texts():
            text.set_color('#ffffff')
            text.set_fontweight('normal')
    
    # 优化时间轴显示
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S', tz=ZoneInfo("Asia/Shanghai")))
    # 根据时间范围自动调整刻度间隔
    if len(xs) > 0:
        time_span = (max(xs) - min(xs)).total_seconds()
        if time_span <= 300:  # 5分钟以内，每30秒一个刻度
            ax.xaxis.set_major_locator(mdates.SecondLocator(interval=30))
        elif time_span <= 900:  # 15分钟以内，每1分钟一个刻度
            ax.xaxis.set_major_locator(mdates.MinuteLocator(interval=1))
        elif time_span <= 3600:  # 1小时以内，每5分钟一个刻度
            ax.xaxis.set_major_locator(mdates.MinuteLocator(interval=5))
        else:  # 超过1小时，每15分钟一个刻度
            ax.xaxis.set_major_locator(mdates.MinuteLocator(interval=15))
    fig = ax.get_figure()
    fig.autofmt_xdate(rotation=45)


def apply_dark_theme(fig, ax):
    """
    应用深色主题样式
    
    Args:
        fig: matplotlib figure 对象
        ax: matplotlib axes 对象
    """
    # 使用深色主题背景
    fig.patch.set_facecolor('#0a0a0f')
    ax.set_facecolor('#151520')
    
    # 创建渐变效果（可选）
    try:
        import numpy as np
        from matplotlib.colors import LinearSegmentedColormap
        
        y_min, y_max = ax.get_ylim()
        x_min, x_max = ax.get_xlim()
        
        y_vals = np.linspace(y_min, y_max, 100)
        x_vals = np.linspace(x_min, x_max, 100)
        X, Y = np.meshgrid(x_vals, y_vals)
        
        Z = np.linspace(0, 1, len(y_vals)).reshape(-1, 1)
        Z = np.tile(Z, (1, len(x_vals)))
        
        colors_gradient = ['#0a0a0f', '#1a1a2e', '#2a2a3e']
        n_bins = 256
        cmap = LinearSegmentedColormap.from_list('custom', colors_gradient, N=n_bins)
        
        ax.imshow(
            Z,
            extent=[x_min, x_max, y_min, y_max],
            aspect='auto',
            cmap=cmap,
            alpha=0.3,
            zorder=0,
            origin='lower'
        )
    except ImportError:
        # 如果没有 numpy，跳过渐变效果
        pass


def get_color_palette(count: int) -> List:
    """
    获取颜色调色板
    
    Args:
        count: 需要的颜色数量
        
    Returns:
        颜色列表
    """
    base_colors = ['#e74c3c', '#3498db', '#2ecc71', '#f39c12', '#9b59b6', '#1abc9c', '#e67e22', '#34495e']
    if count <= len(base_colors):
        return base_colors[:count]
    # 如果需要的颜色超过预设数量，使用调色板生成更多颜色
    return plt.cm.Set2(range(count))

#!/bin/bash

# 告警路由服务管理脚本
# 支持启动、停止、重启、状态查看、优雅关闭

set -e

# 配置变量
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_NAME="alert-router"
PID_FILE="${SCRIPT_DIR}/${PROJECT_NAME}.pid"
LOG_FILE="${SCRIPT_DIR}/logs/${PROJECT_NAME}.log"
PYTHON_CMD="${PYTHON_CMD:-python3.9}"  # 默认使用 python3.9

# 工作进程数和超时时间（可以通过环境变量覆盖）
WORKERS="${WORKERS:-4}"
TIMEOUT="${TIMEOUT:-30}"

# 注意：host 和 port 配置从 config.yaml 读取，应用启动时会自动读取
# 如果需要覆盖，可以通过环境变量 HOST 和 PORT 设置

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 日志函数
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 检查 Python 环境
check_python() {
    # 如果 PYTHON_CMD 未设置，则自动检测
    if [ -z "$PYTHON_CMD" ]; then
        # 优先使用 python3.9，如果没有则尝试 python3
        if command -v python3.9 &> /dev/null; then
            PYTHON_CMD="python3.9"
        elif command -v python3 &> /dev/null; then
            PYTHON_CMD="python3"
            # 检查版本是否为 3.9
            PYTHON_VERSION=$($PYTHON_CMD --version 2>&1 | awk '{print $2}' | cut -d. -f1,2)
            if [ "$PYTHON_VERSION" != "3.9" ]; then
                log_warn "检测到 Python $PYTHON_VERSION，推荐使用 Python 3.9"
            fi
        else
            log_error "未找到 python3.9 或 python3，请先安装 Python 3.9"
            exit 1
        fi
    fi
    
    # 验证 Python 版本和命令是否存在
    if ! command -v "$PYTHON_CMD" &> /dev/null; then
        log_error "Python 命令不存在: $PYTHON_CMD"
        exit 1
    fi
    
    PYTHON_VERSION=$($PYTHON_CMD --version 2>&1)
    log_info "使用 Python: $PYTHON_VERSION"
}

# 检查依赖
check_dependencies() {
    if ! $PYTHON_CMD -c "import fastapi" 2>/dev/null; then
        log_warn "正在安装依赖..."
        ${PYTHON_CMD} -m pip install -r requirements.txt
    fi
}

# 检查配置文件
check_config() {
    if [ ! -f "${SCRIPT_DIR}/config.yaml" ]; then
        log_error "未找到 config.yaml 配置文件"
        exit 1
    fi
}

# 获取进程 PID
get_pid() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if ps -p "$PID" > /dev/null 2>&1; then
            echo "$PID"
            return 0
        else
            # PID 文件存在但进程不存在，清理 PID 文件
            rm -f "$PID_FILE"
            return 1
        fi
    fi
    return 1
}

# 启动服务
start_service() {
    check_python
    check_dependencies
    check_config
    
    if get_pid > /dev/null; then
        log_warn "服务已在运行中 (PID: $(get_pid))"
        return 1
    fi
    
    log_info "正在启动 ${PROJECT_NAME} 服务..."
    log_info "工作进程数: ${WORKERS}"
    log_info "日志文件: ${LOG_FILE}"
    log_info "配置将从 config.yaml 读取"
    
    # 创建日志目录
    mkdir -p "$(dirname "$LOG_FILE")"
    
    # 启动服务（后台运行）
    # 直接运行 app.py，它会自动从 config.yaml 读取配置
    cd "$SCRIPT_DIR"
    WORKERS="$WORKERS" TIMEOUT="$TIMEOUT" nohup $PYTHON_CMD app.py \
        >> "$LOG_FILE" 2>&1 &
    
    PID=$!
    echo $PID > "$PID_FILE"
    
    # 等待服务启动
    sleep 2
    
    if ps -p "$PID" > /dev/null 2>&1; then
        log_info "服务启动成功 (PID: $PID)"
        log_info "查看日志: tail -f ${LOG_FILE}"
        return 0
    else
        log_error "服务启动失败，请查看日志: ${LOG_FILE}"
        rm -f "$PID_FILE"
        return 1
    fi
}

# 停止服务（优雅关闭）
stop_service() {
    if ! PID=$(get_pid); then
        log_warn "服务未运行"
        return 1
    fi
    
    log_info "正在停止服务 (PID: $PID)..."
    
    # 查找所有相关进程（包括 worker 进程）
    # uvicorn 使用多个 workers 时会创建子进程
    PIDS=$(pgrep -P "$PID" 2>/dev/null || true)
    ALL_PIDS="$PID"
    if [ -n "$PIDS" ]; then
        ALL_PIDS="$PID $PIDS"
        log_info "发现子进程: $PIDS"
    fi
    
    # 发送 SIGTERM 信号给主进程（uvicorn 会优雅关闭所有 worker）
    kill -TERM "$PID" 2>/dev/null || true
    
    # 等待所有进程退出（最多等待 30 秒）
    for i in {1..30}; do
        ALL_RUNNING=false
        for p in $ALL_PIDS; do
            if ps -p "$p" > /dev/null 2>&1; then
                ALL_RUNNING=true
                break
            fi
        done
        
        if [ "$ALL_RUNNING" = false ]; then
            log_info "服务已优雅关闭"
            rm -f "$PID_FILE"
            return 0
        fi
        sleep 1
    done
    
    # 如果 30 秒后仍未退出，强制杀死所有进程
    log_warn "优雅关闭超时，强制终止所有进程..."
    for p in $ALL_PIDS; do
        if ps -p "$p" > /dev/null 2>&1; then
            kill -KILL "$p" 2>/dev/null || true
        fi
    done
    sleep 1
    
    # 检查是否还有进程在运行
    STILL_RUNNING=false
    for p in $ALL_PIDS; do
        if ps -p "$p" > /dev/null 2>&1; then
            STILL_RUNNING=true
            break
        fi
    done
    
    if [ "$STILL_RUNNING" = false ]; then
        log_info "服务已强制关闭"
        rm -f "$PID_FILE"
        return 0
    else
        log_error "无法关闭服务，可能还有进程在运行"
        return 1
    fi
}

# 重启服务
restart_service() {
    log_info "正在重启服务..."
    stop_service || true
    sleep 2
    start_service
}

# 查看服务状态
status_service() {
    if PID=$(get_pid); then
        log_info "服务运行中 (PID: $PID)"
        
        # 显示进程信息
        if command -v ps > /dev/null; then
            echo ""
            ps -p "$PID" -o pid,ppid,user,%cpu,%mem,etime,cmd | head -2
        fi
        
        # 显示端口监听情况（从配置读取端口）
        CONFIG_PORT=$(python3 -c "import yaml; f=open('$SCRIPT_DIR/config.yaml'); c=yaml.safe_load(f); f.close(); print(c.get('server', {}).get('port', 8080))" 2>/dev/null || echo "8080")
        if command -v netstat > /dev/null; then
            echo ""
            echo "端口监听情况:"
            netstat -tlnp 2>/dev/null | grep ":$CONFIG_PORT " || echo "未找到端口 $CONFIG_PORT 的监听"
        elif command -v ss > /dev/null; then
            echo ""
            echo "端口监听情况:"
            ss -tlnp 2>/dev/null | grep ":$CONFIG_PORT " || echo "未找到端口 $CONFIG_PORT 的监听"
        fi
        
        return 0
    else
        log_warn "服务未运行"
        return 1
    fi
}

# 查看日志
view_logs() {
    if [ -f "$LOG_FILE" ]; then
        tail -f "$LOG_FILE"
    else
        log_error "日志文件不存在: ${LOG_FILE}"
        return 1
    fi
}

# 重载配置（优雅重启）
reload_service() {
    if ! PID=$(get_pid); then
        log_error "服务未运行，无法重载"
        return 1
    fi
    
    log_info "正在重载配置 (PID: $PID)..."
    
    # 发送 HUP 信号给主进程（uvicorn 支持热重载）
    # 注意：这需要 uvicorn 的 --reload 选项，但生产环境不建议使用
    # 更好的方式是重启服务
    log_warn "配置重载需要重启服务，正在执行重启..."
    restart_service
}

# 主函数
main() {
    case "${1:-}" in
        start)
            start_service
            ;;
        stop)
            stop_service
            ;;
        restart)
            restart_service
            ;;
        status)
            status_service
            ;;
        logs)
            view_logs
            ;;
        reload)
            reload_service
            ;;
        *)
            echo "用法: $0 {start|stop|restart|status|logs|reload}"
            echo ""
            echo "命令说明:"
            echo "  start   - 启动服务"
            echo "  stop    - 停止服务（优雅关闭）"
            echo "  restart - 重启服务"
            echo "  status  - 查看服务状态"
            echo "  logs    - 查看日志（实时）"
            echo "  reload  - 重载配置（重启服务）"
            echo ""
            echo "配置说明:"
            echo "  HOST/PORT - 从 config.yaml 的 server 配置读取"
            echo "  WORKERS   - 工作进程数（默认: 4，可通过环境变量覆盖）"
            echo "  TIMEOUT   - 超时时间（默认: 30，可通过环境变量覆盖）"
            exit 1
            ;;
    esac
}

main "$@"


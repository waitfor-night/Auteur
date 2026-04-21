#!/usr/bin/env bash
# setup.sh — 一键初始化 MoMo 开发环境
# 用法：bash setup.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_ROOT"

# ── 颜色输出 ───────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[setup]${NC} $*"; }
warn()  { echo -e "${YELLOW}[warn]${NC}  $*"; }
error() { echo -e "${RED}[error]${NC} $*" >&2; }

# ── 1. Git submodules ─────────────────────────────────────────────────────────
info "初始化 git submodules..."
git submodule update --init --recursive
info "  social-auto-upload  $(git -C social-auto-upload rev-parse --short HEAD)"
info "  xiaohongshu-mcp     $(git -C xiaohongshu-mcp rev-parse --short HEAD)"

# ── 2. Python 虚拟环境 ────────────────────────────────────────────────────────
if [ ! -d ".venv" ]; then
    info "创建 Python 虚拟环境 (.venv)..."
    python3 -m venv .venv
fi
info "激活虚拟环境并安装依赖..."
# shellcheck disable=SC1091
source .venv/bin/activate

pip install -q --upgrade pip
pip install -q -r requirements.txt
info "  主依赖安装完成"

# tikhub 不在 requirements.txt 中，单独安装
pip install -q tikhub
info "  tikhub 安装完成"

# ── 3. social-auto-upload（sau CLI）──────────────────────────────────────────
info "安装 social-auto-upload（sau CLI）..."
pip install -q -e social-auto-upload
info "  sau 版本：$(sau --version 2>/dev/null || echo '已安装，版本未知')"

# 复制 skill 文件到 Claude Code（可选，目录不存在时跳过）
if [ -d "$HOME/.claude/skills" ] && [ -d "social-auto-upload/skills" ]; then
    cp -r social-auto-upload/skills/* "$HOME/.claude/skills/"
    info "  sau skills 已同步到 ~/.claude/skills/"
elif [ -d "social-auto-upload/skills" ]; then
    warn "  ~/.claude/skills/ 不存在，跳过 skill 复制（手动执行：cp -r social-auto-upload/skills/* ~/.claude/skills/）"
fi

# ── 4. xiaohongshu-mcp（Go 服务）────────────────────────────────────────────
info "编译 xiaohongshu-mcp..."
if ! command -v go &>/dev/null; then
    warn "  未找到 go，跳过编译。请先安装 Go >= 1.24（https://go.dev/doc/install）"
    warn "  编译命令：cd xiaohongshu-mcp && go build -o xiaohongshu-mcp ."
else
    GO_VERSION=$(go version | awk '{print $3}')
    info "  Go 版本：$GO_VERSION"
    # 国内加速
    go env -w GOPROXY=https://goproxy.cn,direct 2>/dev/null || true
    (cd xiaohongshu-mcp && go build -o xiaohongshu-mcp .)
    info "  编译完成：xiaohongshu-mcp/xiaohongshu-mcp"
fi

# ── 5. ffmpeg ─────────────────────────────────────────────────────────────────
if ! command -v ffmpeg &>/dev/null; then
    warn "未找到 ffmpeg，视频处理功能不可用。"
    warn "  安装：sudo apt install -y ffmpeg"
else
    info "ffmpeg：$(ffmpeg -version 2>&1 | head -1 | awk '{print $3}')"
fi

# ── 6. .env 文件 ──────────────────────────────────────────────────────────────
if [ ! -f ".env" ]; then
    warn ".env 文件不存在，请参照以下模板创建："
    cat <<'EOF'

  ARK_API_KEY=        # Doubao/Ark LLM API（必填）
  TOS_ACCESS_KEY=     # ByteDance TOS 文件上传（必填）
  TOS_SECRET_KEY=     # ByteDance TOS 文件上传（必填）
  TIKHUB_API_TOKEN=   # TikHub 多平台数据采集（必填）
  DEEPSEEK_API_KEY=   # 可选
  KIMI_API_KEY=       # 可选

EOF
else
    info ".env 已存在"
fi

# ── 完成 ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}✓ 环境初始化完成${NC}"
echo ""
echo "后续步骤："
echo "  1. 填写 .env 文件中的 API keys"
echo "  2. 激活虚拟环境：source .venv/bin/activate"
echo "  3. 登录各平台账号（首次）："
echo "       sau douyin login --account <name>-douyin"
echo "       sau bilibili login --account <name>-bilibili"
echo "       sau kuaishou login --account <name>-kuaishou"
echo "  4. 启动小红书 MCP 服务："
echo "       cd xiaohongshu-mcp && ./start.sh &"
echo "       # 首次登录：go run cmd/login/main.go"
echo "  5. 运行视频助手："
echo "       python video_assistant.py"

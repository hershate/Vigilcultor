#!/usr/bin/env bash
# Vigilcultor 安装脚本（Linux，用户态，无需 root）
# 用法：
#   ./install.sh --release vX.Y.Z          # 从 GitHub Release 安装（推荐）
#   ./install.sh --git                     # 从 git 主分支安装（开发者）
#   ./install.sh --with-systemd [上述其一]  # 额外安装 systemd 用户服务
set -euo pipefail

RELEASE=""
USE_GIT=0
WITH_SYSTEMD=0
DATA_DIR="${VIGILCULTOR_DATA_DIR:-$HOME/.local/share/vigilcultor}"
REPO="hershate/Vigilcultor"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --release) RELEASE="$2"; shift 2 ;;
    --git) USE_GIT=1; shift ;;
    --with-systemd) WITH_SYSTEMD=1; shift ;;
    *) echo "未知参数：$1"; exit 1 ;;
  esac
done
[[ -n "$RELEASE" || $USE_GIT -eq 1 ]] || { echo "需要 --release vX.Y.Z 或 --git"; exit 1; }

say() { printf '==> %s\n' "$*"; }

say "检查依赖"
command -v uv >/dev/null || {
  say "安装 uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
}
command -v podman >/dev/null || echo "警告：未检测到 podman（容器执行不可用，可先跑面板）"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

say "安装 vigilcultor"
if [[ $USE_GIT -eq 1 ]]; then
  uv tool install --force "git+https://github.com/${REPO}.git"
else
  gh release download "$RELEASE" -R "$REPO" -p 'vigilcultor-*.whl' -D "$TMP" 2>/dev/null \
    || curl -fL -o "$TMP" "https://github.com/${REPO}/releases/download/${RELEASE}/$( \
         curl -fsSL "https://api.github.com/repos/${REPO}/releases/tags/${RELEASE}" \
         | grep -o 'vigilcultor-[^"]*\.whl' | head -1)" 2>/dev/null \
    || { echo "下载 wheel 失败（检查 Release 资产或网络）"; exit 1; }
  uv tool install --force "$TMP"/vigilcultor-*.whl
fi

say "下载执行器二进制 → ${DATA_DIR}/bin/rusty-code"
mkdir -p "${DATA_DIR}/bin"
if [[ $USE_GIT -eq 1 ]]; then
  echo "（--git 模式不下载执行器；请自行执行 scripts/build-executor.sh 并放置）"
else
  gh release download "$RELEASE" -R "$REPO" -p 'rusty-code-linux-x86_64' -D "$TMP" 2>/dev/null \
    || curl -fL -o "$TMP/rusty-code-linux-x86_64" \
         "https://github.com/${REPO}/releases/download/${RELEASE}/rusty-code-linux-x86_64"
  mv "$TMP/rusty-code-linux-x86_64" "${DATA_DIR}/bin/rusty-code"
  chmod 755 "${DATA_DIR}/bin/rusty-code"
fi

if [[ $WITH_SYSTEMD -eq 1 ]]; then
  say "安装 systemd 用户服务"
  mkdir -p "$HOME/.config/systemd/user"
  cat > "$HOME/.config/systemd/user/vigilcultor.service" <<EOF
[Unit]
Description=Vigilcultor（Web 面板 + 调度 + 出口代理）
After=network-online.target

[Service]
ExecStart=%h/.local/bin/vigilcultor all
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now vigilcultor.service
  say "服务已启动：systemctl --user status vigilcultor"
fi

say "初始化数据库"
vigilcultor init-db

say "完成：运行 vigilcultor web 打开面板 http://127.0.0.1:8000"

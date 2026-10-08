#!/usr/bin/env bash
# 维护者工具：从私有上游构建 Linux 执行器（详见 note/agent-framework.md 配方）。
# 产物：dist/rusty-code-linux-x86_64
# 依赖：gh（已登录且可访问上游私有库）、rustup/cargo（crates 走 rsproxy 镜像）。
set -euo pipefail
cd "$(dirname "$0")/.."

EXECUTOR_VERSION="${EXECUTOR_VERSION:-0.19.35}"
UPSTREAM="${UPSTREAM:-hershate/ClaudeCodeRev}"
WORK="${WORK:-$(mktemp -d)}"
OUT="dist/rusty-code-linux-x86_64"

mkdir -p dist
command -v gh >/dev/null || { echo "需要 gh（已登录）"; exit 1; }
command -v cargo >/dev/null || { echo "需要 rustup/cargo"; exit 1; }

echo "==> 拉取上游 v${EXECUTOR_VERSION} tarball"
gh api "repos/${UPSTREAM}/tarball/v${EXECUTOR_VERSION}" > "${WORK}/rc.tar"
mkdir -p "${WORK}/src"
tar -xf "${WORK}/rc.tar" -C "${WORK}/src" --strip-components=1 \
    --wildcards '*/rust/*' '*/prompts/*' '*/share/*'

echo "==> prompts 清单行尾对齐（上游清单为 CRLF 哈希，tarball 导出 LF）"
python3 - "${WORK}/src/prompts" <<'PY'
import hashlib, pathlib, sys
root = pathlib.Path(sys.argv[1])
fixed = bad = 0
for line in (root / "MANIFEST.sha256").read_text().splitlines():
    expected, name = line.split(maxsplit=1)
    p = root / name.strip()
    data = p.read_bytes()
    if hashlib.sha256(data).hexdigest() == expected:
        continue
    crlf = data.replace(b"\n", b"\r\n")
    if hashlib.sha256(crlf).hexdigest() == expected:
        p.write_bytes(crlf)
        fixed += 1
    else:
        bad += 1
        print(f"  无法对齐: {name}")
if bad:
    sys.exit(1)
print(f"  已对齐 {fixed} 个文件")
PY

echo "==> cargo build（release）"
(cd "${WORK}/src/rust" && cargo build --release -p claude-cli)
cp "${WORK}/src/rust/target/release/claude-cli" "${OUT}"
chmod 755 "${OUT}"
rm -rf "${WORK}"
echo "==> 完成：${OUT}（$("${OUT}" --version)）"

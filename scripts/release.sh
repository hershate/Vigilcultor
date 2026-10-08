#!/usr/bin/env bash
# 维护者工具：本地全量校验后打 tag 并发布 Release（附 wheel + Linux 执行器 + 校验和）。
# 依赖：gh、cargo、uv。发布产物先经 scripts/build-executor.sh 构建。
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="$(grep -m1 '^version' pyproject.toml | cut -d'"' -f2)"
TAG="v${VERSION}"

echo "==> 版本：${VERSION}"

echo "==> 门禁：ruff / pytest / 品牌扫描"
set -o pipefail
uv run ruff check src tests
uv run pytest -q
uv run python -m vigilcultor brand-check
set +o pipefail

if git rev-parse -q --verify "refs/tags/${TAG}" >/dev/null; then
    echo "tag ${TAG} 已存在"
else
    echo "==> 创建 tag ${TAG}"
    git tag "${TAG}"
fi

echo "==> 构建 wheel 与执行器"
# 执行器产物跨版本复用：版本仍匹配则不重编（全量 cargo 重编约 7 分钟）
KEEP="$(mktemp -d)"
[ -f dist/rusty-code-linux-x86_64 ] && mv dist/rusty-code-linux-x86_64 "$KEEP/"
rm -rf dist && mkdir dist
if [ -f "$KEEP/rusty-code-linux-x86_64" ] \
    && "$KEEP/rusty-code-linux-x86_64" --version 2>/dev/null \
        | grep -q "$(uv run python -c 'from vigilcultor.agent.executor import EXPECTED_VERSION as v; print(v)')"; then
    echo "==> 复用已构建执行器（版本匹配）"
    mv "$KEEP/rusty-code-linux-x86_64" dist/
else
    scripts/build-executor.sh
fi
rm -rf "$KEEP"
uv build
(cd dist && sha256sum vigilcultor-*.whl rusty-code-linux-x86_64 > SHA256SUMS)

echo "==> 发布 ${TAG}"
# 先推 tag 再建 Release：gh 拒绝基于未推送 tag 的 release（v0.1.1 教训）
git push origin "${TAG}"
gh release create "${TAG}" dist/vigilcultor-*.whl \
    dist/rusty-code-linux-x86_64 dist/SHA256SUMS \
    --title "Vigilcultor ${TAG}" --generate-notes --latest
echo "==> 完成"

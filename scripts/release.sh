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
rm -rf dist
uv build
scripts/build-executor.sh
(cd dist && sha256sum vigilcultor-*.whl rusty-code-linux-x86_64 > SHA256SUMS)

echo "==> 发布 ${TAG}"
# 先推 tag 再建 Release：gh 拒绝基于未推送 tag 的 release（v0.1.1 教训）
git push origin "${TAG}"
gh release create "${TAG}" dist/vigilcultor-*.whl \
    dist/rusty-code-linux-x86_64 dist/SHA256SUMS \
    --title "Vigilcultor ${TAG}" --generate-notes --latest
echo "==> 完成"

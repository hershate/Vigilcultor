# Vigilcultor

自托管的 GitHub 项目常驻 AI 托管管家：接入 GitHub App、添加仓库后，依据协作者
标签自动处理 issue——AI 修复、评论摘要、状态流转、自定义脚本，全部执行都在
rootless 容器内完成，经测试与构建门禁后提交 PR 并回评 issue，PR 的 review 意见
与 CI 失败可继续驱动迭代。

> 状态：早期开发中（v0.1 未发布），功能持续演进。

## 目标特性

- 标签驱动的四类动作：AI 开发任务 / 评论摘要 / 状态流转 / 自定义脚本
- 每项目独立容器环境（rootless Podman），容器出网经宿主审计代理与域名白名单
- AI 产出经测试套件与构建双重门禁；敏感路径变更强制人工确认
- Web 面板四页看板：项目 / 任务 / 日志 / 配置；默认中文、内置英文
- 多 AI 供应商自配切换，支持国内厂商、聚合入口与本地推理

## 开发

要求：Linux、Python 3.14+、[uv](https://docs.astral.sh/uv/)

```bash
uv sync
uv run vigilcultor web     # 面板 http://127.0.0.1:8000
uv run pytest              # 测试
uv run ruff check .        # 静态检查
uv run vigilcultor brand-check   # 品牌用语扫描
```

首次打开面板会引导创建管理员账号（首位注册者即管理员）。

## 许可

Apache-2.0

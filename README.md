# Vigilcultor

自托管的 GitHub 项目常驻 AI 托管管家：接入 GitHub App、添加仓库后，依据协作者
标签自动处理 issue——AI 修复、评论摘要、状态流转、自定义脚本，全部执行都在
rootless 容器内完成，经测试与构建门禁后提交 PR 并回评 issue，PR 的 review 意见
与 CI 失败可继续驱动迭代。

## 目标特性

- 标签驱动的四类动作：AI 开发任务 / 评论摘要 / 状态流转 / 自定义脚本
- 每项目独立容器环境（rootless Podman），容器出网经宿主审计代理与域名白名单
- AI 产出经测试套件与构建双重门禁；敏感路径变更强制人工确认
- 迭代到合入：PR review / 新评论 / CI 失败自动续接修改直至合入
- 韧性：失败自动重试（可配上限）、面板取消与重跑、issue 关闭自动取消、
  高优先级抢占、保留现场分支
- 成本可控：每任务预算上限、token 按项目/月度统计与成本估算
- Web 面板四页看板：项目 / 任务 / 日志 / 配置；默认中文、内置英文
- 多 AI 供应商自配切换，支持国内厂商、聚合入口与本地推理

## 安装

要求：Linux x86_64、用户态即可（无需 root）；推荐 Podman。

```bash
# 从 Release 安装（附执行器二进制）
./install.sh --release vX.Y.Z

# 追加 systemd 用户服务（可选）
./install.sh --release vX.Y.Z --with-systemd

# 开发者模式（源码主分支，执行器需自行构建）
./install.sh --git
```

安装内容：`uv tool` 安装命令行、执行器二进制放置于
`~/.local/share/vigilcultor/bin/rusty-code`、初始化数据库。

## 快速开始

1. `vigilcultor web` 打开面板（http://127.0.0.1:8000），向导创建管理员
   并设置主密码（凭据保险库，Argon2id 派生 + AES-GCM）。
2. 设置页填入 GitHub App 凭据（App ID / 私钥 / Webhook 密钥）与 AI 服务
   连接信息（第三方兼容推理端点 + 凭据，加密存储）。
3. GitHub App 侧配置：
   - Webhook 地址 `https://你的域名/webhook/github`
   - 事件勾选：Issues / Issue comments / Pull requests / Pull request
     reviews / Check suites（轮询作为标签补偿，无需全量事件）
   - 仓库权限：issues(write)、pull requests(write)、contents(read)、
     metadata(read)、单 repo 流转(写入) 按需
4. 面板「项目」页添加仓库（安装列表勾选或 URL 手填），点「立即首检」：
   自动克隆、识别语言环境、构建容器镜像、跑基线构建/测试、对齐标签与规则。
5. 协作者在 issue 上打「需要维护」标签（可在规则页改）→ 任务入队 →
   容器内执行 → 门禁 → PR + issue 回评 → 迭代到合入。

评论指令：协作者在 issue/PR 评论 `@<机器人名> retry|status|skip`。

## 进程模型

```bash
vigilcultor web        # Web 面板（127.0.0.1:8000）
vigilcultor scheduler  # 轮询补偿 + 任务调度循环
vigilcultor netproxy   # 容器出口审计代理（白名单 + JSONL 审计）
vigilcultor all        # 单进程合体（开发/小规模部署）
vigilcultor init-db    # 建表/迁移
vigilcultor brand-check
```

## 安全模型

- 凭据全部入保险库（主密码派生密钥加密），容器内无任何密钥；
  git 推送令牌仅经进程参数传递，不落盘。
- issue/PR 内容一律作为不可信数据块注入系统提示（定界 + 清洗），
  其中的指令不生效。
- 容器出网仅经 netproxy：域名白名单（GitHub / 包源 / 镜像源，可经
  kv `netproxy:allowlist` 覆盖）+ 全量 JSONL 审计。
- 执行器在 `-p` 无头模式下于容器内运行（隔离由容器承担）；
  每任务 `--max-budget-usd` 硬预算上限。
- 每迭代完整执行日志落盘 `data/logs/task-<id>/iter-<n>.jsonl`。

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

发布（维护者）：`scripts/release.sh` 全量校验后打 tag 并创建 Release，
附 wheel、Linux 执行器与校验和。

## 许可

Apache-2.0

## 声明
本项目已经停更，请大家使用https://github.com/deepseek-ai/dsh-superagent-prototype
deepseek的superagent应该会比我做得更好
我也是一个比较懈怠的人吧，很多事情做了但是也做不好，也不是非常有毅力和决心的人
这个项目将会归档，我相信deepseek会做的更好
再次感谢所有关注并支持过本项目的人，
同时也道个歉，我是一个比较懒的vibe coder
很多事情也确实做的不好
希望大家能原谅我

在这里顺便解释一下Vigilcultor这个名字的含义吧
这个词由Vigil+Cultor构成
Vigil-（不眠守望）
-cultor（培育照料）

另外再次声明，本项目与https://github.com/deepseek-ai/dsh-superagent-prototype无关
本项目为本人使用AI开发，与其他任何团体和个人无关

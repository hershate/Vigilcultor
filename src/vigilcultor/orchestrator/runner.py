"""任务执行循环：领取 queued 任务 → 容器内 agent 执行 → 门禁 → 提交/PR/评论。

职责边界（M4 单迭代版，M5 扩展多轮迭代与抢占）：
- agent 在项目容器内修改挂载的工作区（容器内无 git、无凭据）；
- 门禁在独立新容器按两段式执行（运行期安装 + 构建/测试命令）；
- 分支/提交/推送在宿主克隆上完成（token 走 http.extraHeader，不落盘），
  PR 与 issue 评论走 installation API。
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
from datetime import UTC, datetime

from sqlalchemy import func, select

from vigilcultor.agent import compat, driver, executor, prompts
from vigilcultor.containers.imagegen import container_tag, runtime_setup
from vigilcultor.containers.onboard import clone_dir_for, real_cloner
from vigilcultor.containers.podman import Podman
from vigilcultor.containers.profile import EnvProfile
from vigilcultor.store.db import get_session_maker
from vigilcultor.store.kv import kv_get
from vigilcultor.store.models import Project, Task, TaskRun

logger = logging.getLogger(__name__)

LOG_TAIL_CHARS = 4000
EXECUTOR_CONTAINER_PATH = "/usr/local/bin/rusty-code"
WORKSPACE = "/workspace"
SESSIONS_DIR = "/agent-sessions"

_running_lock = threading.Lock()
_running = False


def claim_next_task() -> int | None:
    """queued → running（优先级降序、创建升序）；无可领任务返回 None。"""
    with get_session_maker()() as session:
        task_id = session.scalar(
            select(Task.id)
            .where(Task.state == "queued")
            .order_by(Task.priority.desc(), Task.created_at.asc())
            .limit(1)
        )
        if task_id is None:
            return None
        task = session.get(Task, task_id)
        task.state = "running"
        session.commit()
        return task_id


def run_pending_tasks() -> int:
    """单飞执行：抢占检查 → 领取 → 后台执行；返回本轮启动的任务数。"""
    global _running
    with _running_lock:
        if _running:
            return 0
        from vigilcultor.orchestrator.intervene import preempt_if_needed

        try:
            preempt_if_needed()
        except Exception:  # noqa: BLE001 - 抢占失败不阻塞领取
            logger.warning("preemption check failed", exc_info=True)
        task_id = claim_next_task()
        if task_id is None:
            return 0
        _running = True
    threading.Thread(target=_run_guarded, args=(task_id,), daemon=True).start()
    return 1


def _run_guarded(task_id: int) -> None:
    global _running
    try:
        run_task(task_id)
    except Exception as exc:  # noqa: BLE001 - 后台线程兜底，状态必须落地
        try:
            _set_task_state(task_id, "failed", f"runner_error: {exc}")
        except Exception:  # noqa: BLE001
            pass
    finally:
        with _running_lock:
            _running = False


def load_provider() -> dict | None:
    """读取 vault 加密存储的 Provider 配置；未配置/库锁定返回 None。"""
    from vigilcultor.secrets.vault import decrypt
    from vigilcultor.store.kv import kv_get

    blob = kv_get("agent:provider")
    if not isinstance(blob, str) or not blob:
        return None
    try:
        return json.loads(decrypt(blob))
    except Exception:  # noqa: BLE001 - vault 锁定或数据损坏
        return None


def gate_commands(profile_json: str | None) -> list[tuple[str, str]]:
    """[(kind, full_command)]：运行期安装串接构建/测试命令（两段式）。"""
    if not profile_json:
        return []
    profile = EnvProfile.from_json(profile_json)
    files: list[str] = []  # 清单存在性由 setup_commands/evidence 决定，命令已固化
    setup = runtime_setup(profile, files)
    commands = []
    for kind, command in (
        ("build", profile.build_command),
        ("test", profile.test_command),
    ):
        if command:
            commands.append((kind, f"{setup} && {command}" if setup else command))
    return commands


def _push_branch(dest, branch: str, token: str) -> bool:
    """宿主克隆上提交全部变更并推送（token 仅经进程参数，不落盘）。"""
    auth = ["-c", f"http.extraHeader=Authorization: Bearer {token}"]
    add = subprocess.run(
        ["git", "-C", str(dest), "add", "-A"], capture_output=True, text=True, timeout=120
    )
    if add.returncode != 0:
        return False
    commit = subprocess.run(
        ["git", "-C", str(dest), "commit", "-m", f"Vigilcultor 自动维护（{branch}）"],
        capture_output=True, text=True, timeout=120,
    )
    if commit.returncode != 0:
        return False  # 无可提交变更
    push = subprocess.run(
        ["git", *auth, "push", "origin", f"HEAD:refs/heads/{branch}"],
        cwd=dest, capture_output=True, text=True, timeout=300,
    )
    return push.returncode == 0


def run_task(task_id: int, *, podman: Podman | None = None) -> None:
    """执行一次迭代：issue 首迭代或 PR 反馈续接（--resume），落地 TaskRun。"""
    from vigilcultor.gh import service

    podman = podman or Podman()
    with get_session_maker()() as session:
        task = session.get(Task, task_id)
        if task is None:
            return
        project = session.get(Project, task.project_id)
        full_name, installation_id = project.full_name, project.installation_id
        issue_number, profile_json = task.issue_number, project.env_profile_json
        budget_usd = task.budget_usd / 100
        pr_number = task.pr_number
        pr_mode = project.pr_mode or "ready"
        attribution = project.attribution or "pr_note"
    run = _new_run(task_id)

    client = service.installation_client_for(installation_id)
    provider = load_provider()
    executor_path = executor.find_executor()
    if client is None:
        return _finish(run, task_id, "failed", error="no_installation_client")
    if provider is None:
        return _finish(run, task_id, "failed", error="provider_not_configured")
    if executor_path is None:
        return _finish(run, task_id, "failed", error="executor_not_found")

    # 1) 同步工作区
    dest = clone_dir_for(full_name)
    if not real_cloner(full_name, dest, client.token):
        return _finish(run, task_id, "failed", error="clone_failed")

    # 2) issue 上下文与三层系统提示
    try:
        issue = client.get_issue(full_name, issue_number)
    except Exception as exc:  # noqa: BLE001
        return _finish(run, task_id, "failed", error=f"issue_fetch_failed: {exc}")
    feedback, pr_done = _pr_feedback(client, full_name, task_id, pr_number)
    if pr_done is not None:
        # PR 已被人工合入/关闭：dispatch 正常会先到，这里兜底终态
        return _finish(run, task_id, "completed", error=pr_done)
    system_prompt = prompts.assemble_system_prompt(
        prompts.PromptLayers(
            issue_title=issue.get("title"),
            issue_body=issue.get("body"),
            extra_context=feedback,
        )
    )
    prompt = prompts.task_user_message()
    if feedback:
        prompt = prompts.iteration_user_message()

    # 3) 容器内执行 agent（工作区可写挂载，执行器与会话目录隔离挂载）
    from vigilcultor.netproxy.endpoint import container_proxy_url

    sessions_dir = dest.parent / (dest.name + ".sessions")
    sessions_dir.mkdir(parents=True, exist_ok=True)
    request = driver.AgentRequest(
        prompt=prompt,
        system_prompt=system_prompt,
        resume_session=_last_session_id(task_id),
        budget_usd=budget_usd or None,
        model=provider.get("model") or None,
    )
    env = compat.connection_env(
        base_url=provider["base_url"],
        auth_token=provider.get("auth_token"),
        api_key=provider.get("api_key"),
    )
    env.update(compat.container_env("/agent/cc-switch.db", "/agent/home"))
    env["HOME"] = "/agent/home"
    agent_result = podman.run(
        f"vgc-task-{task_id}",
        container_tag(full_name),
        ["sh", "-c", " ".join(driver.build_argv(EXECUTOR_CONTAINER_PATH, request))],
        volumes={
            str(dest): WORKSPACE,
            str(executor_path): EXECUTOR_CONTAINER_PATH,
            str(sessions_dir): SESSIONS_DIR,
        },
        workdir=WORKSPACE,
        env=env,
        proxy=container_proxy_url(),
    )
    podman.rm(f"vgc-task-{task_id}")
    _dump_stream(task_id, run.iter_no, agent_result.stdout)
    outcome = driver.parse_stream(agent_result.stdout)
    driver.fill_usage_from_result(outcome, agent_result.stdout)
    run.agent_session_id = outcome.session_id
    run.input_tokens = outcome.input_tokens
    run.output_tokens = outcome.output_tokens
    run.log_tail = (agent_result.stdout + agent_result.stderr)[-LOG_TAIL_CHARS:]
    if agent_result.returncode != 0 or not outcome.ok:
        _comment(
            client, full_name, issue_number,
            f"处理失败：agent 执行未完成。\n\n{(outcome.final_text or outcome.tail)[-800:]}",
        )
        return _fail(run, task_id, f"agent_failed:exit={agent_result.returncode}")

    # 4) 门禁：独立容器按两段式跑构建/测试
    for kind, command in gate_commands(profile_json):
        gate = podman.run(
            f"vgc-gate-{task_id}", container_tag(full_name), ["sh", "-c", command],
            volumes={str(dest): WORKSPACE}, workdir=WORKSPACE,
            proxy=container_proxy_url(),
        )
        podman.rm(f"vgc-gate-{task_id}")
        if not gate.ok:
            tail = (gate.stdout + gate.stderr)[-600:]
            _comment(client, full_name, issue_number, f"门禁未通过（{kind}）：\n```\n{tail}\n```")
            return _fail(run, task_id, f"gate_failed:{kind}")

    # 5) 提交、推送、PR、issue 评论（宿主侧 git + installation API）
    base = (client.get_repo(full_name) or {}).get("default_branch") or "main"
    branch = f"vgc/task-{task_id}"
    if not _push_branch(dest, branch, client.token):
        return _finish(run, task_id, "failed", error="push_failed")
    pr = client.create_pull_request(
        full_name,
        title=f"自动维护：处理 #{issue_number}",
        body=_pr_body(outcome.final_text, profile_json, issue_number, attribution),
        head=branch, base=base,
    )
    if pr_mode == "draft" and pr.get("node_id"):
        try:
            client.mark_pull_request_draft(pr.get("repository", {}).get("id", 0), pr["node_id"])
        except Exception:  # noqa: BLE001 - 草稿化失败不阻塞主流程
            logger.warning("mark draft failed for task #%s", task_id)
    run.pr_url = pr.get("html_url")
    _comment(
        client, full_name, issue_number,
        f"已提交处理，进入迭代直至合入：{run.pr_url}\n\n{outcome.final_text[:3000]}",
    )
    with get_session_maker()() as session:
        task = session.get(Task, task_id)
        if task is not None:
            task.pr_number = pr.get("number")
        session.commit()
    _finish(run, task_id, "in_review")


RETRY_KV_KEY = "tasks:retry_max"
DEFAULT_RETRY_MAX = 2


def retry_max() -> int:
    configured = kv_get(RETRY_KV_KEY)
    try:
        return int(configured) if configured is not None else DEFAULT_RETRY_MAX
    except (TypeError, ValueError):
        return DEFAULT_RETRY_MAX


def _new_run(task_id: int) -> TaskRun:
    with get_session_maker()() as session:
        last = session.scalar(
            select(func.max(TaskRun.iter_no)).where(TaskRun.task_id == task_id)
        )
        run = TaskRun(task_id=task_id, iter_no=(last or 0) + 1)
        session.add(run)
        session.commit()
        return run


def _last_session_id(task_id: int) -> str | None:
    with get_session_maker()() as session:
        return session.scalar(
            select(TaskRun.agent_session_id)
            .where(TaskRun.task_id == task_id, TaskRun.agent_session_id.is_not(None))
            .order_by(TaskRun.id.desc())
            .limit(1)
        )


def _fail(run: TaskRun, task_id: int, error: str) -> None:
    """失败落盘：未超重试上限 → 重新排队（下一迭代续接），否则终态 failed。"""
    attempts = run.iter_no
    if attempts <= retry_max():
        logger.warning("task #%s failed (attempt %s), retrying: %s", task_id, attempts, error)
        with get_session_maker()() as session:
            row = session.get(TaskRun, run.id)
            row.exit_status = "failed_retry"
            row.agent_session_id = run.agent_session_id
            row.input_tokens = run.input_tokens
            row.output_tokens = run.output_tokens
            row.log_tail = run.log_tail
            row.pr_url = run.pr_url
            row.finished_at = datetime.now(UTC)
            task = session.get(Task, task_id)
            if task is not None and task.state == "running":
                task.state = "queued"
                task.error = error
            session.commit()
        return
    _finish(run, task_id, "failed", error=error)


def _pr_feedback(client, full_name: str, task_id: int, pr_number: int | None):
    """PR 迭代的反馈上下文（上次运行之后的新评论/review/CI 失败）。

    返回 (feedback_text, done)：done 非 None 表示 PR 已终态（合入/关闭），
    任务直接完成，无需再跑。
    """
    if not pr_number:
        return None, None
    pr = client.get_pull_request(full_name, pr_number)
    if pr.get("merged"):
        return None, "pr_merged"
    if pr.get("state") == "closed":
        return None, "pr_closed_unmerged"
    with get_session_maker()() as session:
        watermark = session.scalar(
            select(func.max(TaskRun.finished_at)).where(TaskRun.task_id == task_id)
        )
    watermark = (watermark or datetime.min.replace(tzinfo=UTC)).isoformat()

    def _fresh(rows, time_key):
        out = []
        for row in rows:
            stamp = row.get(time_key) or ""
            if stamp > watermark:
                out.append(row)
        return out

    bot = None
    from vigilcultor.gh import service

    try:
        bot = service.bot_login()
    except Exception:  # noqa: BLE001
        bot = None
    sections: list[str] = []
    try:
        comments = _fresh(client.list_pr_comments(full_name, pr_number), "created_at")
        comments = [c for c in comments if not bot or (c.get("user") or {}).get("login") != bot]
        for c in comments:
            sections.append(f"评论[{(c.get('user') or {}).get('login', '?')}]：{c.get('body', '')}")
    except Exception:  # noqa: BLE001
        pass
    try:
        reviews = _fresh(
            [r for r in client.list_pr_reviews(full_name, pr_number) if r.get("body")],
            "submitted_at",
        )
        for r in reviews:
            login = (r.get("user") or {}).get("login", "?")
            sections.append(f"评审[{login}]（{r.get('state')}）：{r.get('body')}")
    except Exception:  # noqa: BLE001
        pass
    head_sha = (pr.get("head") or {}).get("sha")
    if head_sha:
        try:
            failed = [
                c
                for c in client.list_check_runs(full_name, head_sha)
                if c.get("conclusion") == "failure"
            ]
            for c in failed:
                summary = ((c.get("output") or {}).get("summary") or "")[:500]
                sections.append(f"CI 失败[{c.get('name')}]：{summary}")
        except Exception:  # noqa: BLE001
            pass
    return ("\n".join(sections)[:8000] or None), None


def _finish(
    run: TaskRun, task_id: int, state: str, *, error: str | None = None
) -> None:
    with get_session_maker()() as session:
        row = session.get(TaskRun, run.id)
        row.exit_status = state
        row.agent_session_id = run.agent_session_id
        row.input_tokens = run.input_tokens
        row.output_tokens = run.output_tokens
        row.log_tail = run.log_tail
        row.pr_url = run.pr_url
        row.finished_at = datetime.now(UTC)
        task = session.get(Task, task_id)
        if task is not None and task.state == "running":
            # 取消/抢占（cancelled）是人工终态，回写不得覆盖
            task.state = state
            task.error = error
        session.commit()


def _set_task_state(task_id: int, state: str, error: str | None) -> None:
    with get_session_maker()() as session:
        task = session.get(Task, task_id)
        if task is not None:
            task.state = state
            task.error = error
            session.commit()


def _pr_body(summary: str, profile_json: str | None, issue_number: int,
             attribution: str) -> str:
    """PR 描述四要素：改动摘要 / 测试结果 / 关联 issue / AI 声明。"""
    gates = [kind for kind, _ in gate_commands(profile_json)] or ["（项目未配置门禁）"]
    parts = [
        "## 改动摘要",
        (summary or "（见 issue 评论）")[:6000],
        "",
        "## 测试结果",
        f"门禁通过：{', '.join(gates)}",
        "",
        "## 关联 issue",
        f"Closes #{issue_number}",
    ]
    if attribution != "none":
        parts += ["", "## 声明", "本拉取请求由 Vigilcultor 托管的 AI 自动生成。"]
    return "\n".join(parts)


def _dump_stream(task_id: int, iter_no: int, stdout: str) -> None:
    """全量执行器输出落盘（完整对话审计；面板日志尾部另有库内副本）。"""
    from vigilcultor.config import data_dir

    log_dir = data_dir() / "logs" / f"task-{task_id}"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"iter-{iter_no}.jsonl").write_text(stdout, encoding="utf-8")


def _comment(client, full_name: str, issue_number: int, body: str) -> None:
    try:
        client.create_issue_comment(full_name, issue_number, body)
    except Exception:  # noqa: BLE001 - 评论失败不改变任务状态
        pass

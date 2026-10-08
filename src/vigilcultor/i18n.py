"""Minimal i18n: Chinese (default) and English dictionaries."""

from __future__ import annotations

from fastapi import Request

STRINGS: dict[str, dict[str, str]] = {
    "zh": {
        "app.name": "Vigilcultor",
        "app.tagline": "GitHub 项目的常驻 AI 托管管家",
        "nav.overview": "概览",
        "nav.projects": "项目",
        "nav.tasks": "任务",
        "nav.logs": "日志",
        "nav.settings": "配置",
        "nav.logout": "登出",
        "login.title": "登录",
        "login.username": "用户名",
        "login.password": "密码",
        "login.submit": "登录",
        "login.failed": "用户名或密码错误",
        "login.go_setup": "初始化系统",
        "setup.title": "初始化系统",
        "setup.hint": "首位注册用户将成为管理员",
        "setup.username": "用户名",
        "setup.password": "密码（至少 8 位）",
        "setup.submit": "创建管理员账号",
        "setup.exists": "系统已初始化，请直接登录",
        "setup.invalid": "用户名至少 2 个字符、密码至少 8 位",
        "setup.taken": "用户名已存在",
        "page.overview.title": "概览",
        "page.overview.desc": "托管项目、任务与系统状态的总体视图。",
        "page.projects.title": "项目",
        "page.projects.desc": "已托管仓库列表与接入、暂停、归档控制。",
        "page.tasks.title": "任务",
        "page.tasks.desc": "标签触发的任务队列、执行状态与迭代历史。",
        "page.logs.title": "日志",
        "page.logs.desc": "任务执行日志与审计记录检索。",
        "page.settings.title": "配置",
        "page.settings.desc": "GitHub App、Provider、规则与系统参数配置。",
        "page.wip": "建设中",
        "error.403": "权限不足",
    },
    "en": {
        "app.name": "Vigilcultor",
        "app.tagline": "Resident AI caretaker for GitHub repositories",
        "nav.overview": "Overview",
        "nav.projects": "Projects",
        "nav.tasks": "Tasks",
        "nav.logs": "Logs",
        "nav.settings": "Settings",
        "nav.logout": "Log out",
        "login.title": "Sign in",
        "login.username": "Username",
        "login.password": "Password",
        "login.submit": "Sign in",
        "login.failed": "Invalid username or password",
        "login.go_setup": "Initialize system",
        "setup.title": "Initialize system",
        "setup.hint": "The first registered user becomes the administrator",
        "setup.username": "Username",
        "setup.password": "Password (at least 8 characters)",
        "setup.submit": "Create administrator account",
        "setup.exists": "System already initialized, please sign in",
        "setup.invalid": "Username needs 2+ characters, password 8+ characters",
        "setup.taken": "Username already exists",
        "page.overview.title": "Overview",
        "page.overview.desc": "Overall view of hosted projects, tasks and system state.",
        "page.projects.title": "Projects",
        "page.projects.desc": "Hosted repositories, onboarding, pause and archive controls.",
        "page.tasks.title": "Tasks",
        "page.tasks.desc": "Label-triggered task queue, execution state and iterations.",
        "page.logs.title": "Logs",
        "page.logs.desc": "Task execution logs and audit records.",
        "page.settings.title": "Settings",
        "page.settings.desc": "GitHub App, providers, rules and system parameters.",
        "page.wip": "Work in progress",
        "error.403": "Insufficient permissions",
    },
}

DEFAULT_LANG = "zh"
COOKIE_NAME = "vgc_lang"


def normalize(lang: str | None) -> str:
    return lang if lang in STRINGS else DEFAULT_LANG


def t(key: str, lang: str | None = None) -> str:
    lang = normalize(lang)
    return STRINGS[lang].get(key, STRINGS[DEFAULT_LANG].get(key, key))


def lang_from(request: Request) -> str:
    return normalize(request.cookies.get(COOKIE_NAME))

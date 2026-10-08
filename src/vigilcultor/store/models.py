"""Database models (M2: accounts, settings, GitHub app, projects, rules, events)."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(16), default="viewer")
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class Kv(Base):
    __tablename__ = "kv"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="{}")


class AppCredentials(Base):
    """Single-row (id=1) GitHub App credentials, secrets encrypted by the vault."""

    __tablename__ = "app_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    app_id: Mapped[int] = mapped_column(Integer)
    app_slug: Mapped[str | None] = mapped_column(String(128), default=None)
    private_key_enc: Mapped[str] = mapped_column(Text)
    webhook_secret_enc: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Installation(Base):
    __tablename__ = "installations"

    installation_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_login: Mapped[str] = mapped_column(String(128))
    account_type: Mapped[str] = mapped_column(String(32), default="Organization")
    repository_selection: Mapped[str] = mapped_column(String(32), default="selected")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(256), unique=True, index=True)
    installation_id: Mapped[int] = mapped_column(ForeignKey("installations.installation_id"))
    state: Mapped[str] = mapped_column(String(16), default="active")  # active|paused|archived
    is_fork: Mapped[bool] = mapped_column(Boolean, default=False)
    parent_full_name: Mapped[str | None] = mapped_column(String(256), default=None)
    sync_interval_min: Mapped[int] = mapped_column(Integer, default=360)
    sync_state: Mapped[str] = mapped_column(String(16), default="ok")  # ok|conflict|paused
    autonomy: Mapped[str] = mapped_column(String(16), default="pr")  # report|pr|auto
    pr_mode: Mapped[str] = mapped_column(String(16), default="ready")  # ready|draft
    # none|pr_note|signature
    attribution: Mapped[str] = mapped_column(String(16), default="pr_note")
    limits_json: Mapped[str] = mapped_column(Text, default="{}")
    env_profile_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class Rule(Base):
    """Label-to-action rule; project_id NULL means a global default rule."""

    __tablename__ = "rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id"), default=None, index=True
    )
    label: Mapped[str] = mapped_column(String(128), index=True)
    action: Mapped[str] = mapped_column(String(32))  # ai_task|comment|label_flow|script
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    priority: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    cooldown_sec: Mapped[int] = mapped_column(Integer, default=300)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class Task(Base):
    """A label-triggered work item (execution arrives with M4+)."""

    __tablename__ = "tasks"
    __table_args__ = (Index("ix_tasks_project_issue", "project_id", "issue_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    rule_id: Mapped[int] = mapped_column(ForeignKey("rules.id"))
    event_id: Mapped[int] = mapped_column(ForeignKey("event_records.id"))
    issue_number: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(32))
    state: Mapped[str] = mapped_column(String(16), default="queued")
    priority: Mapped[int] = mapped_column(Integer, default=0)
    # USD amounts stored in cents to stay integral.
    budget_usd: Mapped[int] = mapped_column(Integer, default=0)
    spent_usd: Mapped[int] = mapped_column(Integer, default=0)
    preserved_branch: Mapped[str | None] = mapped_column(String(256), default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class EventRecord(Base):
    """Normalized inbound GitHub event (webhook or polling compensation)."""

    __tablename__ = "event_records"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    gh_delivery_id: Mapped[str] = mapped_column(String(128), index=True)
    source: Mapped[str] = mapped_column(String(16), default="webhook")  # webhook|poll
    event_type: Mapped[str] = mapped_column(String(64))
    action: Mapped[str | None] = mapped_column(String(64), default=None)
    repo_full_name: Mapped[str | None] = mapped_column(String(256), default=None, index=True)
    actor_login: Mapped[str | None] = mapped_column(String(128), default=None)
    dedup_key: Mapped[str] = mapped_column(String(256), index=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    received_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)

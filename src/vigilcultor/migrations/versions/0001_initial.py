"""Initial schema: users, kv, app_credentials, installations, projects, rules, events.

Revision ID: 0001
Revises:
Create Date: 2026-10-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(64), unique=True, index=True),
        sa.Column("password_hash", sa.String(256), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("disabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "kv",
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
    )
    op.create_table(
        "app_credentials",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("app_id", sa.Integer(), nullable=False),
        sa.Column("app_slug", sa.String(128), nullable=True),
        sa.Column("private_key_enc", sa.Text(), nullable=False),
        sa.Column("webhook_secret_enc", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "installations",
        sa.Column("installation_id", sa.Integer(), primary_key=True),
        sa.Column("account_login", sa.String(128), nullable=False),
        sa.Column("account_type", sa.String(32), nullable=False),
        sa.Column("repository_selection", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("full_name", sa.String(256), unique=True, index=True),
        sa.Column(
            "installation_id",
            sa.Integer(),
            sa.ForeignKey("installations.installation_id"),
            nullable=False,
        ),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("is_fork", sa.Boolean(), nullable=False),
        sa.Column("parent_full_name", sa.String(256), nullable=True),
        sa.Column("sync_interval_min", sa.Integer(), nullable=False),
        sa.Column("sync_state", sa.String(16), nullable=False),
        sa.Column("autonomy", sa.String(16), nullable=False),
        sa.Column("pr_mode", sa.String(16), nullable=False),
        sa.Column("attribution", sa.String(16), nullable=False),
        sa.Column("limits_json", sa.Text(), nullable=False),
        sa.Column("env_profile_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=True),
        sa.Column("label", sa.String(128), nullable=False, index=True),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("cooldown_sec", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "event_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("gh_delivery_id", sa.String(128), nullable=False, index=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("action", sa.String(64), nullable=True),
        sa.Column("repo_full_name", sa.String(256), nullable=True, index=True),
        sa.Column("actor_login", sa.String(128), nullable=True),
        sa.Column("dedup_key", sa.String(256), nullable=False, index=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("processed", sa.Boolean(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("event_records")
    op.drop_table("rules")
    op.drop_table("projects")
    op.drop_table("installations")
    op.drop_table("app_credentials")
    op.drop_table("kv")
    op.drop_table("users")

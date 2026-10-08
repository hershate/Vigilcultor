"""Add tasks table for label-triggered work items.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False
        ),
        sa.Column("rule_id", sa.Integer(), sa.ForeignKey("rules.id"), nullable=False),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("event_records.id"), nullable=False),
        sa.Column("issue_number", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(128), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("budget_usd", sa.Integer(), nullable=False),
        sa.Column("spent_usd", sa.Integer(), nullable=False),
        sa.Column("preserved_branch", sa.String(256), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_tasks_project_issue", "tasks", ["project_id", "issue_number"])


def downgrade() -> None:
    op.drop_index("ix_tasks_project_issue", table_name="tasks")
    op.drop_table("tasks")

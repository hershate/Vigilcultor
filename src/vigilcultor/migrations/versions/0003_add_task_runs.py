"""Add task_runs table: per-iteration agent execution records.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "task_id", sa.Integer(), sa.ForeignKey("tasks.id"), nullable=False
        ),
        sa.Column("iter_no", sa.Integer(), nullable=False),
        sa.Column("agent_session_id", sa.String(64), nullable=True),
        sa.Column("exit_status", sa.String(16), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("pr_url", sa.String(512), nullable=True),
        sa.Column("log_tail", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_task_runs_task", "task_runs", ["task_id", "iter_no"])


def downgrade() -> None:
    op.drop_index("ix_task_runs_task", table_name="task_runs")
    op.drop_table("task_runs")

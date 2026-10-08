"""Rule matching: label → action, project overrides shadow global defaults.

Only collaborators (write permission) may trigger actions — a product-level
security boundary; the caller supplies the permission verdict (M3 resolves it
from the installation API).
"""

from __future__ import annotations

from enum import StrEnum

from vigilcultor.store.db import get_session_maker
from vigilcultor.store.models import Rule


class ActionType(StrEnum):
    AI_TASK = "ai_task"
    COMMENT = "comment"
    LABEL_FLOW = "label_flow"
    SCRIPT = "script"


DEFAULT_GLOBAL_RULES: tuple[tuple[str, str], ...] = (
    # (label, action) — user-derived default: "需要维护" triggers an AI dev task.
    ("需要维护", ActionType.AI_TASK),
)


def match_rule(
    *,
    rules: list[Rule],
    project_id: int | None,
    label: str,
    actor_is_collaborator: bool,
) -> Rule | None:
    """Pick the rule for *label*.

    Project-level rules shadow the global tier entirely for that label; within
    each tier higher priority wins (tie: lower id). Non-collaborators never
    match anything.
    """
    if not actor_is_collaborator:
        return None

    def applicable(rule: Rule) -> bool:
        if not rule.enabled or rule.label != label:
            return False
        if project_id is None:
            return rule.project_id is None
        return rule.project_id in (None, project_id)

    candidates = [rule for rule in rules if applicable(rule)]
    project_rules = sorted(
        (rule for rule in candidates if rule.project_id == project_id and project_id is not None),
        key=lambda rule: (-rule.priority, rule.id),
    )
    if project_rules:
        return project_rules[0]
    global_rules = sorted(
        (rule for rule in candidates if rule.project_id is None),
        key=lambda rule: (-rule.priority, rule.id),
    )
    return global_rules[0] if global_rules else None


def seed_default_rules() -> int:
    """Insert the global default rules once (idempotent); returns rows created."""
    created = 0
    with get_session_maker()() as session:
        existing = {
            (rule.label, rule.action)
            for rule in session.query(Rule).filter(Rule.project_id.is_(None)).all()
        }
        for label, action in DEFAULT_GLOBAL_RULES:
            if (label, action) in existing:
                continue
            session.add(Rule(project_id=None, label=label, action=action, priority=0))
            created += 1
        session.commit()
    return created

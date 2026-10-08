"""Rule engine tests: shadowing, priority, collaborator gate, seeding."""

from __future__ import annotations

from vigilcultor.rules.engine import match_rule, seed_default_rules
from vigilcultor.store.db import get_session_maker
from vigilcultor.store.models import Rule

PROJECT_ID = 1


def make_rule(**kwargs) -> Rule:
    defaults = {
        "project_id": None,
        "label": "需要维护",
        "action": "ai_task",
        "priority": 0,
        "enabled": True,
    }
    defaults.update(kwargs)
    rule = Rule(**defaults)
    rule.id = kwargs.get("id", 0)
    return rule


def test_collaborator_gate_blocks_everything():
    rules = [make_rule()]
    assert match_rule(
        rules=rules, project_id=PROJECT_ID, label="需要维护", actor_is_collaborator=False
    ) is None


def test_project_rule_shadows_global():
    rules = [
        make_rule(id=1, action="comment"),
        make_rule(id=2, project_id=PROJECT_ID, action="ai_task"),
    ]
    match = match_rule(
        rules=rules, project_id=PROJECT_ID, label="需要维护", actor_is_collaborator=True
    )
    assert match is not None and match.action == "ai_task"


def test_global_used_when_no_project_rule():
    rules = [make_rule(id=1, action="comment")]
    match = match_rule(
        rules=rules, project_id=PROJECT_ID, label="需要维护", actor_is_collaborator=True
    )
    assert match is not None and match.action == "comment"


def test_priority_within_tier_then_id_tiebreak():
    rules = [
        make_rule(id=5, priority=0, action="comment"),
        make_rule(id=9, priority=10, action="script"),
        make_rule(id=3, priority=10, action="ai_task"),
    ]
    match = match_rule(
        rules=rules, project_id=PROJECT_ID, label="需要维护", actor_is_collaborator=True
    )
    assert match is not None and match.id == 3  # same priority → lower id wins


def test_disabled_and_other_labels_ignored():
    rules = [
        make_rule(id=1, enabled=False),
        make_rule(id=2, label="别的标签"),
    ]
    assert (
        match_rule(
            rules=rules, project_id=PROJECT_ID, label="需要维护", actor_is_collaborator=True
        )
        is None
    )


def test_seed_defaults_idempotent(tmp_env):
    assert seed_default_rules() == 1
    assert seed_default_rules() == 0
    with get_session_maker()() as session:
        rules = session.query(Rule).all()
    assert len(rules) == 1
    assert rules[0].label == "需要维护" and rules[0].action == "ai_task"
    assert rules[0].project_id is None

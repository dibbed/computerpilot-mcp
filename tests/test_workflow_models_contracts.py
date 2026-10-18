from __future__ import annotations

from core.workflow_models import StepDefinition, WorkflowDefinition
from core.workflow_store import canonical_execution_definition


def test_legacy_sequential_definition_keeps_exact_canonical_bytes() -> None:
    definition = WorkflowDefinition(
        "legacy",
        (StepDefinition("a", "check_file", {"path": "x"}),),
        "desc",
    )
    assert canonical_execution_definition(definition) == (
        '{"description":"desc","name":"legacy","steps":['
        '{"action":"check_file","arguments":{"path":"x"},"execution_intent":null,'
        '"max_retries":0,"name":"a","postcondition":null,"timeout_sec":300}]}'
    )


def test_new_dag_fields_are_appended_without_breaking_legacy_positional_construction() -> None:
    step = StepDefinition("a", "check_file", {"path": "x"}, 12, 1, None, None)
    definition = WorkflowDefinition("legacy", (step,), "desc")
    assert step.step_id is None
    assert step.depends_on == ()
    assert definition.execution_mode == "sequential"

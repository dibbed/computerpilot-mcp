from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from core.errors import ToolError
from core.recovery import JOURNAL_SCHEMA_VERSION, OperationRecoveryJournal
from core.recovery_models import (
    Evidence,
    ExternalOperationMetadata,
    ExternalRequestCorrelation,
    OperationState,
    Postcondition,
)


def _records(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]



def test_m10_1_additive_external_metadata_and_native_isolation(tmp_path: Path) -> None:
    path = tmp_path / "operation-recovery.jsonl"
    journal = OperationRecoveryJournal()
    journal.configure(path, "runtime-a")

    native_handle = journal.begin("write_file", "path=C:/work/native.txt")
    assert native_handle is not None
    native_view = journal.inspect(native_handle.operation_id)
    assert "external" not in native_view
    assert "external_outcome" not in native_view

    correlation = journal.correlate_external_request(
        extension_id="github",
        public_name="external.github.create_issue",
        catalog_generation=3,
        schema_fingerprint="a" * 64,
        arguments={"title": "Bug", "token_echo": "ghp_SUPERSECRET1234567890"},
    )
    assert isinstance(correlation, ExternalRequestCorrelation)
    assert correlation.algorithm == "hmac-sha256-runtime-v1"
    assert correlation.scope == "runtime_only"
    assert len(correlation.key_id) == 16
    assert len(correlation.digest) == 64

    metadata = ExternalOperationMetadata(
        extension_id="github",
        provider="GitHub MCP",
        public_name="external.github.create_issue",
        remote_name="create_issue",
        catalog_generation=3,
        schema_fingerprint="a" * 64,
        mutation_class="remote_mutation",
        policy_revision="pol-1",
        dispatch_stage="prepared",
        correlation=correlation,
    )
    ext_handle = journal.begin(
        "external.github.create_issue",
        "extension=github;tool=create_issue",
        external_metadata=metadata,
    )
    assert ext_handle is not None

    raw_text = path.read_text(encoding="utf-8")
    assert "ghp_SUPERSECRET1234567890" not in raw_text
    assert "token_echo" not in raw_text

    records = _records(path)
    assert len(records) == 2
    assert all(r["schema_version"] == JOURNAL_SCHEMA_VERSION == 1 for r in records)
    assert "external" not in records[0]
    ext_record = records[1]
    assert isinstance(ext_record.get("external"), dict)
    assert ext_record["external"]["external_metadata_version"] == 1
    assert ext_record["external"]["extension_id"] == "github"
    assert ext_record["external"]["public_name"] == "external.github.create_issue"
    assert ext_record["external"]["remote_name"] == "create_issue"
    assert ext_record["external"]["catalog_generation"] == 3
    assert ext_record["external"]["dispatch_stage"] == "prepared"
    assert ext_record["external"]["correlation"]["digest"] == correlation.digest
    assert ext_record["external"]["correlation"]["scope"] == "runtime_only"

    inspected = journal.inspect(ext_handle.operation_id)
    assert inspected["external"]["extension_id"] == "github"
    assert inspected["external"]["dispatch_stage"] == "prepared"
    assert inspected["external"]["correlation"]["key_id"] == correlation.key_id


def test_m10_1_hmac_correlation_is_runtime_scoped_and_rotates_on_restart(tmp_path: Path) -> None:
    path = tmp_path / "operation-recovery.jsonl"
    journal = OperationRecoveryJournal()
    journal.configure(path, "runtime-a")

    c1 = journal.correlate_external_request(
        extension_id="github",
        public_name="external.github.create_issue",
        catalog_generation=1,
        schema_fingerprint="b" * 64,
        arguments={"b": 2, "a": 1},
    )
    c2 = journal.correlate_external_request(
        extension_id="github",
        public_name="external.github.create_issue",
        catalog_generation=1,
        schema_fingerprint="b" * 64,
        arguments={"a": 1, "b": 2},
    )
    c_diff_args = journal.correlate_external_request(
        extension_id="github",
        public_name="external.github.create_issue",
        catalog_generation=1,
        schema_fingerprint="b" * 64,
        arguments={"a": 1, "b": 3},
    )
    assert c1.key_id == c2.key_id
    assert c1.digest == c2.digest
    assert c1.digest != c_diff_args.digest

    # Reconfiguring with a new runtime_id rotates the in-memory HMAC key; no cross-restart equivalence
    journal.configure(path, "runtime-b")
    c_restarted = journal.correlate_external_request(
        extension_id="github",
        public_name="external.github.create_issue",
        catalog_generation=1,
        schema_fingerprint="b" * 64,
        arguments={"a": 1, "b": 2},
    )
    assert c_restarted.key_id != c1.key_id
    assert c_restarted.digest != c1.digest
    assert c_restarted.scope == "runtime_only"


def test_m10_1_rejects_raw_arguments_secrets_and_invalid_external_metadata(tmp_path: Path) -> None:
    path = tmp_path / "operation-recovery.jsonl"
    journal = OperationRecoveryJournal()
    journal.configure(path, "runtime-a")

    base_valid = {
        "extension_id": "github",
        "provider": "GitHub MCP",
        "public_name": "external.github.create_issue",
        "remote_name": "create_issue",
        "catalog_generation": 1,
        "schema_fingerprint": "c" * 64,
        "mutation_class": "remote_mutation",
    }

    for forbidden_key in ("arguments", "args", "env", "credentials", "payload", "argv", "config"):
        bad = {**base_valid, forbidden_key: {"secret": "value"}}
        with pytest.raises(ToolError) as exc_info:
            journal.begin("external.github.create_issue", external_metadata=bad)
        assert exc_info.value.code == "invalid_external_recovery_metadata"

    # Reject mismatched public_name vs extension_id/remote_name
    with pytest.raises(ToolError) as exc_info:
        journal.begin(
            "external.github.create_issue",
            external_metadata={**base_valid, "public_name": "external.other.create_issue"},
        )
    assert exc_info.value.code == "invalid_external_recovery_metadata"

    # Reject non-mutating or unknown mutation_class in recovery journal metadata
    for invalid_mut in ("read_only", "unknown"):
        with pytest.raises(ToolError) as exc_info:
            journal.begin(
                "external.github.create_issue",
                external_metadata={**base_valid, "mutation_class": invalid_mut},
            )
        assert exc_info.value.code == "invalid_external_recovery_metadata"

    # Reject secret-bearing receipt_id or provider string
    with pytest.raises(ToolError) as exc_info:
        journal.begin(
            "external.github.create_issue",
            external_metadata={**base_valid, "receipt_id": "Bearer secret_token_value"},
        )
    assert exc_info.value.code == "invalid_external_recovery_metadata"

    assert _records(path) == [] if path.exists() else True


def test_m10_1_unresolved_external_metadata_survives_restart_compaction_and_legacy_fixture(
    tmp_path: Path,
) -> None:
    path = tmp_path / "operation-recovery.jsonl"
    # Seed a legacy schema1 record first
    legacy_begin = {
        "schema_version": 1,
        "type": "begin",
        "status": "in_progress",
        "operation_id": "0" * 32,
        "runtime_id": "runtime-legacy",
        "operation_type": "write_file",
        "target": "path=C:/legacy.txt",
        "started_at": "2026-10-12T00:00:00.000+00:00",
        "pid": 101,
    }
    path.write_text(json.dumps(legacy_begin) + "\n", encoding="utf-8")

    journal_a = OperationRecoveryJournal()
    journal_a.configure(path, "runtime-a")
    # Legacy pending record from runtime-legacy is now marked uncertain
    legacy_inspected = journal_a.inspect("0" * 32)
    assert legacy_inspected["state"] == "uncertain"
    assert "external" not in legacy_inspected

    correlation = journal_a.correlate_external_request(
        extension_id="github",
        public_name="external.github.create_issue",
        catalog_generation=2,
        schema_fingerprint="d" * 64,
        arguments={"repo": "computerpilot", "title": "Issue 1"},
    )
    ext_handle = journal_a.begin(
        "external.github.create_issue",
        "extension=github;tool=create_issue",
        external_metadata={
            "extension_id": "github",
            "provider": "GitHub",
            "public_name": "external.github.create_issue",
            "remote_name": "create_issue",
            "catalog_generation": 2,
            "schema_fingerprint": "d" * 64,
            "mutation_class": "remote_mutation",
            "correlation": correlation,
        },
    )
    assert ext_handle is not None

    # Simulate restart into runtime-b -> ext_handle becomes uncertain and keeps external metadata
    journal_b = OperationRecoveryJournal()
    journal_b.configure(path, "runtime-b")
    summary = journal_b.summary()
    assert summary["uncertain_count"] == 2
    ext_summary = next(item for item in summary["uncertain"] if item["operation_id"] == ext_handle.operation_id)
    assert ext_summary["external"]["extension_id"] == "github"
    assert ext_summary["external"]["public_name"] == "external.github.create_issue"
    assert ext_summary["external"]["catalog_generation"] == 2
    assert ext_summary["external"]["external_outcome"] == "uncertain"

    # Compact and reload -> both legacy uncertain and external uncertain survive intact
    journal_b.compact()
    journal_c = OperationRecoveryJournal()
    journal_c.configure(path, "runtime-c")

    ext_after_compact = journal_c.inspect(ext_handle.operation_id)
    assert ext_after_compact["state"] == "uncertain"
    assert ext_after_compact["external"]["extension_id"] == "github"
    assert ext_after_compact["external"]["correlation"]["digest"] == correlation.digest
    assert ext_after_compact["external"]["external_outcome"] == "uncertain"

    # Acknowledge external uncertain operation and compact again -> external metadata still preserved
    ack = journal_c.acknowledge(
        ext_handle.operation_id,
        OperationState.ACKNOWLEDGED,
        Evidence("operator", {"note": "verified on GitHub", "outcome": "acknowledged"}),
    )
    assert ack["state"] == "acknowledged"
    assert ack["external"]["extension_id"] == "github"
    journal_c.compact()
    assert journal_c.inspect(ext_handle.operation_id)["external"]["extension_id"] == "github"
    assert journal_c.inspect("0" * 32)["state"] == "uncertain"

    # Verify reconciliation also preserves external metadata
    reconciled_legacy = journal_c.record_reconciliation(
        "0" * 32,
        OperationState.FAILED,
        Postcondition(kind="file_absent", expected={"path": "C:/legacy.txt"}),
        Evidence("filesystem", {"exists": False, "conclusive": True}),
    )
    assert reconciled_legacy["state"] == "failed"
    assert "external" not in reconciled_legacy


def test_m10_2_journal_checkpoint_not_dispatched_and_uncertain_transitions(tmp_path: Path) -> None:
    path = tmp_path / "operation-recovery.jsonl"
    disabled_journal = OperationRecoveryJournal()
    base_meta = {
        "extension_id": "github",
        "provider": "GitHub",
        "public_name": "external.github.increment_value",
        "remote_name": "increment_value",
        "catalog_generation": 1,
        "schema_fingerprint": "e" * 64,
        "mutation_class": "remote_mutation",
    }
    with pytest.raises(ToolError) as exc_info:
        disabled_journal.begin("external.github.increment_value", external_metadata=base_meta)
    assert exc_info.value.code == "recovery_unavailable"

    journal = OperationRecoveryJournal()
    journal.configure(path, "runtime-a")

    # 1. Pre-dispatch deterministic failure -> finish_not_dispatched
    h_not_sent = journal.begin("external.github.increment_value", external_metadata=base_meta)
    assert h_not_sent is not None
    journal.finish_not_dispatched(h_not_sent, "stale_generation")
    view_not_sent = journal.inspect(h_not_sent.operation_id)
    assert view_not_sent["state"] == "failed"
    assert view_not_sent["external_outcome"] == "not_dispatched"
    assert view_not_sent["external"]["external_outcome"] == "not_dispatched"
    assert view_not_sent["external"]["dispatch_stage"] == "prepared"
    assert view_not_sent["external"]["reason_code"] == "stale_generation"

    # Verify schema1 downgrade contract: raw result record uses known_result == 'returned_error'
    result_records = [
        r for r in _records(path)
        if r.get("operation_id") == h_not_sent.operation_id and r.get("type") == "result"
    ]
    assert len(result_records) == 1
    assert result_records[0]["known_result"] == "returned_error"
    assert result_records[0]["external_outcome"] == "not_dispatched"

    # 2. Durable may_dispatch barrier -> cannot finish_not_dispatched afterwards
    h_dispatched = journal.begin("external.github.increment_value", external_metadata=base_meta)
    assert h_dispatched is not None
    journal.checkpoint_external_dispatch(h_dispatched)
    # Idempotent repeat of checkpoint_external_dispatch
    journal.checkpoint_external_dispatch(h_dispatched)

    with pytest.raises(ToolError) as exc_info:
        journal.finish_not_dispatched(h_dispatched, "late_error")
    assert exc_info.value.code == "operation_already_dispatched"

    # Mark uncertain after may_dispatch
    journal.mark_uncertain(h_dispatched, "call_timeout", receipt_id="rcpt-123")
    view_unc = journal.inspect(h_dispatched.operation_id)
    assert view_unc["state"] == "uncertain"
    assert view_unc["external_outcome"] == "uncertain"
    assert view_unc["external"]["dispatch_stage"] == "may_dispatch"
    assert view_unc["external"]["external_outcome"] == "uncertain"
    assert view_unc["external"]["reason_code"] == "call_timeout"
    assert view_unc["external"]["receipt_id"] == "rcpt-123"

    # Cannot finish as succeeded after mark_uncertain
    with pytest.raises(ToolError) as exc_info:
        journal.finish(h_dispatched, known_result="ok")
    assert exc_info.value.code == "operation_already_uncertain"


def test_m10_2_manager_durable_dispatch_barrier_and_outcome_taxonomy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    import anyio

    from core.extensions.manager import ExtensionLifecycleManager
    from core.extensions.models import (
        ExtensionDefinition,
        ExtensionState,
        MutationClass,
        ToolPolicy,
        TrustClass,
    )

    fixture_path = Path(__file__).resolve().parent / "fixtures" / "fake_mcp_server.py"

    def make_definition(workspace: Path, mode: str) -> ExtensionDefinition:
        return ExtensionDefinition(
            extension_id="fake",
            display_name="Fake Provider",
            provider="Fake MCP",
            trust_class=TrustClass.TRUSTED,
            launch_approved=True,
            enabled=True,
            command=sys.executable,
            arguments=(str(fixture_path), "--workspace", str(workspace), "--mode", mode),
            working_directory=str(workspace),
            platforms=("windows", "linux", "macos"),
            tools={
                "increment_value": ToolPolicy(
                    remote_name="increment_value",
                    domain="process",
                    allow_call=True,
                    mutation_class=MutationClass.REMOTE_MUTATION,
                    profiles=("full",),
                ),
                "read_value": ToolPolicy(
                    remote_name="read_value",
                    domain="process",
                    allow_call=True,
                    mutation_class=MutationClass.READ_ONLY,
                    profiles=("full",),
                ),
            },
        )

    def read_dispatch_count(workspace: Path) -> int:
        counter = workspace / "counter.json"
        return int(counter.read_text(encoding="utf-8")) if counter.exists() else 0

    async def run_scenarios() -> None:
        # Verify default manager keeps mutation gate closed before M10.4
        default_mgr = ExtensionLifecycleManager()
        assert default_mgr.is_mutation_gate_closed is True

        # Scenario 1: Journal disabled -> deny before dispatch (marker == 0)
        ws1 = tmp_path / "ws1"
        ws1.mkdir()
        disabled_journal = OperationRecoveryJournal()
        mgr1 = ExtensionLifecycleManager(
            recovery_journal=disabled_journal,
            _mutation_gate_closed=False,
        )
        mgr1.register_extension(make_definition(ws1, "mutation_success"))
        keep1 = anyio.Event()
        async with anyio.create_task_group() as tg:
            await mgr1.start_all_background(tg, keep_alive_event=keep1)
            for _ in range(200):
                if mgr1.get_state("fake") == ExtensionState.HEALTHY:
                    break
                await anyio.sleep(0.02)
            assert mgr1.get_state("fake") == ExtensionState.HEALTHY
            lease1 = mgr1.registry.create_lease("external.fake.increment_value")
            assert lease1 is not None
            with pytest.raises(ToolError) as exc_info:
                await mgr1.execute_guarded_call(lease1, {"amount": 2})
            assert exc_info.value.code == "recovery_unavailable"
            assert read_dispatch_count(ws1) == 0
            await mgr1.drain_and_stop()

        # Scenario 2: Second checkpoint failure -> DO NOT SEND (marker == 0)
        ws2 = tmp_path / "ws2"
        ws2.mkdir()
        journal2 = OperationRecoveryJournal()
        journal2.configure(tmp_path / "journal2.jsonl", "runtime-2")
        mgr2 = ExtensionLifecycleManager(
            recovery_journal=journal2,
            _mutation_gate_closed=False,
        )
        mgr2.register_extension(make_definition(ws2, "mutation_success"))
        keep2 = anyio.Event()
        async with anyio.create_task_group() as tg:
            await mgr2.start_all_background(tg, keep_alive_event=keep2)
            for _ in range(200):
                if mgr2.get_state("fake") == ExtensionState.HEALTHY:
                    break
                await anyio.sleep(0.02)
            lease2 = mgr2.registry.create_lease("external.fake.increment_value")
            assert lease2 is not None

            orig_ckpt = journal2.checkpoint_external_dispatch

            def fail_ckpt(handle: Any) -> None:
                raise ToolError("operation_journal_unavailable", "Simulated fsync failure on may_dispatch")

            monkeypatch.setattr(journal2, "checkpoint_external_dispatch", fail_ckpt)
            with pytest.raises(ToolError) as exc_info:
                await mgr2.execute_guarded_call(lease2, {"amount": 3})
            assert exc_info.value.code in {"recovery_unavailable", "operation_journal_unavailable"}
            assert read_dispatch_count(ws2) == 0
            monkeypatch.setattr(journal2, "checkpoint_external_dispatch", orig_ckpt)

            # Scenario 3: Conclusive validated success -> marker == 1, journal succeeded
            res_ok = await mgr2.execute_guarded_call(lease2, {"amount": 4})
            assert res_ok["ok"] is True
            assert res_ok["outcome"] == "succeeded"
            assert isinstance(res_ok["operation_id"], str)
            assert read_dispatch_count(ws2) == 1
            inspected_ok = journal2.inspect(res_ok["operation_id"])
            assert inspected_ok["state"] == "succeeded"
            assert inspected_ok["external_outcome"] == "succeeded"
            assert inspected_ok["external"]["dispatch_stage"] == "may_dispatch"

            # Scenario 4: Post-dispatch terminal journal failure -> uncertain, not succeeded, owner degraded
            orig_finish = journal2.finish

            def fail_finish(*args: Any, **kwargs: Any) -> None:
                raise ToolError("operation_journal_unavailable", "Simulated post-dispatch fsync failure")

            monkeypatch.setattr(journal2, "finish", fail_finish)
            res_post_fail = await mgr2.execute_guarded_call(lease2, {"amount": 5})
            assert res_post_fail["ok"] is False
            assert res_post_fail["outcome"] == "uncertain"
            assert res_post_fail["error"] == "uncertain_side_effect"
            assert read_dispatch_count(ws2) == 2
            assert mgr2.get_state("fake") == ExtensionState.DEGRADED
            monkeypatch.setattr(journal2, "finish", orig_finish)
            await mgr2.drain_and_stop()

        # Scenario 5: Provider crash/disconnect after durable side effect
        # (uncertain_mutation) -> marker == 1, outcome == uncertain, no retry
        ws3 = tmp_path / "ws3"
        ws3.mkdir()
        journal3 = OperationRecoveryJournal()
        journal3.configure(tmp_path / "journal3.jsonl", "runtime-3")
        mgr3 = ExtensionLifecycleManager(
            recovery_journal=journal3,
            _mutation_gate_closed=False,
        )
        mgr3.register_extension(make_definition(ws3, "uncertain_mutation"))
        keep3 = anyio.Event()
        async with anyio.create_task_group() as tg:
            await mgr3.start_all_background(tg, keep_alive_event=keep3)
            for _ in range(200):
                if mgr3.get_state("fake") == ExtensionState.HEALTHY:
                    break
                await anyio.sleep(0.02)
            lease3 = mgr3.registry.create_lease("external.fake.increment_value")
            assert lease3 is not None
            res_unc = await mgr3.execute_guarded_call(lease3, {"amount": 7})
            assert res_unc["ok"] is False
            assert res_unc["outcome"] == "uncertain"
            assert res_unc["error"] == "uncertain_side_effect"
            assert isinstance(res_unc["operation_id"], str)
            assert read_dispatch_count(ws3) == 1
            inspected_unc = journal3.inspect(res_unc["operation_id"])
            assert inspected_unc["state"] == "uncertain"
            assert inspected_unc["external"]["dispatch_stage"] == "may_dispatch"
            assert inspected_unc["external"]["external_outcome"] == "uncertain"
            await mgr3.drain_and_stop()

    anyio.run(run_scenarios)

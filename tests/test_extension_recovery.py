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


def _sample_external_metadata() -> ExternalOperationMetadata:
    return ExternalOperationMetadata(
        extension_id="github",
        provider="GitHub MCP",
        public_name="external.github.create_issue",
        remote_name="create_issue",
        catalog_generation=1,
        schema_fingerprint="a" * 64,
        mutation_class="remote_mutation",
        dispatch_stage="prepared",
    )


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
        with anyio.fail_after(30):
            # Verify M10.4 activates the default mutation gate (while still requiring recovery availability)
            default_mgr = ExtensionLifecycleManager()
            assert default_mgr.is_mutation_gate_closed is False

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
                keep1.set()

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
                keep2.set()

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
                keep3.set()

    anyio.run(run_scenarios)


def test_m10_3_unresolved_quota_exhaustion_denies_before_dispatch_without_eviction(
    tmp_path: Path,
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
    from core.recovery import MAX_UNRESOLVED_EXTERNAL_OPERATIONS

    assert MAX_UNRESOLVED_EXTERNAL_OPERATIONS == 128

    path = tmp_path / "quota.jsonl"
    journal = OperationRecoveryJournal(max_unresolved_external=4)
    journal.configure(path, "runtime-quota")

    ext_meta = _sample_external_metadata()
    h1 = journal.begin("external.github.create_issue", "t1", external_metadata=ext_meta)
    assert h1 is not None
    h2 = journal.begin("external.github.create_issue", "t2", external_metadata=ext_meta)
    assert h2 is not None
    journal.checkpoint_external_dispatch(h2)
    h3 = journal.begin("external.github.create_issue", "t3", external_metadata=ext_meta)
    assert h3 is not None
    journal.mark_uncertain(h3, "call_timeout")
    h4 = journal.begin("external.github.create_issue", "t4", external_metadata=ext_meta)
    assert h4 is not None
    journal.mark_uncertain(h4, "connection_closed")

    assert journal.unresolved_external_count() == 4
    assert journal.has_external_mutation_capacity() is False

    # 5th direct begin is denied with operation_limit without evicting existing unresolved ops
    with pytest.raises(ToolError) as exc_info:
        journal.begin("external.github.create_issue", "t5", external_metadata=ext_meta)
    assert exc_info.value.code == "operation_limit"
    assert journal.unresolved_external_count() == 4

    fixture_path = Path(__file__).resolve().parent / "fixtures" / "fake_mcp_server.py"
    ws = tmp_path / "ws_quota"
    ws.mkdir()

    defn = ExtensionDefinition(
        extension_id="fake",
        display_name="Fake Provider",
        provider="Fake MCP",
        trust_class=TrustClass.TRUSTED,
        launch_approved=True,
        enabled=True,
        command=sys.executable,
        arguments=(str(fixture_path), "--workspace", str(ws), "--mode", "mutation_success"),
        working_directory=str(ws),
        platforms=("windows", "linux", "macos"),
        tools={
            "increment_value": ToolPolicy(
                remote_name="increment_value",
                domain="process",
                allow_call=True,
                mutation_class=MutationClass.REMOTE_MUTATION,
                profiles=("full",),
            ),
        },
    )

    async def verify_manager_quota() -> None:
        with anyio.fail_after(30):
            mgr = ExtensionLifecycleManager(
                recovery_journal=journal,
                _mutation_gate_closed=False,
            )
            mgr.register_extension(defn)
            keep = anyio.Event()
            async with anyio.create_task_group() as tg:
                await mgr.start_all_background(tg, keep_alive_event=keep)
                for _ in range(200):
                    if mgr.get_state("fake") == ExtensionState.HEALTHY:
                        break
                    await anyio.sleep(0.02)
                lease = mgr.registry.create_lease("external.fake.increment_value")
                assert lease is not None

                with pytest.raises(ToolError) as call_err:
                    await mgr.execute_guarded_call(lease, {"amount": 1})
                assert call_err.value.code == "operation_limit"
                assert not (ws / "counter.json").exists()

                # Acknowledge one uncertain operation -> capacity freed -> call succeeds
                journal.acknowledge(
                    h3.operation_id,
                    OperationState.ACKNOWLEDGED,
                    Evidence("operator", {"confirmed": True}),
                )
                assert journal.unresolved_external_count() == 3
                assert journal.has_external_mutation_capacity() is True

                res = await mgr.execute_guarded_call(lease, {"amount": 5})
                assert res["ok"] is True
                assert int((ws / "counter.json").read_text(encoding="utf-8")) == 1
                await mgr.drain_and_stop()
                keep.set()

    anyio.run(verify_manager_quota)


def test_m10_3_compaction_bounds_resolved_external_history_and_preserves_native_and_unresolved(
    tmp_path: Path,
) -> None:
    from datetime import datetime, timedelta, timezone

    path = tmp_path / "compact_bounds.jsonl"
    journal = OperationRecoveryJournal(
        max_external_terminal_history=3,
        max_external_terminal_days=30,
    )
    journal.configure(path, "runtime-compact")

    now = datetime.now(timezone.utc)
    old_iso = (now - timedelta(days=45)).isoformat(timespec="milliseconds")

    # 1. Native resolved operations (including an old 45-day-old one) -> all preserved
    native_ids: list[str] = []
    for idx in range(4):
        nh = journal.begin("write_file", f"path=C:/work/n{idx}.txt")
        assert nh is not None
        native_ids.append(nh.operation_id)
        journal.acknowledge(
            nh.operation_id,
            OperationState.ACKNOWLEDGED,
            Evidence("operator", {"native": idx}),
        )

    # Manually age the first native acknowledgment to 45 days ago
    records = _records(path)
    for rec in records:
        if rec.get("operation_id") == native_ids[0]:
            if "started_at" in rec:
                rec["started_at"] = old_iso
            if "recorded_at" in rec:
                rec["recorded_at"] = old_iso
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")

    # 2. Unresolved external operations (prepared, may_dispatch, uncertain) -> never evicted
    ext_meta = _sample_external_metadata()
    u_prep = journal.begin("external.github.create_issue", "u1", external_metadata=ext_meta)
    assert u_prep is not None
    u_disp = journal.begin("external.github.create_issue", "u2", external_metadata=ext_meta)
    assert u_disp is not None
    journal.checkpoint_external_dispatch(u_disp)
    u_unc = journal.begin("external.github.create_issue", "u3", external_metadata=ext_meta)
    assert u_unc is not None
    journal.mark_uncertain(u_unc, "call_timeout")

    # 3. Resolved external operations: 1 expired (45 days old) + 5 recent -> only newest 3 recent kept
    expired_ext = journal.begin("external.github.create_issue", "exp", external_metadata=ext_meta)
    assert expired_ext is not None
    journal.mark_uncertain(expired_ext, "call_timeout")
    journal.acknowledge(
        expired_ext.operation_id,
        OperationState.ACKNOWLEDGED,
        Evidence("operator", {"expired": True}),
    )
    records = _records(path)
    for rec in records:
        if rec.get("operation_id") == expired_ext.operation_id:
            if "started_at" in rec:
                rec["started_at"] = old_iso
            if "recorded_at" in rec:
                rec["recorded_at"] = old_iso
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")

    recent_ext_ids: list[str] = []
    for idx in range(5):
        rh = journal.begin("external.github.create_issue", f"rec-{idx}", external_metadata=ext_meta)
        assert rh is not None
        recent_ext_ids.append(rh.operation_id)
        journal.mark_uncertain(rh, "call_timeout")
        journal.acknowledge(
            rh.operation_id,
            OperationState.ACKNOWLEDGED,
            Evidence("operator", {"recent_idx": idx}),
        )

    journal.compact()

    # All 4 native resolved operations survive
    for nid in native_ids:
        assert journal.inspect(nid)["state"] == "acknowledged"

    # All 3 unresolved external operations survive
    assert journal.inspect(u_prep.operation_id)["state"] == "running"
    assert journal.inspect(u_disp.operation_id)["state"] == "running"
    assert journal.inspect(u_unc.operation_id)["state"] == "uncertain"

    # Expired external terminal operation was pruned
    with pytest.raises(ToolError) as exc_info:
        journal.inspect(expired_ext.operation_id)
    assert exc_info.value.code == "operation_not_found"

    # Oldest 2 recent external terminal operations pruned; newest 3 retained
    for dropped_id in recent_ext_ids[:2]:
        with pytest.raises(ToolError):
            journal.inspect(dropped_id)
    for kept_id in recent_ext_ids[2:]:
        inspected = journal.inspect(kept_id)
        assert inspected["state"] == "acknowledged"
        assert inspected["external_outcome"] == "acknowledged"


def test_m10_3_interprocess_atomic_admission_and_cas_ack_reconcile_conflict(
    tmp_path: Path,
) -> None:
    import subprocess
    import sys
    import time

    from core.recovery_models import Postcondition

    repo = Path(__file__).resolve().parents[1]
    path = tmp_path / "interprocess.jsonl"
    go = tmp_path / "go"

    # Seed one uncertain operation for CAS conflict test
    seed_journal = OperationRecoveryJournal(max_unresolved_external=6)
    seed_journal.configure(path, "runtime-seed")
    ext_meta = _sample_external_metadata()
    conflict_handle = seed_journal.begin(
        "external.github.create_issue",
        "conflict-op",
        external_metadata=ext_meta,
    )
    assert conflict_handle is not None
    seed_journal.mark_uncertain(conflict_handle, "call_timeout")

    # Also seed a second uncertain operation for ack vs reconcile conflict
    reconcile_vs_ack = seed_journal.begin(
        "external.github.create_issue",
        "reconcile-vs-ack",
        external_metadata=ext_meta,
    )
    assert reconcile_vs_ack is not None
    seed_journal.mark_uncertain(reconcile_vs_ack, "call_timeout")
    postcondition = Postcondition(kind="file_absent", expected={"path": str(tmp_path / "missing.txt")})
    conc_evidence = Evidence(source="filesystem", data={"exists": False, "conclusive": True})
    seed_journal.record_reconciliation(
        reconcile_vs_ack.operation_id,
        OperationState.SUCCEEDED,
        postcondition,
        conc_evidence,
    )
    with pytest.raises(ToolError) as ack_after_rec:
        seed_journal.acknowledge(
            reconcile_vs_ack.operation_id,
            OperationState.ACKNOWLEDGED,
            Evidence("operator", {"note": "late ack"}),
        )
    assert ack_after_rec.value.code == "operation_already_reconciled"

    # Now conflict_handle is the 1 remaining unresolved external op (1 of 6 used).
    # Spawn 2 processes that each try to:
    # 1) resolve conflict_handle with DIFFERENT state/evidence (one ACKNOWLEDGED, one UNRESOLVABLE)
    # 2) admit 5 new external mutations each (10 total attempts, only 6 slots total when conflict_handle is resolved)
    worker_script = tmp_path / "cas_worker.py"
    worker_script.write_text(
        "\n".join(
            [
                "import json, sys, time",
                "from pathlib import Path",
                f"sys.path.insert(0, {str(repo)!r})",
                "from core.errors import ToolError",
                "from core.recovery import OperationRecoveryJournal",
                "from core.recovery_models import Evidence, ExternalOperationMetadata, OperationState",
                "path = Path(sys.argv[1])",
                "go = Path(sys.argv[2])",
                "ready = Path(sys.argv[3])",
                "worker_id = int(sys.argv[4])",
                "conflict_op_id = sys.argv[5]",
                "journal = OperationRecoveryJournal(max_unresolved_external=6)",
                "journal.configure(path, 'runtime-seed')",
                "ready.write_text('ready', encoding='utf-8')",
                "deadline = time.monotonic() + 10.0",
                "while not go.exists():",
                "    if time.monotonic() >= deadline: raise SystemExit(3)",
                "    time.sleep(0.002)",
                "target_state = OperationState.ACKNOWLEDGED if worker_id == 0 else OperationState.UNRESOLVABLE",
                "ack_outcome = 'none'",
                "try:",
                "    journal.acknowledge(conflict_op_id, target_state, Evidence('operator', {'worker': worker_id}))",
                "    ack_outcome = 'won'",
                "except ToolError as exc:",
                "    ack_outcome = exc.code",
                "meta = ExternalOperationMetadata(",
                "    extension_id='github',",
                "    provider='GitHub MCP',",
                "    public_name='external.github.create_issue',",
                "    remote_name='create_issue',",
                "    catalog_generation=1,",
                "    schema_fingerprint='a' * 64,",
                "    mutation_class='remote_mutation',",
                "    dispatch_stage='prepared',",
                ")",
                "admitted = 0",
                "denied = 0",
                "for idx in range(5):",
                "    try:",
                "        journal.begin('external.github.create_issue', f'w{worker_id}-{idx}', external_metadata=meta)",
                "        admitted += 1",
                "    except ToolError as exc:",
                "        if exc.code == 'operation_limit':",
                "            denied += 1",
                "        else:",
                "            raise",
                "print(json.dumps({'ack_outcome': ack_outcome, 'admitted': admitted, 'denied': denied}))",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    procs: list[subprocess.Popen[str]] = []
    for wid in (0, 1):
        rfile = tmp_path / f"ready-w{wid}"
        procs.append(
            subprocess.Popen(
                [sys.executable, str(worker_script), str(path), str(go), str(rfile), str(wid), conflict_handle.operation_id],
                cwd=repo,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        )

    deadline = time.monotonic() + 10.0
    while len(list(tmp_path.glob("ready-w*"))) < 2 and time.monotonic() < deadline:
        time.sleep(0.002)
    assert len(list(tmp_path.glob("ready-w*"))) == 2
    go.write_text("go", encoding="utf-8")

    outputs: list[dict[str, Any]] = []
    for proc in procs:
        stdout, stderr = proc.communicate(timeout=30)
        assert proc.returncode == 0, stderr
        outputs.append(json.loads(stdout.strip()))

    assert sorted(o["ack_outcome"] for o in outputs) == ["operation_already_acknowledged", "won"]
    total_admitted = sum(int(o["admitted"]) for o in outputs)
    total_denied = sum(int(o["denied"]) for o in outputs)
    assert total_admitted == 6
    assert total_denied == 4
    assert seed_journal.unresolved_external_count() == 6


def test_m10_4_two_runtime_restart_proof_and_recovery_tool_parity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    import anyio
    from mcp import Client
    from mcp.server import MCPServer

    from core import recovery as recovery_mod
    from core.extensions.bridge import ExtensionMCPServer
    from core.extensions.manager import ExtensionLifecycleManager
    from core.extensions.models import (
        ExtensionDefinition,
        ExtensionState,
        MutationClass,
        ToolPolicy,
        TrustClass,
    )
    from core.lifecycle import RUNTIME_LIFECYCLE
    from tools.recovery import registry as recovery_tools_registry

    fixture_path = Path(__file__).resolve().parent / "fixtures" / "fake_mcp_server.py"
    ws = tmp_path / "ws_restart"
    ws.mkdir()
    journal_path = tmp_path / "operations.jsonl"

    defn = ExtensionDefinition(
        extension_id="fake",
        display_name="Fake Provider",
        provider="Fake MCP",
        trust_class=TrustClass.TRUSTED,
        launch_approved=True,
        enabled=True,
        command=sys.executable,
        arguments=(str(fixture_path), "--workspace", str(ws), "--mode", "uncertain_mutation"),
        working_directory=str(ws),
        platforms=("windows", "linux", "macos"),
        tools={
            "increment_value": ToolPolicy(
                remote_name="increment_value",
                domain="process",
                allow_call=True,
                mutation_class=MutationClass.REMOTE_MUTATION,
                profiles=("full",),
            ),
        },
    )

    def read_dispatch_count() -> int:
        counter = ws / "counter.json"
        return int(counter.read_text(encoding="utf-8")) if counter.exists() else 0

    async def run_restart_proof() -> None:
        with anyio.fail_after(30):
            RUNTIME_LIFECYCLE.configure(None, None)
            journal1 = OperationRecoveryJournal()
            journal1.configure(journal_path, "runtime-r1")
            monkeypatch.setattr(recovery_mod, "OPERATION_RECOVERY", journal1)

            # Runtime 1: default ExtensionLifecycleManager (M10.4 gate open)
            mgr1 = ExtensionLifecycleManager(recovery_journal=journal1)
            assert mgr1.is_mutation_gate_closed is False
            mgr1.register_extension(defn)

            keep1 = anyio.Event()
            op_id = ""
            async with anyio.create_task_group() as tg1:
                await mgr1.start_all_background(tg1, keep_alive_event=keep1)
                for _ in range(200):
                    if mgr1.get_state("fake") == ExtensionState.HEALTHY:
                        break
                    await anyio.sleep(0.02)
                assert mgr1.get_state("fake") == ExtensionState.HEALTHY

                server1 = ExtensionMCPServer("test-r1", tool_profile="full")
                server1.extension_manager = mgr1
                recovery_tools_registry.register(server1)

                async with Client(server1) as client1:
                    tools1 = await client1.list_tools()
                    tool_names1 = {t.name for t in tools1.tools}
                    assert "external.fake.increment_value" in tool_names1

                    call_res = await client1.call_tool(
                        "external.fake.increment_value",
                        {"amount": 7},
                    )
                    assert call_res.structured_content is not None
                    sc = call_res.structured_content
                    assert sc["ok"] is False
                    assert sc["outcome"] == "uncertain", sc
                    assert sc["error"] == "uncertain_side_effect"
                    assert isinstance(sc["operation_id"], str)
                    op_id = sc["operation_id"]

                # Durable marker proves provider executed the side effect exactly once
                assert read_dispatch_count() == 1

                # Disable, catalog retirement, and transport loss NEVER resolve an uncertain op
                owner1 = mgr1.get_owner("fake")
                assert owner1 is not None
                owner1.handle_transport_loss("transport_eof", mgr1.registry)
                assert journal1.inspect(op_id)["state"] == "uncertain"
                mgr1.registry.retire_extension_catalog("fake")
                assert journal1.inspect(op_id)["state"] == "uncertain"
                await mgr1.disable_extension("fake")
                assert journal1.inspect(op_id)["state"] == "uncertain"

                await mgr1.drain_and_stop()
                keep1.set()

            # Runtime 2: fresh journal and manager on the same operations.jsonl
            RUNTIME_LIFECYCLE.configure(None, None)
            journal2 = OperationRecoveryJournal()
            journal2.configure(journal_path, "runtime-r2")
            monkeypatch.setattr(recovery_mod, "OPERATION_RECOVERY", journal2)

            mgr2 = ExtensionLifecycleManager(recovery_journal=journal2)
            mgr2.register_extension(defn)
            keep2 = anyio.Event()
            async with anyio.create_task_group() as tg2:
                await mgr2.start_all_background(tg2, keep_alive_event=keep2)
                for _ in range(200):
                    if mgr2.get_state("fake") == ExtensionState.HEALTHY:
                        break
                    await anyio.sleep(0.02)
                assert mgr2.get_state("fake") == ExtensionState.HEALTHY

                # Verify no automatic replay happened on Runtime 2 startup/recovery
                assert read_dispatch_count() == 1

                server2 = MCPServer("test-r2")
                recovery_tools_registry.register(server2)
                async with Client(server2) as client2:
                    listed = await client2.call_tool("list_uncertain_operations", {})
                    assert listed.structured_content is not None
                    assert listed.structured_content["count"] == 1
                    item = listed.structured_content["items"][0]
                    assert item["operation_id"] == op_id
                    assert item["state"] == "uncertain"
                    assert item["external_outcome"] == "uncertain"
                    assert item["external"]["extension_id"] == "fake"
                    assert item["external"]["public_name"] == "external.fake.increment_value"
                    assert item["external"]["dispatch_stage"] == "may_dispatch"

                    inspected = await client2.call_tool(
                        "inspect_uncertain_operation",
                        {"operation_id": op_id},
                    )
                    assert inspected.structured_content is not None
                    assert inspected.structured_content["state"] == "uncertain"
                    assert inspected.structured_content["external"]["external_outcome"] == "uncertain"
                    raw_json = json.dumps(inspected.structured_content)
                    assert "amount" not in raw_json

                    hist = await client2.call_tool(
                        "get_operation_history",
                        {"operation_id": op_id},
                    )
                    assert hist.structured_content is not None
                    assert hist.structured_content["count"] >= 3

                    acked = await client2.call_tool(
                        "acknowledge_uncertain_operation",
                        {
                            "operation_id": op_id,
                            "outcome": "acknowledged",
                            "note": "Verified external counter state manually after restart.",
                        },
                    )
                    assert acked.structured_content is not None
                    assert acked.structured_content["state"] == "acknowledged"
                    assert acked.structured_content["external_outcome"] == "acknowledged"

                    listed_after = await client2.call_tool("list_uncertain_operations", {})
                    assert listed_after.structured_content is not None
                    assert listed_after.structured_content["count"] == 0

                # Still zero replay after inspection and acknowledgement
                assert read_dispatch_count() == 1
                await mgr2.drain_and_stop()
                keep2.set()

    anyio.run(run_restart_proof)


def test_external_dispatch_reads_bounded_legacy_history_and_torn_tail(tmp_path: Path) -> None:
    import tracemalloc

    path = tmp_path / "large-journal.jsonl"
    journal = OperationRecoveryJournal()
    journal.configure(path, "current")
    with path.open("wb") as stream:
        for idx in range(3000):
            stream.write((json.dumps({"schema_version": 1, "type": "begin", "operation_id": f"native-{idx}",
                                     "runtime_id": "current", "target": "x" * 1000}) + "\n").encode())
            stream.write((json.dumps({"schema_version": 1, "type": "acknowledged", "operation_id": f"native-{idx}",
                                     "state": "acknowledged"}) + "\n").encode())
        stream.write(b"{" + b"x" * (4 * 1024 * 1024))  # Torn final append, without newline.

    tracemalloc.start()
    try:
        assert journal.has_external_mutation_capacity()
        handle = journal.begin("external.fake.increment_value", external_metadata=_sample_external_metadata())
        assert handle is not None
        journal.checkpoint_external_dispatch(handle)
        journal.finish(handle, known_result="ok", external_outcome="succeeded")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 2 * 1024 * 1024, peak
    assert journal.inspect(handle.operation_id)["state"] == "succeeded"
    assert journal.inspect("native-2999")["state"] == "acknowledged"


def test_two_core_processes_recover_crash_after_effect_without_replay(tmp_path: Path) -> None:
    import subprocess
    import sys

    repo = Path(__file__).resolve().parents[1]
    fixture = repo / "tests" / "fixtures" / "fake_mcp_server.py"
    definition = tmp_path / "definition.json"
    definition.write_text(json.dumps({
        "extension_id": "fake", "display_name": "Fixture", "provider": "fixture", "enabled": True,
        "launch_approved": True, "trust_class": "trusted", "command": sys.executable,
        "arguments": [str(fixture), "--workspace", str(tmp_path), "--mode", "uncertain_mutation"],
        "working_directory": str(tmp_path), "platforms": ["windows", "linux", "macos"],
        "tools": {"increment_value": {
            "remote_name": "increment_value", "domain": "process", "mutation_class": "remote_mutation",
            "allow_call": True, "profiles": ["full"],
        }},
    }), encoding="utf-8")
    script = '''
import json, os, sys
from pathlib import Path
import anyio
from mcp import Client
from core import recovery
from core.recovery import OperationRecoveryJournal
from core.extensions.bridge import ExtensionMCPServer
from core.extensions.manager import ExtensionLifecycleManager
from core.extensions.models import ExtensionDefinition, ExtensionState
from tools.recovery.registry import register
workspace, mode = Path(sys.argv[1]), sys.argv[2]
class CrashJournal(OperationRecoveryJournal):
    def mark_uncertain(self, handle, reason_code, **kwargs):
        os._exit(41)  # Crash after durable provider effect, before uncertainty is persisted.
async def scenario():
    journal = (CrashJournal if mode == "crash" else OperationRecoveryJournal)()
    journal.configure(workspace / "operations.jsonl", mode)
    recovery.OPERATION_RECOVERY = journal
    manager = ExtensionLifecycleManager(recovery_journal=journal)
    manager.register_extension(ExtensionDefinition.model_validate_json((workspace / "definition.json").read_text()))
    async with anyio.create_task_group() as tasks:
        await manager.start_all_background(tasks, keep_alive_event=anyio.Event())
        with anyio.fail_after(10):
            while manager.get_state("fake") != ExtensionState.HEALTHY:
                await anyio.sleep(0.01)
        server = ExtensionMCPServer("core-restart-test")
        server.extension_manager = manager
        register(server)
        async with Client(server) as client:
            if mode == "crash":
                eligibility = manager.tool_eligibility("external.fake.increment_value")
                assert eligibility.eligible, eligibility
                assert "external.fake.increment_value" in {tool.name for tool in (await client.list_tools()).tools}
                result = await client.call_tool("external.fake.increment_value", {"amount": 2})
                raise AssertionError(result.structured_content)
            listing = (await client.call_tool("list_uncertain_operations", {})).structured_content
            assert listing["count"] == 1
            operation = listing["items"][0]
            assert operation["external"]["dispatch_stage"] == "may_dispatch"
            assert operation["external_outcome"] == "uncertain"
            assert len(operation["external"]["policy_revision"]) == 64
            assert (workspace / "counter.json").read_text() == "1"
            ack = (await client.call_tool("acknowledge_uncertain_operation", {
                "operation_id": operation["operation_id"], "outcome": "acknowledged", "note": "Counter verified once",
            })).structured_content
            assert ack["state"] == "acknowledged"
            assert (await client.call_tool("list_uncertain_operations", {})).structured_content["count"] == 0
            assert (workspace / "counter.json").read_text() == "1"
        await manager.drain_and_stop()
anyio.run(scenario)
'''
    crashed = subprocess.run([sys.executable, "-c", script, str(tmp_path), "crash"],
                             cwd=repo, capture_output=True, text=True, timeout=30)
    assert crashed.returncode == 41, crashed.stderr[-2000:]
    records = _records(tmp_path / "operations.jsonl")
    assert [item["type"] for item in records] == ["begin", "checkpoint"]
    assert (tmp_path / "counter.json").read_text(encoding="utf-8") == "1"
    restarted = subprocess.run([sys.executable, "-c", script, str(tmp_path), "restart"],
                               cwd=repo, capture_output=True, text=True, timeout=30)
    assert restarted.returncode == 0, restarted.stderr[-2000:]
    assert (tmp_path / "counter.json").read_text(encoding="utf-8") == "1"


@pytest.mark.parametrize("mode", ["provider_error", "invalid_output", "malformed", "oversize", "call_timeout", "cancel"])
def test_possible_dispatch_failures_stay_uncertain_without_retry(tmp_path: Path, mode: str) -> None:
    import sys

    import anyio

    from core.extensions.manager import ExtensionLifecycleManager
    from core.extensions.models import ExtensionDefinition, ExtensionState

    fixture = Path(__file__).parent / "fixtures" / "fake_mcp_server.py"
    definition = ExtensionDefinition.model_validate_json(json.dumps({
        "extension_id": "fake", "display_name": "Fixture", "provider": "fixture", "enabled": True,
        "launch_approved": True, "trust_class": "trusted", "command": sys.executable,
        "arguments": [str(fixture.resolve()), "--workspace", str(tmp_path), "--mode", "call_timeout" if mode == "cancel" else mode],
        "working_directory": str(tmp_path), "platforms": ["windows", "linux", "macos"],
        "tools": {"increment_value": {"remote_name": "increment_value", "domain": "process",
                 "mutation_class": "remote_mutation", "allow_call": True, "profiles": ["full"]}},
    }))
    journal = OperationRecoveryJournal()
    journal.configure(tmp_path / "operations.jsonl", "taxonomy")
    manager = ExtensionLifecycleManager(recovery_journal=journal)
    manager.register_extension(definition)

    async def scenario() -> None:
        with anyio.fail_after(30):
            keep = anyio.Event()
            async with anyio.create_task_group() as tasks:
                await manager.start_all_background(tasks, keep_alive_event=keep)
                with anyio.fail_after(10):
                    while manager.get_state("fake") != ExtensionState.HEALTHY:
                        await anyio.sleep(0.01)
                lease = manager.registry.create_lease("external.fake.increment_value")
                assert lease is not None
                if mode == "cancel":
                    async def cancel_after_effect(scope: anyio.CancelScope) -> None:
                        with anyio.fail_after(10):
                            while not (tmp_path / "effect_committed").exists():
                                await anyio.sleep(0.01)
                        scope.cancel()

                    with anyio.CancelScope() as cancel:
                        async with anyio.create_task_group() as cancellation_tasks:
                            cancellation_tasks.start_soon(cancel_after_effect, cancel)
                            await manager.execute_guarded_call(lease, {"amount": 2})
                    assert cancel.cancel_called
                else:
                    result = await manager.execute_guarded_call(lease, {"amount": 2}, timeout_sec=0.5)
                    assert result["outcome"] == "uncertain", result
                operations = journal.list_operations(state=OperationState.UNCERTAIN)
                assert operations["count"] == 1
                row = manager.health_snapshot(unresolved_external_count=1)["items"][0]
                assert row["calls"]["uncertain"] == 1
                assert row["calls"]["failed"] == row["calls"]["succeeded"] == 0
                assert operations["items"][0]["external"]["dispatch_stage"] == "may_dispatch"
                assert (tmp_path / "counter.json").read_text(encoding="utf-8") == "1"
                await manager.drain_and_stop()
                keep.set()
                assert (tmp_path / "counter.json").read_text(encoding="utf-8") == "1"

    anyio.run(scenario)


def test_delayed_dispatch_cancellation_stays_uncertain_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import anyio

    from core.extensions.manager import ExtensionOwner

    original = ExtensionOwner._call_tool

    async def delayed(owner: ExtensionOwner, *args: Any, **kwargs: Any) -> Any:
        await anyio.sleep(0.3)
        return await original(owner, *args, **kwargs)

    monkeypatch.setattr(ExtensionOwner, "_call_tool", delayed)
    test_possible_dispatch_failures_stay_uncertain_without_retry(tmp_path, "cancel")


def test_contended_journal_respects_dispatch_deadline_and_readonly_is_responsive(tmp_path: Path) -> None:
    import subprocess
    import sys
    import time
    from dataclasses import replace

    import anyio
    from mcp import types
    from test_extension_security import admitted_call

    from core.extensions.models import MutationClass

    manager, _, session = admitted_call({"type": "object"}, types.CallToolResult(content=[]))
    journal = OperationRecoveryJournal()
    path = tmp_path / "operations.jsonl"
    journal.configure(path, "contended")
    manager._recovery_journal = journal
    descriptor = manager.list_tools()[0]
    descriptor = replace(descriptor, catalog_generation=descriptor.catalog_generation + 1,
                         policy=descriptor.policy.model_copy(update={"mutation_class": MutationClass.LOCAL_MUTATION}))
    manager.registry.publish_extension_catalog(descriptor.extension_id, descriptor.catalog_generation, (descriptor,))
    lease = manager.registry.create_lease(descriptor.public_name)
    assert lease is not None
    readonly, read_lease, read_session = admitted_call({"type": "object"}, types.CallToolResult(content=[]))
    readonly._recovery_journal = journal
    ready = tmp_path / "ready"
    release = tmp_path / "release"
    code = '''
import sys, time
from pathlib import Path
from core.file_lock import exclusive_file_lock
workspace = Path(sys.argv[1])
with exclusive_file_lock(workspace / ".operations.jsonl.lock", timeout_sec=5):
    (workspace / "ready").touch()
    while not (workspace / "release").exists():
        time.sleep(0.01)
'''
    locker = subprocess.Popen([sys.executable, "-c", code, str(tmp_path)], cwd=Path(__file__).resolve().parents[1])
    try:
        until = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < until:
            time.sleep(0.01)
        assert ready.exists()

        async def scenario() -> None:
            errors: list[str] = []

            async def mutate() -> None:
                started = time.monotonic()
                with pytest.raises(ToolError) as caught:
                    await manager.execute_guarded_call(lease, {}, timeout_sec=0.2)
                errors.append(caught.value.code)
                assert time.monotonic() - started < 1.0

            async with anyio.create_task_group() as tasks:
                tasks.start_soon(mutate)
                await anyio.sleep(0.02)
                with anyio.fail_after(0.2):
                    assert (await readonly.execute_guarded_call(read_lease, {}))["ok"]
            assert errors[0] in {"operation_journal_unavailable", "admission_limit_exceeded"}
            session.send_request.assert_not_awaited()
            read_session.send_request.assert_awaited_once()
            assert journal.list_operations()["count"] == 0

        anyio.run(scenario)
    finally:
        release.touch()
        try:
            locker.wait(timeout=5)
        finally:
            if locker.poll() is None:
                locker.kill()
                locker.wait(timeout=5)


@pytest.mark.parametrize("revocation", ["catalog", "trust", "unchanged"])
def test_authority_revoked_during_durable_checkpoint_never_dispatches(tmp_path: Path, revocation: str) -> None:
    import threading
    from dataclasses import replace

    import anyio
    from mcp import types
    from test_extension_security import admitted_call

    from core.extensions.models import MutationClass, TrustClass

    entered, release = threading.Event(), threading.Event()

    class PausedCheckpoint(OperationRecoveryJournal):
        def checkpoint_external_dispatch(self, handle: Any) -> None:
            super().checkpoint_external_dispatch(handle)
            entered.set()
            assert release.wait(timeout=5)

    journal = PausedCheckpoint(max_unresolved_external=1)
    journal.configure(tmp_path / "operations.jsonl", "race")
    manager, _, session = admitted_call({"type": "object"}, types.CallToolResult(content=[]))
    manager._recovery_journal = journal
    descriptor = manager.list_tools()[0]
    descriptor = replace(descriptor, catalog_generation=descriptor.catalog_generation + 1,
                         policy=descriptor.policy.model_copy(update={"mutation_class": MutationClass.LOCAL_MUTATION}))
    manager.registry.publish_extension_catalog(descriptor.extension_id, descriptor.catalog_generation, (descriptor,))
    lease = manager.registry.create_lease(descriptor.public_name)
    assert lease is not None
    results: list[dict[str, Any]] = []

    async def call() -> None:
        results.append(await manager.execute_guarded_call(lease, {}))

    async def scenario() -> None:
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(call)
            assert await anyio.to_thread.run_sync(entered.wait, 5)
            try:
                if revocation == "catalog":
                    manager.registry.retire_extension_catalog(descriptor.extension_id)
                elif revocation == "trust":
                    owner = manager.get_owner(descriptor.extension_id)
                    assert owner is not None
                    owner.definition = owner.definition.model_copy(update={"trust_class": TrustClass.UNTRUSTED})
            finally:
                release.set()
        if revocation == "unchanged":
            session.send_request.assert_awaited_once()
            assert results[0]["outcome"] == "succeeded"
            assert journal.unresolved_external_count() == 0
        else:
            session.send_request.assert_not_awaited()
            assert results[0]["outcome"] == "uncertain"
            assert journal.list_operations(state=OperationState.UNCERTAIN)["count"] == 1

    anyio.run(scenario)


def test_m10_4_direct_wire_router_mutation_eligibility_and_denial_parity(
    tmp_path: Path,
) -> None:
    import sys

    import anyio
    from mcp import Client

    from core.execution_models import ExecutionIntent, RecoverabilityClass, RouteClass
    from core.execution_router import ExecutionRouter
    from core.extensions.bridge import ExtensionMCPServer
    from core.extensions.manager import ExtensionLifecycleManager
    from core.extensions.models import (
        ExtensionDefinition,
        ExtensionState,
        MutationClass,
        ToolPolicy,
        TrustClass,
    )
    from core.lifecycle import RUNTIME_LIFECYCLE
    from core.platform import detect_capabilities

    fixture_path = Path(__file__).resolve().parent / "fixtures" / "fake_mcp_server.py"

    def make_defn(
        ws: Path,
        *,
        trust_class: TrustClass = TrustClass.TRUSTED,
        mutation_class: MutationClass = MutationClass.DESTRUCTIVE_REMOTE_MUTATION,
        allow_call: bool = True,
        profiles: tuple[str, ...] = ("full", "minimal"),
    ) -> ExtensionDefinition:
        return ExtensionDefinition(
            extension_id="fake",
            display_name="Fake Provider",
            provider="Fake MCP",
            trust_class=trust_class,
            launch_approved=True,
            enabled=True,
            command=sys.executable,
            arguments=(str(fixture_path), "--workspace", str(ws), "--mode", "mutation_success"),
            working_directory=str(ws),
            platforms=("windows", "linux", "macos"),
            tools={
                "increment_value": ToolPolicy(
                    remote_name="increment_value",
                    domain="process",
                    intents=("external.mutate.counter",),
                    allow_call=allow_call,
                    mutation_class=mutation_class,
                    determinism=4,
                    recoverability=RecoverabilityClass.MEDIUM,
                    profiles=profiles,
                ),
            },
        )

    def read_dispatch_count(ws: Path) -> int:
        counter = ws / "counter.json"
        return int(counter.read_text(encoding="utf-8")) if counter.exists() else 0

    async def run_parity_checks() -> None:
        with anyio.fail_after(30):
            RUNTIME_LIFECYCLE.configure(None, None)

            # 1. Eligible across direct, MCP wire, and ExecutionRouter when all M10.4 conditions hold
            ws_ok = tmp_path / "ws_ok"
            ws_ok.mkdir()
            journal_ok = OperationRecoveryJournal(max_unresolved_external=2)
            journal_ok.configure(tmp_path / "ok.jsonl", "runtime-ok")
            mgr_ok = ExtensionLifecycleManager(recovery_journal=journal_ok)
            mgr_ok.register_extension(
                make_defn(
                    ws_ok,
                    trust_class=TrustClass.TRUSTED,
                    mutation_class=MutationClass.DESTRUCTIVE_REMOTE_MUTATION,
                    allow_call=True,
                )
            )
            keep_ok = anyio.Event()
            async with anyio.create_task_group() as tg:
                await mgr_ok.start_all_background(tg, keep_alive_event=keep_ok)
                for _ in range(200):
                    if mgr_ok.get_state("fake") == ExtensionState.HEALTHY:
                        break
                    await anyio.sleep(0.02)
                assert mgr_ok.get_state("fake") == ExtensionState.HEALTHY

                # Eligible in registry / manager
                el_full = mgr_ok.tool_eligibility("external.fake.increment_value", active_profile="full")
                assert el_full.eligible is True

                # Eligible in ExecutionRouter (with allow_destructive=True for destructive_remote_mutation)
                router = ExecutionRouter(
                    registered_tools={tool.public_name for tool in mgr_ok.list_tools()},
                    capabilities=detect_capabilities(), extension_manager=mgr_ok,
                )
                sel = router.recommend(ExecutionIntent("external.mutate.counter", destructive=True))
                assert sel["ok"] is True, sel
                assert sel["selected_route"] == RouteClass.EXTERNAL_MCP.value
                assert sel["representative_tool"] == "external.fake.increment_value"

                # Direct call succeeds and increments counter to 1
                lease = mgr_ok.registry.create_lease("external.fake.increment_value")
                assert lease is not None
                direct_res = await mgr_ok.execute_guarded_call(lease, {"amount": 2})
                assert direct_res["ok"] is True
                assert direct_res["outcome"] == "succeeded"
                assert read_dispatch_count(ws_ok) == 1

                # MCP wire call succeeds and increments counter to 2
                bridge_full = ExtensionMCPServer("bridge-full", tool_profile="full")
                bridge_full.extension_manager = mgr_ok
                async with Client(bridge_full) as client_full:
                    tools_full = await client_full.list_tools()
                    assert "external.fake.increment_value" in {t.name for t in tools_full.tools}
                    wire_res = await client_full.call_tool("external.fake.increment_value", {"amount": 3})
                    assert wire_res.structured_content is not None
                    assert wire_res.structured_content["ok"] is True
                    assert wire_res.structured_content["outcome"] == "succeeded"
                assert read_dispatch_count(ws_ok) == 2

                # Minimal profile denies non-read_only across eligibility, wire list/call, and router
                el_min = mgr_ok.tool_eligibility("external.fake.increment_value", active_profile="minimal")
                assert el_min.eligible is False
                assert el_min.rejection_code == "profile_mismatch"

                router_min = ExecutionRouter(
                    registered_tools={tool.public_name for tool in mgr_ok.list_tools()},
                    capabilities=detect_capabilities(), extension_manager=mgr_ok,
                    active_profile="minimal",
                )
                sel_min = router_min.recommend(ExecutionIntent("external.mutate.counter", destructive=True))
                assert sel_min["ok"] is False

                bridge_min = ExtensionMCPServer("bridge-min", tool_profile="minimal")
                bridge_min.extension_manager = mgr_ok
                async with Client(bridge_min) as client_min:
                    tools_min = await client_min.list_tools()
                    assert "external.fake.increment_value" not in {t.name for t in tools_min.tools}
                    wire_min = await client_min.call_tool("external.fake.increment_value", {"amount": 3})
                    assert wire_min.is_error is True
                assert read_dispatch_count(ws_ok) == 2

                # Exhaust unresolved quota (max_unresolved_external=2) -> denied across eligibility, router, direct, wire
                ext_meta = _sample_external_metadata()
                q1 = journal_ok.begin("external.fake.increment_value", "q1", external_metadata=ext_meta)
                q2 = journal_ok.begin("external.fake.increment_value", "q2", external_metadata=ext_meta)
                assert q1 is not None and q2 is not None
                journal_ok.mark_uncertain(q1, "call_timeout")
                journal_ok.mark_uncertain(q2, "call_timeout")

                el_quota = mgr_ok.tool_eligibility("external.fake.increment_value", active_profile="full")
                assert el_quota.eligible is False
                assert el_quota.rejection_code == "operation_limit"

                router_quota = ExecutionRouter(
                    registered_tools={tool.public_name for tool in mgr_ok.list_tools()},
                    capabilities=detect_capabilities(), extension_manager=mgr_ok,
                )
                assert (
                    router_quota.recommend(ExecutionIntent("external.mutate.counter", destructive=True))["ok"]
                    is False
                )

                with pytest.raises(ToolError) as quota_err:
                    await mgr_ok.execute_guarded_call(lease, {"amount": 9})
                assert quota_err.value.code == "operation_limit"
                assert read_dispatch_count(ws_ok) == 2

                # Lifecycle draining/stopping -> denied with recovery_unavailable before dispatch
                journal_ok.acknowledge(
                    q1.operation_id,
                    OperationState.ACKNOWLEDGED,
                    Evidence("operator", {"clear": True}),
                )
                control = tmp_path / "control.json"
                RUNTIME_LIFECYCLE.configure(control, tmp_path / "status.json")
                control.write_text(json.dumps({"schema_version": 1, "command": "drain", "request_id": "test-drain"}), encoding="utf-8")
                assert RUNTIME_LIFECYCLE.poll_control() == "DRAINING"
                try:
                    el_drain = mgr_ok.tool_eligibility("external.fake.increment_value", active_profile="full")
                    assert el_drain.eligible is False
                    assert el_drain.rejection_code == "recovery_unavailable"
                    with pytest.raises(ToolError) as drain_err:
                        await mgr_ok.execute_guarded_call(lease, {"amount": 9})
                    assert drain_err.value.code == "recovery_unavailable"
                    assert read_dispatch_count(ws_ok) == 2
                finally:
                    RUNTIME_LIFECYCLE.configure(None, None)

                await mgr_ok.drain_and_stop()
                keep_ok.set()

            # 2. RESTRICTED trust and UNKNOWN mutation class denied even when journal is enabled
            for idx, (trust_cls, mut_cls, expected_el_code, expected_call_code) in enumerate(
                (
                    (
                        TrustClass.RESTRICTED,
                        MutationClass.LOCAL_MUTATION,
                        "restricted_trust_mutation_forbidden",
                        "restricted_trust_mutation_forbidden",
                    ),
                    (
                        TrustClass.TRUSTED,
                        MutationClass.UNKNOWN,
                        "unknown_mutation_class_forbidden",
                        "unknown_mutation_class_forbidden",
                    ),
                )
            ):
                ws_den = tmp_path / f"ws_den_{idx}"
                ws_den.mkdir()
                j_den = OperationRecoveryJournal()
                j_den.configure(tmp_path / f"den_{idx}.jsonl", f"runtime-den-{idx}")
                mgr_den = ExtensionLifecycleManager(recovery_journal=j_den)
                mgr_den.register_extension(
                    make_defn(ws_den, trust_class=trust_cls, mutation_class=mut_cls)
                )
                keep_den = anyio.Event()
                async with anyio.create_task_group() as tg:
                    await mgr_den.start_all_background(tg, keep_alive_event=keep_den)
                    for _ in range(200):
                        if mgr_den.get_state("fake") == ExtensionState.HEALTHY:
                            break
                        await anyio.sleep(0.02)
                    assert mgr_den.get_state("fake") == ExtensionState.HEALTHY

                    el_den = mgr_den.tool_eligibility("external.fake.increment_value", active_profile="full")
                    assert el_den.eligible is False
                    assert el_den.rejection_code == expected_el_code

                    router_den = ExecutionRouter(
                        registered_tools={tool.public_name for tool in mgr_den.list_tools()},
                        capabilities=detect_capabilities(), extension_manager=mgr_den,
                    )
                    assert router_den.recommend(ExecutionIntent("external.mutate.counter"))["ok"] is False

                    lease_den = mgr_den.registry.create_lease("external.fake.increment_value")
                    assert lease_den is not None
                    with pytest.raises(ToolError) as err_den:
                        await mgr_den.execute_guarded_call(lease_den, {"amount": 1})
                    assert err_den.value.code == expected_call_code
                    assert read_dispatch_count(ws_den) == 0

                    await mgr_den.drain_and_stop()
                    keep_den.set()

    anyio.run(run_parity_checks)

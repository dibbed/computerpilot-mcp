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

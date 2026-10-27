from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from mcp import Client
from mcp.server import MCPServer

from core import audit, timings
from core.jobs import JobStore
from core.tooling import compact_errors
from tools.project.service import iter_files


@pytest.fixture
def sinks(monkeypatch: pytest.MonkeyPatch) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    monkeypatch.setenv("MCP_TIMINGS", "1")
    monkeypatch.setattr(timings, "_enqueue_record", lambda path, record: records.append(record))
    monkeypatch.setattr(audit, "_get_writer", lambda: SimpleNamespace(submit=lambda payload, **kw: events.append(json.loads(payload))))
    return records, events


def test_direct_concurrent_invocations_link_audit_and_timing_without_context_leaks(
    sinks: tuple[list[dict[str, Any]], list[dict[str, Any]]],
) -> None:
    records, events = sinks

    @compact_errors("trace_probe")
    def probe() -> dict[str, Any]:
        audit.audit_action("trace_probe")
        timings.record_timing("probe", 1)
        return timings.current_trace_metadata()

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: probe(), range(16)))
    ids = {result["correlation_id"] for result in results}
    assert len(ids) == 16
    assert all(re.fullmatch(r"[0-9a-f]{32}", identity) for identity in ids)
    assert {record["correlation_id"] for record in records} == ids
    assert {event["correlation_id"] for event in events} == ids
    assert timings.current_trace_metadata() == {}


def test_job_trace_is_durable_private_and_excluded_from_idempotency_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core import job_scheduler

    monkeypatch.setattr(job_scheduler, "ensure_job_scheduler", lambda store: None)
    monkeypatch.setenv("MCP_TIMINGS", "0")
    store = JobStore(tmp_path / "jobs.sqlite3")
    command = [sys.executable, "-c", "print('trace-test')"]
    with timings.invocation_trace(new=True):
        first_trace = timings.current_trace_metadata()
        first = store.submit(command, tmp_path, 10, "same-command")
    with timings.invocation_trace(new=True):
        assert timings.current_trace_metadata() != first_trace
        second = store.submit(command, tmp_path, 10, "same-command")
    assert first["job_id"] == second["job_id"]
    assert second["deduplicated"] is True
    assert "_trace" not in second
    assert json.loads(store.raw(first["job_id"])["spec"])["_trace"] == first_trace
    assert store.trace_metadata(first["job_id"]) == first_trace


@pytest.mark.parametrize("metadata", [
    {"trace_schema_version": True, "correlation_id": "a" * 32},
    {"trace_schema_version": 1, "correlation_id": "secret-canary"},
    {"trace_schema_version": 1, "correlation_id": "a" * 32, "payload": "secret-canary"},
])
def test_invalid_durable_trace_never_becomes_sink_metadata(
    metadata: dict[str, Any], sinks: tuple[list[dict[str, Any]], list[dict[str, Any]]],
) -> None:
    records, events = sinks
    with timings.invocation_trace(metadata, new=True):
        trace = timings.current_trace_metadata()
        assert trace["correlation_id"] != metadata["correlation_id"]
        audit.audit_action("trace_probe")
        timings.record_timing("probe", 1)
    assert "secret-canary" not in json.dumps([records, events])


def test_trace_and_timing_failures_do_not_break_tool_or_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sinks: tuple[list[dict[str, Any]], list[dict[str, Any]]],
) -> None:
    def unavailable(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("secret-canary")

    monkeypatch.setattr(timings, "_enqueue_record", unavailable)
    with timings.invocation_trace(new=True):
        timings.record_timing("probe", 1)
    monkeypatch.setattr(timings.secrets, "token_hex", unavailable)
    with timings.invocation_trace(new=True):
        assert timings.current_trace_metadata() == {}
        from core import job_scheduler

        monkeypatch.setattr(job_scheduler, "ensure_job_scheduler", lambda store: None)
        store = JobStore(tmp_path / "jobs.sqlite3")
        assert store.submit([sys.executable], tmp_path, 1, "no-telemetry")["status"] == "queued"


def test_actual_mcp_requests_have_distinct_traces_including_validation_denial(
    sinks: tuple[list[dict[str, Any]], list[dict[str, Any]]],
) -> None:
    records, events = sinks
    server = MCPServer("trace-fixture", middleware=[timings.ToolRequestTimingMiddleware()])

    @server.tool(structured_output=True)
    @compact_errors("trace_rpc")
    def trace_rpc(value: int) -> dict[str, Any]:
        audit.audit_action("trace_rpc")
        return {"value": value, **timings.current_trace_metadata()}

    async def scenario() -> set[str]:
        async with Client(server) as client:
            results = await asyncio.gather(*(client.call_tool("trace_rpc", {"value": i}) for i in range(2)))
            denied = await client.call_tool("trace_rpc", {"value": "invalid"})
            assert denied.is_error
            return {result.structured_content["correlation_id"] for result in results if result.structured_content}

    ids = asyncio.run(scenario())
    assert len(ids) == 2
    assert {event["correlation_id"] for event in events} == ids
    pipelines = [record for record in records if record["phase"] == "request_pipeline"]
    assert len(pipelines) == 3
    assert all(re.fullmatch(r"[0-9a-f]{32}", record["correlation_id"]) for record in pipelines)


def test_dag_thread_workers_preserve_trace_without_replacing_recovery_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.workflow_actions import ACTION_HANDLERS
    from core.workflows import NATIVE_ONLY_ROUTING, StepDefinition, WorkflowDefinition, WorkflowExecutor, WorkflowState, WorkflowStore

    seen: list[dict[str, Any]] = []
    target = tmp_path / "present.txt"
    target.write_text("present", encoding="utf-8")

    def inspect(arguments: dict[str, Any], timeout_sec: float) -> dict[str, Any]:
        seen.append(timings.current_trace_metadata())
        return {"ok": True}

    monkeypatch.setitem(ACTION_HANDLERS, "check_file", inspect)
    store = WorkflowStore(tmp_path / "workflows.sqlite3")
    definition = WorkflowDefinition("trace-dag", tuple(
        StepDefinition(name=name, step_id=name, action="check_file", arguments={"path": str(target)})
        for name in ("left", "right")
    ), execution_mode="dag", max_parallel_steps=2)
    workflow = store.create(definition, initial_state=WorkflowState.QUEUED)
    with timings.invocation_trace(new=True), timings.trace_operation("b" * 32):
        expected = timings.current_trace_metadata()
        result = WorkflowExecutor(store, enable_dag_execution=True, route_planner=NATIVE_ONLY_ROUTING).execute(
            workflow["workflow_id"], owner_id="trace-fixture",
        )
    assert result["state"] == "completed"
    assert seen == [expected, expected]


def test_completion_telemetry_failure_does_not_turn_queued_job_into_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core import job_scheduler
    from tools.jobs import registry

    store = JobStore(tmp_path / "jobs.sqlite3")
    monkeypatch.setattr(job_scheduler, "ensure_job_scheduler", lambda store: None)

    def broken_event(*args: Any, **kwargs: Any) -> None:
        if kwargs.get("outcome") == "completed":
            raise OSError("secret-canary")

    monkeypatch.setattr(registry, "audit_action", broken_event)
    server = MCPServer("trace-completion")
    registry.register(server, store_getter=lambda: store)

    async def run() -> None:
        async with Client(server) as client:
            result = await client.call_tool("submit_job", {
                "executable": sys.executable, "idempotency_key": "completion-failure", "cwd": str(tmp_path),
            })
            assert not result.is_error
            assert result.structured_content and result.structured_content["status"] == "queued"

    asyncio.run(run())


@pytest.mark.parametrize("mode", ["success", "timeout", "crash", "legacy", "disabled"])
def test_real_job_scheduler_worker_and_wait_restore_private_trace(tmp_path: Path, mode: str) -> None:
    source = tmp_path / "source"
    repo = Path(__file__).resolve().parents[1]
    for package in ("core", "tools", "scripts"):
        files, truncated = iter_files(repo / package, 1_000)
        assert not truncated
        for path in files:
            if path.suffix == ".py":
                destination = source / path.relative_to(repo)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(path.read_bytes())
    script = r'''
import asyncio,json,os,sys,time
from pathlib import Path
from mcp import Client
from core import job_scheduler
from core.audit import flush_audit
from core.jobs import JobStore,same_process
from core.registry import create_server
from core.timings import flush_timings
mode=sys.argv[1]
job_scheduler.ensure_job_scheduler=lambda store:None
server=create_server()
async def run():
 async with Client(server) as client:
  code="import time;print('SOURCE_CANARY',flush=True);time.sleep("+('0.5' if mode=='timeout' else '0.01')+")"
  if mode=='crash':code+=";import os;os._exit(7)"
  args={'executable':sys.executable,'args':['-c',code],'cwd':str(Path.cwd()),'idempotency_key':'trace-fixture'}
  args['timeout_sec']=0.1 if mode=='timeout' else 10
  result=await client.call_tool('submit_job',args)
  submitted=result.structured_content
  assert submitted and submitted['ok'],submitted
  store=server.job_store_getter();job_id=submitted['job_id'];trace=store.trace_metadata(job_id)
  duplicate=(await client.call_tool('submit_job',args)).structured_content
  assert duplicate['deduplicated'] and duplicate['job_id']==job_id
  assert store.trace_metadata(job_id)==trace
  if mode=='legacy':
   spec=json.loads(store.raw(job_id)['spec']);spec.pop('_trace')
   with store.connect() as db:db.execute('UPDATE jobs SET spec=? WHERE id=?',(json.dumps(spec),job_id));db.commit()
  restored=JobStore(store.path)
  assert restored.trace_metadata(job_id)==({} if mode=='legacy' else trace)
  assert job_scheduler.JobScheduler(restored).launch_once()==1
  version=0;deadline=time.monotonic()+20
  while time.monotonic()<deadline:
   result=await client.call_tool('job_wait',{'job_id':job_id,'after_version':version,'timeout':1})
   state=result.structured_content;assert state and state['ok'],state
   version=state['version']
   if state['status'] in {'succeeded','failed','timed_out','cancelled','interrupted'}:break
  expected={'timeout':'timed_out','crash':'failed'}.get(mode,'succeeded')
  assert state['status']==expected,state
  deadline=time.monotonic()+10
  while same_process(store.raw(job_id)['worker_pid'],store.raw(job_id)['worker_created']):
   assert time.monotonic()<deadline,'worker did not exit'
   await asyncio.sleep(0.02)
  health=(await client.call_tool('server_health',{})).structured_content
  return {'trace':trace,'job_id':job_id,'status':state['status'],'health':health}
report=asyncio.run(run());flush_timings();flush_audit()
state=Path('.agent_state')
report['timings']=[]
if (state/'timings.jsonl').exists():report['timings']=[json.loads(line) for line in (state/'timings.jsonl').read_text().splitlines()]
report['audit']=[json.loads(line) for line in (state/'audit.jsonl').read_text().splitlines()]
print(json.dumps(report))
'''
    env = {key: value for key, value in os.environ.items() if not key.startswith(("MCP_", "PYTHON", "COMPUTERPILOT_"))}
    env.update(
        MCP_TIMINGS="0" if mode == "disabled" else "1", MCP_TOOL_PROFILE="full",
        MCP_RUNTIME_GENERATION_ID="f" * 32, TRACE_ENV_CANARY="ENV_CANARY",
    )
    process = subprocess.run([sys.executable, "-c", script, mode], cwd=source, env=env, capture_output=True, text=True, timeout=45)
    assert process.returncode == 0, process.stderr[-2_000:]
    report = json.loads(process.stdout)
    assert report["trace"]["parent_operation_id"]
    serialized = json.dumps([report["timings"], report["audit"], report["health"]])
    assert "SOURCE_CANARY" not in serialized and "ENV_CANARY" not in serialized
    submitted = next(event for event in report["audit"] if event["operation"] == "submit_job")
    assert submitted["correlation_id"] == report["trace"]["correlation_id"]
    completed = [event for event in report["audit"] if event["operation"] == "submit_job" and event["outcome"] == "completed"]
    assert len(completed) == 2
    assert {event["details"]["job_id"] for event in completed} == {report["job_id"]}
    assert len({event["correlation_id"] for event in completed}) == 2
    if mode == "timeout":
        terminated = next(event for event in report["audit"] if event["operation"] == "job_terminate_process_tree")
        assert terminated["correlation_id"] == report["trace"]["correlation_id"]
        assert terminated["parent_operation_id"] == report["trace"]["parent_operation_id"]
    if mode == "disabled":
        assert report["timings"] == []
    else:
        records = report["timings"]
        launch = next(record for record in records if record["phase"] == "worker_launch")
        worker = next(record for record in records if record["phase"] == "job_worker")
        if mode != "legacy":
            assert launch["correlation_id"] == worker["correlation_id"]
            assert worker["correlation_id"] == report["trace"]["correlation_id"]
            assert worker["parent_operation_id"] == report["trace"]["parent_operation_id"]
        else:
            # Old rows have no originating trace; their existing job ID remains the join key.
            assert launch["job_id"] == worker["job_id"] == report["job_id"]
            assert re.fullmatch(r"[0-9a-f]{32}", worker["correlation_id"])
        assert {"claim_wait", "process_spawn", "process_execution", "job_wait", "serialization"} <= {r["phase"] for r in records}

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import threading
import time
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psutil
import pytest

from core import reconcilers
from core.jobs import JobStore
from core.workflow_actions import ActionContext, execute_action
from core.workflows import StepDefinition, WorkflowDefinition, WorkflowExecutor, WorkflowState, WorkflowStore


@pytest.mark.parametrize("status", ["queued", "running", "orphaned", "succeeded", "failed"])
@pytest.mark.parametrize("kind", ["job_state", "job_request_key_state"])
def test_real_job_state_evidence(tmp_path, monkeypatch, status, kind):
    store = JobStore(tmp_path / "jobs.sqlite3")
    monkeypatch.setattr("core.job_scheduler.ensure_job_scheduler", lambda *_: None)
    monkeypatch.setattr(reconcilers, "JobStore", lambda: store)
    process = psutil.Process(os.getpid())
    with closing(store.connect()) as db, db:
        db.execute(
            "INSERT INTO jobs (id,request_key,fingerprint,spec,status,created,updated,"
            "worker_pid,worker_created,pid,pid_created) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("job-1", "request-1", "fingerprint", "{}", status, time.time(), time.time(),
             process.pid, process.create_time(), process.pid, process.create_time()),
        )
    evidence = getattr(reconcilers, "_" + kind)({
        "job_id": "job-1", "request_key": "request-1", "status": "succeeded",
    }).data
    assert evidence["job_id"] == "job-1"
    assert evidence["status"] == status
    if kind == "job_state":
        assert evidence["conclusive"] is True
    else:
        assert evidence["conclusive"] is (status in {"succeeded", "failed"})
    assert evidence["satisfied"] is (status == "succeeded")



def test_durable_job_workflow_completes_with_public_job_id_contract(tmp_path, monkeypatch):
    job_store = JobStore(tmp_path / "jobs.sqlite3")
    workflow_store = WorkflowStore(tmp_path / "workflows.sqlite3")
    request_key = "workflow-executor-job-id-contract"
    monkeypatch.setattr("core.workflow_actions.JobStore", lambda: job_store)
    monkeypatch.setattr(reconcilers, "JobStore", lambda: job_store)
    workflow = workflow_store.create(
        WorkflowDefinition(
            "job-id-contract",
            (
                StepDefinition(
                    "job",
                    "run_durable_job",
                    {
                        "executable": sys.executable,
                        "args": ["-c", "print('workflow-job-ok')"],
                        "cwd": str(tmp_path),
                        "idempotency_key": request_key,
                    },
                    postcondition={"kind": "job_request_key_intent"},
                ),
            ),
        ),
        initial_state=WorkflowState.QUEUED,
    )

    result = WorkflowExecutor(workflow_store).execute(
        str(workflow["workflow_id"]),
        owner_id="job-id-regression",
    )

    assert result["state"] == "completed"
    operation = workflow_store.list_operations(str(workflow["workflow_id"]))["items"][0]
    assert operation["state"] == "succeeded"
    assert operation["result"]["status"] == "succeeded"
    job_id = operation["result"]["job_id"]
    assert operation["external_ref"] == {"job_id": job_id, "request_key": request_key}
    assert job_store.get_by_request_key(request_key)["job_id"] == job_id
    assert job_store.output(job_id)["stdout"]["text"].strip() == "workflow-job-ok"


def test_durable_job_action_and_reconciler_use_public_job_id_contract(tmp_path, monkeypatch):
    store = JobStore(tmp_path / "jobs.sqlite3")
    external_refs = []
    request_key = "workflow-job-id-contract"
    monkeypatch.setattr("core.workflow_actions.JobStore", lambda: store)
    monkeypatch.setattr(reconcilers, "JobStore", lambda: store)
    context = ActionContext(
        workflow_id="workflow-regression",
        operation_id="operation-regression",
        is_cancel_requested=lambda: False,
        persist_external_ref=external_refs.append,
        persist_intent_evidence=lambda value: None,
    )
    arguments = {
        "executable": sys.executable,
        "args": ["-c", "print('durable-contract-ok')"],
        "cwd": str(tmp_path),
        "idempotency_key": request_key,
        "encoding": "utf-8",
    }

    result = execute_action("run_durable_job", arguments, 20, context)

    assert result["status"] == "succeeded"
    assert result["exit_code"] == 0
    job_id = result["job_id"]
    assert external_refs == [{"job_id": job_id, "request_key": request_key}]
    restored = store.get_by_request_key(request_key)
    assert restored["job_id"] == job_id
    assert "id" not in restored
    assert store.output(job_id)["stdout"]["text"].strip() == "durable-contract-ok"

    deduplicated = execute_action("run_durable_job", arguments, 20, context)
    assert deduplicated["job_id"] == job_id
    assert deduplicated["status"] == "succeeded"

    evidence = reconcilers._job_request_key_state(
        {"request_key": request_key, "status": "succeeded"}
    ).data
    assert evidence == {
        "conclusive": True,
        "satisfied": True,
        "request_key": request_key,
        "job_id": job_id,
        "status": "succeeded",
    }


@pytest.fixture
def http_endpoint():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            status = int(self.path.strip("/"))
            body = b"x" * (1_048_577 if status == 200 else 12)
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("status", [404, 500])
def test_http_error_status_is_observable(http_endpoint, status):
    evidence = reconcilers._http_response({
        "url": f"{http_endpoint}/{status}", "status": status,
        "sha256": hashlib.sha256(b"x" * 12).hexdigest(),
    }).data
    assert evidence["conclusive"] is True
    assert evidence["satisfied"] is True


@pytest.mark.parametrize("wanted_hash", [hashlib.sha256(b"x" * 1_048_577).hexdigest(), "0" * 64])
def test_truncated_http_body_cannot_prove_hash(http_endpoint, wanted_hash):
    evidence = reconcilers._http_response({
        "url": f"{http_endpoint}/200", "sha256": wanted_hash,
    }).data
    assert evidence["conclusive"] is False
    assert evidence["satisfied"] is False
    assert evidence.get("sha256") is None


def test_http_status_only_survives_body_limit(http_endpoint):
    evidence = reconcilers._http_response({"url": f"{http_endpoint}/200"}).data
    assert evidence["conclusive"] is True
    assert evidence["satisfied"] is True
    assert evidence.get("sha256") is None


def test_git_index_unicode_literal_path(tmp_path):
    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    git("init")
    path = "فایل فارسی.txt"
    (tmp_path / path).write_text("content", encoding="utf-8")
    git("add", "--", path)
    evidence = reconcilers._git_index_contains({"repo": str(tmp_path), "paths": [path]}).data
    assert evidence["satisfied"] is True
    assert evidence["staged_paths"] == [path]

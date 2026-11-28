import json
import threading

import mcp_server


def _reset_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_server, "JOB_STATE_PATH", tmp_path / "jobs.json")
    with mcp_server.JOBS_LOCK:
        mcp_server.JOBS.clear()


def test_load_jobs_marks_inflight_failed(tmp_path, monkeypatch):
    _reset_jobs(tmp_path, monkeypatch)
    state = {
        "version": 1,
        "jobs": [
            {"job_id": "a", "status": "queued", "created_at": 1.0},
            {"job_id": "b", "status": "running", "created_at": 2.0, "started_at": 2.5},
            {"job_id": "c", "status": "completed", "created_at": 3.0, "result": {"ok": True}},
        ],
    }
    (tmp_path / "jobs.json").write_text(json.dumps(state), encoding="utf-8")

    mcp_server._load_jobs()

    jobs = mcp_server.JOBS
    assert jobs["a"]["status"] == "failed"
    assert jobs["b"]["status"] == "failed"
    assert "服务重启" in jobs["a"]["error"]
    assert jobs["c"]["status"] == "completed"
    assert jobs["c"]["result"] == {"ok": True}
    assert isinstance(jobs["a"]["cancel_event"], threading.Event)


def test_persist_jobs_strips_cancel_event(tmp_path, monkeypatch):
    _reset_jobs(tmp_path, monkeypatch)
    with mcp_server.JOBS_LOCK:
        mcp_server.JOBS["x"] = {
            "job_id": "x",
            "status": "running",
            "created_at": 1.0,
            "cancel_event": threading.Event(),
        }
        mcp_server._persist_jobs_locked()

    raw = json.loads((tmp_path / "jobs.json").read_text(encoding="utf-8"))
    assert raw["jobs"][0]["job_id"] == "x"
    assert "cancel_event" not in raw["jobs"][0]


def test_new_job_is_persisted(tmp_path, monkeypatch):
    _reset_jobs(tmp_path, monkeypatch)
    job = mcp_server._new_job()
    assert job["status"] == "queued"
    raw = json.loads((tmp_path / "jobs.json").read_text(encoding="utf-8"))
    assert raw["jobs"][0]["job_id"] == job["job_id"]


def test_run_job_marks_failed_when_result_unsuccessful(tmp_path, monkeypatch):
    _reset_jobs(tmp_path, monkeypatch)
    job = mcp_server._new_job()
    mcp_server._run_job(job, lambda: {"success": False, "error": "boom"})

    assert job["status"] == "failed"
    assert job["error"] == "boom"
    assert job["result"]["success"] is False


def test_persisted_job_result_is_truncated(tmp_path, monkeypatch):
    _reset_jobs(tmp_path, monkeypatch)
    monkeypatch.setattr(mcp_server, "JOB_RESULT_MAX_BYTES", 300_000)
    job = mcp_server._new_job()
    mcp_server._run_job(
        job, lambda: {"success": True, "content": "x" * 400_000, "log": "y" * 1000}
    )

    raw = json.loads((tmp_path / "jobs.json").read_text(encoding="utf-8"))
    persisted = raw["jobs"][0]["result"]
    assert persisted["result_truncated"] is True
    assert len(persisted["content"]) <= 200_000
    assert "log" not in persisted

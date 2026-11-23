# 由 mcp_server.py 拆分出的 MCP 工具实现；通过 srv 动态访问 mcp_server 的全局对象，保持可测性。
from smart3w_mcp_tools._server import srv


def smart3w_job(job_id: str = "", cancel: bool = False) -> dict[str, object]:
    """Query or cancel a background crawl job created with smart3w_crawl(run_async=true).

    Args:
        job_id: 任务 id；留空则列出最近任务
        cancel: 是否请求取消（仅 queued/running 任务有效）

    Returns:
        dict: {success, job_id?, status?, result?, error?} 或 {success, jobs, count}
    """
    if not isinstance(job_id, str):
        return {"success": False, "error": "job_id 必须是字符串", "error_code": "INVALID_ARGUMENT"}
    if not isinstance(cancel, bool):
        return {"success": False, "error": "cancel 必须是布尔值", "error_code": "INVALID_ARGUMENT"}
    job_id = job_id.strip()
    if not job_id:
        with srv.JOBS_LOCK:
            jobs = sorted(srv.JOBS.values(), key=lambda item: item.get("created_at", 0), reverse=True)
            summaries = []
            for job in jobs[:20]:
                view = srv._job_view(job)
                view.pop("result", None)
                summaries.append(view)
        return {"success": True, "jobs": summaries, "count": len(summaries)}

    with srv.JOBS_LOCK:
        job = srv.JOBS.get(job_id)
        if job is None:
            return {
                "success": False,
                "job_id": job_id,
                "error": "任务不存在（可能已过期、已被清理或服务曾重启）",
                "error_code": "JOB_NOT_FOUND",
            }
        if cancel and job.get("status") in ("queued", "running"):
            job["cancel_requested"] = True
            event = job.get("cancel_event")
            if event is not None:
                event.set()
            srv._persist_jobs_locked()
        return {"success": True, **srv._job_view(job)}

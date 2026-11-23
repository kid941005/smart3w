# 由 mcp_server.py 拆分出的 MCP 工具实现；通过 srv 动态访问 mcp_server 的全局对象，保持可测性。
from smart3w_mcp_tools._server import srv


def smart3w_doctor(check_search: bool = False) -> dict[str, object]:
    """Check if smart3w dependencies are installed and functional.

    Returns:
        dict: {success, output, rust?} 或 {success: False, error, error_code}
    """
    if not isinstance(check_search, bool):
        return {"success": False, "error": "check_search 必须是布尔值", "error_code": "INVALID_ARGUMENT"}
    args = ["doctor"]
    if check_search:
        args.append("--check-search")
    try:
        output = srv._run_fetch(args)
    except srv.FetchError as exc:
        return {"success": False, "error": str(exc), "error_code": exc.error_code}
    result: dict[str, object] = {"success": True, "output": output.strip()}
    try:
        import smart3w_rust

        result["rust"] = smart3w_rust.doctor()
    except Exception as exc:  # pragma: no cover - 诊断信息不应导致工具失败
        result["rust"] = {"available": False, "error": str(exc)}
    return result

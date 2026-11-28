mod html;
mod limits;
mod pdf;
mod sheet;

use std::io::{self, Read, Write};

fn read_stdin() -> Result<Vec<u8>, String> {
    let limit = limits::max_input_bytes();
    let mut buf = Vec::new();
    // 多读 1 字节用于判断是否超限，避免把超大文档整体读入内存
    io::stdin()
        .take(limit as u64 + 1)
        .read_to_end(&mut buf)
        .map_err(|e| format!("read stdin: {e}"))?;
    if buf.len() > limit {
        return Err(format!("input exceeds limit of {limit} bytes"));
    }
    Ok(buf)
}

fn read_payload() -> Option<Vec<u8>> {
    match read_stdin() {
        Ok(data) => Some(data),
        Err(e) => {
            emit(serde_json::json!({"ok": false, "error": e}));
            None
        }
    }
}

fn emit(v: serde_json::Value) {
    let out = serde_json::to_string(&v)
        .unwrap_or_else(|_| "{\"ok\":false,\"error\":\"json encode failed\"}".to_string());
    let mut stdout = io::stdout();
    let _ = stdout.write_all(out.as_bytes());
    let _ = stdout.flush();
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let cmd = args.get(1).map(|s| s.as_str()).unwrap_or("");
    match cmd {
        "version" | "--version" => {
            println!("smart3w-rs {}", env!("CARGO_PKG_VERSION"));
        }
        "html2md" => {
            let Some(data) = read_payload() else { return; };
            let html = String::from_utf8_lossy(&data).to_string();
            match html::to_markdown(&html, html::Extract::Readability) {
                Ok((md, title)) => emit(serde_json::json!({
                    "ok": true,
                    "markdown": md,
                    "meta": {"title": title, "chars": md.chars().count()}
                })),
                Err(e) => emit(serde_json::json!({"ok": false, "error": e})),
            }
        }
        "wechat2md" => {
            let Some(data) = read_payload() else { return; };
            let html = String::from_utf8_lossy(&data).to_string();
            match html::to_markdown(&html, html::Extract::Wechat) {
                Ok((md, title)) => emit(serde_json::json!({
                    "ok": true,
                    "markdown": md,
                    "meta": {"title": title, "chars": md.chars().count()}
                })),
                Err(e) => emit(serde_json::json!({"ok": false, "error": e})),
            }
        }
        "pdf2md" => {
            let Some(data) = read_payload() else { return; };
            // pdf-extract 对畸形 PDF 可能 panic；转成 JSON 错误，避免 sidecar 直接崩溃
            let outcome = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| pdf::to_markdown(&data)));
            match outcome {
                Ok(Ok((md, pages))) => emit(serde_json::json!({
                    "ok": true, "markdown": md, "meta": {"pages": pages}
                })),
                Ok(Err(e)) => emit(serde_json::json!({"ok": false, "error": e})),
                Err(_) => emit(serde_json::json!({"ok": false, "error": "pdf parser panicked on malformed input"})),
            }
        }
        "xlsx2md" => {
            let Some(data) = read_payload() else { return; };
            match sheet::xlsx_to_markdown(&data) {
                Ok((md, sheets)) => emit(serde_json::json!({
                    "ok": true, "markdown": md, "meta": {"sheets": sheets}
                })),
                Err(e) => emit(serde_json::json!({"ok": false, "error": e})),
            }
        }
        "csv2md" => {
            let Some(data) = read_payload() else { return; };
            match sheet::csv_to_markdown(&data) {
                Ok((md, rows)) => emit(serde_json::json!({
                    "ok": true, "markdown": md, "meta": {"rows": rows}
                })),
                Err(e) => emit(serde_json::json!({"ok": false, "error": e})),
            }
        }
        "doctor" => emit(serde_json::json!({
            "ok": true, "version": env!("CARGO_PKG_VERSION"),
            "capabilities": ["html2md", "wechat2md", "pdf2md", "xlsx2md", "csv2md"]
        })),
        _ => emit(serde_json::json!({
            "ok": false,
            "error": "usage: smart3w-rs <html2md|wechat2md|pdf2md|xlsx2md|csv2md|doctor|version>"
        })),
    }
}

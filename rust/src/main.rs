mod html;
mod pdf;
mod sheet;

use std::io::{self, Read, Write};

fn read_stdin() -> Vec<u8> {
    let mut buf = Vec::new();
    let _ = io::stdin().read_to_end(&mut buf);
    buf
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
            let data = read_stdin();
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
            let data = read_stdin();
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
            let data = read_stdin();
            match pdf::to_markdown(&data) {
                Ok((md, pages)) => emit(serde_json::json!({
                    "ok": true, "markdown": md, "meta": {"pages": pages}
                })),
                Err(e) => emit(serde_json::json!({"ok": false, "error": e})),
            }
        }
        "xlsx2md" => {
            let data = read_stdin();
            match sheet::xlsx_to_markdown(&data) {
                Ok((md, sheets)) => emit(serde_json::json!({
                    "ok": true, "markdown": md, "meta": {"sheets": sheets}
                })),
                Err(e) => emit(serde_json::json!({"ok": false, "error": e})),
            }
        }
        "csv2md" => {
            let data = read_stdin();
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

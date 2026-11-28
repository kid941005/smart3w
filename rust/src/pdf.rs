use crate::limits;

fn normalize(text: &str) -> String {
    let mut out: Vec<&str> = Vec::new();
    let mut blanks = 0usize;
    for line in text.lines() {
        if line.trim().is_empty() {
            blanks += 1;
        } else {
            if blanks > 0 && !out.is_empty() {
                out.push("");
            }
            blanks = 0;
            out.push(line.trim_end());
        }
    }
    out.join("\n").trim().to_string()
}

pub fn to_markdown(data: &[u8]) -> Result<(String, u32), String> {
    if data.is_empty() {
        return Err("empty pdf bytes".to_string());
    }
    // 只解析一次：pdf-extract 内部完成 PDF 解析并返回逐页文本。
    // 避免此前再次 lopdf::Document::load_mem 做页数统计带来的额外内存与栈风险。
    let pages_text = pdf_extract::extract_text_from_mem_by_pages(data)
        .map_err(|e| format!("pdf extract: {e}"))?;
    let max_text = limits::max_text_bytes();
    let mut parts: Vec<String> = Vec::with_capacity(pages_text.len());
    let mut total = 0usize;
    for page in &pages_text {
        let normalized = normalize(page);
        if normalized.is_empty() {
            continue;
        }
        total = total.saturating_add(normalized.len());
        if total > max_text {
            return Err(format!("pdf markdown exceeds {max_text} bytes"));
        }
        parts.push(normalized);
    }
    let md = parts.join("

");
    if md.is_empty() {
        return Err("no extractable text (scanned/image PDF?)".to_string());
    }
    Ok((md, pages_text.len() as u32))
}

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
    let text = pdf_extract::extract_text_from_mem(data).map_err(|e| format!("pdf extract: {e}"))?;
    let md = normalize(&text);
    if md.is_empty() {
        return Err("no extractable text (scanned/image PDF?)".to_string());
    }
    let pages = lopdf::Document::load_mem(data)
        .map(|d| d.get_pages().len() as u32)
        .unwrap_or(0);
    Ok((md, pages))
}

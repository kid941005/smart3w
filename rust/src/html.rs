use scraper::{ElementRef, Html, Selector};

#[derive(Clone, Copy)]
pub enum Extract {
    Readability,
    Wechat,
}

fn text_len(el: &ElementRef) -> usize {
    el.text().collect::<String>().chars().count()
}

fn first_of<'a>(doc: &'a Html, selectors: &[&str]) -> Option<ElementRef<'a>> {
    for sel in selectors {
        if let Ok(s) = Selector::parse(sel) {
            if let Some(el) = doc.select(&s).next() {
                return Some(el);
            }
        }
    }
    None
}

fn pick_root<'a>(doc: &'a Html, extract: Extract) -> Option<ElementRef<'a>> {
    match extract {
        Extract::Wechat => first_of(doc, &["#js_content", "div.rich_media_content"]),
        Extract::Readability => {
            if let Some(el) = first_of(doc, &["article", "main", "[role=main]"]) {
                return Some(el);
            }
            if let Ok(s) = Selector::parse("div") {
                let mut best: Option<(usize, ElementRef<'a>)> = None;
                for el in doc.select(&s) {
                    let n = text_len(&el);
                    if best.map(|(bn, _)| n > bn).unwrap_or(true) {
                        best = Some((n, el));
                    }
                }
                if let Some((n, el)) = best {
                    if n > 200 {
                        return Some(el);
                    }
                }
            }
            first_of(doc, &["body"])
        }
    }
}

/// 把懒加载属性提升为普通 src，避免 html2md 生成空图片链接 ![]()。
fn promote_lazy_images(html: &str) -> String {
    let mut out = String::with_capacity(html.len());
    let mut rest = html;
    while let Some(start) = rest.find("<img") {
        out.push_str(&rest[..start]);
        let after = &rest[start..];
        let Some(end) = after.find('>') else {
            out.push_str(after);
            return out;
        };
        let tag = &after[..=end];
        out.push_str(tag);
        if !tag.contains(" src=") {
            for attr in ["data-src=", "data-original="] {
                if let Some(pos) = tag.find(attr) {
                    let value_start = pos + attr.len();
                    let quote = tag.as_bytes().get(value_start).copied().unwrap_or(b'"');
                    if quote == b'"' || quote == b'\'' {
                        if let Some(value_end) = tag[value_start + 1..].find(quote as char) {
                            let value = &tag[value_start + 1..value_start + 1 + value_end];
                            if !value.trim().is_empty() {
                                let insert_at = out.len() - 1;
                                out.insert_str(insert_at, &format!(" src=\"{}\"", value));
                                break;
                            }
                        }
                    }
                }
            }
        }
        rest = &after[end + 1..];
    }
    out.push_str(rest);
    out
}

fn collapse_blank(text: &str) -> String {
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

pub fn to_markdown(html: &str, extract: Extract) -> Result<(String, String), String> {
    if html.trim().is_empty() {
        return Err("empty html".to_string());
    }
    let doc = Html::parse_document(html);
    let title = Selector::parse("title")
        .ok()
        .and_then(|s| doc.select(&s).next())
        .map(|e| e.text().collect::<String>().trim().to_string())
        .unwrap_or_default();

    let inner_html = match pick_root(&doc, extract) {
        Some(el) => el.html(),
        None => html.to_string(),
    };

    let inner_html = promote_lazy_images(&inner_html);
    let mut md = html2md::parse_html(&inner_html);
    md = collapse_blank(&md);
    if md.is_empty() {
        return Err("empty markdown".to_string());
    }

    if matches!(extract, Extract::Readability) && !title.is_empty() {
        let first = md
            .lines()
            .next()
            .unwrap_or("")
            .trim_start_matches('#')
            .trim()
            .to_string();
        if first != title {
            md = format!("# {}\n\n{}", title, md);
        }
    }
    Ok((md, title))
}

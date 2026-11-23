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

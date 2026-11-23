use std::io::Cursor;

use calamine::{open_workbook_auto_from_rs, Data, Reader};

fn fmt_float(f: f64) -> String {
    if f.is_finite() && f.fract() == 0.0 && f.abs() < 1e15 {
        format!("{}", f as i64)
    } else {
        let s = format!("{}", f);
        s
    }
}

fn cell_str(d: &Data) -> String {
    match d {
        Data::Empty => String::new(),
        Data::String(s) => s.trim().to_string(),
        Data::Float(f) => fmt_float(*f),
        Data::Int(i) => i.to_string(),
        Data::Bool(b) => {
            if *b {
                "TRUE".to_string()
            } else {
                "FALSE".to_string()
            }
        }
        Data::DateTimeIso(s) => s.clone(),
        Data::DurationIso(s) => s.clone(),
        other => format!("{:?}", other),
    }
}

fn escape_cell(value: String) -> String {
    value.replace('|', "\\|").replace('\n', "<br>")
}

fn render_rows(mut rows: Vec<Vec<String>>) -> Option<String> {
    if rows.is_empty() {
        return None;
    }
    let width = rows.iter().map(|r| r.len()).max().unwrap_or(0);
    if width == 0 {
        return None;
    }
    for r in &mut rows {
        while r.len() < width {
            r.push(String::new());
        }
    }
    let mut lines: Vec<String> = Vec::new();
    lines.push(format!("| {} |", rows[0].join(" | ")));
    lines.push(format!("| {} |", vec!["---"; width].join(" | ")));
    for r in &rows[1..] {
        lines.push(format!("| {} |", r.join(" | ")));
    }
    Some(lines.join("\n"))
}

pub fn xlsx_to_markdown(data: &[u8]) -> Result<(String, Vec<String>), String> {
    if data.is_empty() {
        return Err("empty xlsx bytes".to_string());
    }
    let mut wb = open_workbook_auto_from_rs(Cursor::new(data.to_vec()))
        .map_err(|e| format!("xlsx open: {e}"))?;
    let names: Vec<String> = wb.sheet_names().iter().map(|s| s.to_string()).collect();
    let mut blocks: Vec<String> = Vec::new();
    let mut used: Vec<String> = Vec::new();
    for name in &names {
        let range = match wb.worksheet_range(name) {
            Ok(r) => r,
            Err(_) => continue,
        };
        let mut rows: Vec<Vec<String>> = Vec::new();
        for row in range.rows() {
            let mut values: Vec<String> = row.iter().map(cell_str).collect();
            while values.last().map(|v| v.is_empty()).unwrap_or(false) {
                values.pop();
            }
            if values.iter().any(|v| !v.is_empty()) {
                rows.push(values.into_iter().map(escape_cell).collect());
            }
        }
        if let Some(table) = render_rows(rows) {
            blocks.push(format!("## {}\n\n{}", name, table));
            used.push(name.clone());
        }
    }
    if blocks.is_empty() {
        return Err("xlsx: no data".to_string());
    }
    Ok((blocks.join("\n\n"), used))
}

pub fn csv_to_markdown(data: &[u8]) -> Result<(String, usize), String> {
    if data.is_empty() {
        return Err("empty csv bytes".to_string());
    }
    let mut rdr = csv::ReaderBuilder::new()
        .has_headers(false)
        .flexible(true)
        .from_reader(data);
    let mut rows: Vec<Vec<String>> = Vec::new();
    for rec in rdr.records() {
        let rec = rec.map_err(|e| format!("csv parse: {e}"))?;
        let vals: Vec<String> = rec
            .iter()
            .map(|v| escape_cell(v.trim().trim_start_matches('\u{feff}').to_string()))
            .collect();
        if vals.iter().any(|v| !v.is_empty()) {
            rows.push(vals);
        }
    }
    let data_rows = rows.len().saturating_sub(1);
    match render_rows(rows) {
        Some(table) => Ok((table, data_rows)),
        None => Err("csv: empty".to_string()),
    }
}

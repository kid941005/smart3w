use std::io::{Cursor, Read};

use calamine::{open_workbook_auto_from_rs, Data, Reader};

use crate::limits;

fn fmt_float(f: f64) -> String {
    if f.is_finite() && f.fract() == 0.0 && f.abs() < 1e15 {
        format!("{}", f as i64)
    } else {
        format!("{}", f)
    }
}

fn cell_str(d: &Data) -> String {
    match d {
        Data::Empty => String::new(),
        // 与 Python/openpyxl 语义保持一致：保留字符串原样，不做 trim
        Data::String(s) => s.clone(),
        Data::Float(f) => fmt_float(*f),
        Data::Int(i) => i.to_string(),
        Data::Bool(b) => {
            if *b {
                "True".to_string()
            } else {
                "False".to_string()
            }
        }
        Data::DateTime(dt) => match dt.as_datetime() {
            // 与 Python/openpyxl 的 str(datetime) 对齐：始终输出完整日期时间
            Some(value) => value.format("%Y-%m-%d %H:%M:%S").to_string(),
            // 无 dates feature 或非法日期时回退到序列号，避免输出 Debug 内部结构
            None => fmt_float(dt.as_f64()),
        },
        Data::DateTimeIso(s) => s.clone(),
        Data::DurationIso(s) => s.clone(),
        Data::Error(err) => format!("{:?}", err),
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

fn parse_cell_ref(raw: &str) -> Option<(usize, usize)> {
    let mut col = 0usize;
    let mut row = 0usize;
    let mut saw_digit = false;
    for ch in raw.chars() {
        if ch.is_ascii_alphabetic() {
            if saw_digit {
                return None;
            }
            col = col * 26 + (ch.to_ascii_uppercase() as u8 - b'A' + 1) as usize;
        } else if ch.is_ascii_digit() {
            saw_digit = true;
            row = row * 10 + (ch as u8 - b'0') as usize;
        } else {
            return None;
        }
    }
    if col == 0 || row == 0 {
        None
    } else {
        Some((row, col))
    }
}

/// 在 calamine 展开整个工作表之前，先从 worksheet XML 的 <dimension ref="A1:XFD1048576">
/// 读取逻辑尺寸，拒绝稀疏/超大表格导致的巨量内存分配。
fn preflight_xlsx_dimensions(data: &[u8]) -> Result<(), String> {
    let mut archive = zip::ZipArchive::new(Cursor::new(data)).map_err(|e| format!("xlsx zip: {e}"))?;
    let max_rows = limits::max_table_rows();
    let max_cols = limits::max_table_columns();
    let max_cells = limits::max_table_cells();
    for index in 0..archive.len() {
        let mut file = match archive.by_index(index) {
            Ok(f) => f,
            Err(_) => continue,
        };
        let name = file.name().to_string();
        if !(name.starts_with("xl/worksheets/") && name.ends_with(".xml")) {
            continue;
        }
        let mut xml = String::new();
        let _ = file
            .by_ref()
            .take(2 * 1024 * 1024)
            .read_to_string(&mut xml);
        let Some(pos) = xml.find("dimension ref=\"") else {
            continue;
        };
        let rest = &xml[pos + "dimension ref=\"".len()..];
        let Some(end) = rest.find('"') else {
            continue;
        };
        let reference = &rest[..end];
        let point = reference.split(':').last().unwrap_or(reference);
        if let Some((rows, cols)) = parse_cell_ref(point) {
            if rows > max_rows || cols > max_cols || rows.saturating_mul(cols) > max_cells {
                return Err(format!(
                    "xlsx table too large: {reference} ({rows} rows x {cols} cols)"
                ));
            }
        }
    }
    Ok(())
}

pub fn xlsx_to_markdown(data: &[u8]) -> Result<(String, Vec<String>), String> {
    if data.is_empty() {
        return Err("empty xlsx bytes".to_string());
    }
    preflight_xlsx_dimensions(data)?;
    let mut wb = open_workbook_auto_from_rs(Cursor::new(data.to_vec()))
        .map_err(|e| format!("xlsx open: {e}"))?;
    let names: Vec<String> = wb.sheet_names().iter().map(|s| s.to_string()).collect();
    let mut blocks: Vec<String> = Vec::new();
    let mut used: Vec<String> = Vec::new();
    let max_text = limits::max_text_bytes();
    let max_rows = limits::max_table_rows();
    let max_cols = limits::max_table_columns();
    let max_cells = limits::max_table_cells();
    let mut total_bytes = 0usize;
    for name in &names {
        let range = match wb.worksheet_range(name) {
            Ok(r) => r,
            Err(_) => continue,
        };
        let (range_rows, range_cols) = range.get_size();
        if range_rows > max_rows || range_cols > max_cols || range_rows.saturating_mul(range_cols) > max_cells {
            return Err(format!(
                "xlsx sheet too large: {name} ({range_rows} rows x {range_cols} cols)"
            ));
        }
        let mut rows: Vec<Vec<String>> = Vec::new();
        let mut cell_count = 0usize;
        for row in range.rows() {
            let mut values: Vec<String> = row.iter().map(cell_str).collect();
            while values.last().map(|v| v.is_empty()).unwrap_or(false) {
                values.pop();
            }
            if values.iter().any(|v| !v.is_empty()) {
                cell_count = cell_count.saturating_add(values.len());
                if cell_count > max_cells {
                    return Err(format!("xlsx sheet too many cells: {name}"));
                }
                rows.push(values.into_iter().map(escape_cell).collect());
            }
        }
        if let Some(table) = render_rows(rows) {
            total_bytes = total_bytes.saturating_add(table.len());
            if total_bytes > max_text {
                return Err(format!("xlsx markdown exceeds {max_text} bytes"));
            }
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
    let max_rows = limits::max_table_rows();
    let max_cols = limits::max_table_columns();
    let max_cells = limits::max_table_cells();
    let max_text = limits::max_text_bytes();
    let mut rdr = csv::ReaderBuilder::new()
        .has_headers(false)
        .flexible(true)
        .from_reader(data);
    let mut rows: Vec<Vec<String>> = Vec::new();
    let mut cell_count = 0usize;
    for rec in rdr.records() {
        let rec = rec.map_err(|e| format!("csv parse: {e}"))?;
        if rows.len() >= max_rows {
            return Err(format!("csv exceeds {max_rows} rows"));
        }
        if rec.len() > max_cols {
            return Err(format!("csv exceeds {max_cols} columns"));
        }
        let vals: Vec<String> = rec
            .iter()
            .map(|v| escape_cell(v.trim_start_matches('\u{feff}').to_string()))
            .collect();
        if vals.iter().any(|v| !v.is_empty()) {
            cell_count = cell_count.saturating_add(vals.len());
            if cell_count > max_cells {
                return Err(format!("csv exceeds {max_cells} cells"));
            }
            rows.push(vals);
        }
    }
    let data_rows = rows.len().saturating_sub(1);
    match render_rows(rows) {
        Some(table) => {
            if table.len() > max_text {
                return Err(format!("csv markdown exceeds {max_text} bytes"));
            }
            Ok((table, data_rows))
        }
        None => Err("csv: empty".to_string()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_cell_references() {
        assert_eq!(parse_cell_ref("A1"), Some((1, 1)));
        assert_eq!(parse_cell_ref("XFD1048576"), Some((1048576, 16384)));
        assert_eq!(parse_cell_ref("AA10"), Some((10, 27)));
        assert_eq!(parse_cell_ref("A"), None);
        assert_eq!(parse_cell_ref("1A"), None);
        assert_eq!(parse_cell_ref(""), None);
    }

    #[test]
    fn formats_numbers_and_booleans_without_trimming_strings() {
        assert_eq!(fmt_float(3.0), "3");
        assert_eq!(fmt_float(3.5), "3.5");
        assert_eq!(cell_str(&Data::Bool(true)), "True");
        assert_eq!(cell_str(&Data::Bool(false)), "False");
        assert_eq!(cell_str(&Data::String("  hi  ".to_string())), "  hi  ");
    }

    #[test]
    fn renders_small_csv_as_markdown() {
        let (table, rows) = csv_to_markdown(b"a,b\n1,2\n").unwrap();
        assert_eq!(rows, 1);
        assert!(table.contains("| a | b |"));
        assert!(table.contains("| 1 | 2 |"));
    }
}

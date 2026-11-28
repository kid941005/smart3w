use std::env;

pub fn env_usize(name: &str, default: usize) -> usize {
    env::var(name)
        .ok()
        .and_then(|v| v.trim().parse::<usize>().ok())
        .filter(|v| *v > 0)
        .unwrap_or(default)
}

pub fn max_input_bytes() -> usize {
    env_usize("SMART3W_MAX_DOCUMENT_BYTES", 64 * 1024 * 1024)
}

pub fn max_text_bytes() -> usize {
    env_usize("SMART3W_MAX_DOCUMENT_TEXT_BYTES", 10 * 1024 * 1024)
}

pub fn max_table_cells() -> usize {
    env_usize("SMART3W_MAX_TABLE_CELLS", 2_000_000)
}

pub fn max_table_columns() -> usize {
    env_usize("SMART3W_MAX_TABLE_COLUMNS", 10_000)
}

pub fn max_table_rows() -> usize {
    env_usize("SMART3W_MAX_TABLE_ROWS", 500_000)
}

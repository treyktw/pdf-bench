use std::env;
use std::fs;
use std::time::Instant;

use pdf_bench::{extract_pdf_bytes, Engine};
use serde_json::json;

fn main() -> anyhow::Result<()> {
    let mut args = env::args().skip(1);
    let pdf_path = args
        .next()
        .ok_or_else(|| anyhow::anyhow!("usage: pdf-extract-rust <pdf-path> [--no-ocr]"))?;
    let allow_ocr = !args.any(|arg| arg == "--no-ocr");

    let started = Instant::now();
    let data = fs::read(pdf_path)?;
    let engine = Engine::load()?;
    let result = extract_pdf_bytes(&engine, &data, allow_ocr, 500, 30)?;
    let elapsed_ms = started.elapsed().as_secs_f64() * 1000.0;

    println!(
        "{}",
        json!({
            "engine": "rust",
            "elapsedMs": (elapsed_ms * 100.0).round() / 100.0,
            "textChars": result.text.len(),
            "tableCount": result.tables.len(),
            "ocrUsed": result.ocr_used,
        })
    );

    Ok(())
}

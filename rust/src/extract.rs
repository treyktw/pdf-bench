//! Extraction orchestration. Direct port of `extract_pdf_bytes` in
//! `python/src/pdf_bench/extractor.py`: text first; if the result is too short and
//! OCR is allowed + available, render pages and append OCR text.

use crate::engine::Engine;
use crate::errors::ExtractError;
use crate::ocr;

/// Threshold below which we attempt OCR (matches Python's `< 100`).
const OCR_TEXT_THRESHOLD: usize = 100;

/// Extraction output. `tables` is always empty (see crate docs in `lib.rs`).
#[derive(Debug, Clone)]
pub struct ExtractionResult {
    pub text: String,
    pub tables: Vec<Vec<Vec<String>>>,
    pub ocr_used: bool,
}

/// Extract text from raw PDF bytes.
///
/// - `allow_ocr`: permit the OCR fallback path.
/// - `max_pages`: page cap (`<= 0` disables).
/// - `ocr_timeout_sec`: hard per-page OCR timeout.
pub fn extract_pdf_bytes(
    engine: &Engine,
    data: &[u8],
    allow_ocr: bool,
    max_pages: i64,
    ocr_timeout_sec: u64,
) -> Result<ExtractionResult, ExtractError> {
    let (text, _page_count) = engine.extract_text(data, max_pages)?;
    let mut combined = text.trim().to_string();
    let mut ocr_used = false;

    if combined.chars().count() < OCR_TEXT_THRESHOLD && allow_ocr && ocr::ocr_available() {
        let images = engine.render_pages(data)?;
        let ocr_text = ocr::ocr_images(&images, ocr_timeout_sec)?;
        if !ocr_text.trim().is_empty() {
            combined = format!("{combined}\n\n{ocr_text}").trim().to_string();
            ocr_used = true;
        }
    }

    Ok(ExtractionResult {
        text: combined,
        tables: Vec::new(),
        ocr_used,
    })
}

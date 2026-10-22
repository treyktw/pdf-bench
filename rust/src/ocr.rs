//! Tesseract OCR fallback. Shells out to the `tesseract` CLI per page with a
//! hard per-page timeout (kill on overrun → `OcrTimeout`, treated as permanent).
//!
//! Parity note: like Python, non-timeout OCR failures degrade to empty text
//! rather than erroring — but here they are logged via `tracing`, not silent.

use std::fs;
use std::path::Path;
use std::process::{Command, Stdio};
use std::time::Duration;

use image::DynamicImage;
use wait_timeout::ChildExt;

use crate::errors::ExtractError;

/// True if the `tesseract` binary is invokable.
pub fn ocr_available() -> bool {
    Command::new("tesseract")
        .arg("--version")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

/// OCRs each rendered page image, concatenating non-empty results with `\n\n`.
/// `timeout_sec` is the hard per-page limit.
pub fn ocr_images(images: &[DynamicImage], timeout_sec: u64) -> Result<String, ExtractError> {
    if images.is_empty() {
        return Ok(String::new());
    }

    let dir = tempfile::tempdir()
        .map_err(|e| ExtractError::Retryable(format!("failed to create OCR temp dir: {e}")))?;

    let mut parts: Vec<String> = Vec::new();
    for (idx, img) in images.iter().enumerate() {
        let png = dir.path().join(format!("page-{idx}.png"));
        if let Err(e) = img.save(&png) {
            tracing::warn!(error = %e, page = idx, "ocr_page_image_write_failed");
            continue;
        }
        let text = ocr_one_page(&png, dir.path(), idx, timeout_sec)?;
        if !text.trim().is_empty() {
            parts.push(text);
        }
    }
    Ok(parts.join("\n\n"))
}

fn ocr_one_page(
    png: &Path,
    work_dir: &Path,
    idx: usize,
    timeout_sec: u64,
) -> Result<String, ExtractError> {
    let out_base = work_dir.join(format!("ocr-{idx}"));
    let mut child = match Command::new("tesseract")
        .arg(png)
        .arg(&out_base)
        .arg("--psm")
        .arg("3")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
    {
        Ok(child) => child,
        Err(e) => {
            tracing::warn!(error = %e, page = idx, "ocr_spawn_failed");
            return Ok(String::new());
        }
    };

    match child.wait_timeout(Duration::from_secs(timeout_sec)) {
        Ok(Some(status)) => {
            if !status.success() {
                tracing::warn!(page = idx, code = ?status.code(), "ocr_nonzero_exit");
                return Ok(String::new());
            }
            let txt_path = out_base.with_extension("txt");
            Ok(fs::read_to_string(&txt_path).unwrap_or_default())
        }
        Ok(None) => {
            // Overran the hard limit — kill and surface as a permanent timeout.
            let _ = child.kill();
            let _ = child.wait();
            Err(ExtractError::OcrTimeout(timeout_sec))
        }
        Err(e) => {
            let _ = child.kill();
            let _ = child.wait();
            tracing::warn!(error = %e, page = idx, "ocr_wait_failed");
            Ok(String::new())
        }
    }
}

//! PDFium engine wrapper: binding discovery, text extraction, page rendering.
//!
//! `Pdfium` wraps a non-thread-safe FFI library, so `Engine` is `!Send`. The
//! `pdf-extract-rust` binary is single-threaded and one-shot, which is exactly the
//! isolation model we want (a PDFium segfault kills only the child process).

use std::env;
use std::path::{Path, PathBuf};

use image::DynamicImage;
use pdfium_render::prelude::*;

use crate::errors::ExtractError;

const RENDER_DPI: f32 = 150.0;
const PDF_POINTS_PER_INCH: f32 = 72.0;

/// Owns a bound PDFium instance.
pub struct Engine {
    pdfium: Pdfium,
}

impl Engine {
    /// Binds to a PDFium dynamic library.
    ///
    /// Resolution order:
    /// 1. `PDFIUM_DYNAMIC_LIB_PATH` — a directory containing the platform lib,
    ///    or a direct path to the library file.
    /// 2. System library search path.
    ///
    /// Failure is an environment/deploy problem (missing lib), surfaced to the
    /// caller as a retryable condition rather than a per-PDF permanent error.
    pub fn load() -> anyhow::Result<Self> {
        let bindings = match env::var("PDFIUM_DYNAMIC_LIB_PATH") {
            Ok(raw) if !raw.trim().is_empty() => bind_from_path(raw.trim())?,
            _ => Pdfium::bind_to_system_library()
                .map_err(|e| anyhow::anyhow!("failed to bind system PDFium library: {e}"))?,
        };
        Ok(Self {
            pdfium: Pdfium::new(bindings),
        })
    }

    /// Extracts concatenated page text and returns `(text, page_count)`.
    ///
    /// `max_pages <= 0` disables the page cap. Exceeding the cap and unreadable
    /// documents are permanent failures (mirrors Python).
    pub fn extract_text(
        &self,
        data: &[u8],
        max_pages: i64,
    ) -> Result<(String, usize), ExtractError> {
        let doc = self.open(data)?;
        let count = doc.pages().len() as i64;
        if max_pages > 0 && count > max_pages {
            return Err(ExtractError::Permanent(format!(
                "PDF exceeds max allowed page count of {max_pages} pages."
            )));
        }

        let mut parts: Vec<String> = Vec::with_capacity(count.max(0) as usize);
        for page in doc.pages().iter() {
            // A single page's text layer failing is not fatal; treat as empty.
            let text = page.text().map(|t| t.all()).unwrap_or_default();
            parts.push(text);
        }
        Ok((parts.join("\n\n"), count.max(0) as usize))
    }

    /// Renders every page to an RGB image at `RENDER_DPI` for OCR input.
    pub fn render_pages(&self, data: &[u8]) -> Result<Vec<DynamicImage>, ExtractError> {
        let doc = self.open(data)?;
        let scale = RENDER_DPI / PDF_POINTS_PER_INCH;
        let config = PdfRenderConfig::new().scale_page_by_factor(scale);

        let mut images = Vec::with_capacity(doc.pages().len() as usize);
        for page in doc.pages().iter() {
            let bitmap = page
                .render_with_config(&config)
                .map_err(|e| ExtractError::Retryable(format!("page render failed: {e}")))?;
            images.push(bitmap.as_image());
        }
        Ok(images)
    }

    fn open<'a>(&'a self, data: &'a [u8]) -> Result<PdfDocument<'a>, ExtractError> {
        self.pdfium
            .load_pdf_from_byte_slice(data, None)
            .map_err(|_| ExtractError::Permanent("Invalid or unreadable PDF document.".into()))
    }
}

fn bind_from_path(raw: &str) -> anyhow::Result<Bindings> {
    let p = Path::new(raw);
    // Directory → append the platform-specific lib filename.
    if p.is_dir() {
        let lib = Pdfium::pdfium_platform_library_name_at_path(p);
        return Pdfium::bind_to_library(&lib)
            .map_err(|e| anyhow::anyhow!("failed to bind PDFium at {}: {e}", lib.display()));
    }
    // Otherwise treat as a direct path to the library file.
    let path: PathBuf = p.to_path_buf();
    Pdfium::bind_to_library(&path)
        .map_err(|e| anyhow::anyhow!("failed to bind PDFium at {}: {e}", path.display()))
}

/// Type alias for the PDFium bindings trait object returned by binding helpers.
type Bindings = Box<dyn PdfiumLibraryBindings>;

//! PDF extraction: PDFium text + optional Tesseract OCR fallback.
//!
//! Parity target: the Python extractor in this benchmark folder. Structured
//! tables are omitted in the Rust path so text extraction speed can be compared
//! directly against the Python text + table path.

pub mod errors;
mod engine;
mod extract;
mod ocr;

pub use errors::ExtractError;
pub use engine::Engine;
pub use extract::{extract_pdf_bytes, ExtractionResult};
pub use ocr::ocr_available;

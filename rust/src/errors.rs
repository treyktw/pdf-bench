//! Minimal extraction error taxonomy for the standalone Rust benchmark.

use thiserror::Error;

#[derive(Debug, Error)]
pub enum ExtractError {
    #[error("permanent extraction failure: {0}")]
    Permanent(String),

    #[error("retryable extraction failure: {0}")]
    Retryable(String),

    #[error("OCR exceeded timeout of {0} seconds")]
    OcrTimeout(u64),

    #[error("PDF parse failed")]
    Parse(#[source] anyhow::Error),
}

impl ExtractError {
    pub fn is_permanent(&self) -> bool {
        matches!(
            self,
            ExtractError::Permanent(_) | ExtractError::OcrTimeout(_)
        )
    }
}

use std::path::PathBuf;

use pdf_bench::{extract_pdf_bytes, Engine, ExtractError};

fn sample_fixture() -> Vec<u8> {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../fixtures/sample.pdf");
    std::fs::read(&path).unwrap_or_else(|e| panic!("read {}: {e}", path.display()))
}

#[test]
fn synthetic_fixture_yields_text() {
    let engine = Engine::load().expect("PDFium must be available for this test");
    let result = extract_pdf_bytes(&engine, &sample_fixture(), false, 500, 30)
        .expect("synthetic fixture should extract");

    assert!(result.text.contains("Load Confirmation EXAMPLE-LOAD-1001"));
    assert!(result.tables.is_empty(), "Rust path does not extract tables");
    assert!(!result.ocr_used);
}

#[test]
fn page_cap_is_permanent_error() {
    let engine = Engine::load().expect("PDFium must be available for this test");
    let error = extract_pdf_bytes(&engine, &sample_fixture(), false, 3, 30)
        .expect_err("10-page fixture exceeds a 3-page cap");

    assert!(error.is_permanent());
    assert!(matches!(error, ExtractError::Permanent(_)));
}

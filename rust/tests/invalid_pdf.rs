use pdf_bench::{extract_pdf_bytes, Engine, ExtractError};

#[test]
fn invalid_pdf_is_permanent_error() {
    let engine = Engine::load().expect("PDFium must be available for this test");
    let error = extract_pdf_bytes(&engine, b"not a pdf", false, 500, 30)
        .expect_err("invalid PDF should fail");

    assert!(matches!(error, ExtractError::Permanent(_)));
}

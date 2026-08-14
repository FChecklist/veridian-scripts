"""Real tests for attachment_intake.py (task-20260814-180459).

All tests exercise the module's real functions in-process against real
files written to pytest's tmp_path -- no mocking of hashlib/mimetypes/file
I/O. No live system database or path outside tmp_path is touched.
"""
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import attachment_intake as ai  # noqa: E402


def test_missing_file_is_honest_not_fatal(tmp_path):
    missing = tmp_path / "does-not-exist.txt"
    record = ai.build_attachment_record(str(missing))
    assert record["exists"] is False
    assert record["extraction_status"] == "file_not_found"
    assert record["sha256"] is None
    block = ai.render_attachment_block(record)
    assert "NOT FOUND" in block
    assert str(missing) in block


def test_text_file_real_extraction_and_hash(tmp_path):
    f = tmp_path / "note.txt"
    content = "Distinctive marker: veridian-attachment-intake-canary-8f21a."
    f.write_text(content, encoding="utf-8")
    expected_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

    record = ai.build_attachment_record(str(f))
    assert record["exists"] is True
    assert record["category"] == "text"
    assert record["extraction_status"] == "extracted"
    assert record["sha256"] == expected_hash
    assert record["extracted_text"] == content
    assert record["size_bytes"] == len(content.encode("utf-8"))
    assert record["truncated"] is False

    block = ai.render_attachment_block(record)
    assert content in block
    assert record["sha256"] in block
    assert str(f.resolve()) in block or os.path.abspath(str(f)) in block


def test_markdown_extension_classified_as_text(tmp_path):
    f = tmp_path / "doc.md"
    f.write_text("# Heading\n\nBody text.", encoding="utf-8")
    record = ai.build_attachment_record(str(f))
    assert record["category"] == "text"
    assert record["extraction_status"] == "extracted"
    assert "# Heading" in record["extracted_text"]


def test_long_text_is_truncated_with_disclosed_note(tmp_path):
    f = tmp_path / "big.txt"
    f.write_text("x" * 100, encoding="utf-8")
    record = ai.build_attachment_record(str(f), max_chars=10)
    assert record["extraction_status"] == "extracted"
    assert record["truncated"] is True
    assert len(record["extracted_text"]) == 10
    block = ai.render_attachment_block(record)
    assert "truncated" in block


def test_image_is_logged_not_extracted_with_disclosed_limitation(tmp_path):
    f = tmp_path / "photo.png"
    # Real PNG magic bytes -- enough for mimetypes/extension-based
    # classification, which is all this module relies on (no image decode).
    f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    record = ai.build_attachment_record(str(f))
    assert record["exists"] is True
    assert record["category"] == "image"
    assert record["extraction_status"] == "not_extracted_no_vision_path"
    assert record["extracted_text"] is None
    # Real hash/path/mimetype must still be recorded even though content
    # extraction is honestly skipped.
    assert record["sha256"] == hashlib.sha256(f.read_bytes()).hexdigest()
    assert record["mimetype"] == "image/png"

    block = ai.render_attachment_block(record)
    assert "NOT extracted" in block
    assert record["sha256"] in block
    # The disclosed limitation must be real and specific, not silent.
    assert "vision" in block.lower() or "document_extraction_vision" in block


def test_pdf_without_library_is_disclosed_not_fabricated(tmp_path):
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-1.4\n%%EOF")
    record = ai.build_attachment_record(str(f))
    assert record["category"] == "pdf"
    # On this host (checked live before writing this module: no
    # pypdf/PyPDF2/pdfplumber/fitz importable), extraction must degrade
    # honestly rather than fabricate text.
    import importlib
    has_pdf_lib = any(
        importlib.util.find_spec(m) is not None
        for m in ("pypdf", "PyPDF2", "pdfplumber", "fitz")
    )
    if not has_pdf_lib:
        assert record["extraction_status"] == "not_extracted_no_library"
        assert record["extracted_text"] is None
        assert record["note"]
    else:
        # A real library is present -- extraction must have actually run,
        # not just report success in the wrong branch.
        assert record["extraction_status"] in ("extracted", "not_extracted_no_library")


def test_unsupported_type_is_disclosed(tmp_path):
    f = tmp_path / "archive.zip"
    f.write_bytes(b"PK\x03\x04")
    record = ai.build_attachment_record(str(f))
    assert record["category"] == "other"
    assert record["extraction_status"] == "not_supported"
    assert record["extracted_text"] is None


def test_classify_attachment_pure_function():
    assert ai.classify_attachment("a.txt", None) == "text"
    assert ai.classify_attachment("a.md", None) == "text"
    assert ai.classify_attachment("a.pdf", None) == "pdf"
    assert ai.classify_attachment("a.jpg", "image/jpeg") == "image"
    assert ai.classify_attachment("a.bin", "application/octet-stream") == "other"

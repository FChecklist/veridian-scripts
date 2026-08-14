#!/usr/bin/env python3
"""attachment_intake.py -- real file-attachment intake for the single owner
dispatch gateway (task-20260814-180459-add-real-file-attachment-intake-to-the-s).

Real gap this closes: before this module existed, only raw owner chat text
went through task-gateway.py submit's real pipeline (OWNER_ENGINE gate,
capability lookup, check-duplicate/search/query-knowledge/zoekt). There was
no path for a real local file (text document, PDF, image) to enter that
same pipeline -- callers had to manually paste file contents into the
prompt text themselves, or the file's content never reached the gate/
dedup/search machinery at all.

Design (deliberately NOT a second, parallel content path): this module only
turns one real local file into a text block (render_attachment_block).
task-gateway.py's cmd_submit prepends that block onto the same `text`
variable that already goes through run_owner_engine_gate() and
extract_keywords_mechanical() -- so attachment-derived content is gated,
deduped, and searched by the exact same real code every typed prompt
already goes through, not a separate mechanism.

What is and isn't real here (2026-08-14, checked live on this host before
writing any of this):
  - .txt/.md/and other text/* files: real stdlib-only text extraction, no
    new dependency needed (`pip3 list` / `python3 -c "import ..."` checked
    first, per this task's own instruction).
  - PDF: best-effort real extraction IF a PDF-parsing library is already
    importable (pypdf/PyPDF2/pdfplumber/fitz, checked in that order at
    runtime) -- confirmed live that NONE of these are installed on this
    host as of 2026-08-14 (`pip3 list` showed none; no pdftotext/poppler
    binary either), so PDFs currently fall back to the same honest,
    disclosed non-extraction path as images, never fabricated text. Adding
    a new PDF dependency was deliberately not done here -- this task's own
    instruction is to check for an existing library first, and a live
    production host's system Python is not somewhere to add a new global
    dependency without a separate, explicit decision.
  - Images: a real vision-capable extraction path DOES exist elsewhere in
    this codebase (capability_id=CAP-20260724-134704-6e49
    document_extraction_vision, repos/compliance-tracker/src/lib/services/
    document-extraction-service.ts callLLMVision) -- but it is a Next.js
    server action wired into compliance-tracker's own DB/tenant-context/
    LLM-client config and invoked fire-and-forget from THAT app's own
    upload route. It is not a function or CLI this Python gateway can call
    directly without standing up that entire separate app+DB stack, which
    is out of scope for this task. So: images are accepted, hashed, and
    logged (path/mimetype/sha256), but honestly NOT content-extracted --
    see KNOWN_VISION_CAPABILITY_NOTE below, which is carried into the
    rendered block so the gap is visible in the actual prompt, not silent.
"""
import hashlib
import mimetypes
import os

# Ceiling on how much extracted text gets folded into the prompt -- same
# real discipline compliance-tracker's own document-extraction-service.ts
# already applies via its MAX_EXTRACTED_CHARS=12000 constant (kept prompt
# cost/latency bounded for unusually long documents). Set a little higher
# here since this text also has to survive OWNER_ENGINE's own document-mode
# digesting (prompt_gateway/engine/document_engine.py), not go straight to
# an LLM call itself.
MAX_EXTRACTED_CHARS = 20000

TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".rst", ".csv", ".log", ".yaml", ".yml", ".json",
}

KNOWN_VISION_CAPABILITY_NOTE = (
    "a real vision-capable extraction path exists elsewhere in this codebase "
    "(capability_id=CAP-20260724-134704-6e49 document_extraction_vision, "
    "repos/compliance-tracker/src/lib/services/document-extraction-service.ts "
    "callLLMVision) but it is a Next.js server action coupled to "
    "compliance-tracker's own DB/tenant context and LLM-client config, invoked "
    "fire-and-forget from THAT app's own upload route -- not callable from this "
    "Python CLI gateway without standing up that entire separate app+DB stack, "
    "which is out of scope here. Image content is therefore accepted and logged "
    "(hash/path/mimetype) but NOT extracted -- a real, disclosed limitation, not "
    "a silent gap."
)

NO_PDF_LIBRARY_NOTE = (
    "no PDF-text-extraction library is importable in this Python environment "
    "(checked pypdf, PyPDF2, pdfplumber, fitz/PyMuPDF, confirmed none installed "
    "on this host on 2026-08-14) -- content NOT extracted, per this task's own "
    "instruction to reuse an existing real library rather than add a new "
    "dependency. File is still accepted and logged (hash/path/mimetype); if a "
    "PDF library is installed on this host later, this module starts using it "
    "automatically, no code change required."
)


def _sha256_of_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _guess_mimetype(path):
    mt, _ = mimetypes.guess_type(path)
    return mt or "application/octet-stream"


def classify_attachment(path, mimetype):
    """Category is decided from real signals only (extension + guessed
    mimetype) -- never from file content sniffing beyond what mimetypes
    stdlib already does from the extension."""
    ext = os.path.splitext(path)[1].lower()
    if ext in TEXT_EXTENSIONS or (mimetype and mimetype.startswith("text/")):
        return "text"
    if ext == ".pdf" or mimetype == "application/pdf":
        return "pdf"
    if mimetype and mimetype.startswith("image/"):
        return "image"
    return "other"


def _extract_with_pypdf(path):
    import pypdf
    reader = pypdf.PdfReader(path)
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _extract_with_pypdf2(path):
    import PyPDF2
    reader = PyPDF2.PdfReader(path)
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _extract_with_pdfplumber(path):
    import pdfplumber
    parts = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            parts.append(page.extract_text() or "")
    return "\n".join(parts)


def _extract_with_fitz(path):
    import fitz
    doc = fitz.open(path)
    try:
        return "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()


_PDF_EXTRACTORS = (
    ("pypdf", _extract_with_pypdf),
    ("PyPDF2", _extract_with_pypdf2),
    ("pdfplumber", _extract_with_pdfplumber),
    ("fitz", _extract_with_fitz),
)


def _try_extract_pdf_text(path):
    """Best-effort real PDF text extraction using whatever PDF library is
    already importable -- checked at runtime via a real import, never
    assumed present. Returns (text, error_note); exactly one is None."""
    for modname, extractor in _PDF_EXTRACTORS:
        try:
            __import__(modname)
        except ImportError:
            continue
        try:
            return extractor(path), None
        except Exception as e:  # a real, but non-fatal, extraction failure
            return None, (
                f"{modname} is installed but real extraction raised "
                f"{type(e).__name__}: {e}"
            )
    return None, NO_PDF_LIBRARY_NOTE


def build_attachment_record(path, max_chars=MAX_EXTRACTED_CHARS):
    """Real intake of one local attachment file. hash/size/mimetype are
    always computed from a live read of `path` (never guessed or
    fabricated); text content is really extracted for text/markdown and
    for PDFs when a PDF library happens to be importable, and honestly NOT
    extracted (with a disclosed, specific reason) for images and anything
    else. Never raises for a missing/unreadable file -- records
    exists=False / extraction_status='file_not_found' instead so the
    caller can decide whether that's fatal."""
    record = {
        "path": os.path.abspath(path),
        "exists": False,
        "size_bytes": None,
        "sha256": None,
        "mimetype": None,
        "category": None,
        "extraction_status": None,
        "extracted_text": None,
        "extracted_chars": 0,
        "truncated": False,
        "note": None,
    }
    if not os.path.isfile(path):
        record["extraction_status"] = "file_not_found"
        record["note"] = f"path does not exist or is not a regular file: {path}"
        return record

    record["exists"] = True
    record["size_bytes"] = os.path.getsize(path)
    record["sha256"] = _sha256_of_file(path)
    mimetype = _guess_mimetype(path)
    record["mimetype"] = mimetype
    category = classify_attachment(path, mimetype)
    record["category"] = category

    text = None
    if category == "text":
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
            record["extraction_status"] = "extracted"
        except OSError as e:
            record["extraction_status"] = "read_error"
            record["note"] = f"{type(e).__name__}: {e}"
            return record
    elif category == "pdf":
        text, err = _try_extract_pdf_text(path)
        if text is not None:
            record["extraction_status"] = "extracted"
        else:
            record["extraction_status"] = "not_extracted_no_library"
            record["note"] = err
            return record
    elif category == "image":
        record["extraction_status"] = "not_extracted_no_vision_path"
        record["note"] = KNOWN_VISION_CAPABILITY_NOTE
        return record
    else:
        record["extraction_status"] = "not_supported"
        record["note"] = (
            f"category '{category}' (mimetype={mimetype}) has no real "
            f"text-extraction path in this gateway yet"
        )
        return record

    if len(text) > max_chars:
        record["truncated"] = True
        text = text[:max_chars]
    record["extracted_text"] = text
    record["extracted_chars"] = len(text)
    return record


def render_attachment_block(record):
    """Renders `record` (from build_attachment_record) into the literal
    text block that gets prepended/appended onto the owner prompt before it
    goes through OWNER_ENGINE -- same real text either way, whether it was
    typed or came from an attachment, per this task's own requirement not
    to build a second, parallel content path."""
    if record["extraction_status"] == "file_not_found":
        return f"[ATTACHMENT NOT FOUND: {record['path']} -- {record['note']}]"

    header = (
        f"[ATTACHMENT path={record['path']} mimetype={record['mimetype']} "
        f"sha256={record['sha256']} size_bytes={record['size_bytes']}]"
    )
    if record["extraction_status"] == "extracted":
        body = record["extracted_text"]
        if record["truncated"]:
            body += (
                f"\n[...attachment truncated at {MAX_EXTRACTED_CHARS} chars "
                f"for prompt size...]"
            )
        return f"{header}\n{body}\n[END ATTACHMENT]"

    return f"{header} -- content NOT extracted ({record['extraction_status']}): {record['note']}"

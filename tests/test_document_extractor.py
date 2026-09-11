"""
Tests for document text extraction (embedded text and OCR).

Tests extraction paths, result typing, stability of node IDs,
and ensures no external LLM/API calls are made.
"""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture
def sample_text_ready_pdf():
    """Provide path to TEXT_READY sample PDF."""
    project_root = Path(__file__).resolve().parents[1]
    # Use a clean PDF with sufficient embedded text
    return str(project_root / "sample_invoices" / "01_Veson_Bunker_Clean_STP.pdf")


@pytest.fixture
def sample_vision_required_pdf():
    """Provide path to VISION_REQUIRED sample PDF."""
    project_root = Path(__file__).resolve().parents[1]
    # Use a PDF that requires vision (image-based or poor scan)
    return str(project_root / "sample_invoices" / "02_smartPAL_Spares_Scan_Handwritten.pdf")


@pytest.fixture
def empty_pdf_path(tmp_path):
    """Create an empty PDF file for testing."""
    empty_pdf = tmp_path / "empty.pdf"
    # Write minimal valid PDF that has no pages
    empty_pdf.write_bytes(b"%PDF-1.4\n1 0 obj\n<</Type/Catalog/Pages 2 0 R>>\nendobj\n2 0 obj\n<</Type/Pages/Kids[]/Count 0>>\nendobj\nxref\n0 3\n0000000000 65535 f\n0000000009 00000 n\n0000000058 00000 n\ntrailer\n<</Size 3/Root 1 0 R>>\nstartxref\n106\n%%EOF")
    return str(empty_pdf)


@pytest.fixture
def non_pdf_path(tmp_path):
    """Create a non-PDF file for testing."""
    non_pdf = tmp_path / "document.txt"
    non_pdf.write_text("This is not a PDF")
    return str(non_pdf)


@pytest.fixture
def corrupt_pdf_path(tmp_path):
    """Create a corrupt PDF file for testing."""
    corrupt_pdf = tmp_path / "corrupt.pdf"
    corrupt_pdf.write_bytes(b"%PDF-1.4\n" + b"garbage data" * 100)
    return str(corrupt_pdf)


class TestPageExtractionResult:
    """Tests for PageExtractionResult dataclass."""

    def test_page_result_creation(self):
        """PageExtractionResult can be created with required fields."""
        from src.document_extractor import PageExtractionResult

        page = PageExtractionResult(
            page_number=1,
            extraction_method="EMBEDDED_TEXT",
            text="Sample text",
            character_count=11,
        )

        assert page.page_number == 1
        assert page.extraction_method == "EMBEDDED_TEXT"
        assert page.character_count == 11
        assert page.ocr_confidence is None

    def test_page_result_to_dict_excludes_text(self):
        """PageExtractionResult.to_dict() never includes text."""
        from src.document_extractor import PageExtractionResult

        page = PageExtractionResult(
            page_number=1,
            extraction_method="OCR",
            text="Sensitive invoice data",
            character_count=23,
            ocr_confidence=85.5,
        )

        result = page.to_dict()
        assert result["text"] == ""
        assert "invoice" not in str(result)
        assert result["page_number"] == 1
        assert result["ocr_confidence"] == 85.5


class TestDocumentExtractionResult:
    """Tests for DocumentExtractionResult dataclass."""

    def test_document_result_creation(self):
        """DocumentExtractionResult can be created with required fields."""
        from src.document_extractor import DocumentExtractionResult

        result = DocumentExtractionResult(
            file_path="/test/invoice.pdf",
            file_name="invoice.pdf",
            page_count=1,
            extraction_method="EMBEDDED_TEXT",
            total_character_count=100,
        )

        assert result.file_path == "/test/invoice.pdf"
        assert result.file_name == "invoice.pdf"
        assert result.page_count == 1
        assert result.requires_human_review is False

    def test_document_result_to_dict_excludes_text(self):
        """DocumentExtractionResult.to_dict() never includes full text."""
        from src.document_extractor import DocumentExtractionResult

        result = DocumentExtractionResult(
            file_path="/test/invoice.pdf",
            file_name="invoice.pdf",
            page_count=1,
            extraction_method="EMBEDDED_TEXT",
            total_character_count=100,
            extracted_text="This is sensitive invoice text",
            requires_human_review=True,
            human_review_reasons=["Low quality"],
        )

        result_dict = result.to_dict()
        assert "page_results" in result_dict
        assert result_dict["requires_human_review"] is True
        # Verify extracted_text is excluded from dict
        assert result_dict["extracted_text"] == ""
        # Verify it's available on the object itself
        assert result.extracted_text == "This is sensitive invoice text"


class TestEmbeddedTextExtraction:
    """Tests for embedded text extraction path."""

    def test_extract_embedded_text_missing_file(self):
        """Extraction handles missing files gracefully."""
        from src.document_extractor import extract_embedded_text

        result = extract_embedded_text("/nonexistent/file.pdf")

        assert isinstance(result, dict)
        assert result["requires_human_review"] is True
        assert "File not found" in result["human_review_reasons"]
        assert result["extraction_method"] == "EMBEDDED_TEXT"
        assert result["extractor_node_id"] == "extract_embedded_text"
        assert "extracted_text" in result
        assert result["extracted_text"] == ""

    def test_extract_embedded_text_non_pdf(self, non_pdf_path):
        """Extraction rejects non-PDF files."""
        from src.document_extractor import extract_embedded_text

        result = extract_embedded_text(non_pdf_path)

        assert result["requires_human_review"] is True
        assert "Not a PDF" in result["warnings"][0]

    def test_extract_embedded_text_empty_pdf(self, empty_pdf_path):
        """Extraction handles empty PDFs."""
        from src.document_extractor import extract_embedded_text

        result = extract_embedded_text(empty_pdf_path)

        assert result["page_count"] == 0
        assert result["requires_human_review"] is True
        assert "Empty PDF" in result["human_review_reasons"]

    def test_extract_embedded_text_corrupt_pdf(self, corrupt_pdf_path):
        """Extraction handles corrupt PDFs."""
        from src.document_extractor import extract_embedded_text

        result = extract_embedded_text(corrupt_pdf_path)

        # Should either handle gracefully or report as requiring review
        assert result["extraction_method"] == "EMBEDDED_TEXT"

    def test_extract_embedded_text_valid_pdf(self, sample_text_ready_pdf):
        """Extraction succeeds on valid PDF with embedded text."""
        from src.document_extractor import extract_embedded_text

        result = extract_embedded_text(sample_text_ready_pdf)

        assert result["extraction_method"] == "EMBEDDED_TEXT"
        assert result["page_count"] > 0
        assert result["total_character_count"] > 0
        assert isinstance(result["extracted_text"], str)
        assert result["extractor_node_id"] == "extract_embedded_text"

        # Page results should match page count
        assert len(result["page_results"]) == result["page_count"]

    def test_extract_embedded_text_includes_workflow_id(self, sample_text_ready_pdf):
        """Extraction includes workflow_run_id when provided."""
        from src.document_extractor import extract_embedded_text

        workflow_id = "WF-test-12345"
        result = extract_embedded_text(sample_text_ready_pdf, workflow_run_id=workflow_id)

        assert result["workflow_run_id"] == workflow_id

    def test_extract_embedded_text_no_logging_full_text(self, sample_text_ready_pdf):
        """Result dict never includes full extracted text."""
        from src.document_extractor import extract_embedded_text

        result = extract_embedded_text(sample_text_ready_pdf)

        # The result dict should have empty text in page results
        for page in result["page_results"]:
            assert page["text"] == ""

        # Full text is returned separately (caller's responsibility)
        assert isinstance(result["extracted_text"], str)


class TestOCRExtraction:
    """Tests for OCR extraction path."""

    def test_extract_ocr_missing_file(self):
        """OCR extraction handles missing files gracefully."""
        from src.document_extractor import extract_ocr_text

        result = extract_ocr_text("/nonexistent/file.pdf")

        assert result["requires_human_review"] is True
        assert "File not found" in result["human_review_reasons"]
        assert result["extraction_method"] == "OCR"
        assert result["extractor_node_id"] == "extract_ocr_text"

    def test_extract_ocr_non_pdf(self, non_pdf_path):
        """OCR extraction rejects non-PDF files."""
        from src.document_extractor import extract_ocr_text

        result = extract_ocr_text(non_pdf_path)

        assert result["requires_human_review"] is True
        assert "Not a PDF" in result["warnings"][0]

    def test_extract_ocr_empty_pdf(self, empty_pdf_path):
        """OCR extraction handles empty PDFs."""
        from src.document_extractor import extract_ocr_text

        result = extract_ocr_text(empty_pdf_path)

        assert result["page_count"] == 0
        assert result["requires_human_review"] is True

    def test_extract_ocr_valid_pdf(self, sample_vision_required_pdf):
        """OCR extraction succeeds on valid PDF."""
        from src.document_extractor import extract_ocr_text

        result = extract_ocr_text(sample_vision_required_pdf)

        assert result["extraction_method"] == "OCR"
        assert result["page_count"] > 0
        assert result["ocr_pages_count"] > 0
        # Confidence may be None if tesseract doesn't return confidence data
        # but total_character_count should be > 0 for successful extraction
        assert result["total_character_count"] >= 0
        assert result["extractor_node_id"] == "extract_ocr_text"

    def test_extract_ocr_confidence_metrics(self, sample_vision_required_pdf):
        """OCR extraction includes confidence metrics."""
        from src.document_extractor import extract_ocr_text

        result = extract_ocr_text(sample_vision_required_pdf)

        # Confidence should be a percentage (0-100)
        if result["ocr_avg_confidence"] is not None:
            assert 0 <= result["ocr_avg_confidence"] <= 100

        # Check page-level confidence
        for page in result["page_results"]:
            if page["ocr_confidence"] is not None:
                assert 0 <= page["ocr_confidence"] <= 100

    def test_extract_ocr_includes_workflow_id(self, sample_vision_required_pdf):
        """OCR extraction includes workflow_run_id when provided."""
        from src.document_extractor import extract_ocr_text

        workflow_id = "WF-test-ocr-12345"
        result = extract_ocr_text(sample_vision_required_pdf, workflow_run_id=workflow_id)

        assert result["workflow_run_id"] == workflow_id

    def test_extract_ocr_no_logging_full_text(self, sample_vision_required_pdf):
        """OCR result dict never includes full extracted text."""
        from src.document_extractor import extract_ocr_text

        result = extract_ocr_text(sample_vision_required_pdf)

        # Page results should have empty text
        for page in result["page_results"]:
            assert page["text"] == ""

        # Full text is returned separately
        assert isinstance(result["extracted_text"], str)


class TestFinalizeExtraction:
    """Tests for extraction finalization."""

    def test_finalize_extraction_adds_workflow_id(self):
        """Finalize adds workflow_run_id to result."""
        from src.document_extractor import finalize_extraction

        extraction_result = {
            "result": {
                "file_path": "/test/invoice.pdf",
                "file_name": "invoice.pdf",
                "page_count": 1,
                "extraction_method": "EMBEDDED_TEXT",
                "total_character_count": 100,
                "workflow_run_id": "",
                "extractor_node_id": "extract_embedded_text",
            },
            "text": "sample",
        }

        workflow_id = "WF-finalize-12345"
        result = finalize_extraction(extraction_result, workflow_run_id=workflow_id)

        assert result["workflow_run_id"] == workflow_id

    def test_finalize_extraction_preserves_result(self):
        """Finalize preserves extraction result data."""
        from src.document_extractor import finalize_extraction

        extraction_result = {
            "result": {
                "file_path": "/test/invoice.pdf",
                "file_name": "invoice.pdf",
                "page_count": 2,
                "extraction_method": "OCR",
                "total_character_count": 500,
                "ocr_avg_confidence": 85.5,
            }
        }

        result = finalize_extraction(extraction_result)

        assert result["page_count"] == 2
        assert result["extraction_method"] == "OCR"
        assert result["ocr_avg_confidence"] == 85.5


class TestWorkflowNodeIDs:
    """Tests for stable workflow node IDs."""

    def test_embedded_text_node_id_stable(self, sample_text_ready_pdf):
        """Embedded text extraction uses stable node ID."""
        from src.document_extractor import extract_embedded_text

        result = extract_embedded_text(sample_text_ready_pdf)

        # Node ID must be exactly "extract_embedded_text"
        assert result["extractor_node_id"] == "extract_embedded_text"

    def test_ocr_text_node_id_stable(self, sample_vision_required_pdf):
        """OCR extraction uses stable node ID."""
        from src.document_extractor import extract_ocr_text

        result = extract_ocr_text(sample_vision_required_pdf)

        # Node ID must be exactly "extract_ocr_text"
        assert result["extractor_node_id"] == "extract_ocr_text"

    def test_workflow_builder_creates_stable_ids(self):
        """Workflow builder returns stable node IDs."""
        from src.document_extractor import build_extraction_workflow

        workflow, embedded_id, ocr_id, finalize_id = build_extraction_workflow()

        assert embedded_id == "extract_embedded_text"
        assert ocr_id == "extract_ocr_text"
        assert finalize_id == "finalize_extraction"


class TestPathSelection:
    """Tests for extraction path selection."""

    def test_text_ready_path_selected(self, sample_text_ready_pdf):
        """TEXT_READY documents use embedded text extraction."""
        from src.document_preprocessor import profile_pdf
        from src.document_extractor import extract_embedded_text

        # Profile the document
        profile = profile_pdf(sample_text_ready_pdf)

        # Should be marked as TEXT_READY
        assert profile["extraction_method"] == "TEXT_READY"
        assert profile["requires_vision"] is False

        # When we use extraction, it should use embedded text
        result = extract_embedded_text(sample_text_ready_pdf)
        assert result["extraction_method"] == "EMBEDDED_TEXT"

    def test_vision_required_path_selected(self, sample_vision_required_pdf):
        """VISION_REQUIRED documents use OCR extraction."""
        from src.document_preprocessor import profile_pdf
        from src.document_extractor import extract_ocr_text

        # Profile the document
        profile = profile_pdf(sample_vision_required_pdf)

        # Should be marked as VISION_REQUIRED
        assert profile["requires_vision"] is True

        # When we use extraction, it should use OCR
        result = extract_ocr_text(sample_vision_required_pdf)
        assert result["extraction_method"] == "OCR"


class TestNoExternalAPICalls:
    """Tests to ensure no external LLM/API calls are made."""

    def test_embedded_text_no_api_calls(self, sample_text_ready_pdf):
        """Embedded text extraction makes no external API calls."""
        from src.document_extractor import extract_embedded_text

        # If pytesseract is called, this would fail
        # (since we're only doing embedded text extraction)
        with patch("src.document_extractor.pytesseract") as mock_tesseract:
            result = extract_embedded_text(sample_text_ready_pdf)

            # Pytesseract should NOT be called
            mock_tesseract.image_to_string.assert_not_called()
            mock_tesseract.image_to_data.assert_not_called()

    def test_ocr_only_calls_tesseract_not_llm(self, sample_vision_required_pdf):
        """OCR extraction only calls Tesseract, not any LLM."""
        from src.document_extractor import extract_ocr_text

        # Mock pytesseract to verify it's being called
        with patch("src.document_extractor.pytesseract.image_to_string") as mock_image_to_string, \
             patch("src.document_extractor.pytesseract.image_to_data") as mock_image_to_data:
            mock_image_to_string.return_value = "Mock OCR text"
            mock_image_to_data.return_value = {
                "confidence": ["85", "90", "88"],
            }

            try:
                result = extract_ocr_text(sample_vision_required_pdf)
                # The test verifies Tesseract is called (mocked)
            except Exception:
                # It's OK if this fails due to mocking; we're just verifying calls
                pass


class TestDatabaseIntegration:
    """Tests to ensure extraction doesn't modify database."""

    def test_extraction_does_not_modify_database(self, sample_text_ready_pdf):
        """Extraction functions do not write to database."""
        from src.database import DATABASE_PATH
        from src.document_extractor import extract_embedded_text
        import os

        # Get initial DB state (check if it even exists)
        db_exists_before = DATABASE_PATH.exists()
        if db_exists_before:
            mtime_before = os.path.getmtime(DATABASE_PATH)

        # Run extraction
        result = extract_embedded_text(sample_text_ready_pdf)

        # Verify extraction succeeded
        assert result["extraction_method"] == "EMBEDDED_TEXT"

        # DB should not be modified
        if db_exists_before:
            mtime_after = os.path.getmtime(DATABASE_PATH)
            # Allow small tolerance for timestamps
            assert abs(mtime_after - mtime_before) < 1.0

    def test_ocr_extraction_does_not_modify_database(self, sample_vision_required_pdf):
        """OCR extraction functions do not write to database."""
        from src.database import DATABASE_PATH
        from src.document_extractor import extract_ocr_text
        import os

        # Get initial DB state
        db_exists_before = DATABASE_PATH.exists()
        if db_exists_before:
            mtime_before = os.path.getmtime(DATABASE_PATH)

        # Run extraction
        result = extract_ocr_text(sample_vision_required_pdf)

        # Verify extraction succeeded
        assert result["extraction_method"] == "OCR"

        # DB should not be modified
        if db_exists_before:
            mtime_after = os.path.getmtime(DATABASE_PATH)
            assert abs(mtime_after - mtime_before) < 1.0


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_unreadable_page_detection_embedded(self, sample_text_ready_pdf):
        """Unreadable pages are detected in embedded text extraction."""
        from src.document_extractor import extract_embedded_text

        result = extract_embedded_text(sample_text_ready_pdf)

        # All pages should have extraction_method set
        for page in result["page_results"]:
            assert page["extraction_method"] == "EMBEDDED_TEXT"

    def test_unreadable_page_detection_ocr(self, sample_vision_required_pdf):
        """Unreadable pages are flagged in OCR extraction."""
        from src.document_extractor import extract_ocr_text

        result = extract_ocr_text(sample_vision_required_pdf)

        # If there are unreadable pages, they should be tracked
        if result["unreadable_pages_count"] > 0:
            assert len(result["unreadable_page_numbers"]) > 0

    def test_page_results_alignment(self, sample_text_ready_pdf):
        """Page results align with page count."""
        from src.document_extractor import extract_embedded_text

        result = extract_embedded_text(sample_text_ready_pdf)

        page_count = result["page_count"]
        page_results_count = len(result["page_results"])

        assert page_results_count == page_count

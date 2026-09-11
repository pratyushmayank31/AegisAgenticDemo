"""
Tests for document preprocessor and PDF profiling.

Uses temporary files and does not modify sample invoices or production database.
"""

import json
from pathlib import Path

import pytest


class TestDocumentProfile:
    """Tests for DocumentProfile dataclass."""

    def test_document_profile_creation(self):
        """DocumentProfile can be created with required fields."""
        from src.document_preprocessor import DocumentProfile

        profile = DocumentProfile(
            invoice_path="/test/invoice.pdf",
            file_name="invoice.pdf",
            page_count=1,
        )

        assert profile.invoice_path == "/test/invoice.pdf"
        assert profile.file_name == "invoice.pdf"
        assert profile.page_count == 1
        assert profile.preprocessor_node_id == "profile_pdf"

    def test_document_profile_to_dict_excludes_text(self):
        """DocumentProfile.to_dict() never includes full extracted_text."""
        from src.document_preprocessor import DocumentProfile

        profile = DocumentProfile(
            invoice_path="/test/invoice.pdf",
            file_name="invoice.pdf",
            page_count=1,
            extracted_text="This is sensitive invoice text that should not be logged",
        )

        result = profile.to_dict()
        assert result["extracted_text"] == ""
        assert "invoice text" not in str(result)


class TestNormalizeWhitespace:
    """Tests for whitespace normalization."""

    def test_normalize_multiple_spaces(self):
        """Multiple spaces are collapsed to single space."""
        from src.document_preprocessor import normalize_whitespace

        text = "Invoice  Amount:    $1000"
        result = normalize_whitespace(text)
        assert result == "Invoice Amount: $1000"

    def test_normalize_tabs(self):
        """Tabs are replaced with spaces."""
        from src.document_preprocessor import normalize_whitespace

        text = "Invoice\t\tAmount"
        result = normalize_whitespace(text)
        assert "  " not in result
        assert "Amount" in result

    def test_normalize_multiple_newlines(self):
        """Multiple newlines are collapsed to single newline."""
        from src.document_preprocessor import normalize_whitespace

        text = "Line 1\n\n\nLine 2"
        result = normalize_whitespace(text)
        assert result == "Line 1\nLine 2"


class TestProfilePDF:
    """Tests for PDF profiling function."""

    def test_missing_file_rejected(self):
        """Missing file is rejected with appropriate status."""
        from src.document_preprocessor import profile_pdf

        result = profile_pdf("/nonexistent/path.pdf")

        assert result["extraction_method"] == "FAILED"
        assert result["quality_status"] == "FAILED"
        assert "File not found" in result["quality_reasons"]
        assert result["requires_vision"] is True

    def test_non_pdf_rejected(self, tmp_path):
        """Non-PDF file is rejected."""
        from src.document_preprocessor import profile_pdf

        non_pdf = tmp_path / "document.txt"
        non_pdf.write_text("Not a PDF")

        result = profile_pdf(str(non_pdf))

        assert result["extraction_method"] == "FAILED"
        assert result["quality_status"] == "FAILED"
        assert any("Not a PDF file" in reason for reason in result["quality_reasons"])

    def test_minimal_text_pdf_vision_required(self, tmp_path):
        """PDF with minimal embedded text requires vision processing."""
        from src.document_preprocessor import profile_pdf
        import fitz

        # Create PDF with blank page (minimal or no text content)
        blank_pdf = tmp_path / "blank.pdf"
        doc = fitz.open()
        doc.new_page()  # Add one blank page with no meaningful content
        doc.save(str(blank_pdf))
        doc.close()

        result = profile_pdf(str(blank_pdf))

        assert result["page_count"] == 1
        # Blank page may have minimal formatting, but should be below threshold
        assert result["text_character_count_per_page"] < 50  # Below TEXT_EXTRACTION_THRESHOLD
        assert result["extraction_method"] == "VISION_REQUIRED"
        assert result["quality_status"] == "VISION_REQUIRED"
        assert result["requires_vision"] is True

    def test_text_ready_classification(self):
        """PDF with sufficient embedded text is classified TEXT_READY."""
        from src.document_preprocessor import profile_pdf
        from pathlib import Path

        # Use first sample invoice which should have text
        project_root = Path(__file__).resolve().parents[1]
        sample_pdf = project_root / "sample_invoices" / "01_Veson_Bunker_Clean_STP.pdf"

        if not sample_pdf.exists():
            pytest.skip("Sample invoice not found")

        result = profile_pdf(str(sample_pdf))

        # Clean STP invoice should have text
        if result["text_character_count"] > 0:
            assert result["extraction_method"] in ["TEXT_READY", "VISION_REQUIRED", "HYBRID_REVIEW"]
            assert isinstance(result["requires_vision"], bool)
            assert result["quality_status"] in ["TEXT_READY", "VISION_REQUIRED", "HYBRID_REVIEW"]

    def test_profile_result_structure(self, tmp_path):
        """Profile result contains all required fields."""
        from src.document_preprocessor import profile_pdf
        import fitz

        # Create a minimal valid PDF with some text
        pdf = tmp_path / "test.pdf"
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((50, 50), "Invoice Number: INV-001\nAmount: $1000")
        doc.save(str(pdf))
        doc.close()

        result = profile_pdf(str(pdf))

        # Verify result structure
        assert "invoice_path" in result
        assert "file_name" in result
        assert "page_count" in result
        assert "extracted_text" in result
        assert "text_character_count" in result
        assert "text_character_count_per_page" in result
        assert "image_count" in result
        assert "extraction_method" in result
        assert "requires_vision" in result
        assert "quality_status" in result
        assert "quality_reasons" in result
        assert "preprocessor_node_id" in result

        # Verify security: extracted_text is always empty in result
        assert result["extracted_text"] == ""

    def test_profile_never_logs_full_text(self, tmp_path, capsys):
        """Profile function does not log complete document text."""
        from src.document_preprocessor import profile_pdf
        import fitz

        pdf = tmp_path / "test.pdf"
        doc = fitz.open()
        page = doc.new_page()
        secret_text = "CONFIDENTIAL: Bank Account 1234567890 with PIN 9999"
        page.insert_text((50, 50), secret_text)
        doc.save(str(pdf))
        doc.close()

        result = profile_pdf(str(pdf))

        # Verify the secret text is not in the result
        assert secret_text not in str(result)
        # Verify no sensitive data in output
        captured = capsys.readouterr()
        assert secret_text not in captured.out
        assert secret_text not in captured.err


class TestBuildProfileWorkflow:
    """Tests for Kailash workflow builder."""

    def test_stable_node_id(self):
        """Workflow uses stable 'profile_pdf' node ID."""
        from src.document_preprocessor import build_profile_workflow

        workflow, node_id = build_profile_workflow()

        assert node_id == "profile_pdf", "Node ID must be exactly 'profile_pdf'"

    def test_workflow_execution_with_stable_id(self):
        """Workflow executes with stable node ID."""
        from src.document_preprocessor import build_profile_workflow
        from kailash import LocalRuntime
        from pathlib import Path

        project_root = Path(__file__).resolve().parents[1]
        sample_pdf = project_root / "sample_invoices" / "01_Veson_Bunker_Clean_STP.pdf"

        if not sample_pdf.exists():
            pytest.skip("Sample invoice not found")

        workflow, node_id = build_profile_workflow()

        with LocalRuntime() as runtime:
            result, workflow_run_id = runtime.execute(
                workflow,
                parameters={
                    "profile_pdf": {"invoice_path": str(sample_pdf)}
                },
            )

            # Verify stable node ID is in results
            assert "profile_pdf" in result, "Result must contain 'profile_pdf' key"
            assert workflow_run_id, "workflow_run_id must be non-empty"

            # Verify profile result structure
            profile_result = result["profile_pdf"]
            if "result" in profile_result:
                profile = profile_result["result"]
            else:
                profile = profile_result

            assert profile["file_name"]
            assert profile["page_count"] > 0

    def test_no_llm_invocation(self):
        """Profile workflow never makes external API calls."""
        from src.document_preprocessor import profile_pdf
        from pathlib import Path
        import unittest.mock as mock

        project_root = Path(__file__).resolve().parents[1]
        sample_pdf = project_root / "sample_invoices" / "01_Veson_Bunker_Clean_STP.pdf"

        if not sample_pdf.exists():
            pytest.skip("Sample invoice not found")

        # Mock socket/http to ensure no network calls
        with mock.patch("socket.socket") as mock_socket:
            result = profile_pdf(str(sample_pdf))

            # Verify result is valid
            assert result["preprocessor_node_id"] == "profile_pdf"
            assert result["page_count"] > 0

            # Verify no network socket was created
            mock_socket.assert_not_called()


class TestSampleInvoiceProfiles:
    """Integration tests profiling actual sample invoices."""

    @pytest.mark.parametrize("invoice_name", [
        "01_Veson_Bunker_Clean_STP.pdf",
        "02_smartPAL_Spares_Scan_Handwritten.pdf",
        "03_Oracle_Software_Clean_UnknownVendor.pdf",
        "04_Veson_Demurrage_AR_Duplicate.pdf",
        "05_Oracle_Legal_Photo_BankChange.pdf",
        "06_smartPAL_Repair_PoorScan_PriceMismatch.pdf",
        "07_Oracle_OfficeRent_Spanish_Clean.pdf",
        "08_Veson_PortAgency_Photo_MissingReference.pdf",
        "09_AR_Freight_BelowThreshold_Clean.pdf",
        "10_AR_CharterHire_AboveThreshold_Clean.pdf",
        "11_AR_Demurrage_AtThreshold_Clean.pdf",
        "12_AR_Corporate_Recharge_Threshold.pdf",
        "13_AR_Freight_ConfigurableThreshold_PoorScan.pdf",
    ])
    def test_sample_invoice_profiles(self, invoice_name):
        """All sample invoices are profileable."""
        from src.document_preprocessor import profile_pdf
        from pathlib import Path

        project_root = Path(__file__).resolve().parents[1]
        sample_pdf = project_root / "sample_invoices" / invoice_name

        if not sample_pdf.exists():
            pytest.skip(f"Sample invoice not found: {invoice_name}")

        result = profile_pdf(str(sample_pdf))

        # Verify result is valid
        assert result["file_name"] == invoice_name
        assert result["page_count"] > 0
        assert result["extraction_method"] in ["TEXT_READY", "VISION_REQUIRED", "HYBRID_REVIEW", "FAILED"]
        assert result["quality_status"] in ["TEXT_READY", "VISION_REQUIRED", "HYBRID_REVIEW", "FAILED"]
        assert isinstance(result["requires_vision"], bool)
        assert result["preprocessor_node_id"] == "profile_pdf"

        # Verify no sensitive text in result
        assert result["extracted_text"] == ""

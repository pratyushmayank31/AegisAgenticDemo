"""
Unified text extraction from invoices using two deterministic paths.

TEXT_READY (embedded text) → PyMuPDF text extraction
VISION_REQUIRED (image-based) → PyMuPDF rendering + Tesseract OCR

Both paths return the same typed extraction result.
"""

import json
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Dict, Optional, List
from copy import deepcopy

import pymupdf
import pytesseract
from PIL import Image
import io

from kailash import WorkflowBuilder


# Tesseract output confidence threshold: pages below this may need human review
TESSERACT_CONFIDENCE_THRESHOLD = 50.0


@dataclass
class PageExtractionResult:
    """Typed result for a single page extraction."""

    page_number: int
    extraction_method: str  # "EMBEDDED_TEXT" or "OCR"
    text: str
    character_count: int
    ocr_confidence: Optional[float] = None  # Only for OCR pages
    is_unreadable: bool = False
    unreadable_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary, excluding full text."""
        result = asdict(self)
        result["text"] = ""
        return result


@dataclass
class DocumentExtractionResult:
    """Typed result of complete document extraction."""

    file_path: str
    file_name: str
    page_count: int
    extraction_method: str  # "EMBEDDED_TEXT" or "OCR"
    total_character_count: int

    page_results: List[PageExtractionResult] = field(default_factory=list)

    # Metrics
    ocr_pages_count: int = 0
    ocr_avg_confidence: Optional[float] = None
    unreadable_pages_count: int = 0
    unreadable_page_numbers: List[int] = field(default_factory=list)

    # Flags
    requires_human_review: bool = False
    human_review_reasons: List[str] = field(default_factory=list)

    # Warnings
    warnings: List[str] = field(default_factory=list)

    # Full extracted text (available internally for Chapter 4C, excluded from default dicts)
    extracted_text: str = ""

    # Workflow context
    workflow_run_id: str = ""
    extractor_node_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for logging/CLI (no full text or page text)."""
        result = asdict(self)
        result["page_results"] = [p.to_dict() for p in self.page_results]
        result["extracted_text"] = ""
        return result

    def to_dict_programmatic(self) -> Dict[str, Any]:
        """Convert to dict for programmatic use (includes extracted_text, excludes page text)."""
        result = asdict(self)
        result["page_results"] = [p.to_dict() for p in self.page_results]
        # Keep extracted_text for Chapter 4C programmatic access
        return result


def extract_embedded_text(
    file_path: str,
    workflow_run_id: str = "",
) -> Dict[str, Any]:
    """
    Extract text from PDF using embedded text (PyMuPDF).

    Used for TEXT_READY documents. Returns extraction result with
    extracted_text available internally for programmatic use.

    Args:
        file_path: Path to the PDF file
        workflow_run_id: Kailash workflow execution ID for tracing

    Returns:
        Dictionary with extraction result (extracted_text available internally)
    """
    path = Path(file_path)

    # Reject missing files
    if not path.exists():
        return asdict(DocumentExtractionResult(
            file_path=file_path,
            file_name=path.name,
            page_count=0,
            extraction_method="EMBEDDED_TEXT",
            total_character_count=0,
            requires_human_review=True,
            human_review_reasons=["File not found"],
            warnings=["File not found"],
            extracted_text="",
            workflow_run_id=workflow_run_id,
            extractor_node_id="extract_embedded_text",
        ))

    # Reject non-PDF files
    if path.suffix.lower() != ".pdf":
        return asdict(DocumentExtractionResult(
            file_path=file_path,
            file_name=path.name,
            page_count=0,
            extraction_method="EMBEDDED_TEXT",
            total_character_count=0,
            requires_human_review=True,
            human_review_reasons=[f"Not a PDF file: {path.suffix}"],
            warnings=[f"Not a PDF file: {path.suffix}"],
            extracted_text="",
            workflow_run_id=workflow_run_id,
            extractor_node_id="extract_embedded_text",
        ))

    # Try to open and extract from PDF
    try:
        doc = pymupdf.open(file_path)
    except Exception as e:
        return asdict(DocumentExtractionResult(
            file_path=file_path,
            file_name=path.name,
            page_count=0,
            extraction_method="EMBEDDED_TEXT",
            total_character_count=0,
            requires_human_review=True,
            human_review_reasons=[f"Failed to open PDF: {str(e)}"],
            warnings=[f"Failed to open PDF: {str(e)}"],
            extracted_text="",
            workflow_run_id=workflow_run_id,
            extractor_node_id="extract_embedded_text",
        ))

    try:
        page_count = len(doc)
        if page_count == 0:
            return asdict(DocumentExtractionResult(
                file_path=file_path,
                file_name=path.name,
                page_count=0,
                extraction_method="EMBEDDED_TEXT",
                total_character_count=0,
                requires_human_review=True,
                human_review_reasons=["Empty PDF"],
                warnings=["Empty PDF"],
                extracted_text="",
                workflow_run_id=workflow_run_id,
                extractor_node_id="extract_embedded_text",
            ))

        total_text = ""
        page_results = []
        total_char_count = 0

        for page_num in range(page_count):
            try:
                page = doc[page_num]
                page_text = page.get_text()

                char_count = len(page_text)
                total_text += page_text + "\n"
                total_char_count += char_count

                page_results.append(
                    PageExtractionResult(
                        page_number=page_num + 1,
                        extraction_method="EMBEDDED_TEXT",
                        text=page_text,
                        character_count=char_count,
                        ocr_confidence=None,
                        is_unreadable=False,
                    )
                )

            except Exception as page_error:
                page_results.append(
                    PageExtractionResult(
                        page_number=page_num + 1,
                        extraction_method="EMBEDDED_TEXT",
                        text="",
                        character_count=0,
                        ocr_confidence=None,
                        is_unreadable=True,
                        unreadable_reason=str(page_error),
                    )
                )

        # Build extraction result
        unreadable_pages = [p for p in page_results if p.is_unreadable]
        unreadable_count = len(unreadable_pages)

        human_review_reasons = []
        if unreadable_count > 0:
            human_review_reasons.append(f"{unreadable_count} page(s) could not be extracted")

        result = DocumentExtractionResult(
            file_path=file_path,
            file_name=path.name,
            page_count=page_count,
            extraction_method="EMBEDDED_TEXT",
            total_character_count=total_char_count,
            page_results=page_results,
            unreadable_pages_count=unreadable_count,
            unreadable_page_numbers=[p.page_number for p in unreadable_pages],
            requires_human_review=unreadable_count > 0,
            human_review_reasons=human_review_reasons,
            extracted_text=total_text,
            workflow_run_id=workflow_run_id,
            extractor_node_id="extract_embedded_text",
        )

        return result.to_dict_programmatic()

    finally:
        doc.close()


def extract_ocr_text(
    file_path: str,
    workflow_run_id: str = "",
) -> Dict[str, Any]:
    """
    Extract text from PDF using OCR (PyMuPDF rendering + Tesseract).

    Used for VISION_REQUIRED documents. Renders each page to image,
    then uses Tesseract for OCR. Returns extraction result with text.

    Args:
        file_path: Path to the PDF file
        workflow_run_id: Kailash workflow execution ID for tracing

    Returns:
        Dictionary with extraction result (extracted_text available internally)
    """
    path = Path(file_path)

    # Reject missing files
    if not path.exists():
        return asdict(DocumentExtractionResult(
            file_path=file_path,
            file_name=path.name,
            page_count=0,
            extraction_method="OCR",
            total_character_count=0,
            requires_human_review=True,
            human_review_reasons=["File not found"],
            warnings=["File not found"],
            extracted_text="",
            workflow_run_id=workflow_run_id,
            extractor_node_id="extract_ocr_text",
        ))

    # Reject non-PDF files
    if path.suffix.lower() != ".pdf":
        return asdict(DocumentExtractionResult(
            file_path=file_path,
            file_name=path.name,
            page_count=0,
            extraction_method="OCR",
            total_character_count=0,
            requires_human_review=True,
            human_review_reasons=[f"Not a PDF file: {path.suffix}"],
            warnings=[f"Not a PDF file: {path.suffix}"],
            extracted_text="",
            workflow_run_id=workflow_run_id,
            extractor_node_id="extract_ocr_text",
        ))

    # Try to open and process PDF
    try:
        doc = pymupdf.open(file_path)
    except Exception as e:
        return asdict(DocumentExtractionResult(
            file_path=file_path,
            file_name=path.name,
            page_count=0,
            extraction_method="OCR",
            total_character_count=0,
            requires_human_review=True,
            human_review_reasons=[f"Failed to open PDF: {str(e)}"],
            warnings=[f"Failed to open PDF: {str(e)}"],
            extracted_text="",
            workflow_run_id=workflow_run_id,
            extractor_node_id="extract_ocr_text",
        ))

    try:
        page_count = len(doc)
        if page_count == 0:
            return asdict(DocumentExtractionResult(
                file_path=file_path,
                file_name=path.name,
                page_count=0,
                extraction_method="OCR",
                total_character_count=0,
                requires_human_review=True,
                human_review_reasons=["Empty PDF"],
                warnings=["Empty PDF"],
                extracted_text="",
                workflow_run_id=workflow_run_id,
                extractor_node_id="extract_ocr_text",
            ))

        total_text = ""
        page_results = []
        total_char_count = 0
        ocr_confidences = []
        unreadable_pages = []

        for page_num in range(page_count):
            try:
                page = doc[page_num]

                # Render page to image at 200 DPI for better OCR
                pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2))
                img_data = pix.tobytes("ppm")
                img = Image.open(io.BytesIO(img_data))

                # Run Tesseract OCR with confidence data
                page_text = pytesseract.image_to_string(img)

                # Extract confidence from Tesseract data
                data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
                confidences = [int(c) for c in data.get("conf", []) if int(c) > 0]
                page_confidence = sum(confidences) / len(confidences) if confidences else 0

                char_count = len(page_text)
                total_text += page_text + "\n"
                total_char_count += char_count
                ocr_confidences.append(page_confidence)

                is_unreadable = page_confidence < TESSERACT_CONFIDENCE_THRESHOLD

                page_results.append(
                    PageExtractionResult(
                        page_number=page_num + 1,
                        extraction_method="OCR",
                        text=page_text,
                        character_count=char_count,
                        ocr_confidence=page_confidence,
                        is_unreadable=is_unreadable,
                        unreadable_reason="Low OCR confidence" if is_unreadable else None,
                    )
                )

                if is_unreadable:
                    unreadable_pages.append(page_num + 1)

            except Exception as page_error:
                page_results.append(
                    PageExtractionResult(
                        page_number=page_num + 1,
                        extraction_method="OCR",
                        text="",
                        character_count=0,
                        ocr_confidence=None,
                        is_unreadable=True,
                        unreadable_reason=str(page_error),
                    )
                )
                unreadable_pages.append(page_num + 1)

        # Calculate average OCR confidence
        avg_confidence = (
            sum(ocr_confidences) / len(ocr_confidences)
            if ocr_confidences else None
        )

        # Build extraction result
        human_review_reasons = []
        if unreadable_pages:
            human_review_reasons.append(
                f"Pages with low OCR confidence: {unreadable_pages}"
            )

        result = DocumentExtractionResult(
            file_path=file_path,
            file_name=path.name,
            page_count=page_count,
            extraction_method="OCR",
            total_character_count=total_char_count,
            page_results=page_results,
            ocr_pages_count=page_count,
            ocr_avg_confidence=avg_confidence,
            unreadable_pages_count=len(unreadable_pages),
            unreadable_page_numbers=unreadable_pages,
            requires_human_review=len(unreadable_pages) > 0,
            human_review_reasons=human_review_reasons,
            extracted_text=total_text,
            workflow_run_id=workflow_run_id,
            extractor_node_id="extract_ocr_text",
        )

        return result.to_dict_programmatic()

    finally:
        doc.close()


def finalize_extraction(
    extraction_result: Dict[str, Any],
    workflow_run_id: str = "",
) -> Dict[str, Any]:
    """
    Finalize extraction result (common finalization for both paths).

    Args:
        extraction_result: Result from extract_embedded_text or extract_ocr_text
        workflow_run_id: Kailash workflow execution ID for tracing

    Returns:
        Finalized extraction result with workflow context
    """
    result = extraction_result.get("result", extraction_result)

    # Ensure node ID is set
    if "extractor_node_id" not in result or not result["extractor_node_id"]:
        result["extractor_node_id"] = "finalize_extraction"

    # Add workflow run ID if provided
    if workflow_run_id and not result.get("workflow_run_id"):
        result["workflow_run_id"] = workflow_run_id

    return result


def build_extraction_workflow() -> tuple[Any, str, str, str]:
    """
    Build document extraction workflow using Kailash.

    Creates a workflow with three nodes:
    - extract_embedded_text: For TEXT_READY documents
    - extract_ocr_text: For VISION_REQUIRED documents
    - finalize_extraction: Common finalization

    Returns:
        Tuple of (Kailash Workflow object, embedded_node_id, ocr_node_id, finalize_node_id)
    """
    from kailash.nodes import HandlerNode

    builder = WorkflowBuilder()

    # Create handler nodes for extraction methods
    embedded_handler = HandlerNode(handler=extract_embedded_text)
    ocr_handler = HandlerNode(handler=extract_ocr_text)
    finalize_handler = HandlerNode(handler=finalize_extraction)

    # Add nodes with stable, deterministic IDs
    embedded_node_id = builder.add_node(embedded_handler, "extract_embedded_text")
    ocr_node_id = builder.add_node(ocr_handler, "extract_ocr_text")
    finalize_node_id = builder.add_node(finalize_handler, "finalize_extraction")

    # Build and return workflow with stable node IDs
    workflow = builder.build(workflow_id="document_extraction_workflow")
    return workflow, embedded_node_id, ocr_node_id, finalize_node_id

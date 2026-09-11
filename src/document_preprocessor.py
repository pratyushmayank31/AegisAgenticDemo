"""
Deterministic PDF document profiling without LLM.

Profiles PDFs to determine whether they have sufficient embedded text
for normal text extraction or require vision/OCR processing.
"""

import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, Optional

import fitz


# Text extraction threshold: minimum characters per page needed for TEXT_READY classification
# This threshold balances between:
# - TEXT_READY: document has meaningful embedded text for extraction
# - VISION_REQUIRED: document is image-based or has insufficient text
# Value: 50 characters per page allows for simple invoices with sparse structured text
# while filtering out image-only or severely degraded documents
TEXT_EXTRACTION_THRESHOLD = 50


@dataclass
class DocumentProfile:
    """Typed result of PDF document profiling analysis."""

    invoice_path: str
    file_name: str
    page_count: int
    extracted_text: str = ""
    text_character_count: int = 0
    text_character_count_per_page: float = 0.0
    image_count: int = 0
    extraction_method: str = ""
    requires_vision: bool = False
    quality_status: str = ""
    quality_reasons: list[str] = None
    preprocessor_node_id: str = "profile_pdf"

    def __post_init__(self):
        if self.quality_reasons is None:
            self.quality_reasons = []

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary, excluding full text for security."""
        result = asdict(self)
        # Never include full extracted_text in dict output
        result["extracted_text"] = ""
        return result


def normalize_whitespace(text: str) -> str:
    """
    Normalize repeated whitespace in extracted text.

    Replaces multiple spaces, tabs, and newlines with single instances
    to prepare for analysis.
    """
    # Replace tabs with spaces
    text = text.replace("\t", " ")
    # Replace multiple spaces with single space
    text = re.sub(r" +", " ", text)
    # Replace multiple newlines with single newline
    text = re.sub(r"\n\n+", "\n", text)
    return text.strip()


def profile_pdf(invoice_path: str) -> Dict[str, Any]:
    """
    Profile a PDF document without LLM invocation.

    Extracts embedded text, counts pages and images, and classifies
    whether the document has sufficient text for extraction or requires vision/OCR.

    Args:
        invoice_path: Path to the PDF file

    Returns:
        DocumentProfile result as dictionary
    """
    path = Path(invoice_path)

    # Reject missing files
    if not path.exists():
        return DocumentProfile(
            invoice_path=invoice_path,
            file_name=path.name,
            page_count=0,
            extraction_method="FAILED",
            quality_status="FAILED",
            quality_reasons=["File not found"],
            requires_vision=True,
        ).to_dict()

    # Reject non-PDF files
    if path.suffix.lower() != ".pdf":
        return DocumentProfile(
            invoice_path=invoice_path,
            file_name=path.name,
            page_count=0,
            extraction_method="FAILED",
            quality_status="FAILED",
            quality_reasons=[f"Not a PDF file: {path.suffix}"],
            requires_vision=True,
        ).to_dict()

    # Try to open and profile PDF
    try:
        doc = fitz.open(invoice_path)
    except Exception as e:
        return DocumentProfile(
            invoice_path=invoice_path,
            file_name=path.name,
            page_count=0,
            extraction_method="FAILED",
            quality_status="FAILED",
            quality_reasons=[f"Failed to open PDF: {str(e)}"],
            requires_vision=True,
        ).to_dict()

    try:
        # Count pages and extract text
        page_count = len(doc)
        total_text = ""
        image_count = 0
        text_per_page = []

        for page_num in range(page_count):
            try:
                page = doc[page_num]

                # Extract text from page
                page_text = page.get_text()
                page_text = normalize_whitespace(page_text)
                total_text += page_text + "\n"
                text_per_page.append(len(page_text))

                # Count images on page
                for img in page.get_images():
                    image_count += 1

            except Exception as page_error:
                return DocumentProfile(
                    invoice_path=invoice_path,
                    file_name=path.name,
                    page_count=page_count,
                    extraction_method="FAILED",
                    quality_status="FAILED",
                    quality_reasons=[f"Error processing page {page_num + 1}: {str(page_error)}"],
                    requires_vision=True,
                ).to_dict()

        # Reject empty PDFs
        if page_count == 0:
            return DocumentProfile(
                invoice_path=invoice_path,
                file_name=path.name,
                page_count=0,
                extraction_method="FAILED",
                quality_status="FAILED",
                quality_reasons=["Empty PDF"],
                requires_vision=True,
            ).to_dict()

        # Analyze extracted text
        text_char_count = len(total_text)
        avg_chars_per_page = text_char_count / page_count if page_count > 0 else 0

        # Classify extraction readiness
        quality_reasons = []
        if text_char_count == 0:
            extraction_method = "VISION_REQUIRED"
            requires_vision = True
            quality_reasons.append("No embedded text detected")
        elif avg_chars_per_page < TEXT_EXTRACTION_THRESHOLD:
            # Check if all pages or just some have low text
            pages_with_text = sum(1 for chars in text_per_page if chars > 0)
            pages_without_text = page_count - pages_with_text

            if pages_without_text > 0 and pages_with_text > 0:
                extraction_method = "HYBRID_REVIEW"
                requires_vision = True
                quality_reasons.append(
                    f"Mixed content: {pages_with_text} pages with text, {pages_without_text} without"
                )
                quality_reasons.append(f"Average {avg_chars_per_page:.1f} chars/page (threshold: {TEXT_EXTRACTION_THRESHOLD})")
            else:
                extraction_method = "VISION_REQUIRED"
                requires_vision = True
                quality_reasons.append(f"Insufficient text: {avg_chars_per_page:.1f} chars/page (threshold: {TEXT_EXTRACTION_THRESHOLD})")
        else:
            extraction_method = "TEXT_READY"
            requires_vision = False
            quality_reasons.append(f"Sufficient embedded text: {avg_chars_per_page:.1f} chars/page")

        # Determine quality status
        if extraction_method == "FAILED":
            quality_status = "FAILED"
        elif extraction_method == "VISION_REQUIRED":
            quality_status = "VISION_REQUIRED"
        elif extraction_method == "HYBRID_REVIEW":
            quality_status = "HYBRID_REVIEW"
        else:
            quality_status = "TEXT_READY"

        # Add image information to quality reasons if relevant
        if image_count > 0:
            quality_reasons.append(f"Contains {image_count} image(s)")

        return DocumentProfile(
            invoice_path=invoice_path,
            file_name=path.name,
            page_count=page_count,
            extracted_text="",
            text_character_count=text_char_count,
            text_character_count_per_page=avg_chars_per_page,
            image_count=image_count,
            extraction_method=extraction_method,
            requires_vision=requires_vision,
            quality_status=quality_status,
            quality_reasons=quality_reasons,
            preprocessor_node_id="profile_pdf",
        ).to_dict()

    finally:
        doc.close()


def build_profile_workflow() -> tuple[Any, str]:
    """
    Build PDF profiling workflow using Kailash with HandlerNode.

    Creates a workflow with a single HandlerNode that profiles PDFs
    without any sandbox restrictions.

    Returns:
        Tuple of (Kailash Workflow object, stable node_id="profile_pdf")
    """
    from kailash import WorkflowBuilder
    from kailash.nodes import HandlerNode

    builder = WorkflowBuilder()

    # Create handler node for PDF profiling
    handler_node = HandlerNode(handler=profile_pdf)

    # Add node with stable, deterministic ID for governance traceability
    node_id = builder.add_node(handler_node, "profile_pdf")

    # Build and return the workflow with the stable node ID
    workflow = builder.build(workflow_id="document_profiling_workflow")
    return workflow, node_id

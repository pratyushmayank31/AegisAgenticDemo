"""
Extract text from one or all sample invoices.

Usage:
  python -m scripts.extract_invoices                  # Extract all 13 sample PDFs
  python -m scripts.extract_invoices <path/to/file.pdf>  # Extract single PDF
  python -m scripts.extract_invoices --preview           # Show 150-char preview

The extraction method (embedded text or OCR) is determined by:
1. Running profile_pdf() to classify the document
2. If TEXT_READY: use embedded text extraction
3. If VISION_REQUIRED: use OCR extraction
"""

import sys
from pathlib import Path

from src.document_preprocessor import profile_pdf
from src.document_extractor import extract_embedded_text, extract_ocr_text


def format_extraction_summary(extraction_result: dict, show_preview: bool = False) -> str:
    """Format extraction result for compact display (no full text in CLI)."""
    from src.document_extractor import DocumentExtractionResult

    # Convert back to dataclass and call to_dict() to exclude extracted_text from display
    result_for_display = extraction_result.copy()
    text_for_preview = extraction_result.get("extracted_text", "")
    result_for_display["extracted_text"] = ""  # Don't show full text in CLI

    file_name = result_for_display.get("file_name", "unknown")
    method = result_for_display.get("extraction_method", "unknown")
    page_count = result_for_display.get("page_count", 0)
    char_count = result_for_display.get("total_character_count", 0)
    requires_review = result_for_display.get("requires_human_review", False)
    human_review_reasons = result_for_display.get("human_review_reasons", [])
    warnings = result_for_display.get("warnings", [])

    # OCR-specific metrics
    ocr_conf = result_for_display.get("ocr_avg_confidence")
    unreadable_pages = result_for_display.get("unreadable_page_numbers", [])

    # Basic info
    info = f"{file_name:<35} {method:<15} {page_count:>2}p {char_count:>6}ch"

    # OCR confidence if available
    if ocr_conf is not None:
        info += f" conf={ocr_conf:>5.1f}%"

    # Human review flag
    if requires_review:
        info += " [REVIEW]"

    # Warnings
    if warnings:
        info += f" !{len(warnings)}w"

    # Optional preview (sanitized, max 150 chars)
    if show_preview and text_for_preview:
        # Get first 150 chars, sanitize
        preview = text_for_preview[:150].replace("\n", " ").replace("\r", "")
        if len(text_for_preview) > 150:
            preview += "..."
        info += f"\n  Preview: {preview}"

    return info


def extract_invoice(pdf_path: str, show_preview: bool = False) -> dict:
    """
    Extract text from a single invoice using appropriate method.

    Args:
        pdf_path: Path to PDF file
        show_preview: Whether to show text preview

    Returns:
        Extraction result dictionary
    """
    # Step 1: Profile the document to determine extraction method
    profile = profile_pdf(pdf_path)

    extraction_method = profile.get("extraction_method", "FAILED")
    requires_vision = profile.get("requires_vision", True)

    # Step 2: Select extraction path based on profile
    if extraction_method == "TEXT_READY" and not requires_vision:
        # Use embedded text extraction
        result = extract_embedded_text(pdf_path)
    elif extraction_method in ("VISION_REQUIRED", "HYBRID_REVIEW") or requires_vision:
        # Use OCR extraction
        result = extract_ocr_text(pdf_path)
    else:
        # Profile failed
        result = {
            "file_path": pdf_path,
            "file_name": Path(pdf_path).name,
            "page_count": 0,
            "extraction_method": "FAILED",
            "total_character_count": 0,
            "requires_human_review": True,
            "human_review_reasons": ["Profile classification failed"],
            "warnings": [f"Profile method: {extraction_method}"],
            "extracted_text": "",
        }

    return result


def main() -> None:
    """Extract text from invoices and print summary."""
    project_root = Path(__file__).resolve().parents[1]

    # Check for flags
    show_preview = "--preview" in sys.argv
    if show_preview:
        sys.argv.remove("--preview")

    # Determine which PDFs to extract
    if len(sys.argv) > 1:
        # Extract single PDF specified on command line
        pdf_paths = [sys.argv[1]]
        print(f"Extracting: {pdf_paths[0]}\n")
    else:
        # Extract all sample invoices
        sample_dir = project_root / "sample_invoices"
        pdf_paths = sorted(sample_dir.glob("*.pdf"))
        print(f"Extracting {len(pdf_paths)} sample invoices\n")

    # Extraction statistics
    results_by_method = {"EMBEDDED_TEXT": 0, "OCR": 0, "FAILED": 0}
    total_chars = 0
    review_count = 0
    avg_ocr_conf_values = []

    # Extract each PDF
    print("Filename                           Method          Pages  Chars")
    print("─" * 75)

    for pdf_path in pdf_paths:
        try:
            result = extract_invoice(str(pdf_path), show_preview=show_preview)

            result_data = result  # Result is now the dict directly
            method = result_data.get("extraction_method", "FAILED")
            char_count = result_data.get("total_character_count", 0)
            requires_review = result_data.get("requires_human_review", False)
            ocr_conf = result_data.get("ocr_avg_confidence")

            # Track statistics
            results_by_method[method] = results_by_method.get(method, 0) + 1
            total_chars += char_count
            if requires_review:
                review_count += 1
            if ocr_conf is not None:
                avg_ocr_conf_values.append(ocr_conf)

            # Format and print row
            summary = format_extraction_summary(result, show_preview=show_preview)
            print(summary)

        except Exception as e:
            print(f"{str(pdf_path):<35} ERROR: {str(e)}")

    # Print summary
    print("─" * 75)
    print("\nExtraction Summary:")
    print(f"  EMBEDDED_TEXT:  {results_by_method.get('EMBEDDED_TEXT', 0):>3} invoice(s)")
    print(f"  OCR:            {results_by_method.get('OCR', 0):>3} invoice(s)")
    print(f"  FAILED:         {results_by_method.get('FAILED', 0):>3} invoice(s)")

    if total_chars > 0:
        avg_chars = total_chars / len(pdf_paths)
        print(f"\nAverage Characters per Invoice: {avg_chars:.0f}")

    if avg_ocr_conf_values:
        avg_conf = sum(avg_ocr_conf_values) / len(avg_ocr_conf_values)
        print(f"Average OCR Confidence: {avg_conf:.1f}%")

    if review_count > 0:
        print(f"\nDocuments Requiring Human Review: {review_count}")


if __name__ == "__main__":
    main()

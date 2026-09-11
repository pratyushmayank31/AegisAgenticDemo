"""
Profile one or all sample invoices for text extraction readiness.

Usage:
  python -m scripts.profile_invoices                  # Profile all sample PDFs
  python -m scripts.profile_invoices <path/to/file.pdf>  # Profile single PDF
"""

import sys
from pathlib import Path
from collections import defaultdict

from src.document_preprocessor import profile_pdf


def format_table_row(name: str, pages: int, chars: int, chars_per_page: float, images: int, method: str, vision: str, quality: str) -> str:
    """Format a row for the output table."""
    # Truncate filename if too long
    name_display = name[:30] if len(name) > 30 else name
    return f"{name_display:<32} {pages:>3}  {chars:>6}  {chars_per_page:>7.1f}  {images:>3}  {method:<14} {vision:<5} {quality:<15}"


def main() -> None:
    """Profile one or all sample invoices."""
    project_root = Path(__file__).resolve().parents[1]

    # Determine which PDFs to profile
    if len(sys.argv) > 1:
        # Profile single PDF specified on command line
        pdf_paths = [sys.argv[1]]
        print(f"Profiling: {pdf_paths[0]}\n")
    else:
        # Profile all sample invoices
        sample_dir = project_root / "sample_invoices"
        pdf_paths = sorted(sample_dir.glob("*.pdf"))
        print(f"Profiling {len(pdf_paths)} sample invoices\n")

    # Profile each PDF
    results = []
    method_counts = defaultdict(int)

    print("Filename                         Pages Chars  Chars/Pg  Imgs  Extraction     Vision Quality")
    print("─" * 106)

    for pdf_path in pdf_paths:
        try:
            profile = profile_pdf(str(pdf_path))
            results.append(profile)

            # Count extraction methods
            method_counts[profile["extraction_method"]] += 1

            # Format and print row
            row = format_table_row(
                Path(pdf_path).name,
                profile["page_count"],
                profile["text_character_count"],
                profile["text_character_count_per_page"],
                profile["image_count"],
                profile["extraction_method"],
                "Yes" if profile["requires_vision"] else "No",
                profile["quality_status"],
            )
            print(row)

        except Exception as e:
            print(f"{str(pdf_path):<32} ERROR: {str(e)}")

    # Print summary
    print("─" * 106)
    print("\nSummary by Extraction Method:")
    print("-" * 40)
    for method, count in sorted(method_counts.items()):
        print(f"  {method:<20} {count:>3} invoice(s)")

    # Print vision requirement summary
    vision_required = sum(1 for r in results if r["requires_vision"])
    text_ready = sum(1 for r in results if r["extraction_method"] == "TEXT_READY")
    print(f"\nExtraction Readiness:")
    print(f"  TEXT_READY:        {text_ready:>3} invoice(s) - Ready for text extraction")
    print(f"  VISION_REQUIRED:   {vision_required:>3} invoice(s) - Require OCR/vision processing")

    if results:
        avg_chars = sum(r["text_character_count"] for r in results) / len(results)
        print(f"\nAverage Embedded Text: {avg_chars:.0f} characters per invoice")


if __name__ == "__main__":
    main()

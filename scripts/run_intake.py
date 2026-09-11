"""
Run the invoice intake workflow.

Default invoice: sample_invoices/01_Veson_Bunker_Clean_STP.pdf
Usage: python -m scripts.run_intake [path/to/invoice.pdf]
"""

import json
import sys
from pathlib import Path

from src.database import db
from src.intake_service import run_intake


def main() -> None:
    """Execute intake workflow and print formatted JSON output."""
    # Initialize database tables if needed
    db.create_tables_sync()

    # Determine invoice path
    if len(sys.argv) > 1:
        invoice_path = sys.argv[1]
    else:
        # Default to first sample invoice
        project_root = Path(__file__).resolve().parents[1]
        invoice_path = str(project_root / "sample_invoices" / "01_Veson_Bunker_Clean_STP.pdf")

    # Run intake workflow
    result = run_intake(invoice_path)

    # Print formatted JSON output
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

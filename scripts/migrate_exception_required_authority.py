"""
Migration script for Step 7B: Add required_authority column to exception_cases table.

This migration adds the missing required_authority column to the exception_cases table.
For backward compatibility, legacy records without required_authority will be marked with
a NULL value. When resolving such exceptions, the system will fail closed (BLOCKED) with
a clear message that required_authority is missing, forcing manual intervention.

This is a one-time schema migration only - not part of application service logic.
"""

import sqlite3
from pathlib import Path


def migrate_exception_required_authority():
    """Add required_authority column to exception_cases table."""
    db_path = Path(__file__).resolve().parents[1] / "data" / "finance_demo.db"

    if not db_path.exists():
        print(f"Database not found at {db_path}. Skipping migration.")
        return

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Check if required_authority column already exists
        cursor.execute("PRAGMA table_info(exception_cases);")
        columns = {row[1] for row in cursor.fetchall()}

        if "required_authority" in columns:
            print("Column 'required_authority' already exists in exception_cases table.")
            conn.close()
            return

        # Add required_authority column
        cursor.execute(
            "ALTER TABLE exception_cases ADD COLUMN required_authority TEXT"
        )
        conn.commit()

        # Verify the column was added
        cursor.execute("PRAGMA table_info(exception_cases);")
        columns = {row[1] for row in cursor.fetchall()}

        if "required_authority" in columns:
            print("Successfully added required_authority column to exception_cases table.")
            print(
                "Legacy records with NULL required_authority will fail closed during resolution."
            )
        else:
            print("ERROR: Failed to add required_authority column.")

        conn.close()

    except Exception as e:
        print(f"Migration error: {e}")
        raise


if __name__ == "__main__":
    migrate_exception_required_authority()

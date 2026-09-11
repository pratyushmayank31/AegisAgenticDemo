#!/usr/bin/env python3
"""
Unit tests for quality_check.py hook.

Tests cover:
- Valid Python files (Write and Edit)
- Non-Python files (Markdown, PDF, JSON)
- Python filenames with spaces
- Encoding declarations
- Various syntax errors
- Missing files
- Paths outside project directory
- Malformed inputs
"""

import unittest
import json
import tempfile
import os
import sys
from pathlib import Path
from io import StringIO

# Import the hook module
sys.path.insert(0, str(Path(__file__).parent))
import quality_check


class TestQualityCheckHook(unittest.TestCase):
    """Test cases for the quality_check PostToolUse hook."""

    def setUp(self):
        """Set up temporary directory for test files."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.temp_dir.name)
        # Set environment for hook
        os.environ["CLAUDE_PROJECT_DIR"] = str(self.project_dir)

    def tearDown(self):
        """Clean up temporary directory."""
        self.temp_dir.cleanup()
        # Check no __pycache__ was created
        for pycache_dir in self.project_dir.rglob("__pycache__"):
            # Should not reach here - fail if __pycache__ found
            self.fail(f"__pycache__ directory created: {pycache_dir}")

    def _create_python_file(self, name: str, content: str) -> Path:
        """Helper to create a Python file in temp directory."""
        file_path = self.project_dir / name
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)
        return file_path

    def _make_hook_payload(self, tool_name: str, file_path: str) -> dict:
        """Helper to create a PostToolUse hook JSON payload."""
        return {
            "hook_event_name": "PostToolUse",
            "tool_name": tool_name,
            "tool_input": {
                "file_path": str(file_path)
            },
            "tool_output": {},
            "cwd": str(self.project_dir)
        }

    # ========== ALLOWED CASES ==========

    def test_valid_python_write(self):
        """Valid Python file created through Write."""
        file_path = self._create_python_file(
            "test_valid.py",
            "def hello():\n    print('hello')\n"
        )
        payload = self._make_hook_payload("Write", str(file_path))
        result = quality_check.process_hook_input(payload)
        self.assertEqual(result, {}, f"Should allow valid Python, got: {result}")

    def test_valid_python_edit(self):
        """Valid Python file modified through Edit."""
        file_path = self._create_python_file(
            "test_edit.py",
            "x = 1\n"
        )
        payload = self._make_hook_payload("Edit", str(file_path))
        result = quality_check.process_hook_input(payload)
        self.assertEqual(result, {})

    def test_markdown_file(self):
        """Markdown files should be allowed (non-Python)."""
        file_path = self.project_dir / "README.md"
        file_path.write_text("# Title\nInvalid python: def ()\n")
        payload = self._make_hook_payload("Write", str(file_path))
        result = quality_check.process_hook_input(payload)
        self.assertEqual(result, {})

    def test_pdf_file(self):
        """PDF files should be allowed (non-Python)."""
        file_path = self.project_dir / "document.pdf"
        file_path.write_bytes(b"%PDF-1.4")
        payload = self._make_hook_payload("Write", str(file_path))
        result = quality_check.process_hook_input(payload)
        self.assertEqual(result, {})

    def test_json_file(self):
        """JSON files should be allowed (non-Python)."""
        file_path = self.project_dir / "data.json"
        file_path.write_text('{"invalid": "python syntax"}')
        payload = self._make_hook_payload("Write", str(file_path))
        result = quality_check.process_hook_input(payload)
        self.assertEqual(result, {})

    def test_python_filename_with_spaces(self):
        """Python filenames with spaces should be validated."""
        file_path = self._create_python_file(
            "my test file.py",
            "x = 1\ny = 2\n"
        )
        payload = self._make_hook_payload("Write", str(file_path))
        result = quality_check.process_hook_input(payload)
        self.assertEqual(result, {})

    def test_python_with_encoding_declaration(self):
        """Python file with encoding declaration should be parsed correctly."""
        file_path = self._create_python_file(
            "test_encoding.py",
            "# -*- coding: utf-8 -*-\nname = 'José'\nprint(name)\n"
        )
        payload = self._make_hook_payload("Write", str(file_path))
        result = quality_check.process_hook_input(payload)
        self.assertEqual(result, {})

    def test_non_write_edit_tool_ignored(self):
        """Tools other than Write/Edit should be ignored."""
        file_path = self._create_python_file("test.py", "invalid python (")
        payload = self._make_hook_payload("Read", str(file_path))
        payload["tool_name"] = "Read"
        result = quality_check.process_hook_input(payload)
        self.assertEqual(result, {}, "Non-Write/Edit tools should be ignored")

    def test_bash_tool_ignored(self):
        """Bash tool should be ignored."""
        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "invalid syntax )("},
            "cwd": str(self.project_dir)
        }
        result = quality_check.process_hook_input(payload)
        self.assertEqual(result, {})

    # ========== BLOCKED CASES ==========

    def test_syntax_error_write(self):
        """Syntax error in Python file from Write should be blocked."""
        file_path = self._create_python_file(
            "test_syntax_error.py",
            "def hello(\n"  # unclosed
        )
        payload = self._make_hook_payload("Write", str(file_path))
        result = quality_check.process_hook_input(payload)
        self.assertEqual(result["decision"], "block")
        self.assertIn("syntax error", result["reason"].lower())
        self.assertIn("test_syntax_error.py", result["reason"])

    def test_syntax_error_edit(self):
        """Syntax error in Python file from Edit should be blocked."""
        file_path = self._create_python_file(
            "test_edit_error.py",
            "x = 1\n"
        )
        # Edit with invalid syntax
        file_path.write_text("x = [1, 2, 3\n")  # unclosed bracket
        payload = self._make_hook_payload("Edit", str(file_path))
        result = quality_check.process_hook_input(payload)
        self.assertEqual(result["decision"], "block")
        self.assertIn("syntax error", result["reason"].lower())

    def test_unexpected_indentation(self):
        """Unexpected indentation should be blocked."""
        file_path = self._create_python_file(
            "test_indent.py",
            "def foo():\npass\n    x = 1\n"  # bad indentation
        )
        payload = self._make_hook_payload("Write", str(file_path))
        result = quality_check.process_hook_input(payload)
        self.assertEqual(result["decision"], "block")
        self.assertIn("syntax error", result["reason"].lower())

    def test_unclosed_bracket(self):
        """Unclosed bracket should be blocked."""
        file_path = self._create_python_file(
            "test_bracket.py",
            "my_list = [1, 2, 3\nprint(my_list)\n"
        )
        payload = self._make_hook_payload("Write", str(file_path))
        result = quality_check.process_hook_input(payload)
        self.assertEqual(result["decision"], "block")
        self.assertIn("syntax error", result["reason"].lower())

    def test_missing_python_file(self):
        """Missing Python file should be blocked."""
        file_path = self.project_dir / "nonexistent.py"
        payload = self._make_hook_payload("Write", str(file_path))
        result = quality_check.process_hook_input(payload)
        self.assertEqual(result["decision"], "block")
        self.assertIn("not found", result["reason"].lower())

    def test_path_outside_project_directory(self):
        """Python file outside project directory should be blocked."""
        with tempfile.TemporaryDirectory() as other_dir:
            other_file = Path(other_dir) / "outside.py"
            other_file.write_text("x = 1\n")
            payload = self._make_hook_payload("Write", str(other_file))
            result = quality_check.process_hook_input(payload)
            self.assertEqual(result["decision"], "block")
            self.assertIn("outside", result["reason"].lower())

    def test_malformed_json_stdin(self):
        """Malformed JSON should be handled safely."""
        # This would be caught at main() level, but process_hook_input
        # can receive already-parsed input
        result = quality_check.process_hook_input(None)
        self.assertEqual(result["decision"], "block")

    def test_missing_tool_input(self):
        """Missing tool_input field should be blocked."""
        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Write"
            # Missing tool_input
        }
        result = quality_check.process_hook_input(payload)
        self.assertEqual(result["decision"], "block")
        self.assertIn("missing", result["reason"].lower())

    def test_missing_file_path(self):
        """Missing file_path should be blocked."""
        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Write",
            "tool_input": {}
            # Missing file_path
        }
        result = quality_check.process_hook_input(payload)
        self.assertEqual(result["decision"], "block")
        self.assertIn("missing", result["reason"].lower())

    def test_no_pycache_created(self):
        """Verify no __pycache__ directories are created during validation."""
        file_path = self._create_python_file(
            "test_no_cache.py",
            "import sys\nx = 1\n"
        )
        payload = self._make_hook_payload("Write", str(file_path))
        quality_check.process_hook_input(payload)

        # Check for __pycache__ in project directory
        for pycache in self.project_dir.rglob("__pycache__"):
            self.fail(f"__pycache__ was created: {pycache}")

    # ========== FORMAT VALIDATION ==========

    def test_success_response_format(self):
        """Success response must be exactly {}."""
        file_path = self._create_python_file("test.py", "x = 1\n")
        payload = self._make_hook_payload("Write", str(file_path))
        result = quality_check.process_hook_input(payload)
        self.assertIsInstance(result, dict)
        self.assertEqual(len(result), 0)

    def test_failure_response_format(self):
        """Failure response must have decision and reason at top level."""
        file_path = self._create_python_file("test.py", "invalid (")
        payload = self._make_hook_payload("Write", str(file_path))
        result = quality_check.process_hook_input(payload)
        self.assertIn("decision", result)
        self.assertIn("reason", result)
        self.assertEqual(result["decision"], "block")
        self.assertIsInstance(result["reason"], str)
        # Should not have hookSpecificOutput
        self.assertNotIn("hookSpecificOutput", result)

    def test_reason_does_not_expose_source(self):
        """Failure reason should not include source code."""
        file_path = self._create_python_file(
            "test.py",
            "SECRET_KEY = 'my-secret-password-123'\ndef foo():\n"
        )
        payload = self._make_hook_payload("Write", str(file_path))
        result = quality_check.process_hook_input(payload)
        self.assertIn("decision", result)
        # Reason should not contain the secret
        self.assertNotIn("secret", result["reason"].lower())


class TestQualityCheckMainFunction(unittest.TestCase):
    """Test cases for the main() function's JSON handling."""

    def setUp(self):
        """Set up temporary directory."""
        self.temp_dir = tempfile.TemporaryDirectory()
        os.environ["CLAUDE_PROJECT_DIR"] = self.temp_dir.name

    def tearDown(self):
        """Clean up."""
        self.temp_dir.cleanup()

    def test_main_with_valid_json(self):
        """main() should accept valid JSON from stdin."""
        # Create a valid file first
        test_file = Path(self.temp_dir.name) / "test.py"
        test_file.write_text("x = 1\n")

        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": str(test_file)},
            "cwd": self.temp_dir.name
        }

        # Simulate stdin
        old_stdin = sys.stdin
        sys.stdin = StringIO(json.dumps(payload))

        # Capture stdout
        old_stdout = sys.stdout
        sys.stdout = StringIO()

        try:
            quality_check.main()
            output = sys.stdout.getvalue()
            result = json.loads(output)
            # Valid file should return empty dict
            self.assertEqual(result, {})
        finally:
            sys.stdin = old_stdin
            sys.stdout = old_stdout

    def test_main_with_malformed_json(self):
        """main() should handle malformed JSON safely."""
        # Malformed JSON input
        old_stdin = sys.stdin
        sys.stdin = StringIO("not valid json {")

        old_stdout = sys.stdout
        sys.stdout = StringIO()

        try:
            quality_check.main()
            output = sys.stdout.getvalue()
            result = json.loads(output)
            self.assertEqual(result["decision"], "block")
            self.assertIn("json", result["reason"].lower())
        finally:
            sys.stdin = old_stdin
            sys.stdout = old_stdout


if __name__ == "__main__":
    unittest.main()

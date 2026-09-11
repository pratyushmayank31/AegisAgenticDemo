#!/usr/bin/env python3
"""
Claude Code Python syntax quality hook (PostToolUse).

Validates Python syntax after Write and Edit operations.
Runs after the tool has executed (cannot roll back the edit).
Tells Claude to correct syntax errors before proceeding.
"""

import json
import sys
import ast
import os
import tokenize
from pathlib import Path
from io import StringIO


def get_project_dir() -> Path:
    """Get the Claude project directory from environment."""
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if not project_dir:
        return Path.cwd()
    return Path(project_dir)


def is_python_file(file_path: str) -> bool:
    """Check if the file is a Python source file."""
    return file_path.endswith(".py")


def is_inside_project(file_path: str, project_dir: Path) -> bool:
    """Check if file_path is inside the project directory."""
    try:
        resolved = Path(file_path).resolve()
        resolved_project = project_dir.resolve()
        # Check if file is under project directory
        return str(resolved).startswith(str(resolved_project))
    except (OSError, ValueError):
        return False


def validate_python_syntax(file_path: str) -> tuple[bool, str]:
    """
    Validate Python syntax by parsing with ast.parse().
    Uses tokenize.open() to respect encoding declarations.
    Returns (is_valid, error_message).
    """
    try:
        # Use tokenize.open() to respect Python encoding declarations
        with tokenize.open(file_path) as f:
            source_code = f.read()

        # Parse the source code with ast.parse()
        ast.parse(source_code, filename=file_path)
        return True, ""

    except FileNotFoundError:
        return False, f"Python file not found: {file_path}"

    except SyntaxError as e:
        # Extract line and column information safely
        line_num = e.lineno if e.lineno is not None else "unknown"
        col_num = e.offset if e.offset is not None else "unknown"
        msg = e.msg if e.msg else "syntax error"
        return False, f"line {line_num}, column {col_num}: {msg}"

    except UnicodeDecodeError as e:
        return False, f"encoding error: {e.reason}"

    except Exception as e:
        # Generic error - don't expose full exception details
        return False, f"parse error: unable to validate syntax"


def process_hook_input(hook_json: dict) -> dict:
    """
    Process a Claude Code PostToolUse hook input.
    Returns the hook response (block or allow).

    PostToolUse cannot roll back an edit; it tells Claude to correct it.
    """
    try:
        tool_name = hook_json.get("tool_name", "")
        tool_input = hook_json.get("tool_input", {})
        cwd = hook_json.get("cwd", "")

        # Accept only Write and Edit tools
        if tool_name not in ["Write", "Edit"]:
            return {}

        # Extract file_path
        file_path = tool_input.get("file_path", "")
        if not file_path:
            return {
                "decision": "block",
                "reason": f"{tool_name}: missing file_path in hook input"
            }

        # Return {} for non-Python files
        if not is_python_file(file_path):
            return {}

        # Get project directory
        project_dir = get_project_dir()

        # Check if path is inside project directory
        if not is_inside_project(file_path, project_dir):
            relative_path = file_path
            try:
                relative_path = str(Path(file_path).relative_to(project_dir))
            except ValueError:
                pass
            return {
                "decision": "block",
                "reason": f"Python file {relative_path} is outside the project directory"
            }

        # Validate Python syntax
        is_valid, error_msg = validate_python_syntax(file_path)
        if not is_valid:
            try:
                relative_path = str(Path(file_path).relative_to(project_dir))
            except ValueError:
                relative_path = file_path

            return {
                "decision": "block",
                "reason": f"Python syntax error in {relative_path} at {error_msg}"
            }

        # Valid Python - allow
        return {}

    except Exception:
        # Fail safely without exposing input
        return {
            "decision": "block",
            "reason": "Hook processing error: unable to validate tool input"
        }


def main():
    """Read hook JSON from stdin and output decision."""
    try:
        hook_input = json.load(sys.stdin)
        response = process_hook_input(hook_input)
        json.dump(response, sys.stdout)
    except json.JSONDecodeError:
        # Malformed JSON - block safely
        json.dump({
            "decision": "block",
            "reason": "Hook input was not valid JSON"
        }, sys.stdout)
    except Exception:
        # Any other error - block safely
        json.dump({
            "decision": "block",
            "reason": "Hook processing error: unable to validate tool input"
        }, sys.stdout)


if __name__ == "__main__":
    main()

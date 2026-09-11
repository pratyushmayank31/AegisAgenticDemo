#!/usr/bin/env python3
"""
Claude Code secret-protection hook.

Intercepts Read, Grep, Glob, Write, Edit, and Bash operations to prevent
accidental exposure of environment variables, API keys, and credential files.
"""

import json
import sys
import re
from pathlib import Path


# Patterns for protected files and credentials
PROTECTED_FILE_PATTERNS = [
    r"^\.env$",
    r"^\.env\..+",
    r"^secrets/",
    r"\.pem$",
    r"\.key$",
    r"\.p12$",
    r"\.pfx$",
]

# Safe template files that should be readable
SAFE_PATTERNS = [
    r"^\.env\.example$",
    r"^\.env\.template$",
]

# Secret variable names to protect in Bash and writes
SECRET_VAR_NAMES = [
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_ACCESS_KEY_ID",
    "DATABASE_PASSWORD",
    "JWT_SECRET",
    "PRIVATE_KEY",
]

# Bash commands that can leak secrets
DANGEROUS_READ_COMMANDS = [
    "cat",
    "head",
    "tail",
    "sed",
    "awk",
    "grep",
    "rg",
    "less",
    "more",
    "strings",
    "xxd",
    "base64",
]

# Known API key prefixes
SECRET_PREFIXES = [
    "sk-ant-",
    "sk-",
    "ghp_",
    "gho_",
    "ghu_",
    "ghs_",
    "ghr_",
]


def is_protected_file(path_str: str) -> bool:
    """Check if a file path matches protected patterns."""
    path_obj = Path(path_str)
    filename = path_obj.name

    # Check safe patterns first (match on filename)
    for pattern in SAFE_PATTERNS:
        if re.match(pattern, filename):
            return False

    # Check .env filename
    if filename == ".env":
        return True

    # Check .env.* patterns (deny except .env.example and .env.template)
    if filename.startswith(".env."):
        if filename not in [".env.example", ".env.template"]:
            return True

    # Check for "secrets" as a complete path component
    if "secrets" in path_obj.parts:
        return True

    # Check for credential file extensions
    if filename.endswith((".pem", ".key", ".p12", ".pfx")):
        return True

    return False


def check_bash_command(command: str) -> tuple[bool, str]:
    """
    Check if a Bash command could leak secrets.
    Returns (is_allowed, reason).
    """
    # Normalize whitespace
    cmd = command.strip()

    # Deny env/printenv output unconditionally
    if re.match(r"^\s*(env|printenv)(\s|$)", cmd):
        return False, "Bash: env/printenv is denied to prevent credential exposure"

    # Deny source/dot-loading of protected files (.env, .env.*, etc.)
    if re.search(r"^\s*(\.|source)\s+\.env(\s|$)", cmd):
        return False, "Bash: sourcing protected environment files is denied"

    # Check for dangerous read commands against protected files
    for cmd_name in DANGEROUS_READ_COMMANDS:
        # Match command like: cat .env, sed -e ... < .env, etc.
        if re.search(rf"\b{cmd_name}\b.*\.(env|pem|key|p12|pfx)", cmd):
            return False, f"Bash: {cmd_name} against protected files is denied"

    # Deny echo/printf of secret variables
    for var_name in SECRET_VAR_NAMES:
        if re.search(rf"(echo|printf).*\${var_name}|\${{{var_name}}}", cmd):
            return False, "Bash: echoing secret variables is denied"

    # Allow safe presence checks
    if re.search(r"test\s+-[nfz]\s+\$\w+", cmd):
        return True, ""
    if re.search(r"test\s+-f\s+\.", cmd):
        return True, ""
    if re.search(r"bool\(os\.getenv\(", cmd):
        return True, ""

    return True, ""


def check_write_edit_content(content: str) -> tuple[bool, str]:
    """
    Check if content being written/edited contains obvious credential assignments.
    Returns (is_allowed, reason).
    """
    lines = content.split("\n")

    for line in lines:
        # Skip comments
        if line.strip().startswith("#"):
            continue

        # Check for secret variable assignments
        for var_name in SECRET_VAR_NAMES:
            # Match: VAR_NAME=<value> with various quote styles
            pattern = rf"{var_name}\s*=\s*(.+?)(?:\s*$|[\n;#])"
            match = re.search(pattern, line)
            if match:
                value = match.group(1).strip().strip("\"'")
                # Allow placeholder values
                if value in ["", "<value>", "your-key-here", "placeholder", "REDACTED"]:
                    continue
                # Check for known secret prefixes
                for prefix in SECRET_PREFIXES:
                    if value.startswith(prefix):
                        return False, "Write/Edit: credential assignment detected"
                # Assume non-placeholder assignment is suspicious
                if len(value) > 8 and not value.startswith("${"):
                    return False, "Write/Edit: credential assignment detected"

    return True, ""


def deny_response(reason: str) -> dict:
    """Create a properly-structured Claude Code PreToolUse denial response."""
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason
        }
    }


def process_hook_input(hook_json: dict) -> dict:
    """
    Process a Claude Code PreToolUse hook input.
    Returns the hook response (deny or allow).
    """
    try:
        tool_name = hook_json.get("tool_name", "")
        tool_input = hook_json.get("tool_input", {})

        if not tool_name:
            return deny_response("Invalid hook input: missing tool_name")

        # Handle Read tool - check file_path
        if tool_name == "Read":
            file_path = tool_input.get("file_path", "")
            if not file_path:
                return deny_response(f"{tool_name}: missing file_path")

            if is_protected_file(file_path):
                return deny_response(
                    f"{tool_name}: access to protected environment or credential files is denied"
                )

        # Handle Grep, Glob tools - check path (not file_path)
        elif tool_name in ["Grep", "Glob"]:
            path = tool_input.get("path", "")
            if not path:
                return deny_response(f"{tool_name}: missing path")

            if is_protected_file(path):
                return deny_response(
                    f"{tool_name}: access to protected environment or credential files is denied"
                )

        # Handle Write tool - check file_path and content
        elif tool_name == "Write":
            file_path = tool_input.get("file_path", "")
            content = tool_input.get("content", "")

            if is_protected_file(file_path):
                return deny_response("Write: cannot write to protected environment or credential files")

            is_allowed, reason = check_write_edit_content(content)
            if not is_allowed:
                return deny_response(reason)

        # Handle Edit tool - check file_path and new_string
        elif tool_name == "Edit":
            file_path = tool_input.get("file_path", "")
            new_string = tool_input.get("new_string", "")

            if is_protected_file(file_path):
                return deny_response("Edit: cannot edit protected environment or credential files")

            is_allowed, reason = check_write_edit_content(new_string)
            if not is_allowed:
                return deny_response(reason)

        # Handle Bash tool
        elif tool_name == "Bash":
            command = tool_input.get("command", "")
            if not command:
                return deny_response("Bash: missing command")

            is_allowed, reason = check_bash_command(command)
            if not is_allowed:
                return deny_response(reason)

        # Allow by default
        return {}

    except Exception:
        # Fail safely without echoing input
        return deny_response("Hook processing error: unable to validate tool input")


def main():
    """Read hook JSON from stdin and output decision."""
    try:
        hook_input = json.load(sys.stdin)
        response = process_hook_input(hook_input)
        json.dump(response, sys.stdout)
    except json.JSONDecodeError:
        # Malformed JSON - deny safely with proper schema
        json.dump(deny_response("Hook input was not valid JSON"), sys.stdout)
    except Exception:
        # Any other error - deny safely with proper schema
        json.dump(
            deny_response("Hook processing error: unable to validate tool input"),
            sys.stdout
        )


if __name__ == "__main__":
    main()

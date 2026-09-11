#!/usr/bin/env python3
"""
Unit tests for the secret-protection hook.

Uses synthetic values only. Never tests with real credentials.
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path


class TestSecretProtectionHook(unittest.TestCase):
    """Test the protect_secrets.py hook."""

    @classmethod
    def setUpClass(cls):
        """Locate the hook script."""
        cls.hook_script = Path(__file__).parent / "protect_secrets.py"
        cls.python_exe = sys.executable

    def run_hook(self, hook_input: dict) -> dict:
        """
        Run the hook with given input and return parsed JSON output.
        """
        result = subprocess.run(
            [self.python_exe, str(self.hook_script)],
            input=json.dumps(hook_input).encode(),
            capture_output=True,
            text=False,
        )

        # Parse output as JSON
        try:
            return json.loads(result.stdout.decode())
        except json.JSONDecodeError:
            self.fail(f"Hook output was not valid JSON: {result.stdout.decode()}")

    def assert_allowed(self, hook_input: dict):
        """Assert that a hook input is allowed (returns empty dict)."""
        response = self.run_hook(hook_input)
        self.assertEqual(
            response,
            {},
            f"Expected empty dict (allow), got: {response}"
        )

    def assert_denied(self, hook_input: dict, reason_contains: str = None):
        """Assert that a hook input is denied with proper Claude Code schema."""
        response = self.run_hook(hook_input)

        # Verify nested structure
        self.assertIn("hookSpecificOutput", response,
                     f"Missing hookSpecificOutput: {response}")
        hook_output = response.get("hookSpecificOutput", {})

        self.assertEqual(
            hook_output.get("hookEventName"),
            "PreToolUse",
            f"Expected hookEventName=PreToolUse, got: {hook_output}"
        )
        self.assertEqual(
            hook_output.get("permissionDecision"),
            "deny",
            f"Expected permissionDecision=deny, got: {hook_output}"
        )
        self.assertIn(
            "permissionDecisionReason",
            hook_output,
            f"Missing permissionDecisionReason: {hook_output}"
        )

        reason = hook_output.get("permissionDecisionReason", "")
        if reason_contains:
            self.assertIn(
                reason_contains,
                reason,
                f"Reason does not contain '{reason_contains}': {reason}"
            )

    # ===== Allowed Operations =====

    def test_allowed_normal_python_command(self):
        """Allow normal Python command via Bash."""
        self.assert_allowed({
            "tool_name": "Bash",
            "tool_input": {
                "command": "python -m pytest tests/ -v"
            }
        })

    def test_allowed_pytest_command(self):
        """Allow pytest command."""
        self.assert_allowed({
            "tool_name": "Bash",
            "tool_input": {
                "command": "pytest src/ --cov"
            }
        })

    def test_allowed_test_presence_check(self):
        """Allow safe presence check with test -n."""
        self.assert_allowed({
            "tool_name": "Bash",
            "tool_input": {
                "command": "test -n \"$ANTHROPIC_API_KEY\" && echo 'present'"
            }
        })

    def test_allowed_test_file_check(self):
        """Allow safe file existence check with test -f."""
        self.assert_allowed({
            "tool_name": "Bash",
            "tool_input": {
                "command": "test -f .env && echo 'found'"
            }
        })

    def test_allowed_boolean_env_check(self):
        """Allow Python boolean environment check."""
        self.assert_allowed({
            "tool_name": "Bash",
            "tool_input": {
                "command": "python -c \"import os; print(bool(os.getenv('ANTHROPIC_API_KEY')))\""
            }
        })

    def test_allowed_read_env_example(self):
        """Allow reading .env.example template."""
        self.assert_allowed({
            "tool_name": "Read",
            "tool_input": {
                "file_path": "/Users/pmayank/workspace/LdcDemo/.env.example"
            }
        })

    def test_allowed_read_env_template(self):
        """Allow reading .env.template."""
        self.assert_allowed({
            "tool_name": "Read",
            "tool_input": {
                "file_path": "/Users/pmayank/workspace/LdcDemo/.env.template"
            }
        })

    def test_allowed_read_normal_file(self):
        """Allow reading normal source files."""
        self.assert_allowed({
            "tool_name": "Read",
            "tool_input": {
                "file_path": "/Users/pmayank/workspace/LdcDemo/src/database.py"
            }
        })

    def test_allowed_read_secrets_handler_file(self):
        """Allow reading legitimate filename with 'secrets' in the name (not a path component)."""
        self.assert_allowed({
            "tool_name": "Read",
            "tool_input": {
                "file_path": "/Users/pmayank/workspace/LdcDemo/src/my_secrets_handler.py"
            }
        })

    def test_allowed_read_secretary_file(self):
        """Allow reading legitimate filename with 'secret' substring."""
        self.assert_allowed({
            "tool_name": "Read",
            "tool_input": {
                "file_path": "/Users/pmayank/workspace/LdcDemo/src/secretary.py"
            }
        })

    def test_allowed_write_safe_content(self):
        """Allow writing safe content (no credentials)."""
        self.assert_allowed({
            "tool_name": "Write",
            "tool_input": {
                "file_path": "/Users/pmayank/workspace/LdcDemo/src/new_module.py",
                "content": "# Safe Python code\ndef hello():\n    return 'world'\n"
            }
        })

    def test_allowed_edit_safe_content(self):
        """Allow editing safe content."""
        self.assert_allowed({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": "/Users/pmayank/workspace/LdcDemo/src/module.py",
                "old_string": "def old():\n    pass",
                "new_string": "def new():\n    return True"
            }
        })

    def test_allowed_write_placeholder_env_assignment(self):
        """Allow writing placeholder environment assignments."""
        self.assert_allowed({
            "tool_name": "Write",
            "tool_input": {
                "file_path": "/Users/pmayank/workspace/LdcDemo/.env.example",
                "content": "ANTHROPIC_API_KEY=<value>\nOPENAI_API_KEY=placeholder\n"
            }
        })

    # ===== Denied Operations: .env Files =====

    def test_denied_read_env(self):
        """Deny reading .env (absolute path)."""
        self.assert_denied(
            {
                "tool_name": "Read",
                "tool_input": {
                    "file_path": "/Users/pmayank/workspace/LdcDemo/.env"
                }
            },
            reason_contains="protected"
        )

    def test_denied_read_env_production(self):
        """Deny reading .env.production."""
        self.assert_denied(
            {
                "tool_name": "Read",
                "tool_input": {
                    "file_path": ".env.production"
                }
            },
            reason_contains="protected"
        )

    def test_denied_read_env_local(self):
        """Deny reading .env.local."""
        self.assert_denied(
            {
                "tool_name": "Read",
                "tool_input": {
                    "file_path": ".env.local"
                }
            },
            reason_contains="protected"
        )

    def test_denied_grep_env(self):
        """Deny grepping .env file."""
        self.assert_denied(
            {
                "tool_name": "Grep",
                "tool_input": {
                    "path": ".env",
                    "pattern": "API_KEY"
                }
            },
            reason_contains="protected"
        )

    # ===== Denied Operations: Bash Commands =====

    def test_denied_bash_cat_env(self):
        """Deny cat command on .env."""
        self.assert_denied(
            {
                "tool_name": "Bash",
                "tool_input": {
                    "command": "cat .env"
                }
            },
            reason_contains="denied"
        )

    def test_denied_bash_sed_env(self):
        """Deny sed command against .env."""
        self.assert_denied(
            {
                "tool_name": "Bash",
                "tool_input": {
                    "command": "sed -e 's/x/y/' .env"
                }
            },
            reason_contains="denied"
        )

    def test_denied_bash_source_env(self):
        """Deny sourcing .env file."""
        self.assert_denied(
            {
                "tool_name": "Bash",
                "tool_input": {
                    "command": "source .env"
                }
            },
            reason_contains="denied"
        )

    def test_denied_bash_dot_load_env(self):
        """Deny dot-loading .env file."""
        self.assert_denied(
            {
                "tool_name": "Bash",
                "tool_input": {
                    "command": ". .env"
                }
            },
            reason_contains="denied"
        )

    def test_denied_bash_printenv(self):
        """Deny printenv command."""
        self.assert_denied(
            {
                "tool_name": "Bash",
                "tool_input": {
                    "command": "printenv | grep API"
                }
            },
            reason_contains="env/printenv"
        )

    def test_denied_bash_env(self):
        """Deny env command."""
        self.assert_denied(
            {
                "tool_name": "Bash",
                "tool_input": {
                    "command": "env"
                }
            },
            reason_contains="env/printenv"
        )

    def test_denied_bash_echo_secret_var(self):
        """Deny echo of secret variable."""
        self.assert_denied(
            {
                "tool_name": "Bash",
                "tool_input": {
                    "command": "echo \"$ANTHROPIC_API_KEY\""
                }
            },
            reason_contains="secret"
        )

    # ===== Denied Operations: Write/Edit Credentials =====

    def test_denied_write_anthropic_key(self):
        """Deny writing ANTHROPIC_API_KEY assignment with synthetic key."""
        self.assert_denied(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": "/Users/pmayank/workspace/LdcDemo/config.py",
                    "content": "ANTHROPIC_API_KEY=sk-ant-1234567890abcdef\n"
                }
            },
            reason_contains="credential"
        )

    def test_denied_write_openai_key(self):
        """Deny writing OPENAI_API_KEY assignment."""
        self.assert_denied(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": "/Users/pmayank/workspace/LdcDemo/config.py",
                    "content": "OPENAI_API_KEY=sk-test-1234567890abcdef\n"
                }
            },
            reason_contains="credential"
        )

    def test_denied_edit_api_key_assignment(self):
        """Deny editing to add API key assignment."""
        self.assert_denied(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": "/Users/pmayank/workspace/LdcDemo/src/config.py",
                    "old_string": "API_KEY = None",
                    "new_string": "ANTHROPIC_API_KEY=sk-ant-test123456789"
                }
            },
            reason_contains="credential"
        )

    # ===== Denied Operations: Protected Directories =====

    def test_denied_read_secrets_directory(self):
        """Deny reading from secrets/ directory."""
        self.assert_denied(
            {
                "tool_name": "Read",
                "tool_input": {
                    "file_path": "secrets/credentials.json"
                }
            },
            reason_contains="protected"
        )

    def test_denied_read_secrets_directory_absolute_path_regression(self):
        """Regression test: exact observed failing payload with absolute path to secrets/."""
        self.assert_denied(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "Read",
                "tool_input": {
                    "file_path": "/Users/pmayank/workspace/LdcDemo/secrets/hook-test.txt"
                },
                "cwd": "/Users/pmayank/workspace/LdcDemo"
            },
            reason_contains="protected"
        )

    def test_denied_write_secrets_directory_absolute_path(self):
        """Deny writing to secrets/ directory with absolute path."""
        self.assert_denied(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": "/Users/pmayank/workspace/LdcDemo/secrets/output.txt",
                    "content": "test data"
                }
            },
            reason_contains="protected"
        )

    def test_denied_edit_secrets_directory_absolute_path(self):
        """Deny editing files in secrets/ directory with absolute path."""
        self.assert_denied(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": "/Users/pmayank/workspace/LdcDemo/secrets/hook-test.txt",
                    "old_string": "old content",
                    "new_string": "new content"
                }
            },
            reason_contains="protected"
        )

    # ===== Denied Operations: Certificate/Key Files =====

    def test_denied_read_pem_file(self):
        """Deny reading .pem certificate files."""
        self.assert_denied(
            {
                "tool_name": "Read",
                "tool_input": {
                    "file_path": "/Users/pmayank/workspace/LdcDemo/certs/private.pem"
                }
            },
            reason_contains="protected"
        )

    def test_denied_read_key_file(self):
        """Deny reading .key files."""
        self.assert_denied(
            {
                "tool_name": "Read",
                "tool_input": {
                    "file_path": "/Users/pmayank/workspace/LdcDemo/keys/rsa.key"
                }
            },
            reason_contains="protected"
        )

    def test_denied_edit_credentials_private_key(self):
        """Deny editing .key files in credentials/ directory (absolute path)."""
        self.assert_denied(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": "/Users/pmayank/workspace/LdcDemo/credentials/private.key",
                    "old_string": "old key",
                    "new_string": "new key"
                }
            },
            reason_contains="protected"
        )

    def test_denied_read_p12_file(self):
        """Deny reading .p12 certificate files."""
        self.assert_denied(
            {
                "tool_name": "Read",
                "tool_input": {
                    "file_path": "/Users/pmayank/workspace/LdcDemo/certs/cert.p12"
                }
            },
            reason_contains="protected"
        )

    def test_denied_read_pfx_file(self):
        """Deny reading .pfx certificate files."""
        self.assert_denied(
            {
                "tool_name": "Read",
                "tool_input": {
                    "file_path": "/Users/pmayank/workspace/LdcDemo/certs/cert.pfx"
                }
            },
            reason_contains="protected"
        )

    # ===== Malformed Input =====

    def test_denied_malformed_json(self):
        """Deny malformed JSON input with proper schema."""
        result = subprocess.run(
            [self.python_exe, str(self.hook_script)],
            input=b"not valid json {",
            capture_output=True,
            text=False,
        )
        response = json.loads(result.stdout.decode())
        self.assertIn("hookSpecificOutput", response)
        hook_output = response.get("hookSpecificOutput", {})
        self.assertEqual(hook_output.get("permissionDecision"), "deny")
        reason = hook_output.get("permissionDecisionReason", "")
        self.assertNotIn("not valid json", reason)

    def test_denied_missing_tool_name(self):
        """Deny input missing tool_name."""
        self.assert_denied(
            {
                "tool_input": {"file_path": "test.txt"}
            },
            reason_contains="tool_name"
        )

    def test_denied_missing_file_path_read(self):
        """Deny Read without file_path."""
        self.assert_denied(
            {
                "tool_name": "Read",
                "tool_input": {}
            },
            reason_contains="file_path"
        )

    def test_denied_missing_command_bash(self):
        """Deny Bash without command."""
        self.assert_denied(
            {
                "tool_name": "Bash",
                "tool_input": {}
            },
            reason_contains="command"
        )

    # ===== JSON Output Validation =====

    def test_json_output_format_allow(self):
        """Verify allow response is valid JSON."""
        response = self.run_hook({
            "tool_name": "Bash",
            "tool_input": {"command": "echo test"}
        })
        # Empty dict is valid allow response
        self.assertIsInstance(response, dict)

    def test_json_output_format_deny(self):
        """Verify deny response is valid JSON with nested structure."""
        response = self.run_hook({
            "tool_name": "Read",
            "tool_input": {"file_path": ".env"}
        })
        self.assertIsInstance(response, dict)
        self.assertIn("hookSpecificOutput", response)
        hook_output = response.get("hookSpecificOutput", {})
        self.assertIn("permissionDecision", hook_output)
        self.assertIn("permissionDecisionReason", hook_output)

    def test_deny_never_echoes_input(self):
        """Verify deny reasons never echo suspected secrets."""
        test_secret = "sk-ant-synthetic-test-key-1234567890"
        response = self.run_hook({
            "tool_name": "Write",
            "tool_input": {
                "file_path": "/Users/pmayank/workspace/LdcDemo/config.py",
                "content": f"ANTHROPIC_API_KEY={test_secret}\n"
            }
        })
        hook_output = response.get("hookSpecificOutput", {})
        reason = hook_output.get("permissionDecisionReason", "")
        self.assertNotIn(test_secret, reason)
        self.assertNotIn("sk-ant-", reason)

    # ===== Claude Code Schema Validation =====

    def test_deny_uses_nested_schema_not_deprecated_toplevel(self):
        """Verify denial uses nested hookSpecificOutput, not deprecated top-level fields."""
        response = self.run_hook({
            "tool_name": "Read",
            "tool_input": {"file_path": ".env"}
        })

        # Must have nested structure
        self.assertIn("hookSpecificOutput", response,
                     "Denial must use hookSpecificOutput (not top-level permissionDecision)")

        # Must NOT have deprecated top-level fields
        self.assertNotIn("permissionDecision", response,
                        "Denial must not have top-level permissionDecision field")
        self.assertNotIn("reason", response,
                        "Denial must not have top-level reason field")

        # Nested structure must be complete
        hook_output = response["hookSpecificOutput"]
        self.assertEqual(hook_output.get("hookEventName"), "PreToolUse")
        self.assertEqual(hook_output.get("permissionDecision"), "deny")
        self.assertIn("permissionDecisionReason", hook_output)


if __name__ == "__main__":
    unittest.main(verbosity=2)

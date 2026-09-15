"""Inspect Aegis workspaces and available LLM models safely."""

from __future__ import annotations

import asyncio
import getpass
import json
from pathlib import Path as FileSystemPath
from typing import Any

from aegis_sdk import AgenticOSClient, AuthenticationError
from dotenv import dotenv_values


DEFAULT_BASE_URL = "https://deloitte.aegisdelegate.com"

PROJECT_ROOT = FileSystemPath(__file__).resolve().parents[1]
AEGIS_ENV_FILE = PROJECT_ROOT / ".env.aegis"

SENSITIVE_TERMS = {
    "secret",
    "password",
    "token",
    "api_key",
    "apikey",
    "credential",
}


def convert_to_data(value: Any) -> Any:
    """Convert SDK/Pydantic responses into printable data."""

    if hasattr(value, "model_dump"):
        return value.model_dump()

    if isinstance(value, list):
        return [convert_to_data(item) for item in value]

    if isinstance(value, tuple):
        return [convert_to_data(item) for item in value]

    if isinstance(value, dict):
        return {
            key: convert_to_data(item)
            for key, item in value.items()
        }

    return value


def redact(value: Any) -> Any:
    """Redact fields that may contain credentials."""

    if isinstance(value, list):
        return [redact(item) for item in value]

    if isinstance(value, dict):
        sanitized = {}

        for key, item in value.items():
            normalized_key = str(key).lower()

            if any(
                term in normalized_key
                for term in SENSITIVE_TERMS
            ):
                sanitized[key] = "<REDACTED>"
            else:
                sanitized[key] = redact(item)

        return sanitized

    return value


def display(title: str, value: Any) -> None:
    safe_value = redact(convert_to_data(value))

    print(f"\n{title}")
    print(json.dumps(safe_value, indent=2, default=str))


async def inspect_runtime_options(
    *,
    email: str,
    password: str,
) -> None:
    config = dotenv_values(AEGIS_ENV_FILE)

    base_url = str(
        config.get("AEGIS_BASE_URL") or DEFAULT_BASE_URL
    ).strip().rstrip("/")

    async with AgenticOSClient(
        base_url=base_url,
        timeout=30.0,
        max_retries=3,
        debug=False,
    ) as client:
        token = await client.auth.login(
            email=email,
            password=password,
        )
        client.set_auth_token(token.access_token)

        print("\nAUTHENTICATION SUCCESSFUL")
        print("Logged in as:", token.user.email)

        workspaces = await client.workspaces.list(
            include_archived=False
        )
        display("AVAILABLE WORKSPACES", workspaces)

        providers = await client.llm_providers.list_providers()
        display("AVAILABLE PROVIDERS", providers)

        models = await client.llm_providers.get_all_models(
            include_unavailable=False
        )
        display("AVAILABLE MODELS", models)

        for provider_name in (
            "anthropic",
            "azure",
            "ollama",
        ):
            try:
                provider_models = (
                    await client.llm_providers.list_models(
                        provider_name
                    )
                )

                display(
                    f"{provider_name.upper()} MODELS",
                    provider_models,
                )

            except Exception as exc:
                print(
                    f"\n{provider_name.upper()} MODEL LIST FAILED"
                )
                print("Error type:", type(exc).__name__)
                print("Error:", exc)

            try:
                health = (
                    await client.llm_providers
                    .check_provider_health(provider_name)
                )

                display(
                    f"{provider_name.upper()} HEALTH",
                    health,
                )

            except Exception as exc:
                print(
                    f"\n{provider_name.upper()} HEALTH CHECK FAILED"
                )
                print("Error type:", type(exc).__name__)
                print("Error:", exc)

        accessible_agents = (
            await client.agents.list_accessible()
        )
        display(
            "ACCESSIBLE AGENTS",
            accessible_agents,
        )

        finance_unit_id = str(
            config.get("AEGIS_ORGANIZATION_UNIT_ID") or ""
        ).strip()

        if finance_unit_id:
            unit_agents = (
                await client.agents
                .list_delegate_agents_for_unit(
                    finance_unit_id
                )
            )
            display(
                "FINANCE UNIT DELEGATE AGENTS",
                unit_agents,
            )


def main() -> None:
    email = input("Aegis email: ").strip()
    password = getpass.getpass("Aegis password: ")

    if not email or not password:
        raise SystemExit(
            "Aegis email and password are required."
        )

    try:
        asyncio.run(
            inspect_runtime_options(
                email=email,
                password=password,
            )
        )

    except AuthenticationError as exc:
        print("\nAUTHENTICATION FAILED")
        print("Error:", exc)
        raise SystemExit(1)

    except Exception as exc:
        print("\nRUNTIME INSPECTION FAILED")
        print("Error type:", type(exc).__name__)
        print("Error:", exc)
        raise SystemExit(1)

if __name__ == "__main__":
    main()
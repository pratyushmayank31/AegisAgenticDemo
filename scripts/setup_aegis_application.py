"""Register or verify the LdcDemo application in Aegis."""

from __future__ import annotations

import asyncio
import getpass
import json
from pathlib import Path as FileSystemPath
from typing import Any

from aegis_sdk import (
    AgenticOSClient,
    AuthenticationError,
)
from dotenv import dotenv_values


DEFAULT_BASE_URL = "https://deloitte.aegisdelegate.com"
APPLICATION_NAME = "LdcDemo Finance Invoice Governance"

PROJECT_ROOT = FileSystemPath(__file__).resolve().parents[1]
AEGIS_ENV_FILE = PROJECT_ROOT / ".env.aegis"


def get_value(item: Any, field: str) -> Any:
    """Read a field from either a dictionary or SDK model."""

    if isinstance(item, dict):
        return item.get(field)

    return getattr(item, field, None)


def require_configuration(
    config: dict[str, object],
    name: str,
) -> str:
    value = str(config.get(name) or "").strip()

    if not value:
        raise RuntimeError(
            f"{name} is missing from {AEGIS_ENV_FILE}"
        )

    return value


def extract_applications(result: Any) -> list[Any]:
    """Normalize the SDK application-list response."""

    if isinstance(result, list):
        return result

    if isinstance(result, dict):
        for field in ("records", "applications", "items", "results"):
            value = result.get(field)

            if isinstance(value, list):
                return value

    for field in ("records", "applications", "items", "results"):
        value = getattr(result, field, None)

        if isinstance(value, list):
            return value

    raise RuntimeError(
        "Could not identify applications in the SDK list response."
    )


async def register_application(
    *,
    email: str,
    password: str,
) -> Any:
    config = dotenv_values(AEGIS_ENV_FILE)

    base_url = str(
        config.get("AEGIS_BASE_URL") or DEFAULT_BASE_URL
    ).strip().rstrip("/")

    owner_role_id = require_configuration(
        config,
        "AEGIS_OWNER_ROLE_ID",
    )

    configured_application_id = str(
        config.get("AEGIS_APPLICATION_ID") or ""
    ).strip()

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
        print("Organization:", token.user.organization_id)

        # Preferred path after the application ID has been saved.
        if configured_application_id:
            application = await client.applications.get(
                configured_application_id
            )

            print("\nAPPLICATION VERIFIED")
            display_application(application)

            return application

        # Duplicate check before first-time creation.
        response = await client.applications.list(
            limit=100,
            offset=0,
        )
        applications = extract_applications(response)

        existing = next(
            (
                application
                for application in applications
                if get_value(application, "name")
                == APPLICATION_NAME
            ),
            None,
        )

        if existing is not None:
            print("\nEXISTING APPLICATION FOUND")
            display_application(existing)

            return existing

        application = await client.applications.create(
            name=APPLICATION_NAME,
            owner_role_id=owner_role_id,
            description=(
                "Governed agentic finance application for invoice "
                "intake, document intelligence, routing decisions, "
                "human approval, exception handling, and audit."
            ),
            default_posture_ceiling="supervised",
            budget_monthly="100.00",
            data_scope_json=json.dumps(
                [
                    "invoice_documents",
                    "invoice_extracted_fields",
                    "finance_decisions",
                    "approval_requests",
                    "exception_cases",
                    "posting_records",
                    "business_audit_events",
                ]
            ),
            allowed_models_json=json.dumps(
                [
                    "claude-haiku-4-5",
                ]
            ),
        )

        print("\nAPPLICATION CREATED")
        display_application(application)

        return application


def display_application(application: Any) -> None:
    print("Application ID:", get_value(application, "id"))
    print("Application name:", get_value(application, "name"))
    print("Status:", get_value(application, "status"))
    print(
        "Posture ceiling:",
        get_value(application, "default_posture_ceiling"),
    )
    print(
        "Monthly budget:",
        get_value(application, "budget_monthly"),
    )
    print(
        "Owner role ID:",
        get_value(application, "owner_role_id"),
    )


def main() -> None:
    email = input("Aegis email: ").strip()
    password = getpass.getpass("Aegis password: ")

    if not email or not password:
        raise SystemExit(
            "Aegis email and password are required."
        )

    try:
        application = asyncio.run(
            register_application(
                email=email,
                password=password,
            )
        )

        application_id = get_value(application, "id")

        if not application_id:
            raise RuntimeError(
                "Aegis did not return an application ID."
            )

        print("\nAPPLICATION SETUP COMPLETED")
        print("Save this in .env.aegis:")
        print(f"AEGIS_APPLICATION_ID={application_id}")

    except AuthenticationError as exc:
        print("\nAEGIS AUTHENTICATION FAILED")
        print("Error:", exc)
        raise SystemExit(1)

    except Exception as exc:
        print("\nAPPLICATION SETUP FAILED")
        print("Error type:", type(exc).__name__)
        print("Error:", exc)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
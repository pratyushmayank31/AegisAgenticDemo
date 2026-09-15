"""Explicitly activate the registered LdcDemo Aegis application."""

from __future__ import annotations

import argparse
import asyncio
import getpass
from pathlib import Path as FileSystemPath
from typing import Any

from aegis_sdk import (
    AgenticOSClient,
    AuthenticationError,
    AuthorizationError,
)
from dotenv import dotenv_values


DEFAULT_BASE_URL = "https://deloitte.aegisdelegate.com"

PROJECT_ROOT = FileSystemPath(__file__).resolve().parents[1]
AEGIS_ENV_FILE = PROJECT_ROOT / ".env.aegis"


def get_value(item: Any, field: str) -> Any:
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


async def activate_application(
    *,
    email: str,
    password: str,
    activation_authorized: bool,
) -> Any:
    if not activation_authorized:
        raise RuntimeError(
            "Activation requires the explicit --activate flag."
        )

    config = dotenv_values(AEGIS_ENV_FILE)

    base_url = str(
        config.get("AEGIS_BASE_URL") or DEFAULT_BASE_URL
    ).strip().rstrip("/")

    application_id = require_configuration(
        config,
        "AEGIS_APPLICATION_ID",
    )

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

        application = await client.applications.get(
            application_id
        )
        current_status = get_value(application, "status")

        print("\nCURRENT APPLICATION")
        print("Application ID:", application_id)
        print("Name:", get_value(application, "name"))
        print("Status:", current_status)

        if current_status == "active":
            print("\nAPPLICATION ALREADY ACTIVE")
            return application

        if current_status != "pending_approval":
            raise RuntimeError(
                "Expected status pending_approval, but found "
                f"{current_status!r}."
            )

        application = await client.applications.change_status(
            application_id,
            "active",
        )

        print("\nAPPLICATION ACTIVATED")
        print("Application ID:", get_value(application, "id"))
        print("Name:", get_value(application, "name"))
        print("Status:", get_value(application, "status"))

        return application


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Activate the registered LdcDemo application."
    )
    parser.add_argument(
        "--activate",
        action="store_true",
        help="Explicitly approve application activation.",
    )
    args = parser.parse_args()

    email = input("Aegis email: ").strip()
    password = getpass.getpass("Aegis password: ")

    try:
        asyncio.run(
            activate_application(
                email=email,
                password=password,
                activation_authorized=args.activate,
            )
        )

    except AuthenticationError as exc:
        print("\nAUTHENTICATION FAILED")
        print("Error:", exc)
        raise SystemExit(1)

    except AuthorizationError as exc:
        print("\nACTIVATION NOT AUTHORIZED")
        print("Error:", exc)
        print(
            "A separate Aegis approver must activate "
            "this application."
        )
        raise SystemExit(1)

    except Exception as exc:
        print("\nACTIVATION FAILED")
        print("Error type:", type(exc).__name__)
        print("Error:", exc)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
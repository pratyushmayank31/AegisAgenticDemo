"""Create the Aegis owner role for the LdcDemo application."""

from __future__ import annotations

import asyncio
import getpass
from pathlib import Path as FileSystemPath

from aegis_sdk import (
    AgenticOSClient,
    AuthenticationError,
)
from dotenv import dotenv_values


DEFAULT_BASE_URL = "https://deloitte.aegisdelegate.com"

PROJECT_ROOT = FileSystemPath(__file__).resolve().parents[1]
AEGIS_ENV_FILE = PROJECT_ROOT / ".env.aegis"


def require_configuration(
    config: dict[str, object],
    name: str,
) -> str:
    """Retrieve a required configuration value."""

    value = str(config.get(name) or "").strip()

    if not value:
        raise RuntimeError(
            f"{name} is missing from {AEGIS_ENV_FILE}"
        )

    return value


async def create_owner_role(
    *,
    email: str,
    password: str,
) -> dict[str, object]:
    """Authenticate and create the Finance application-owner role."""

    config = dotenv_values(AEGIS_ENV_FILE)

    base_url = str(
        config.get("AEGIS_BASE_URL") or DEFAULT_BASE_URL
    ).strip().rstrip("/")

    organization_unit_id = require_configuration(
        config,
        "AEGIS_ORGANIZATION_UNIT_ID",
    )
    existing_role_id = str(
        config.get("AEGIS_OWNER_ROLE_ID") or ""
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
        if existing_role_id:
            role = await client.roles.get(existing_role_id)

            if role.get("id") != existing_role_id:
                raise RuntimeError(
                    "Aegis returned an unexpected role ID."
                )

            role_unit_id = role.get("organization_unit_id")

            if (
                role_unit_id
                and role_unit_id != organization_unit_id
            ):
                raise RuntimeError(
                    "The owner role belongs to an unexpected "
                    "organizational unit."
                )

            print("\nOWNER ROLE VERIFIED")
            print("Role ID:", role.get("id"))
            print("Role title:", role.get("title"))
            print(
                "Authority level:",
                role.get("authority_level"),
            )
            print(
                "Primary for unit:",
                role.get("is_primary_for_unit"),
            )

            return role

        role = await client.roles.create(
            organization_unit_id=organization_unit_id,
            title="LdcDemo Finance Application Owner",
            authority_level=4,
            description=(
                "Owns the governed LdcDemo finance invoice-processing "
                "application and provides operational oversight."
            ),
            responsibilities=[
                "Own the LdcDemo finance application",
                "Oversee invoice-processing governance",
                "Review escalated finance exceptions",
                "Monitor controls and audit outcomes",
            ],
            auto_generate_agent=False,
            is_primary_for_unit=True,
            metadata={
                "application": "LdcDemo",
                "domain": "finance-operations",
                "purpose": "application-ownership",
            },
            tags=[
                "ldcdemo",
                "finance",
                "application-owner",
            ],
        )

        print("\nOWNER ROLE CREATED")
        print("Role ID:", role.get("id"))
        print("Role title:", role.get("title"))
        print("Authority level:", role.get("authority_level"))
        print(
            "Primary for unit:",
            role.get("is_primary_for_unit"),
        )

        return role


def main() -> None:
    """Collect credentials and create the owner role."""

    email = input("Aegis email: ").strip()
    password = getpass.getpass("Aegis password: ")

    if not email:
        raise RuntimeError("Aegis email is required.")

    if not password:
        raise RuntimeError("Aegis password is required.")

    try:
        asyncio.run(
            create_owner_role(
                email=email,
                password=password,
            )
        )

    except AuthenticationError as exc:
        print("\nAUTHENTICATION FAILED")
        print("Error:", exc)
        raise SystemExit(1)

    except KeyboardInterrupt:
        print("\nOperation cancelled.")
        raise SystemExit(130)

    except Exception as exc:
        print("\nROLE CREATION FAILED")
        print("Error type:", type(exc).__name__)
        print("Error:", exc)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
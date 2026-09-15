"""Connect to Aegis and verify or create an organization."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import inspect
import os
import re
from pathlib import Path
from typing import Any
from dotenv import load_dotenv


from aegis_sdk import AgenticOSClient
from aegis_sdk import (
    AgenticOSClient,
    AuthenticationError,
    AuthToken,
)
from dotenv import load_dotenv




DEFAULT_BASE_URL = "https://deloitte.aegisdelegate.com"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
AEGIS_ENV_FILE = PROJECT_ROOT / ".env.aegis"

load_dotenv(AEGIS_ENV_FILE)

def require_environment_variable(name: str) -> str:
    """Return a required environment variable without exposing its value."""
    value = os.getenv(name, "").strip()

    if not value:
        raise RuntimeError(
            f"Required environment variable {name!r} is not configured."
        )

    return value


def normalise_base_url(base_url: str) -> str:
    """Ensure the SDK base URL does not contain /api/v1."""
    cleaned_url = base_url.strip().rstrip("/")

    if cleaned_url.endswith("/api/v1"):
        cleaned_url = cleaned_url.removesuffix("/api/v1")

    return cleaned_url


def validate_slug(slug: str) -> str:
    """Validate an organization slug before sending it to Aegis."""
    cleaned_slug = slug.strip().lower()

    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", cleaned_slug):
        raise ValueError(
            "Organization slug may contain lowercase letters, numbers, "
            "and single hyphens only."
        )

    return cleaned_slug


def create_authenticated_client(
    access_token: str,
) -> AgenticOSClient:
    """Create an Aegis client using a runtime access token."""
    if not access_token or not access_token.strip():
        raise ValueError("A valid runtime access token is required.")

    base_url = normalise_base_url(
        os.getenv("AEGIS_BASE_URL", DEFAULT_BASE_URL)
    )

    client = AgenticOSClient(
        base_url=base_url,
        timeout=30.0,
        max_retries=3,
        debug=False,
    )

    client.set_auth_token(access_token.strip())

    return client


async def close_client(client: AgenticOSClient) -> None:
    """Close the SDK client whether close() is synchronous or asynchronous."""
    close_result = client.close()

    if inspect.isawaitable(close_result):
        await close_result


async def get_organization(
    client: AgenticOSClient,
    organization_id: str,
) -> dict[str, Any]:
    """Retrieve and verify an existing Aegis organization."""
    organization = await client.organizations.get(organization_id)

    if organization.get("id") != organization_id:
        raise RuntimeError(
            "Aegis returned an unexpected organization identifier."
        )

    return organization


async def create_organization(
    client: AgenticOSClient,
    *,
    name: str,
    slug: str,
    plan_tier: str,
    allow_creation: bool,
) -> dict[str, Any]:
    """
    Create an organization only when creation is explicitly authorized.

    This guard reduces the risk of accidentally creating duplicate tenants.
    """
    if not allow_creation:
        raise RuntimeError(
            "Organization creation is disabled. "
            "Pass --create only when a new tenant is genuinely required."
        )

    return await client.organizations.create(
        name=name.strip(),
        slug=validate_slug(slug),
        plan_tier=plan_tier,
    )


def display_organization(
    organization: dict[str, Any],
    *,
    action: str,
) -> None:
    """Display non-sensitive organization information."""
    print(f"\nORGANIZATION {action}")
    print("Organization ID:", organization.get("id"))
    print("Organization name:", organization.get("name"))
    print("Organization slug:", organization.get("slug"))
    print("Plan tier:", organization.get("plan_tier"))


async def get_or_create_finance_unit(
    client: AgenticOSClient,
    *,
    organization_unit_id: str | None = None,
    allow_creation: bool = False,
) -> dict[str, Any]:
    """
    Retrieve an existing unit or create the LdcDemo Finance unit.

    If organization_unit_id is provided, the existing unit is retrieved.
    Otherwise, creation must be explicitly enabled.
    """

    if organization_unit_id:
        unit = await client.units.get(organization_unit_id)

        print("\nORGANIZATIONAL UNIT VERIFIED")
        print("Unit ID:", unit.get("id"))
        print("Unit name:", unit.get("name"))
        print("Unit type:", unit.get("unit_type"))

        return unit

    if not allow_creation:
        raise RuntimeError(
            "No organization-unit ID was provided. "
            "Enable allow_creation only for the first setup."
        )

    unit = await client.units.create(
        name="LdcDemo Finance Operations",
        unit_type="department",
        description=(
            "Finance function responsible for governed invoice "
            "processing and orchestration."
        ),
        responsibilities=[
            "Invoice processing",
            "Financial governance",
            "Exception management",
            "Approval oversight",
        ],
        default_classification="confidential",
        tags=[
            "ldcdemo",
            "finance",
            "invoice-processing",
        ],
    )

    print("\nORGANIZATIONAL UNIT CREATED")
    print("Unit ID:", unit.get("id"))
    print("Unit name:", unit.get("name"))
    print("Unit type:", unit.get("unit_type"))

    return unit

async def run(
    access_token: str,
    *,
    create_unit: bool = False,
) -> dict[str, str]:
    """Verify the organization and set up its Finance unit."""

    client = create_authenticated_client(access_token)

    try:
        organization_id = os.getenv(
            "AEGIS_ORG_ID",
            "c0f12167-6514-40f6-8ef8-522ea0b06c89",
        )

        organization = await client.organizations.get(
            organization_id
        )

        print("\nORGANIZATION VERIFIED")
        print("Organization ID:", organization.get("id"))
        print("Organization name:", organization.get("name"))
        print("Organization slug:", organization.get("slug"))

        existing_unit_id = os.getenv(
            "AEGIS_ORGANIZATION_UNIT_ID",
            "",
        ).strip()

        unit = await get_or_create_finance_unit(
            client,
            organization_unit_id=existing_unit_id or None,
            allow_creation=create_unit,
        )

        return {
            "organization_id": organization["id"],
            "organization_unit_id": unit["id"],
        }

    finally:
        await close_client(client)


async def login_and_run(
    *,
    email: str,
    password: str,
    create_unit: bool,
) -> dict[str, str]:
    """
    Log in to Aegis and run the organization setup.

    The access token remains in memory and is never stored in a file.
    """
    base_url = normalise_base_url(
        os.getenv("AEGIS_BASE_URL", DEFAULT_BASE_URL)
    )

    async with AgenticOSClient(
        base_url=base_url,
        timeout=30.0,
        max_retries=3,
        debug=False,
    ) as authentication_client:
        token: AuthToken = await authentication_client.auth.login(
            email=email,
            password=password,
        )

        print("\nAUTHENTICATION SUCCESSFUL")
        print("Logged in as:", token.user.email)
        print("Organization:", token.user.organization_id)
        print("Token expires in:", token.expires_in, "seconds")

        return await run(
            token.access_token,
            create_unit=create_unit,
        )
    
def build_argument_parser() -> argparse.ArgumentParser:

    parser = argparse.ArgumentParser(
        description=(
            "Connect to Aegis and verify or create an organization."
        )
    )

    parser.add_argument(
        "--organization-id",
        help="Existing organization ID to retrieve and verify.",
    )

    parser.add_argument(
        "--create",
        action="store_true",
        help="Explicitly authorize creation of a new organization.",
    )

    parser.add_argument(
        "--name",
        default="Deloitte",
        help="Organization name used with --create.",
    )

    parser.add_argument(
        "--slug",
        default="deloitte",
        help="Unique organization slug used with --create.",
    )

    parser.add_argument(
        "--plan-tier",
        default="pro",
        choices=["free", "pro", "enterprise"],
        help="Organization plan tier used with --create.",
    )

    parser.add_argument(
        "--create-unit",
        action="store_true",
        help=(
            "Create the LdcDemo Finance Operations unit when "
            "AEGIS_ORGANIZATION_UNIT_ID is not configured."
        ),
    )
    return parser


def main() -> None:
    parser = build_argument_parser()
    args = parser.parse_args()

    email = input("Aegis email: ").strip()
    password = getpass.getpass("Aegis password: ")

    if not email:
        parser.error("Aegis email is required.")

    if not password:
        parser.error("Aegis password is required.")

    try:
        result = asyncio.run(
            login_and_run(
                email=email,
                password=password,
                create_unit=args.create_unit,
            )
        )

        print("\nAEGIS SETUP COMPLETED")
        print("Organization ID:", result["organization_id"])
        print(
            "Organization-unit ID:",
            result["organization_unit_id"],
        )

    except AuthenticationError as exc:
        print("\nAEGIS AUTHENTICATION FAILED")
        print("Error:", exc)
        raise SystemExit(1)

    except KeyboardInterrupt:
        print("\nOperation cancelled.")
        raise SystemExit(130)

    except Exception as exc:
        print("\nAEGIS SETUP FAILED")
        print("Error type:", type(exc).__name__)
        print("Error:", exc)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
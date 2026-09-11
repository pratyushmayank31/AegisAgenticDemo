"""
Exception Visibility Service for LdcDemo.

Provides querying, filtering, and reporting capabilities for ExceptionCases.
Enables dashboards and tracking of exceptions throughout their lifecycle.

Designed for read-only access; state changes handled by ExceptionResolutionService.
"""

from typing import List, Dict, Optional, Any
from datetime import datetime, timezone
from pydantic import BaseModel, Field, ConfigDict

from src.database import db


class ExceptionQueryFilter(BaseModel):
    """Filter criteria for exception queries."""

    model_config = ConfigDict(extra="forbid")

    invoice_id: Optional[str] = None
    correlation_id: Optional[str] = None
    reason_code: Optional[str] = None
    owner_id: Optional[str] = None
    exception_status: Optional[str] = None  # "OPEN", "RESOLVED", etc.
    limit: int = Field(100, ge=1, le=1000)


class ExceptionSummary(BaseModel):
    """Summary of an exception for dashboard/reporting."""

    model_config = ConfigDict(extra="forbid")

    exception_id: str
    invoice_id: str
    correlation_id: str
    reason_code: str
    reason_detail: str
    recommended_action: str
    owner_id: str
    exception_status: str
    opened_at: str
    resolved_at: Optional[str] = None
    days_open: float = 0.0


class ExceptionMetrics(BaseModel):
    """Metrics and statistics about exceptions."""

    model_config = ConfigDict(extra="forbid")

    total_open: int
    total_resolved: int
    by_reason_code: Dict[str, int]  # reason_code -> count
    by_owner: Dict[str, int]  # owner_id -> count
    average_resolution_time_hours: float


class ExceptionVisibilityService:
    """
    Provides read-only visibility into ExceptionCases.

    Supports:
    - Querying exceptions with flexible filtering
    - Computing metrics and statistics
    - Generating reports for dashboards
    - Tracking exception lifecycle (open, resolved)
    """

    def __init__(self):
        """Initialize with database connection."""
        self.db = db

    def get_exceptions(self, filter_criteria: ExceptionQueryFilter) -> List[Dict]:
        """
        Query exceptions with optional filtering.

        Args:
            filter_criteria: ExceptionQueryFilter with query parameters

        Returns:
            List of ExceptionCase records matching criteria
        """
        try:
            # Build query filter dict
            query_filter = {}
            if filter_criteria.invoice_id:
                query_filter["invoice_id"] = filter_criteria.invoice_id
            if filter_criteria.correlation_id:
                query_filter["correlation_id"] = filter_criteria.correlation_id
            if filter_criteria.reason_code:
                query_filter["reason_code"] = filter_criteria.reason_code
            if filter_criteria.owner_id:
                query_filter["owner_id"] = filter_criteria.owner_id
            if filter_criteria.exception_status:
                query_filter["exception_status"] = filter_criteria.exception_status

            # Query with limit
            exceptions = self.db.express_sync.list(
                "ExceptionCase",
                query_filter,
                limit=filter_criteria.limit,
            )
            return exceptions or []
        except Exception:
            return []

    def get_open_exceptions(self, limit: int = 100) -> List[Dict]:
        """
        Get all open (unresolved) exceptions.

        Args:
            limit: Maximum number to return

        Returns:
            List of open ExceptionCase records
        """
        return self.get_exceptions(
            ExceptionQueryFilter(exception_status="OPEN", limit=limit)
        )

    def get_exception_by_id(self, exception_id: str) -> Optional[Dict]:
        """
        Fetch a specific exception by ID.

        Args:
            exception_id: Exception ID

        Returns:
            ExceptionCase record or None if not found
        """
        try:
            return self.db.express_sync.find_one(
                "ExceptionCase", {"id": exception_id}
            )
        except Exception:
            return None

    def get_exceptions_for_invoice(
        self, invoice_id: str, status: Optional[str] = None
    ) -> List[Dict]:
        """
        Get all exceptions for a specific invoice.

        Args:
            invoice_id: Invoice ID
            status: Optional filter by status ("OPEN", "RESOLVED", etc.)

        Returns:
            List of ExceptionCase records for the invoice
        """
        query_filter = {"invoice_id": invoice_id}
        if status:
            query_filter["exception_status"] = status

        try:
            return self.db.express_sync.list("ExceptionCase", query_filter) or []
        except Exception:
            return []

    def get_exception_summaries(
        self, filter_criteria: ExceptionQueryFilter
    ) -> List[ExceptionSummary]:
        """
        Get exception summaries with calculated fields (e.g., days_open).

        Args:
            filter_criteria: Query filter

        Returns:
            List of ExceptionSummary with calculated metrics
        """
        exceptions = self.get_exceptions(filter_criteria)
        summaries = []
        now = datetime.now(timezone.utc)

        for exc in exceptions:
            opened_at_str = exc.get("opened_at", "")
            resolved_at_str = exc.get("resolved_at", "")

            days_open = 0.0
            if opened_at_str:
                try:
                    opened_at = datetime.fromisoformat(opened_at_str.replace("Z", "+00:00"))
                    close_time = (
                        datetime.fromisoformat(resolved_at_str.replace("Z", "+00:00"))
                        if resolved_at_str
                        else now
                    )
                    days_open = (close_time - opened_at).total_seconds() / 86400
                except (ValueError, AttributeError):
                    pass

            summary = ExceptionSummary(
                exception_id=exc.get("id", ""),
                invoice_id=exc.get("invoice_id", ""),
                correlation_id=exc.get("correlation_id", ""),
                reason_code=exc.get("reason_code", ""),
                reason_detail=exc.get("reason_detail", ""),
                recommended_action=exc.get("recommended_action", ""),
                owner_id=exc.get("owner_id", ""),
                exception_status=exc.get("exception_status", ""),
                opened_at=opened_at_str,
                resolved_at=resolved_at_str,
                days_open=days_open,
            )
            summaries.append(summary)

        return summaries

    def compute_metrics(self) -> ExceptionMetrics:
        """
        Compute aggregated metrics about all exceptions.

        Returns:
            ExceptionMetrics with counts and statistics
        """
        try:
            all_exceptions = self.db.express_sync.list("ExceptionCase") or []

            total_open = 0
            total_resolved = 0
            by_reason_code: Dict[str, int] = {}
            by_owner: Dict[str, int] = {}
            resolution_times: List[float] = []

            for exc in all_exceptions:
                status = exc.get("exception_status", "")
                reason = exc.get("reason_code", "")
                owner = exc.get("owner_id", "")

                if status == "OPEN":
                    total_open += 1
                elif status == "RESOLVED":
                    total_resolved += 1

                by_reason_code[reason] = by_reason_code.get(reason, 0) + 1
                by_owner[owner] = by_owner.get(owner, 0) + 1

                # Calculate resolution time if resolved
                if status == "RESOLVED":
                    opened_str = exc.get("opened_at", "")
                    resolved_str = exc.get("resolved_at", "")
                    if opened_str and resolved_str:
                        try:
                            opened = datetime.fromisoformat(opened_str.replace("Z", "+00:00"))
                            resolved = datetime.fromisoformat(resolved_str.replace("Z", "+00:00"))
                            hours = (resolved - opened).total_seconds() / 3600
                            resolution_times.append(hours)
                        except (ValueError, AttributeError):
                            pass

            avg_resolution_hours = (
                sum(resolution_times) / len(resolution_times)
                if resolution_times
                else 0.0
            )

            return ExceptionMetrics(
                total_open=total_open,
                total_resolved=total_resolved,
                by_reason_code=by_reason_code,
                by_owner=by_owner,
                average_resolution_time_hours=avg_resolution_hours,
            )
        except Exception:
            return ExceptionMetrics(
                total_open=0,
                total_resolved=0,
                by_reason_code={},
                by_owner={},
                average_resolution_time_hours=0.0,
            )

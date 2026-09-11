"""
Audit Chain Service for Step 7A: Hash-Linked Audit Trail.

Implements cryptographic hash-linking of BusinessAuditEvent records to create
a tamper-evident audit chain. Each event references the previous event's hash,
enabling chain verification and tamper detection.

Design:
- Each BusinessAuditEvent has event_hash (full SHA256 of current event)
- Each event's previous_hash points to the preceding event's event_hash
- Chain is ordered by timestamp within a correlation_id
- Chain integrity can be verified by following previous_hash links

Application-level tamper-evident (not tamper-proof): modified event_payload
changes the computed hash, enabling detection via chain verification.
"""

import hashlib
import json
from typing import Optional, List
from datetime import datetime, timezone

from src.database import db

# Sensitive fields that must NOT appear in event_payload
PROHIBITED_PAYLOAD_FIELDS = {
    "raw_text",
    "extracted_text",
    "bank_details",
    "account_number",
    "api_key",
    "token",
    "secret",
    "password",
}


class AuditChainService:
    """
    Manages cryptographic hash-linking of audit events.

    Ensures:
    1. Each event references previous event's hash (previous_hash field)
    2. Hash computation is deterministic
    3. Audit chain is verifiable by traversing hash links
    4. No breaks in the chain for a given correlation_id
    """

    def __init__(self):
        """Initialize with database connection."""
        self.db = db

    def get_last_audit_event(self, correlation_id: str) -> Optional[dict]:
        """
        Fetch the most recent audit event for a correlation_id.

        Args:
            correlation_id: Correlation ID for the invoice/workflow

        Returns:
            Last BusinessAuditEvent record or None if none exist
        """
        try:
            # Query all events for this correlation, ordered by timestamp DESC
            # (We'll get them all and sort in Python for consistency)
            events = self.db.express_sync.list(
                "BusinessAuditEvent", {"correlation_id": correlation_id}
            )
            if not events:
                return None

            # Sort by event_timestamp descending to get most recent
            sorted_events = sorted(
                events,
                key=lambda e: e.get("event_timestamp", ""),
                reverse=True
            )
            return sorted_events[0] if sorted_events else None
        except Exception:
            return None

    def _canonicalize_payload(self, event_payload: str) -> str:
        """
        Parse and re-serialize event_payload as canonical JSON.

        Ensures:
        - Sorted keys
        - No extra whitespace
        - Consistent ordering for hashing

        Args:
            event_payload: JSON string (or "{}" default)

        Returns:
            Canonical JSON string with sorted keys
        """
        try:
            payload_dict = json.loads(event_payload)
            # Re-serialize with sorted keys, no spaces
            return json.dumps(payload_dict, sort_keys=True, separators=(",", ":"))
        except (json.JSONDecodeError, TypeError):
            return "{}"

    def _validate_no_sensitive_data(self, event_payload: str) -> None:
        """
        Validate that event_payload does not contain prohibited sensitive fields.

        Raises:
            ValueError: If any prohibited field is present
        """
        try:
            payload_dict = json.loads(event_payload)
            if isinstance(payload_dict, dict):
                for key in payload_dict.keys():
                    if key.lower() in PROHIBITED_PAYLOAD_FIELDS:
                        raise ValueError(
                            f"Prohibited sensitive field '{key}' found in event_payload"
                        )
        except json.JSONDecodeError:
            pass

    def compute_event_hash(
        self,
        event_id: str,
        correlation_id: str,
        action_type: str,
        event_payload: str,
        timestamp: str,
        previous_hash: str = ""
    ) -> str:
        """
        Compute deterministic SHA256 hash for audit event.

        Hash includes all event content (payload, action, ids) and previous link.
        Uses canonical JSON to ensure deterministic hashing.

        Args:
            event_id: Unique event identifier
            correlation_id: Correlation ID for tracing
            action_type: Type of action (e.g., APPROVAL_DECISION)
            event_payload: JSON event payload (must be canonicalized)
            timestamp: Event timestamp (ISO 8601)
            previous_hash: Previous event's hash (for linking)

        Returns:
            Full 64-character SHA256 hexadecimal digest

        Raises:
            ValueError: If sensitive data found in payload
        """
        self._validate_no_sensitive_data(event_payload)
        canonical_payload = self._canonicalize_payload(event_payload)

        # Build canonical event content with sorted keys
        canonical_event = {
            "action_type": action_type,
            "correlation_id": correlation_id,
            "event_id": event_id,
            "event_payload": canonical_payload,
            "previous_hash": previous_hash,
            "timestamp": timestamp,
        }

        # Serialize to canonical JSON
        canonical_json = json.dumps(canonical_event, sort_keys=True, separators=(",", ":"))
        canonical_bytes = canonical_json.encode("utf-8")

        # Compute full SHA-256 hash
        full_hash = hashlib.sha256(canonical_bytes).hexdigest()

        # Assert exactly 64 hex characters
        assert len(full_hash) == 64, f"SHA256 hash must be 64 chars, got {len(full_hash)}"

        return full_hash

    def link_audit_event(
        self,
        audit_event_data: dict,
        correlation_id: str
    ) -> dict:
        """
        Link a new audit event to the previous event in the chain.

        Populates previous_hash and recomputes event_hash to include the link.

        Args:
            audit_event_data: Audit event dict with id, timestamp, action_type, etc.
            correlation_id: Correlation ID for finding previous event

        Returns:
            Updated audit_event_data with previous_hash and linked event_hash
        """
        # Find previous event in this chain
        last_event = self.get_last_audit_event(correlation_id)
        previous_hash = last_event.get("event_hash", "") if last_event else ""

        # Update event data with chain link
        audit_event_data["previous_hash"] = previous_hash

        # Recompute event_hash including the chain link and all required fields
        event_id = audit_event_data.get("id", "")
        action_type = audit_event_data.get("action_type", "")
        event_payload = audit_event_data.get("event_payload", "{}")
        timestamp = audit_event_data.get("event_timestamp", "")

        linked_hash = self.compute_event_hash(
            event_id=event_id,
            correlation_id=correlation_id,
            action_type=action_type,
            event_payload=event_payload,
            timestamp=timestamp,
            previous_hash=previous_hash,
        )
        audit_event_data["event_hash"] = linked_hash

        return audit_event_data

    def verify_audit_chain(self, correlation_id: str) -> tuple[bool, List[str]]:
        """
        Verify the integrity of an audit chain.

        Reconstructs canonical event content from stored records and recomputes
        hashes to detect any tampering (modified payload, action_type, etc.).

        Verifies:
        1. No broken links (previous_hash matches preceding event's event_hash)
        2. Stored event_hash matches recomputed hash from all event fields
        3. Chain is complete (first event has empty previous_hash)
        4. Tampering detection: payload modifications change the hash

        Args:
            correlation_id: Correlation ID to verify

        Returns:
            (is_valid, issues) tuple
            - is_valid: True if chain is intact, False otherwise
            - issues: List of issues found (empty if valid)
        """
        issues = []

        try:
            # Fetch all events for this correlation
            events = self.db.express_sync.list(
                "BusinessAuditEvent", {"correlation_id": correlation_id}
            )

            if not events:
                return True, []  # No events to verify

            # Sort by timestamp (oldest first) for chain verification
            sorted_events = sorted(
                events,
                key=lambda e: e.get("event_timestamp", "")
            )

            # First event should have empty previous_hash
            first_event = sorted_events[0]
            if first_event.get("previous_hash") != "":
                issues.append(
                    f"First event {first_event.get('id')} has non-empty previous_hash"
                )

            # Verify each event's hash is consistent with its content
            first_affected_event = None
            for i, current_event in enumerate(sorted_events):
                event_id = current_event.get("id", "")
                action_type = current_event.get("action_type", "")
                event_payload = current_event.get("event_payload", "{}")
                timestamp = current_event.get("event_timestamp", "")
                stored_previous_hash = current_event.get("previous_hash", "")
                stored_hash = current_event.get("event_hash", "")
                # Use stored correlation_id to detect tampering of that field
                stored_correlation_id = current_event.get("correlation_id", correlation_id)

                # Recompute hash from all event content (using stored correlation_id)
                try:
                    recomputed_hash = self.compute_event_hash(
                        event_id=event_id,
                        correlation_id=stored_correlation_id,
                        action_type=action_type,
                        event_payload=event_payload,
                        timestamp=timestamp,
                        previous_hash=stored_previous_hash,
                    )
                except ValueError as ve:
                    if not first_affected_event:
                        first_affected_event = event_id
                    issues.append(
                        f"Event {event_id} validation error: {str(ve)}"
                    )
                    continue

                # Detect payload/action/correlation tampering by hash mismatch
                if recomputed_hash != stored_hash:
                    if not first_affected_event:
                        first_affected_event = event_id
                    issues.append(
                        f"Event {event_id} hash mismatch (possible tampering): "
                        f"expected {recomputed_hash}, got {stored_hash}"
                    )

                # Verify chain linkage (previous_hash points to correct event)
                if i > 0:
                    previous_event = sorted_events[i - 1]
                    previous_event_hash = previous_event.get("event_hash", "")

                    if stored_previous_hash != previous_event_hash:
                        issues.append(
                            f"Event {event_id} previous_hash mismatch: "
                            f"expected {previous_event_hash}, got {stored_previous_hash}"
                        )

        except Exception as e:
            issues.append(f"Error verifying chain: {str(e)}")

        return len(issues) == 0, issues

    def get_audit_chain(self, correlation_id: str) -> List[dict]:
        """
        Retrieve the complete audit chain for a correlation_id.

        Returns events in chronological order (oldest first).

        Args:
            correlation_id: Correlation ID

        Returns:
            List of BusinessAuditEvent records in order
        """
        try:
            events = self.db.express_sync.list(
                "BusinessAuditEvent", {"correlation_id": correlation_id}
            )
            if not events:
                return []

            # Sort by timestamp
            return sorted(
                events,
                key=lambda e: e.get("event_timestamp", "")
            )
        except Exception:
            return []

    def get_chain_summary(self, correlation_id: str) -> dict:
        """
        Get a summary of the audit chain for reporting.

        Args:
            correlation_id: Correlation ID

        Returns:
            Summary dict with chain statistics
        """
        events = self.get_audit_chain(correlation_id)
        is_valid, issues = self.verify_audit_chain(correlation_id)

        return {
            "correlation_id": correlation_id,
            "event_count": len(events),
            "is_valid": is_valid,
            "issues": issues,
            "first_event_id": events[0].get("id", "") if events else None,
            "last_event_id": events[-1].get("id", "") if events else None,
            "first_timestamp": events[0].get("event_timestamp", "") if events else None,
            "last_timestamp": events[-1].get("event_timestamp", "") if events else None,
        }

"""Print logging service — structured print event history."""
from __future__ import annotations

from typing import Any


class PrintLogService:
    """Queries the UserLog table for structured print history."""

    PRINT_TARGET_TYPES = frozenset([
        'receipt_print',
        'receipt_print_sale',
        'receipt_print_job',
        'label_print',
        'test_print',
    ])

    def __init__(self, user_log_model: Any):
        self.user_log_model = user_log_model

    def history(
        self,
        *,
        limit: int = 50,
        target_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return print history rows, newest first."""
        q = self.user_log_model.query.filter(
            self.user_log_model.target_type.in_(self.PRINT_TARGET_TYPES)
        )
        if target_type:
            q = q.filter(self.user_log_model.target_type == target_type)
        rows = q.order_by(self.user_log_model.id.desc()).limit(max(1, min(int(limit), 500))).all()
        return [self._row_to_dict(r) for r in rows]

    def last_receipt_print(self) -> dict[str, Any] | None:
        row = (
            self.user_log_model.query
            .filter(self.user_log_model.target_type.in_([
                'receipt_print', 'receipt_print_sale', 'receipt_print_job',
            ]))
            .order_by(self.user_log_model.id.desc())
            .first()
        )
        return self._row_to_dict(row) if row else None

    def last_label_print(self) -> dict[str, Any] | None:
        row = (
            self.user_log_model.query
            .filter(self.user_log_model.target_type == 'label_print')
            .order_by(self.user_log_model.id.desc())
            .first()
        )
        return self._row_to_dict(row) if row else None

    def failure_summary(self, limit: int = 5) -> list[dict[str, Any]]:
        """Return the last N failed print events (identified by FAIL/ERROR in metadata)."""
        rows = (
            self.user_log_model.query
            .filter(self.user_log_model.target_type.in_(self.PRINT_TARGET_TYPES))
            .filter(self.user_log_model.metadata_summary.ilike('%fail%'))
            .order_by(self.user_log_model.id.desc())
            .limit(max(1, min(int(limit), 50)))
            .all()
        )
        return [self._row_to_dict(r) for r in rows]

    @staticmethod
    def _row_to_dict(r: Any) -> dict[str, Any]:
        return {
            'id': r.id,
            'action': r.action or '',
            'target_type': r.target_type or '',
            'target_id': r.target_id,
            'user': r.user.full_name if getattr(r, 'user', None) else '',
            'user_id': r.user_id if hasattr(r, 'user_id') else None,
            'metadata': r.metadata_summary or '',
            'date': r.date.strftime('%Y-%m-%d %H:%M:%S') if r.date else '',
            'ip': r.ip or '',
        }

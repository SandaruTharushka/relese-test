"""Canonical receipt print service.

Thin wrapper around :class:`services.printing.domain_service.PrintingDomainService`
used by the new ``/api/receipts/<billing|repair>/<id>/print`` endpoints.

Why this wrapper exists:

* The HTTP routes should be tiny — they receive a request, build a
  receipt, dispatch it, and return JSON.  Putting the dispatch glue
  here keeps :file:`routes/receipt_routes.py` declarative.
* Centralises structured logging so every print attempt emits a
  consistent ``[PRINT]`` line: target, type, record id, char count,
  result, copies.

This module never builds receipt text directly — that is the job of
:class:`services.receipt_service.ReceiptService` which delegates to
the canonical :class:`ReceiptEngine`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from services.receipt_service import ReceiptDocument


_log = logging.getLogger(__name__)


@dataclass
class PrintOutcome:
    """Result of a thermal print dispatch."""
    ok: bool
    payload: dict[str, Any]
    status_code: int
    target: str = ''
    copies: int = 1


class ReceiptPrintService:
    """Dispatches a built receipt to the configured thermal printer."""

    def __init__(self, *, print_domain: Any, log_action: Any | None = None) -> None:
        self._domain = print_domain
        self._log_action = log_action

    def print_document(
        self,
        doc: ReceiptDocument,
        *,
        copies: int = 1,
        actor_id: int | None = None,
    ) -> PrintOutcome:
        copies = max(1, min(int(copies or 1), 5))
        title = self._title_for(doc)

        _log.info(
            '[RECEIPT REQUEST] type=%s id=%s display_no=%s chars=%s cpl=%s paper=%s copies=%s actor=%s',
            doc.kind, doc.record_id, doc.display_no, doc.char_count, doc.cpl,
            doc.paper_size, copies, actor_id,
        )

        ok, payload, status_code = self._domain.print_receipt(
            receipt_text=doc.receipt_text,
            title=title,
            copies=copies,
            actor_id=actor_id,
        )

        target = str(payload.get('target') or '')
        _log.info(
            '[PRINT] type=%s id=%s display_no=%s printer=%s dispatch=%s copies=%s status=%s',
            doc.kind, doc.record_id, doc.display_no, target,
            'success' if ok else 'failed', copies, status_code,
        )
        if not ok:
            _log.warning(
                '[PRINT FAIL] type=%s id=%s code=%s msg=%s',
                doc.kind, doc.record_id,
                payload.get('code'), payload.get('msg'),
            )

        if ok and self._log_action is not None:
            try:
                self._log_action(
                    f'{doc.kind.title()} receipt printed (canonical)',
                    target_type=f'receipt_{doc.kind}_print',
                    target_id=doc.record_id,
                    metadata={
                        'display_no': doc.display_no,
                        'target': target,
                        'copies': copies,
                        'cpl': doc.cpl,
                        'paper_size': doc.paper_size,
                        'chars': doc.char_count,
                    },
                )
            except Exception:
                _log.exception('[PRINT] log_action failed for %s id=%s', doc.kind, doc.record_id)

        return PrintOutcome(
            ok=ok,
            payload=payload,
            status_code=status_code,
            target=target,
            copies=copies,
        )

    @staticmethod
    def _title_for(doc: ReceiptDocument) -> str:
        if doc.kind == 'billing':
            return f'Receipt {doc.display_no}'
        if doc.kind == 'repair':
            return f'Service Job {doc.display_no}'
        return f'Receipt {doc.display_no}'

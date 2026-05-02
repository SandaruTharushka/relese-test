from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.printing.label.engine import LabelPrintEngine
from services.printing.receipt_printer import ReceiptPrintExecutionService
from services.printing.spooler import PrintSpooler


@dataclass
class PrintingDomainService:
    printer_service: Any
    settings: Any
    logger: Any
    log_action: Any
    user_log_model: Any

    def _receipt_cfg(self) -> dict[str, Any]:
        return self.printer_service.normalize_receipt_config(self.settings.load_receipt())

    def _label_cfg(self) -> dict[str, Any]:
        raw = self.settings.load_label()
        return self.printer_service.normalize_label_config(raw) | raw

    def status(self) -> dict[str, Any]:
        return {
            'receipt': self.printer_service.build_receipt_status(self._receipt_cfg()),
            'label': self.printer_service.build_label_status(self._label_cfg()),
        }

    def diagnostics(self) -> dict[str, Any]:
        st = self.status()
        return {**st, 'scanner': self.settings.load_scanner()}

    def receipt_test(self) -> tuple[bool, dict[str, Any], int]:
        cfg = self._receipt_cfg()
        resolved = self.printer_service.resolve_receipt_printer(cfg)
        if not resolved.connected:
            reason = resolved.reason or 'Receipt printer is not connected.'
            return False, {'code': self.printer_service.map_resolution_error(reason), 'msg': reason}, 400
        svc = ReceiptPrintExecutionService(printer_service=self.printer_service, spooler=PrintSpooler(self.logger), logger=self.logger)
        out = svc.execute_text(
            cfg=cfg,
            receipt_text=ReceiptPrintExecutionService.build_test_receipt(resolved.name, resolved.mode),
            title='Garage Management System Receipt Printer Test',
            copies=1,
            resolved=resolved,
        )
        if not out.ok:
            return False, {'code': out.error_code or 'PRINT_FAILED', 'msg': out.error or 'Test print failed'}, 400
        return True, {'msg': f'Test receipt sent to {resolved.name}'}, 200

    def print_receipt(self, *, receipt_text: str, title: str, copies: int, actor_id: int | None) -> tuple[bool, dict[str, Any], int]:
        cfg = self._receipt_cfg()
        resolved = self.printer_service.resolve_receipt_printer(cfg)
        if not resolved.connected:
            reason = resolved.reason or 'Receipt printer is not connected.'
            return False, {
                'code': self.printer_service.map_resolution_error(reason),
                'msg': reason,
                'receipt': self.printer_service.build_receipt_status(cfg),
            }, 400
        svc = ReceiptPrintExecutionService(printer_service=self.printer_service, spooler=PrintSpooler(self.logger), logger=self.logger)
        out = svc.execute_text(cfg=cfg, receipt_text=receipt_text, title=title, copies=copies, resolved=resolved)
        if not out.ok:
            return False, {
                'code': out.error_code or 'PRINT_FAILED',
                'msg': out.error or 'Receipt print failed',
                'receipt': self.printer_service.build_receipt_status(cfg),
            }, 502
        self.log_action('Receipt print dispatched', target_type='receipt_print', target_id=actor_id, metadata={'target': out.target, 'copies': out.copies, 'mode': resolved.mode})
        return True, {
            'msg': f'Receipt sent to {out.target}',
            'target': out.target,
            'copies': out.copies,
            'receipt': self.printer_service.build_receipt_status(cfg),
        }, 200

    def print_label(self, *, product: dict[str, Any], copies: int) -> tuple[bool, dict[str, Any], int]:
        cfg = self._label_cfg()
        resolved = self.printer_service.resolve_label_printer(cfg)
        if not resolved.connected:
            reason = resolved.reason or 'Label printer is not connected.'
            return False, {'code': self.printer_service.map_resolution_error(reason), 'msg': reason}, 400
        svc = LabelPrintEngine(self.logger)
        out = svc.execute(
            label_cfg=cfg,
            resolved=resolved,
            product=product,
            copies=max(1, min(int(copies or 1), 50)),
            company_name=self.settings.store_settings.get('store_name', '') or '',
        )
        if not out.ok:
            status = 400 if out.code in {'INVALID_BARCODE', 'UNSUPPORTED_HOST'} else 502
            return False, {'code': out.code, 'msg': out.message}, status
        self.log_action('Label print dispatched', target_type='label_print', target_id=product.get('id'), metadata={'barcode': product.get('barcode'), 'name': product.get('name'), 'copies': copies})
        return True, {'code': out.code, 'msg': out.message, 'resolved_size_mm': out.resolved_size_mm}, 200

    def history(self, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = (
            self.user_log_model.query
            .filter(self.user_log_model.target_type.in_(['receipt_print', 'label_print']))
            .order_by(self.user_log_model.id.desc())
            .limit(max(1, min(int(limit), 200)))
            .all()
        )
        return [{
            'id': r.id,
            'action': r.action or '',
            'target_type': r.target_type or '',
            'user': r.user.full_name if r.user else '',
            'metadata': r.metadata_summary or '',
            'date': r.date.strftime('%Y-%m-%d %H:%M:%S') if r.date else '',
            'ip': r.ip or '',
        } for r in rows]

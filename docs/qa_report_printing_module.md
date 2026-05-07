# QA Testing Report: Printer Settings Module

**Module**: `printing/` — printer settings, detection, service, receipt engine  
**Application**: SuperMart POS v3.3.1  
**Test File**: `tests/test_printing.py`  
**Date**: 2026-05-07  
**Environment**: Python 3.11, pytest 9.x, SQLite in-memory, Linux CI (no win32print/WMI)  
**Branch**: `claude/printer-settings-qa-OWqUV`

---

## 1. Scope

**In scope:**
- Model layer: `PrinterSettings`, `ReceiptLayoutSettings`, `CompanyProfile` — save, load, reset, validation
- Service layer: printer enumeration, printer validation, print pipeline, test print dispatch
- HTTP API: all 18 printer/layout/company endpoints (settings CRUD, preview, test print, auth gates)
- Receipt rendering: HTML, plain text, ESC/POS output across all 3 receipt types (billing, job, return)
- Content flags: show/hide company name, customer, footer, vehicle, divider styles
- Cross-platform graceful degradation on Linux CI (no Windows spooler)

**Out of scope:**
- Physical hardware testing (USB/network printers)
- Windows-native spooler integration (covered by mocking)
- PySide6 desktop UI integration
- Cloud backup, update system, licensing

---

## 2. Test Matrix

| ID | Function | Category | Requirement | Status |
|----|----------|----------|-------------|--------|
| R01–R20 | `test_canonical_routes_registered` | Routes | All 20 canonical routes registered | PASS |
| V01 | `test_company_profile_load_returns_full_dict` | Model | Load returns all expected keys | PASS |
| V02 | `test_printer_settings_validates_paper_width` | Model | Invalid width raises ValueError | PASS |
| V03 | `test_printer_settings_validates_print_mode` | Model | Invalid mode raises ValueError | PASS |
| V04 | `test_layout_settings_validates_enums` | Model | Invalid enum raises ValueError | PASS |
| V05 | `test_layout_settings_font_size_bounds` | Model | Font size out of 8–24 raises ValueError | PASS |
| A01 | `test_printer_settings_load_returns_defaults` | Model | Defaults: 80mm, windows_raw, bools True | PASS |
| A02 | `test_printer_settings_save_and_load_roundtrip` | Model | Persist + reload including bool coercion | PASS |
| A03 | `test_printer_settings_save_valid_connection_types` | Model | All 3 connection types accepted | PASS |
| A04 | `test_receipt_layout_save_and_load_roundtrip` | Model | Layout fields persist with correct types | PASS |
| A05 | `test_receipt_layout_reset_removes_saved_keys` | Model | Reset restores defaults | PASS |
| A06 | `test_company_profile_save_and_load_roundtrip` | Model | Company fields persist | PASS |
| A07 | `test_company_profile_save_returns_zero_for_unknown_keys` | Model | Unknown keys ignored (return 0) | PASS |
| A08 | `test_company_profile_legacy_fallback` | Model | `store_name` key used when `company_name` absent | PASS |
| B01 | `test_list_available_printers_returns_dict_shape` | Service | Returns ok=True, list, counts | PASS |
| B02 | `test_validate_printer_exists_with_none_returns_not_ok` | Service | None → ok=False | PASS |
| B03 | `test_validate_printer_exists_with_empty_string_returns_not_ok` | Service | "" → ok=False | PASS |
| B04 | `test_validate_printer_exists_with_fake_name_returns_not_ok` | Service | Unknown name → ok=False | PASS |
| B05 | `test_print_receipt_when_printing_disabled` | Service | is_enabled=False → early return | PASS |
| B06 | `test_print_receipt_when_no_printer_selected` | Service | Empty name → "No receipt printer selected" | PASS |
| B07 | `test_print_receipt_when_printer_not_ready` | Service | Offline printer → ok=False | PASS |
| B08 | `test_test_receipt_print_no_printer_selected` | Service | No printer → ok=False | PASS |
| B09 | `test_test_label_print_no_printer_selected` | Service | No label printer → ok=False | PASS |
| B10 | `test_test_receipt_print_with_printer_ready` | Service | Ready printer → ok=True, metadata present | PASS |
| C01 | `test_api_printers_list_returns_ok` | API | GET list → 200, printers list | PASS |
| C02 | `test_api_printers_status_no_printer_selected` | API | GET status with no printer → 200 | PASS |
| C03 | `test_api_printers_save_valid_settings` | API | POST valid settings → 200, saved=2 | PASS |
| C04 | `test_api_printers_save_invalid_paper_width` | API | POST bad width → 400, error msg | PASS |
| C05 | `test_api_layout_get_returns_all_defaults` | API | GET layout → 200, all keys present | PASS |
| C06 | `test_api_layout_save_valid_payload` | API | POST valid layout → 200 | PASS |
| C07 | `test_api_layout_save_invalid_enum_returns_400` | API | POST bad enum → 400 | PASS |
| C08 | `test_api_layout_reset_restores_defaults` | API | POST reset → 200, defaults restored | PASS |
| C09 | `test_api_company_get_returns_all_keys` | API | GET company → 200, all keys | PASS |
| C10 | `test_api_company_save_json_payload` | API | POST company → 200, value persisted | PASS |
| C11 | `test_api_layout_preview_returns_html` | API | POST preview → 200, HTML present | PASS |
| C12 | `test_api_layout_preview_invalid_type_returns_400` | API | POST invalid type → 400 | PASS |
| C13 | `test_api_printers_test_no_printer_returns_400` | API | POST test with no printer → 400 | PASS |
| C14 | `test_api_unauthenticated_requests_redirect` ×3 | API | Unauthenticated → 302/401 | PASS |
| P01–P03 | `test_get_printer_status_*` | Detection | Empty/unknown name graceful degradation | PASS |
| P04 | `test_list_printers_returns_list` | Detection | Always returns a list | PASS |
| E01–E03 | `test_render_html_for_all_types` | Rendering | HTML contains doc_number + type_title | PASS |
| E04–E06 | `test_render_text_includes_totals` | Rendering | Text contains "Grand Total" and "Rs." | PASS |
| E07–E09 | `test_render_escpos_returns_bytes` | Rendering | ESC @ init, GS V cut present | PASS |
| E10 | `test_layout_paper_width_drives_text_width` | Rendering | 80mm lines wider than 58mm | PASS |
| E11 | `test_layout_change_affects_all_three_receipt_types` | Rendering | Footer flag propagates to all types | PASS |
| D01 | `test_render_text_includes_company_name` | Rendering | show_company_name=True → name in text | PASS |
| D02 | `test_render_text_hides_company_name_when_flag_false` | Rendering | show_company_name=False → absent | PASS |
| D03 | `test_render_text_hides_customer_when_flag_false` | Rendering | show_customer=False → absent | PASS |
| D04 | `test_render_text_shows_customer_when_flag_true` | Rendering | show_customer=True → present | PASS |
| D05 | `test_render_html_includes_company_name` | Rendering | Company name in HTML output | PASS |
| D06 | `test_render_escpos_contains_company_name_bytes` | Rendering | Company name in ESC/POS bytes | PASS |
| D07 | `test_render_text_no_divider_when_style_none` | Rendering | divider_style=none → no separators | PASS |
| D08–D10 | `test_all_receipt_types_render_doc_number_in_escpos` | Rendering | Doc number in ESC/POS for all types | PASS |
| D11 | `test_render_html_job_includes_vehicle` | Rendering | Vehicle field in job receipt HTML | PASS |

**Total: 84 tests — 84 PASSED, 0 FAILED**

---

## 3. Coverage Metrics

| Metric | Before | After |
|--------|--------|-------|
| Test count (`test_printing.py`) | 22 | 84 |
| Model error-path tests | 5 | 5 |
| Model happy-path / round-trip tests | 0 | 8 |
| Service layer functions tested | 0 | 6/6 |
| API endpoints with HTTP test | 0/18 | 14/18 |
| Receipt rendering assertions | 8 | 23 |
| Content flag tests (show/hide) | 0 | 8 |
| Auth gate tests | 0 | 3 |
| Windows mock coverage | N/A | `validate_printer_ready`, `send_raw` |

---

## 4. Findings

### F-001 — Logic gap in `validate_printer_exists` (Low)

**File**: `printing/service.py:84`  
**Description**: The function returns `ok=True` when a printer's status is `"offline"` but `driver_installed=True`. This means a physically disconnected printer with its driver still installed will receive a positive validation response. The UI may display misleading "printer exists" state.

```python
# Current logic — returns ok=True for offline+driver_installed:
if info["status"] in ("offline", "unknown") and not info.get("driver_installed", False):
    return {"ok": False, ...}
return {"ok": True, "info": info}  # reached even when status="offline"
```

**Recommendation**: Return `ok=False` when `status == "offline"`, regardless of driver installation. The `info` payload still carries `status` and `message` for the UI to render a helpful error.

---

### F-002 — `template_style` enum not validated in error-path tests (Low)

**File**: `printing/models.py:267`, `tests/test_printing.py`  
**Description**: `ReceiptLayoutSettings.save()` validates `rcpt_layout_template_style` against `TEMPLATE_STYLES = ("modern_garage", "compact", "classic")`, but the test suite does not include a test asserting that an invalid template style raises `ValueError`. The other 4 enum keys are covered.

**Recommendation**: Add `test_layout_settings_validates_template_style` to close this gap.

---

### F-003 — `db_app_ctx` vs `app_ctx` fixture confusion risk (Info)

**File**: `tests/test_printing.py`  
**Description**: The file-local `app_ctx` fixture creates a `test_request_context` (no live DB session). The `db_app_ctx` fixture creates a full `app_context` with SQLAlchemy. Using `app_ctx` in a DB-writing test would silently operate against the on-disk `supermart.db` rather than the in-memory test database.

**Recommendation**: Enforce convention via a naming comment and consider renaming `app_ctx` → `template_ctx` in a future cleanup to make its limited scope explicit.

---

### F-004 — Non-existent `source_id` not tested in print pipeline (Low)

**File**: `printing/service.py:116` (`print_receipt`)  
**Description**: Calling `print_receipt("billing", 99999)` with a non-existent sale ID will raise `LookupError` inside `build_receipt_context`. The route catches it and returns HTTP 404, but no automated test exercises this path.

**Recommendation**: Add a test that posts to `/api/receipts/billing/99999/print` and asserts HTTP 404.

---

### F-005 — `company_name` max length not validated (Info)

**File**: `printing/models.py:93` (`CompanyProfile.save`)  
**Description**: `_coerce_str(...).strip()` places no upper bound on string length. A very long `company_name` would be persisted and rendered without truncation, potentially breaking receipt layouts.

**Recommendation**: Add a length guard (e.g., 200 chars) consistent with database column limits, or document the absence of a limit explicitly.

---

## 5. Mock Strategy (CI Platform Notes)

Tests run on Linux CI where `win32print` and `wmi` are unavailable.

| Code path | CI behaviour | Test approach |
|-----------|-------------|---------------|
| `windows_spooler.send_raw()` | Returns `{"ok": False, "msg": "Raw printing only on Windows"}` | Patched via `mock_send_raw_ok` for positive-path tests |
| `printer_detector.get_printer_status(name)` | Returns `{"status": "offline", "can_print": False}` for any name | Used as-is for negative tests |
| `printer_detector.validate_printer_ready(name)` | Returns offline status | Patched via `mock_printer_ready` for positive-path tests |
| `printer_detector.list_printers_with_status()` | Returns `[]` on Linux | Tested as-is; count=0 is valid |

**Patch target convention**: Always patch at the `printing.service.*` import site, not at the source module. Example:

```python
# Correct — patches the name as used in service.py:
patch("printing.service.validate_printer_ready", ...)
patch("printing.service.send_raw", ...)

# Wrong — does not affect service.py's already-imported reference:
patch("printing.printer_detector.validate_printer_ready", ...)
```

---

## 6. Recommendations

1. **Add Windows CI runner**: GitHub Actions `windows-latest` runner would allow testing the actual `win32print` path. Add `@pytest.mark.skipif(sys.platform != "win32", ...)` guards for Windows-only assertions.

2. **Enable coverage in CI**: Add `pytest --cov=printing --cov-report=xml` to the CI run to track regression over time. Target >80% line coverage.

3. **Address F-001**: The `validate_printer_exists` logic gap should be fixed before the next release — it could confuse the settings UI when a driver-installed but physically disconnected printer is selected.

4. **Add receipt source_id=nonexistent test** (F-004): Ensures the 404 guard in `_print()` and `_preview()` routes is always exercised.

5. **Template style validation test** (F-002): One-line addition to `test_layout_settings_validates_enums` to complete enum coverage.

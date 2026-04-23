# SuperMart POS — Release Workflow

This document describes how versioning, building, and publishing work so that
the built-in auto-updater can detect and deliver new versions to installed users.

---

## 1. Version Management

**Single source of truth:** `version.py`

```python
__version__ = '3.1.0'   # ← change only this line when bumping
APP_VERSION = __version__
```

`APP_VERSION` is imported by:

| File | Purpose |
|------|---------|
| `desktop_runtime.py` | Splash screen, Qt window title |
| `app.py` | Flask app version header |
| `update_routes.py` | Sent to GitHub API as current version |
| `services/updater.py` | Compared against latest release tag |
| `settings.html` (via template context) | About and Updates pages |
| `SuperMartPOS.spec` (comment header) | Documentation only |

Update `update_config.py` has **no** version number — it only holds GitHub
coordinates and tuning constants that rarely change.

---

## 2. Semantic Version Format

Use [SemVer](https://semver.org): `MAJOR.MINOR.PATCH`

| Release type | Example | When |
|---|---|---|
| Patch | `3.1.1` | Bug fixes, no new features |
| Minor | `3.2.0` | New features, backwards compatible |
| Major | `4.0.0` | Breaking changes or major redesign |

The updater uses tuple comparison `(3, 1, 0) < (3, 1, 1)` — pre-release suffixes
like `-beta` are stripped during parsing.

---

## 3. Release Process (step-by-step)

### Step 1 — Bump the version

Edit `version.py`:

```python
__version__ = '3.2.0'   # ← new version
```

Both Inno Setup scripts (`SuperMartPOS_Setup.iss` and `installer/SuperMartPOS_Setup.iss`)
also contain the version string. Update them to match:

```ini
#define MyAppVersion "3.2.0"
#define MyInstallerBaseName "SuperMartPOS_Setup_v3.2.0"
```

And `VersionInfoVersion` (4-part Windows version):

```ini
VersionInfoVersion=3.2.0.0
```

### Step 2 — Build the Windows EXE

```powershell
python -m PyInstaller --clean --noconfirm SuperMartPOS.spec
# Output: dist\SuperMartPOS.exe
```

### Step 3 — Build the installer

Compile with Inno Setup (from project root):

```
iscc SuperMartPOS_Setup.iss
# Output: release\SuperMartPOS_Setup_v3.2.0.exe
```

Or use the subdirectory script:

```
iscc installer\SuperMartPOS_Setup.iss
```

### Step 4 — Publish a GitHub Release

1. Go to `https://github.com/sandarutharushka/relese-test/releases/new`
2. **Tag:** `v3.2.0`  ← must use `v` prefix
3. **Title:** `SuperMart POS v3.2.0`
4. **Description:** paste release notes (shown to users in the update dialog)
5. **Attach asset:** drag `release\SuperMartPOS_Setup_v3.2.0.exe` to the upload box
6. Click **Publish release**

---

## 4. Asset Naming Convention

The updater selects the installer asset by matching the following criteria
(in priority order):

1. `.exe` file whose name contains `setup` or `install` (case-insensitive)
2. Any other `.exe` file
3. `.msi` file

**Recommended naming:**

```
SuperMartPOS_Setup_v3.2.0.exe
```

This matches criterion 1 (`setup` in name) and makes releases easy to identify.

Source archives (`source.zip`, `.tar.gz`) are ignored automatically.

---

## 5. GitHub API Configuration

`update_config.py` contains all tunable constants:

```python
GITHUB_OWNER = "sandarutharushka"
GITHUB_REPO  = "relese-test"
UPDATE_CHECK_TIMEOUT_SECONDS = 8
UPDATE_CHECK_INTERVAL_HOURS  = 24
GITHUB_API_TOKEN_ENV_VAR     = "GITHUB_API_TOKEN"
```

### Optional: Authenticated API calls

GitHub rate-limits unauthenticated calls to **60 per hour per IP**. For most
deployments this is more than sufficient.

If you are deploying to many machines on a shared IP (corporate NAT), set a
fine-grained personal access token with read-only `contents` scope:

```env
# .env  (in %LOCALAPPDATA%\SuperMart POS\)
GITHUB_API_TOKEN=github_pat_xxxx
```

Tokens are **never logged**.

---

## 6. How the Updater Works

### Startup (automatic, non-blocking)

1. The Qt event loop starts.
2. After 8 seconds a daemon thread runs.
3. If a check was performed within the last 24 hours, the thread exits immediately
   (timestamp stored in `%LOCALAPPDATA%\SuperMart POS\last_update_check.txt`).
4. The thread calls the GitHub API (`/repos/.../releases/latest`).
5. If a newer version is found **and** an installer asset exists, a dismissible
   green banner is injected into the live web page.
6. The banner links to Settings → Updates.

### Manual check (Settings → Updates)

1. User opens **Settings → Updates** (left nav).
2. Clicks **Check Now**.
3. The browser calls `GET /api/updates/check` (login required).
4. The response JSON is rendered: version, release notes, and an Install button.

### Install flow

1. User clicks **Install Update**.
2. The browser calls `POST /api/updates/download-install` (login required, CSRF protected).
3. The server:
   - Re-fetches release metadata to get the download URL.
   - Streams the installer to a temp file (`%TEMP%\SuperMartPOS_upd_*.exe`).
   - Launches the installer as a **detached process** (`DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`).
   - Returns `{ "success": true }`.
4. The browser receives success and calls `window.pywebview.api.close_window()`.
5. The Qt window closes; the running Flask server stops.
6. The Inno Setup installer (now running independently) detects the existing
   installation via `AppId` and performs an in-place upgrade.
7. User data in `%LOCALAPPDATA%\SuperMart POS\` is untouched.

---

## 7. User Data Safety

| Data | Location | Affected by update? |
|------|----------|---------------------|
| SQLite database | `%LOCALAPPDATA%\SuperMart POS\supermart.db` | **No** |
| Backups | `%USERPROFILE%\Documents\SuperMartPOS\Backups\` | **No** |
| Activation / license | `C:\ProgramData\SuperMartPOS\activation.json` | **No** |
| Hardware config JSON | `%LOCALAPPDATA%\SuperMart POS\config\` | **No** |
| Application EXE | `%ProgramFiles%\SuperMart POS\` | Yes — replaced |

The Inno Setup `[UninstallDelete]` section only removes `{app}` (the Program
Files directory). The `{localappdata}\SuperMart POS` directory is created with
`users-modify` permissions and is **never deleted** by the installer.

---

## 8. Testing the Updater

### No-update case

Set `version.py` to a high version (e.g. `99.0.0`) that is newer than any
published release. Call `GET /api/updates/check`. Expected response:

```json
{ "is_update_available": false, "error": null }
```

### Update-available case

Set `version.py` to `1.0.0` and publish a release tagged `v3.1.0` with a
`.exe` asset attached. Call the check endpoint. Expected:

```json
{
  "is_update_available": true,
  "latest_version": "3.1.0",
  "installer_asset": { "name": "SuperMartPOS_Setup_v3.1.0.exe", ... }
}
```

### Offline / network failure case

Disconnect the machine or set `UPDATE_CHECK_TIMEOUT_SECONDS = 0` in
`update_config.py` temporarily.  The check endpoint returns:

```json
{ "is_update_available": false, "error": "Connection timed out" }
```

The app continues loading normally.

### No installer asset case

Publish a release with only a `source.zip` asset (no `.exe`). The check
endpoint returns `is_update_available: true` but `installer_asset: null`.
The Install Update button must never appear in this case (frontend hides it
when `installer_asset` is null).

### Unit tests

```bash
python -m pytest tests/test_updater.py -v
```

All 36 tests cover version parsing, asset selection, HTTP mocking, and
graceful failure modes.

---

## 9. Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Banner never appears | Checked within last 24 h | Delete `last_update_check.txt` in `%LOCALAPPDATA%\SuperMart POS\` |
| `No releases published yet` | No GitHub Release exists | Publish a release with a tag like `v3.1.0` |
| `No installer asset found` | EXE not attached to release | Re-publish release and attach `SuperMartPOS_Setup_v*.exe` |
| `GitHub API rate limited` | >60 anon req/hr from shared IP | Set `GITHUB_API_TOKEN` in `.env` |
| Download fails mid-way | Partial temp file left behind | Updater cleans up temp file on failure automatically |
| Installer won't launch on Windows | Permissions or antivirus | Run app as admin or add exclusion |

# SuperMart POS — Update & Release Guide

Complete guide for releasing a new version and delivering it safely to customers.

---

## 1. Bump the Version

All version strings live in **three files**. Update them together:

### `version.py`
```python
__version__ = '3.3.0'   # ← change this
APP_VERSION = __version__
```

### `SuperMartPOS_Setup.iss`
```ini
#define MyAppVersion "3.3.0"
#define MyInstallerBaseName "SuperMartPOS_Setup_v3.3.0"
VersionInfoVersion=3.3.0.0
```

### `installer/SuperMartPOS_Setup.iss` (if present)
Same changes as above.

**Version format:** `MAJOR.MINOR.PATCH` (SemVer)

| Type | Example | When |
|------|---------|------|
| Patch | `3.2.1` | Bug fixes only |
| Minor | `3.3.0` | New features, no breaking changes |
| Major | `4.0.0` | Breaking changes or major redesign |

---

## 2. Build the Windows EXE

```powershell
# In project root (Windows, with venv active):
pyinstaller --clean --noconfirm SuperMartPOS.spec
# Output: dist\SuperMartPOS.exe
```

Or use the batch wrapper:
```
BUILD.bat
```

---

## 3. Build the Installer

```powershell
# Compile Inno Setup installer:
iscc SuperMartPOS_Setup.iss
# Output: release\SuperMartPOS_Setup_v3.3.0.exe
```

Or use the batch wrapper:
```
build_installer.bat
```

### Calculate SHA256 Checksum

```powershell
# PowerShell:
Get-FileHash release\SuperMartPOS_Setup_v3.3.0.exe -Algorithm SHA256

# Or Python:
python -c "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" release\SuperMartPOS_Setup_v3.3.0.exe
```

Copy the hash — you will paste it into the GitHub release notes.

---

## 4. Create the GitHub Release

1. Go to: `https://github.com/SandaruTharushka/relese-test/releases/new`

2. **Tag:** `v3.3.0`  ← must use the `v` prefix

3. **Title:** `SuperMart POS v3.3.0`

4. **Description** (paste this template):
   ```
   ## What's New
   - [List your changes here]

   ## Bug Fixes
   - [List bug fixes here]

   ## SHA256 Checksum
   SHA256: <paste the hex digest from Step 3 here>
   ```

   > The updater parses `SHA256: <hex>` from the release notes and verifies the
   > downloaded installer before launching it. Always include this line.

5. **Attach asset:** drag `release\SuperMartPOS_Setup_v3.3.0.exe` to the upload box

6. Click **Publish release**

### Asset Naming Convention

The updater selects the installer by priority:
1. `.exe` whose name contains `setup` or `install` (preferred)
2. Any other `.exe`
3. `.msi`

**Always use:** `SuperMartPOS_Setup_v3.3.0.exe` (matches priority 1)

---

## 5. How Customers Update

Customers with an installed version will see an update notification:

### Automatic (background)
- 8 seconds after the app starts, a background thread checks for updates.
- The check is throttled: if already checked within 24 hours, it is skipped.
- If an update is available, a green banner appears in the app.

### Manual (Settings → Updates)
1. Open **Settings → Updates** from the left navigation.
2. Click **Check Now** to query the latest release.
3. Review the version number and release notes.
4. Click **Install Update**.

### What happens when the customer clicks Install Update:
1. **Backup created first** — the app backs up:
   - `%LOCALAPPDATA%\SuperMart POS\supermart.db` (via SQLite online backup API)
   - `%LOCALAPPDATA%\SuperMart POS\config\*.json` (all settings)
   - `%LOCALAPPDATA%\SuperMart POS\.env`
   - License / activation files from `%ProgramData%\SuperMartPOS\`
   - Saved to: `%LOCALAPPDATA%\SuperMart POS\backups\pre_update_YYYYMMDD_HHMMSS\`
2. **Installer downloaded** to `%LOCALAPPDATA%\SuperMart POS\updates\`
3. **SHA256 verified** against the checksum in the release notes (if published)
4. **Installer launched** as a detached process
5. **App closes** so the installer can replace program files
6. Inno Setup installer upgrades program files in `%ProgramFiles%\SuperMart POS\`
7. App restarts — **all customer data is untouched**

---

## 6. Data Safety Guarantees

| Data | Location | Affected by update? |
|------|----------|---------------------|
| SQLite database | `%LOCALAPPDATA%\SuperMart POS\supermart.db` | **No** |
| Config JSON | `%LOCALAPPDATA%\SuperMart POS\config\` | **No** |
| License / activation | `%ProgramData%\SuperMartPOS\activation.json` | **No** |
| Pre-update backup | `%LOCALAPPDATA%\SuperMart POS\backups\pre_update_*\` | **No** |
| Update log | `%LOCALAPPDATA%\SuperMart POS\logs\update.log` | Appended |
| Application EXE | `%ProgramFiles%\SuperMart POS\` | **Yes — replaced** |

The Inno Setup installer:
- Only deletes `{app}` (`%ProgramFiles%\SuperMart POS\`) during uninstall.
- Never touches `%LOCALAPPDATA%\SuperMart POS\` — ever.
- The `[Dirs]` section creates `{localappdata}\SuperMart POS` with `users-modify` permissions.

---

## 7. Database Migration

When the app starts after an update, `run_migrations()` is called automatically.

### How it works:
- Migrations are tracked in the `schema_migrations` table inside `supermart.db`.
- Each migration has a version number (1, 2, 3 …) and runs once.
- Statements that fail with "duplicate column" or "already exists" are silently
  skipped — making migrations idempotent and safe to re-run.
- If a completely new table is needed, add it as a new `Migration` entry at the
  bottom of `MIGRATIONS` in `services/migrations.py`.

### Adding a new migration:
```python
# services/migrations.py — add at the END of MIGRATIONS list:
Migration(18, 'v7.0 new feature columns', [
    "ALTER TABLE products ADD COLUMN new_field VARCHAR(80) DEFAULT ''",
    "CREATE INDEX IF NOT EXISTS idx_products_new_field ON products(new_field)",
]),
```

**Never edit or delete existing migrations.** Only add new ones at the end.

---

## 8. Rollback / Recovery

If an update causes problems:

### Option 1 — Use pre-update backup (recommended)
1. Open `%LOCALAPPDATA%\SuperMart POS\backups\`
2. Find the folder `pre_update_YYYYMMDD_HHMMSS` from the failed update
3. Copy `supermart.db` from that folder to `%LOCALAPPDATA%\SuperMart POS\`
4. Copy `config\*.json` back to `%LOCALAPPDATA%\SuperMart POS\config\`
5. Copy `license\activation.json` back to `%ProgramData%\SuperMartPOS\`
6. Restart the app

### Option 2 — Admin restore endpoint
1. Log in as Admin → Settings → Backup
2. Click the backup file to restore from the list
3. Or call the API: `POST /api/backup/restore` with `{"file": "<path>"}`

### Option 3 — Reinstall old version
1. Download the previous installer from GitHub Releases
2. Run it — the installer detects the same `AppId` and upgrades in-place
3. Customer data in `%LOCALAPPDATA%\SuperMart POS\` is untouched

### View pre-update backups via the API:
```
GET /api/updates/pre-update-backups
```
Returns a list of all backup directories with metadata (version, date, size, files present).

---

## 9. Update Log

Every update event is written to:
```
%LOCALAPPDATA%\SuperMart POS\logs\update.log
```

Logged events:
- Update check results (current version, latest version, error if any)
- Download start and completion (filename, size)
- Pre-update backup path and size
- SHA256 verification result
- Installer launch

Tokens and secrets are **never** logged.

---

## 10. Testing Before Release

### Unit tests
```bash
python -m pytest tests/test_updater.py -v
python -m pytest tests/test_update_system.py -v
python -m pytest tests/test_database_migrations.py -v
python -m pytest tests/test_backup_restore.py -v
```

### Manual update test (end-to-end)
1. Install the **previous** version on a test machine.
2. Add customer, product, and sale records.
3. Activate a license.
4. Publish the new GitHub release with the new installer.
5. Open the app → Settings → Updates → Check Now.
6. Click **Install Update**.
7. Verify after restart:
   - ✅ Old data (customers, sales, products) still exists
   - ✅ License is still active
   - ✅ DB schema migrations applied correctly
   - ✅ Version number updated
   - ✅ Pre-update backup folder created in `%LOCALAPPDATA%\SuperMart POS\backups\`
   - ✅ Update log written to `%LOCALAPPDATA%\SuperMart POS\logs\update.log`
   - ✅ App opens normally

---

## 11. GitHub API Configuration

`update_config.py`:
```python
GITHUB_OWNER = "SandaruTharushka"
GITHUB_REPO  = "relese-test"
UPDATE_CHECK_TIMEOUT_SECONDS = 8
UPDATE_CHECK_INTERVAL_HOURS  = 24
GITHUB_API_TOKEN_ENV_VAR     = "GITHUB_API_TOKEN"
```

### Optional: Authenticated API calls

GitHub rate-limits unauthenticated calls to **60 requests/hour per IP**.
For shared NAT/proxy deployments, set a fine-grained personal access token
(read-only `contents` scope):

```env
# %LOCALAPPDATA%\SuperMart POS\.env
GITHUB_API_TOKEN=github_pat_xxxx
```

---

## 12. Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| No update banner | Checked within last 24 h | Delete `last_update_check.txt` in `%LOCALAPPDATA%\SuperMart POS\` |
| "No releases published yet" | No GitHub Release | Publish a release tagged `v3.x.x` |
| "No installer asset found" | EXE not attached to release | Re-publish and attach `SuperMartPOS_Setup_v*.exe` |
| "GitHub API rate limited" | >60 anon req/hr | Set `GITHUB_API_TOKEN` in `.env` |
| "Checksum verification failed" | Wrong SHA256 in release notes | Recalculate and edit the release description |
| Download stuck / timeout | Slow connection or large file | Wait and retry; 10-minute timeout |
| App won't open after update | Migration or config issue | Restore DB from `%LOCALAPPDATA%\SuperMart POS\backups\pre_update_*\` |
| License deactivated after update | Should never happen | License is in `%ProgramData%\SuperMartPOS\` — untouched by installer |

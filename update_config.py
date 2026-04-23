"""
GitHub Releases update configuration for SuperMart POS.

All update-related constants live here. Change once; the updater,
the Flask routes, and the desktop runtime all read from this module.
"""

# ── GitHub repository coordinates ────────────────────────────────────────────
# Must match the GitHub account/repo where releases are published.
GITHUB_OWNER = "sandarutharushka"
GITHUB_REPO  = "relese-test"

# ── Network tunables ──────────────────────────────────────────────────────────
# Seconds to wait for the GitHub API response before giving up.
# Keep short so a slow network never blocks the app from starting.
UPDATE_CHECK_TIMEOUT_SECONDS = 8

# Seconds to wait for the installer asset download stream to complete.
# 10 minutes is generous; typical installer is 60–120 MB on a home connection.
DOWNLOAD_TIMEOUT_SECONDS = 600

# ── Startup check throttle ────────────────────────────────────────────────────
# Skip the automatic background check if one was performed within this window.
UPDATE_CHECK_INTERVAL_HOURS = 24

# ── Optional GitHub API authentication ───────────────────────────────────────
# Unauthenticated GitHub API calls are rate-limited to 60 requests/hour per IP.
# That is plenty for a POS app, but operators sharing a NAT/proxy may hit it.
# Set this env-var in .env or system environment to authenticate and get 5 000/hr.
# The token is NEVER logged.
GITHUB_API_TOKEN_ENV_VAR = "GITHUB_API_TOKEN"

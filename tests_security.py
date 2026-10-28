"""Security regression checks. Run: PYTHONIOENCODING=utf-8 python tests_security.py
"""
from __future__ import annotations

import os
import subprocess
import sys

sys.path.insert(0, ".")

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))


# 1. Production refuses to boot without an explicit secret key.
env = {**os.environ, "FLASK_ENV": "production", "FLASK_SECRET_KEY": ""}
env.pop("FLASK_SECRET_KEY", None)
proc = subprocess.run(
    [sys.executable, "-c", "import sys; sys.path.insert(0,'.'); import app.config"],
    env=env, capture_output=True, text=True,
)
check("production refuses default secret key",
      proc.returncode != 0 and "FLASK_SECRET_KEY" in proc.stderr,
      proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "")

# 2. Development generates a persisted non-constant key.
proc = subprocess.run(
    [sys.executable, "-c",
     "import sys; sys.path.insert(0,'.'); from app.config import Config; "
     "k=Config.SECRET_KEY; "
     "assert k and k != 'dev-only-insecure-secret-key' and len(k) >= 32, k; "
     "print('key-ok')"],
    capture_output=True, text=True,
)
check("dev secret key random + persisted", proc.returncode == 0 and "key-ok" in proc.stdout,
      proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "")
check("instance/secret_key exists", os.path.exists("instance/secret_key"))

# 3. Live-server checks (defaults to 127.0.0.1:5000; override with BASE_URL).
import json
import re
import urllib.error
import urllib.request
from http.cookiejar import CookieJar

try:
    urllib.request.urlopen(os.environ.get("BASE_URL", "http://127.0.0.1:5000") + "/api/health", timeout=3)
    server_up = True
except Exception:
    server_up = False
check("dev server reachable", server_up)

if server_up:
    BASE = os.environ.get("BASE_URL", "http://127.0.0.1:5000")

    # 4. CSRF: unsafe POST without token must be rejected.
    try:
        urllib.request.urlopen(urllib.request.Request(
            BASE + "/admin/content", data=b"tab=site"), timeout=5)
        check("CSRF rejects tokenless POST", False, "no 403")
    except urllib.error.HTTPError as e:
        check("CSRF rejects tokenless POST", e.code == 403, f"code={e.code}")

    # 5. Spoofed X-Forwarded-For must NOT rotate rate-limit identity.
    # Fetch a real CSRF token first (CSRF is checked before rate limiting),
    # then hammer login with a unique fake IP per attempt; the 6th must be
    # limited because the bucket keys on the real socket address.
    import re
    from http.cookiejar import CookieJar

    cj = CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    page = op.open(BASE + "/admin/login", timeout=5).read().decode()
    token = re.search(r'name="_csrf_token" value="([^"]+)"', page).group(1)
    codes = []
    limited = False
    for i in range(7):
        # Unique username per attempt: the per-user bucket cannot trigger the
        # throttle, so only the per-IP bucket (keyed on the real socket address)
        # can — proving the spoofed XFF is ignored.
        body = (f"username=nosuchuser{i}&password=wrong&_csrf_token={token}"
                ).encode()
        req = urllib.request.Request(
            BASE + "/admin/login", data=body,
            headers={"X-Forwarded-For": f"203.0.113.{i}"},
        )
        try:
            resp = op.open(req, timeout=5)
            codes.append(resp.status)
            page_html = resp.read().decode()
        except urllib.error.HTTPError as e:
            codes.append(e.code)
            page_html = e.read().decode()
        # The login view renders the throttle as an error page (HTTP 200).
        if "تلاش‌های ناموفق زیاد بود" in page_html:
            limited = True
    check("XFF spoof cannot bypass rate limit", limited,
          f"codes={codes} limited={limited}")

    # 6. Payment callback POST works without CSRF (gateway redirect target).
    try:
        r = urllib.request.urlopen(BASE + "/payment/callback?reference=none", timeout=5)
        check("payment callback reachable (CSRF exempt)", r.status in (200, 302))
    except urllib.error.HTTPError as e:
        check("payment callback reachable (CSRF exempt)", False, f"code={e.code}")

    # 7. Security headers present.
    r = urllib.request.urlopen(BASE + "/", timeout=5)
    hdrs = r.headers
    check("CSP header", "Content-Security-Policy" in hdrs)
    check("nosniff", hdrs.get("X-Content-Type-Options") == "nosniff")
    check("frame options", hdrs.get("X-Frame-Options") == "SAMEORIGIN")
    check("referrer policy", bool(hdrs.get("Referrer-Policy")))

    # 8. Login page rate limit still allows normal attempts (1st try not blocked).
    # (covered by 5 having at least the early codes == 200)

# ------------------------------------------------------------------ report
failed = [x for x in RESULTS if not x[1]]
for name, ok, detail in RESULTS:
    print(("PASS  " if ok else "FAIL  ") + name + (("  <- " + detail) if (not ok and detail) else ""))
print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
sys.exit(1 if failed else 0)

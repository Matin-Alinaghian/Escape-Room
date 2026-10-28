"""Sign-up flow checks: phone number -> SMS code -> password -> login.

Run:  PYTHONIOENCODING=utf-8 python tests_signup.py

Covers the behaviour the owner asked for:
  * a visitor who is not registered lands on a page of their own and types
    their number there
  * a code is issued for that number (by SMS; shown on-screen while SMS is off)
  * the password step is unreachable until the code is accepted
  * a wrong / expired / over-guessed code never creates an account
  * the account is created with the chosen password and the visitor is signed
    in immediately, and can sign in again later with phone + password
  * a phone number with no account is routed from the login page to sign-up
"""
from __future__ import annotations

import json
import re
import sys
import time

sys.path.insert(0, ".")

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))


from app import create_app
from app.db import execute, query_one
from app.jalali import fa_digits
from app.security import limiter

app = create_app()
limiter.reset()  # a clean sliding window so a re-run is never throttled

PHONE = "09121112233"
PHONE2 = "09121112244"
UNREGISTERED = "09129998877"
NAME = "کاربر تازه‌وارد"
PASSWORD = "signup-pass-1"


def csrf(client, path: str) -> str | None:
    page = client.get(path).get_data(as_text=True)
    m = re.search(r'name="_csrf_token" value="([^"]+)"', page)
    return m.group(1) if m else None


def archived_code(phone: str) -> str | None:
    """Pull the code out of the archived SMS body (send_direct stores it)."""
    row = query_one(
        "SELECT payload FROM notifications WHERE event_type='account.signup_code' "
        "AND recipient=? ORDER BY id DESC LIMIT 1",
        (phone,),
    )
    if row is None:
        return None
    text = json.loads(row["payload"] or "{}").get("text", "")
    m = re.search(r"(\d{6})", text)
    return m.group(1) if m else None


def reset(phone: str) -> None:
    with app.app_context():
        execute("DELETE FROM signup_codes WHERE phone=?", (phone,))
        execute("DELETE FROM customers WHERE phone=?", (phone,))


reset(PHONE)
reset(PHONE2)
reset(UNREGISTERED)

with app.test_client() as c:
    print("--- step 1: the phone number ---")
    r = c.post("/register", data={"phone": PHONE})
    check("register POST without CSRF rejected", r.status_code == 403, str(r.status_code))
    check("register page needs no login", c.get("/register").status_code == 200)

    token = csrf(c, "/register")
    r = c.post("/register", data={"phone": "12345", "_csrf_token": token}, follow_redirects=True)
    check("malformed phone rejected", "معتبر نیست" in r.get_data(as_text=True))

    token = csrf(c, "/register")
    r = c.post("/register", data={"phone": PHONE, "_csrf_token": token}, follow_redirects=True)
    body = r.get_data(as_text=True)
    check("code page reached after sending", "کد تأیید" in body and "کد ۶ رقمی" in body)
    with c.session_transaction() as s:
        demo = s.get("signup", {}).get("demo_code")
    code = archived_code(PHONE)
    check("code issued for the number", bool(code) and code.isdigit() and len(code) == 6, str(code))
    check("no SMS gateway -> code shown on the page",
          bool(demo) and demo == code and fa_digits(code) in body, str(demo))

    print("\n--- step 2: the code ---")
    r = c.get("/register/password", follow_redirects=True)
    check("password step blocked before verifying",
          "ابتدا کد تأیید" in r.get_data(as_text=True))

    token = csrf(c, "/register/code")
    r = c.post("/register/code", data={"code": "000000", "_csrf_token": token},
               follow_redirects=True)
    body = r.get_data(as_text=True)
    check("wrong code rejected", "کد تأیید اشتباه" in body)
    check("remaining attempts reported", "تلاش دیگر" in body)
    with app.app_context():
        check("account not created for a wrong code",
              query_one("SELECT id FROM customers WHERE phone=?", (PHONE,)) is None)

    token = csrf(c, "/register/code")
    r = c.post("/register/code", data={"code": code, "_csrf_token": token},
               follow_redirects=True)
    check("correct code accepted -> password step",
          "تکرار رمز عبور" in r.get_data(as_text=True), str(r.status_code))

    print("\n--- step 3: the password ---")
    token = csrf(c, "/register/password")
    r = c.post("/register/password",
               data={"name": NAME, "password": "short", "confirm": "short",
                     "_csrf_token": token})
    check("short password rejected", "حداقل" in r.get_data(as_text=True))

    token = csrf(c, "/register/password")
    r = c.post("/register/password",
               data={"name": NAME, "password": PASSWORD, "confirm": "different-pass",
                     "_csrf_token": token})
    check("password/confirm mismatch rejected", "یکسان نیستند" in r.get_data(as_text=True))

    token = csrf(c, "/register/password")
    r = c.post("/register/password",
               data={"name": NAME, "password": PASSWORD, "confirm": PASSWORD,
                     "_csrf_token": token},
               follow_redirects=True)
    body = r.get_data(as_text=True)
    check("account created and signed in", NAME in body and "حساب کاربری" in body)
    check("welcome message queued", "خوش آمدید" in body)

    with app.app_context():
        row = query_one("SELECT id, name, password_hash, must_change_pw FROM customers "
                        "WHERE phone=?", (PHONE,))
        check("customer row persisted with a password",
              row is not None and bool(row["password_hash"]) and not row["must_change_pw"])
        check("signup code consumed",
              query_one("SELECT phone FROM signup_codes WHERE phone=?", (PHONE,)) is None)

    print("\n--- logout, then sign in with phone + password ---")
    token = csrf(c, "/account")
    r = c.post("/account", data={"action": "logout", "_csrf_token": token},
               follow_redirects=True)
    check("logout message is «خارج شدید»", "خارج شدید" in r.get_data(as_text=True))

    token = csrf(c, "/login")
    r = c.post("/login", data={"identifier": PHONE, "password": PASSWORD,
                               "_csrf_token": token}, follow_redirects=True)
    check("login with the new account works", NAME in r.get_data(as_text=True))

    token = csrf(c, "/login")
    r = c.post("/login", data={"identifier": "0912 111 2233", "password": PASSWORD,
                               "_csrf_token": token}, follow_redirects=True)
    check("spaced/persian digits still log in", NAME in r.get_data(as_text=True))

with app.test_client() as c2:
    print("\n--- an unknown number is sent to sign-up instead of an error ---")
    token = csrf(c2, "/login")
    r = c2.post("/login", data={"identifier": UNREGISTERED, "password": PASSWORD,
                                "_csrf_token": token}, follow_redirects=True)
    body = r.get_data(as_text=True)
    check("unregistered phone routed to sign-up",
          "ثبت‌نام نشده" in body and "شماره تلفن" in body)

with app.test_client() as c3:
    print("\n--- code hardening: attempt cap, resend, expiry ---")
    token = csrf(c3, "/register")
    c3.post("/register", data={"phone": PHONE2, "_csrf_token": token})
    code2 = archived_code(PHONE2)
    check("second number also gets a code", bool(code2), str(code2))

    for _ in range(5):
        t = csrf(c3, "/register/code")
        c3.post("/register/code", data={"code": "111111", "_csrf_token": t})
    t = csrf(c3, "/register/code")
    r = c3.post("/register/code", data={"code": code2, "_csrf_token": t},
                follow_redirects=True)
    check("attempt cap refuses even the right code",
          "کد جدید درخواست کنید" in r.get_data(as_text=True))

    t = csrf(c3, "/register/code")
    r = c3.post("/register/code", data={"action": "resend", "_csrf_token": t},
                follow_redirects=True)
    check("resend cooldown explained", "ثانیه دیگر" in r.get_data(as_text=True))

    with app.app_context():
        execute("UPDATE signup_codes SET last_sent_at=0 WHERE phone=?", (PHONE2,))
    t = csrf(c3, "/register/code")
    r = c3.post("/register/code", data={"action": "resend", "_csrf_token": t},
                follow_redirects=True)
    code3 = archived_code(PHONE2)
    check("resend issues a fresh code", bool(code3) and code3 != code2, f"{code2} -> {code3}")

    with app.app_context():
        execute("UPDATE signup_codes SET expires_at=? WHERE phone=?",
                (time.time() - 1, PHONE2))
    t = csrf(c3, "/register/code")
    r = c3.post("/register/code", data={"code": code3, "_csrf_token": t},
                follow_redirects=True)
    check("expired code refused", "منقضی" in r.get_data(as_text=True))

    t = csrf(c3, "/register/code")
    r = c3.post("/register/code", data={"action": "change", "_csrf_token": t},
                follow_redirects=True)
    check("change-number goes back to step 1", "دریافت کد تأیید" in r.get_data(as_text=True))
    with app.app_context():
        check("change-number forgets the pending phone",
              query_one("SELECT phone FROM signup_codes WHERE phone=?", (PHONE2,)) is None)

print("\n--- an account that already exists cannot register again ---")
with app.test_client() as c4:
    token = csrf(c4, "/register")
    r = c4.post("/register", data={"phone": PHONE, "_csrf_token": token}, follow_redirects=True)
    check("existing account refused at step 1", "قبلاً ثبت‌نام" in r.get_data(as_text=True))

reset(PHONE)
reset(PHONE2)

print()
failed = 0
for name, ok, detail in RESULTS:
    if ok:
        print(f"ok    {name}")
    else:
        failed += 1
        print(f"FAIL  {name}" + (f"  [{detail}]" if detail else ""))
print(f"\n{len(RESULTS) - failed}/{len(RESULTS)} checks passed")
raise SystemExit(1 if failed else 0)

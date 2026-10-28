"""The super administrator's full control over the panel:

* cancelling a reservation (and freeing its seats again),
* defining a secondary administrator, scoping their permissions,
  disabling, re-keying and finally deleting them,
* the guards that stop the panel from deleting the super account.

Each database call opens its own app context on purpose: a long-lived pushed
context would cache ``g.admin`` and every simulated client would then look like
the same signed-in administrator.

Run: PYTHONIOENCODING=utf-8 python tests_admin_powers.py
"""
from __future__ import annotations

import re
import sys
from datetime import timedelta

sys.path.insert(0, ".")

from app import create_app
from app.auth import verify_password
from app.db import execute, query, query_one
from app.services import availability

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))


app = create_app()


def q(sql: str, args: tuple = ()):
    with app.app_context():
        return query_one(sql, args)


def qa(sql: str, args: tuple = ()):
    with app.app_context():
        return query(sql, args)


def ex(sql: str, args: tuple = ()):
    with app.app_context():
        return execute(sql, args)


TOK = "admin-powers-token"


def as_admin(client, admin_id: int, token: str = TOK) -> None:
    with client.session_transaction() as sess:
        sess["admin_id"] = admin_id
        sess["_csrf_token"] = token


def login(client, username: str, password: str):
    """Drive the real login form (fetching its CSRF token first)."""
    page = client.get("/admin/login").get_data(as_text=True)
    m = re.search(r'name="_csrf_token"\s+value="([^"]+)"', page)
    return client.post(
        "/admin/login",
        data={"username": username, "password": password, "_csrf_token": m.group(1) if m else ""},
        follow_redirects=True,
    )


super_id = q("SELECT id FROM admins WHERE role='super' ORDER BY id LIMIT 1")["id"]
client = app.test_client()
as_admin(client, super_id)

# ==========================================================================
# 1. Cancelling a reservation
# ==========================================================================
with app.app_context():
    scenario = query_one("SELECT * FROM scenarios WHERE status='published' ORDER BY id LIMIT 1")
    when = availability.business_today() + timedelta(days=31)
    availability.ensure_slots(scenario["id"], when)
slot = q(
    "SELECT * FROM slots WHERE scenario_id=? AND slot_date=? AND status='open' "
    "ORDER BY start_time LIMIT 1",
    (scenario["id"], when.isoformat()),
)

with app.app_context():
    res = availability.create_reservation(
        scenario_id=scenario["id"],
        slot_id=slot["id"],
        participants=max(int(scenario["min_players"]), 2),
        name="تست لغو",
        phone="09991110001",
        source="test",
    )
res_id = q("SELECT id FROM reservations WHERE code=?", (res["code"],))["id"]


def taken() -> int:
    with app.app_context():
        return [s for s in availability.slots_for_date(scenario["id"], when)
                if s["id"] == slot["id"]][0]["taken"]


seat_before = taken()
check("booking occupies seats", seat_before > 0, f"taken={seat_before}")

r = client.post(
    f"/admin/bookings/{res_id}/status",
    data={"_csrf_token": TOK, "status": "cancelled", "note": "لغو آزمایشی"},
    follow_redirects=False,
)
check("admin cancel redirects", r.status_code == 302, f"status={r.status_code}")
row = q("SELECT status, cancelled_at, cancel_reason FROM reservations WHERE id=?", (res_id,))
check("reservation is cancelled", row["status"] == "cancelled", repr(dict(row)))
check("cancelled_at recorded", bool(row["cancelled_at"]), repr(dict(row)))
check("cancel reason stored", row["cancel_reason"] == "لغو آزمایشی", repr(row["cancel_reason"]))
hist = q("SELECT * FROM reservation_history WHERE reservation_id=? AND to_status='cancelled'", (res_id,))
check("history row written", hist is not None and hist["admin_id"] is not None, "")
check("cancelling frees the seats", taken() < seat_before, f"{seat_before} -> {taken()}")

# A pending reservation can be cancelled too (the common operator action).
with app.app_context():
    res2 = availability.create_reservation(
        scenario_id=scenario["id"], slot_id=slot["id"],
        participants=max(int(scenario["min_players"]), 2),
        name="تست لغو دوم", phone="09991110002", source="test",
    )
res2_id = q("SELECT id FROM reservations WHERE code=?", (res2["code"],))["id"]
client.post(f"/admin/bookings/{res2_id}/status",
            data={"_csrf_token": TOK, "status": "cancelled"}, follow_redirects=False)
check("pending booking is cancellable",
      q("SELECT status FROM reservations WHERE id=?", (res2_id,))["status"] == "cancelled", "")

# ==========================================================================
# 2. Defining and fully controlling a secondary admin
# ==========================================================================
NEW_USER = "staff-probe"
ex("DELETE FROM admin_permissions WHERE admin_id IN (SELECT id FROM admins WHERE username=?)", (NEW_USER,))
ex("DELETE FROM admins WHERE username=?", (NEW_USER,))

r = client.post(
    "/admin/admins",
    data={"_csrf_token": TOK, "username": NEW_USER, "display_name": "کارمند آزمون",
          "password": "StaffProbe123", "permissions": ["bookings.view", "content.edit"]},
    follow_redirects=False,
)
check("super admin creates a secondary", r.status_code == 302, f"status={r.status_code}")
staff = q("SELECT * FROM admins WHERE username=?", (NEW_USER,))
check("secondary row exists", staff is not None, "")
check("secondary role is 'secondary'",
      staff and staff["role"] == "secondary", repr(staff and staff["role"]))
check("temporary password flagged", staff and staff["must_change_pw"] == 1, "")


def perms(admin_id: int) -> set[str]:
    return {x["permission"] for x in
            qa("SELECT permission FROM admin_permissions WHERE admin_id=?", (admin_id,))}


check("granted permissions stored",
      perms(staff["id"]) == {"bookings.view", "content.edit"}, repr(perms(staff["id"])))

page = client.get("/admin/admins").get_data(as_text=True)
check("admin list shows the new secondary", NEW_USER in page, "")
check("admin list shows the permission count", "2 مورد" in page, "")

# --- permission editing: the super admin re-scopes the account -------------
client.post(f"/admin/admins/{staff['id']}/permissions",
            data={"_csrf_token": TOK, "permissions": ["bookings.view"]},
            follow_redirects=False)
check("permissions replaced", perms(staff["id"]) == {"bookings.view"}, repr(perms(staff["id"])))

client.post(f"/admin/admins/{staff['id']}/permissions",
            data={"_csrf_token": TOK, "permissions": ["bookings.view", "media.manage"]},
            follow_redirects=False)
check("permissions can be added back",
      perms(staff["id"]) == {"bookings.view", "media.manage"}, repr(perms(staff["id"])))

# --- the secondary signs in and is held to their scope ---------------------
staff_client = app.test_client()
r = login(staff_client, NEW_USER, "StaffProbe123")
check("secondary can sign in", r.status_code == 200 and "خوش آمدید" in r.get_data(as_text=True),
      f"status={r.status_code}")
check("secondary reaches bookings", staff_client.get("/admin/bookings").status_code == 200, "")
check("secondary reaches media", staff_client.get("/admin/media").status_code == 200, "")
check("secondary blocked from admin management",
      staff_client.get("/admin/admins").status_code == 403, "")
check("secondary blocked from content", staff_client.get("/admin/content").status_code == 403, "")
check("secondary blocked from audit log", staff_client.get("/admin/logs").status_code == 403, "")

# --- disabling the account locks it out ------------------------------------
client.post(f"/admin/admins/{staff['id']}/toggle", data={"_csrf_token": TOK}, follow_redirects=False)
check("secondary disabled",
      q("SELECT is_active FROM admins WHERE id=?", (staff["id"],))["is_active"] == 0, "")
check("disabled admin is kicked out",
      staff_client.get("/admin/bookings").status_code == 302, "")

blocked = login(app.test_client(), NEW_USER, "StaffProbe123").get_data(as_text=True)
check("disabled admin cannot sign in", "غیرفعال است" in blocked, "")

# --- re-enabling + re-keying ----------------------------------------------
client.post(f"/admin/admins/{staff['id']}/toggle", data={"_csrf_token": TOK}, follow_redirects=False)
check("secondary re-enabled",
      q("SELECT is_active FROM admins WHERE id=?", (staff["id"],))["is_active"] == 1, "")

client.post(f"/admin/admins/{staff['id']}/reset-password",
            data={"_csrf_token": TOK, "password": "RotatedPass123"}, follow_redirects=False)
check("password rotated",
      verify_password(q("SELECT password_hash FROM admins WHERE id=?",
                        (staff["id"],))["password_hash"], "RotatedPass123"), "")

client.post(f"/admin/admins/{staff['id']}/reset-password",
            data={"_csrf_token": TOK, "password": "short"}, follow_redirects=False)
check("short password does not replace the old one",
      verify_password(q("SELECT password_hash FROM admins WHERE id=?",
                        (staff["id"],))["password_hash"], "RotatedPass123"), "")
check("rotated password actually works",
      "خوش آمدید" in login(app.test_client(), NEW_USER, "RotatedPass123").get_data(as_text=True), "")

# --- the super account is protected ---------------------------------------
r = client.post(f"/admin/admins/{super_id}/delete", data={"_csrf_token": TOK},
                follow_redirects=True)
check("super admin cannot be deleted",
      q("SELECT COUNT(*) AS c FROM admins WHERE role='super'")["c"] == 1, "")
check("delete-super shows an explanation", "مدیر اصلی" in r.get_data(as_text=True), "")

client.post(f"/admin/admins/{super_id}/permissions",
            data={"_csrf_token": TOK, "permissions": []}, follow_redirects=True)
check("super admin is still super after a permission reset",
      q("SELECT role FROM admins WHERE id=?", (super_id,))["role"] == "super", "")
check("super admin still holds every capability",
      client.get("/admin/admins").status_code == 200, "")

# --- deleting a secondary removes the account and its permissions ----------
r = client.post(f"/admin/admins/{staff['id']}/delete", data={"_csrf_token": TOK},
                follow_redirects=False)
check("delete secondary redirects", r.status_code == 302, f"status={r.status_code}")
check("secondary account deleted", q("SELECT id FROM admins WHERE username=?", (NEW_USER,)) is None, "")
check("secondary permissions deleted", perms(staff["id"]) == set(), repr(perms(staff["id"])))

# ==========================================================================
# 3. Clean up the probe bookings
# ==========================================================================
for rid in (res_id, res2_id):
    ex("DELETE FROM notifications WHERE reservation_id=?", (rid,))
    ex("DELETE FROM reservation_history WHERE reservation_id=?", (rid,))
    ex("DELETE FROM reservations WHERE id=?", (rid,))
for stale in qa(
    "SELECT id FROM reservations WHERE source='test' OR customer_id IN "
    "(SELECT id FROM customers WHERE phone LIKE '099911100%')"
):
    ex("DELETE FROM notifications WHERE reservation_id=?", (stale["id"],))
    ex("DELETE FROM reservation_history WHERE reservation_id=?", (stale["id"],))
    ex("DELETE FROM reservations WHERE id=?", (stale["id"],))
ex("DELETE FROM customers WHERE phone LIKE '099911100%'")
check("probe bookings removed",
      q("SELECT COUNT(*) AS c FROM reservations WHERE source='test'")["c"] == 0, "")

failed = [x for x in RESULTS if not x[1]]
for name, ok, detail in RESULTS:
    print(("PASS  " if ok else "FAIL  ") + name + (("  <- " + detail) if (not ok and detail) else ""))
print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
sys.exit(1 if failed else 0)

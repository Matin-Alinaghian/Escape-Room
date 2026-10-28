"""Payment provider interface — prepared, never faked.

Until the business owner supplies gateway credentials the booking flow runs a
real *reservation request* workflow: the reservation stays unpaid and the UI
says so. ``verify()`` is the only code path allowed to mark a transaction as
paid, and it always re-checks with the provider.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from app.db import execute, now_iso, query_one


class PaymentNotConfigured(RuntimeError):
    pass


class PaymentError(RuntimeError):
    pass


@dataclass
class PaymentIntent:
    transaction_id: int
    reference: str
    url: str | None
    status: str


class PaymentProvider:
    """Interface implemented by real gateways."""

    name = "base"

    def create(self, transaction_id: int, amount: int, description: str, callback_url: str) -> PaymentIntent:
        raise NotImplementedError

    def verify(self, reference: str) -> str:
        """Return one of: 'paid' | 'pending' | 'failed'. Must re-query the gateway."""
        raise NotImplementedError


class UnconfiguredProvider(PaymentProvider):
    """Default: no gateway. Creates a recordable, clearly-unpaid intent."""

    name = "unconfigured"

    def create(self, transaction_id, amount, description, callback_url) -> PaymentIntent:
        return PaymentIntent(transaction_id, f"pending-{transaction_id}", None, "pending")

    def verify(self, reference: str) -> str:
        return "pending"


class MockPaymentProvider(PaymentProvider):
    """Local stand-in for integration testing.

    It NEVER reports success: verification always returns 'pending' so no one
    can mistake it for a real captured payment.
    """

    name = "mock"

    def create(self, transaction_id, amount, description, callback_url) -> PaymentIntent:
        return PaymentIntent(transaction_id, f"mock-{transaction_id}", None, "pending")

    def verify(self, reference: str) -> str:
        return "pending"


def get_provider() -> PaymentProvider:
    from flask import current_app

    cfg = current_app.config
    if not cfg.get("PAYMENT_ENABLED"):
        return UnconfiguredProvider()
    provider = cfg.get("PAYMENT_PROVIDER")
    if provider == "mock":
        return MockPaymentProvider()
    # A real gateway adapter would be selected here once credentials exist.
    return UnconfiguredProvider()


def payment_enabled() -> bool:
    from flask import current_app

    return bool(current_app.config.get("PAYMENT_ENABLED"))


def create_payment(reservation_id: int) -> PaymentIntent:
    """Open a payment intent for a reservation (never invents a success)."""
    res = query_one(
        "SELECT r.*, s.title FROM reservations r JOIN scenarios s ON s.id=r.scenario_id WHERE r.id=?",
        (reservation_id,),
    )
    if res is None:
        raise PaymentError("reservation_not_found")
    if res["status"] in ("paid", "completed"):
        raise PaymentError("already_paid")

    cur = execute(
        "INSERT INTO payment_transactions(reservation_id, provider, amount, status, created_at) "
        "VALUES (?,?,?, 'created', ?)",
        (reservation_id, get_provider().name, res["total_price"], now_iso()),
    )
    transaction_id = cur.lastrowid
    from flask import current_app

    callback = f"{current_app.config['SITE_URL']}/payment/callback"
    intent = get_provider().create(
        transaction_id, res["total_price"], res["title"], callback
    )
    execute(
        "UPDATE payment_transactions SET reference=?, status=? WHERE id=?",
        (intent.reference, intent.status, transaction_id),
    )
    return intent


def verify(reference: str) -> str:
    """Server-side verification — the only path to status 'paid'."""
    row = query_one("SELECT * FROM payment_transactions WHERE reference=?", (reference,))
    if row is None:
        raise PaymentError("unknown_reference")
    if row["status"] == "paid":
        return "paid"  # idempotent: never double-processes

    status = get_provider().verify(reference)
    if status == "paid":
        execute(
            "UPDATE payment_transactions SET status='paid', verified_at=?, raw=? WHERE id=?",
            (now_iso(), json.dumps({"verified": True}), row["id"]),
        )
        from app.services.availability import change_status

        change_status(row["reservation_id"], "paid", note="پرداخت تأیید شد")
    elif status == "failed":
        execute(
            "UPDATE payment_transactions SET status='failed', verified_at=? WHERE id=?",
            (now_iso(), row["id"]),
        )
    else:
        execute(
            "UPDATE payment_transactions SET status='pending', verified_at=? WHERE id=?",
            (now_iso(), row["id"]),
        )
    return status

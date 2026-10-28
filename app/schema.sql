-- ===========================================================================
-- Escape Room — database schema (SQLite)
--
-- Conventions
--   * Timestamps are stored as UTC ISO-8601 text: 'YYYY-MM-DDTHH:MM:SSZ'
--   * Booking dates are stored as 'YYYY-MM-DD' in the business timezone.
--   * Foreign keys are enforced (PRAGMA foreign_keys=ON per connection).
-- ===========================================================================

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- Administration: two-level role system
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS admins (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    password_hash   TEXT    NOT NULL,
    display_name    TEXT    NOT NULL,
    role            TEXT    NOT NULL DEFAULT 'secondary'
                    CHECK (role IN ('super', 'secondary')),
    is_active       INTEGER NOT NULL DEFAULT 1,
    must_change_pw  INTEGER NOT NULL DEFAULT 0,
    last_login_at   TEXT,
    last_login_ip   TEXT,
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
-- Exactly one super administrator is enforced by application code + trigger.
CREATE UNIQUE INDEX IF NOT EXISTS idx_admins_single_super
    ON admins (role) WHERE role = 'super';

CREATE TABLE IF NOT EXISTS admin_permissions (
    admin_id    INTEGER NOT NULL REFERENCES admins(id) ON DELETE CASCADE,
    permission  TEXT    NOT NULL,
    granted_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    granted_by  INTEGER REFERENCES admins(id) ON DELETE SET NULL,
    PRIMARY KEY (admin_id, permission)
);
CREATE INDEX IF NOT EXISTS idx_admin_permissions_admin ON admin_permissions(admin_id);

-- ---------------------------------------------------------------------------
-- Audit log (append-only for secondary admins: no UPDATE/DELETE permission)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id    INTEGER REFERENCES admins(id) ON DELETE SET NULL,
    admin_name  TEXT,
    action      TEXT    NOT NULL,
    entity_type TEXT,
    entity_id   TEXT,
    detail      TEXT,
    ip          TEXT,
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_admin    ON audit_logs(admin_id);
CREATE INDEX IF NOT EXISTS idx_audit_action   ON audit_logs(action);

-- ---------------------------------------------------------------------------
-- Customers
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS customers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    phone       TEXT    NOT NULL UNIQUE,
    email       TEXT,
    notes       TEXT,
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_customers_phone ON customers(phone);

-- ---------------------------------------------------------------------------
-- Scenarios (rooms)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS scenarios (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    slug              TEXT    NOT NULL UNIQUE,
    title             TEXT    NOT NULL,
    short_description TEXT    NOT NULL DEFAULT '',
    story             TEXT    NOT NULL DEFAULT '',
    cover_image       TEXT,
    genre             TEXT    NOT NULL DEFAULT '',
    horror_intensity  INTEGER NOT NULL DEFAULT 3 CHECK (horror_intensity BETWEEN 1 AND 5),
    difficulty        INTEGER NOT NULL DEFAULT 3 CHECK (difficulty BETWEEN 1 AND 5),
    duration_minutes  INTEGER NOT NULL DEFAULT 60 CHECK (duration_minutes > 0),
    min_players       INTEGER NOT NULL DEFAULT 2 CHECK (min_players > 0),
    max_players       INTEGER NOT NULL DEFAULT 6 CHECK (max_players >= min_players),
    price_per_person  INTEGER NOT NULL DEFAULT 0 CHECK (price_per_person >= 0),
    price_per_group   INTEGER NOT NULL DEFAULT 0 CHECK (price_per_group >= 0),
    discount_percent  INTEGER NOT NULL DEFAULT 0 CHECK (discount_percent BETWEEN 0 AND 100),
    address           TEXT    NOT NULL DEFAULT '',
    age_restriction   TEXT    NOT NULL DEFAULT '',
    safety            TEXT    NOT NULL DEFAULT '',
    rules             TEXT    NOT NULL DEFAULT '',
    faqs              TEXT    NOT NULL DEFAULT '[]',   -- JSON array of {q,a}
    video_url         TEXT,
    featured          INTEGER NOT NULL DEFAULT 0,
    status            TEXT    NOT NULL DEFAULT 'draft'
                      CHECK (status IN ('draft','published','inactive','archived')),
    -- The administrator's per-room booking switch: 0 means the room stays on
    -- the site (and keeps its history) but takes no new reservation.
    is_bookable       INTEGER NOT NULL DEFAULT 1 CHECK (is_bookable IN (0,1)),
    sort_order        INTEGER NOT NULL DEFAULT 0,
    seo_title         TEXT,
    seo_description   TEXT,
    created_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    CHECK (max_players >= min_players)
);
CREATE INDEX IF NOT EXISTS idx_scenarios_status ON scenarios(status, featured, sort_order);

CREATE TABLE IF NOT EXISTS scenario_images (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_id INTEGER NOT NULL REFERENCES scenarios(id) ON DELETE CASCADE,
    path        TEXT    NOT NULL,
    alt         TEXT    NOT NULL DEFAULT '',
    sort_order  INTEGER NOT NULL DEFAULT 0,
    is_cover    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_scenario_images_scenario ON scenario_images(scenario_id, sort_order);

-- ---------------------------------------------------------------------------
-- Schedules: recurring opening windows per scenario
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schedules (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_id   INTEGER NOT NULL REFERENCES scenarios(id) ON DELETE CASCADE,
    weekday       INTEGER CHECK (weekday IS NULL OR weekday BETWEEN 0 AND 6), -- 0=Saturday ... 6=Friday (Persian week)
    open_time     TEXT    NOT NULL,   -- 'HH:MM'
    close_time    TEXT    NOT NULL,   -- 'HH:MM'
    slot_minutes  INTEGER NOT NULL DEFAULT 60 CHECK (slot_minutes >= 15),
    capacity      INTEGER NOT NULL DEFAULT 6 CHECK (capacity > 0),
    price_override INTEGER CHECK (price_override IS NULL OR price_override >= 0),
    valid_from    TEXT,               -- 'YYYY-MM-DD' or NULL = forever
    valid_to      TEXT,
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_schedules_scenario ON schedules(scenario_id, is_active);

-- ---------------------------------------------------------------------------
-- Booking slots: materialised, one row per scenario/date/start-time.
-- Lazily generated from schedules; individually blockable/editable.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS slots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_id INTEGER NOT NULL REFERENCES scenarios(id) ON DELETE CASCADE,
    slot_date   TEXT    NOT NULL,          -- 'YYYY-MM-DD'
    start_time  TEXT    NOT NULL,          -- 'HH:MM'
    end_time    TEXT    NOT NULL,          -- 'HH:MM'
    capacity    INTEGER NOT NULL CHECK (capacity > 0),
    price       INTEGER NOT NULL DEFAULT 0 CHECK (price >= 0),
    status      TEXT    NOT NULL DEFAULT 'open'
                CHECK (status IN ('open','blocked','sold_out')),
    generated   INTEGER NOT NULL DEFAULT 1,
    updated_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE (scenario_id, slot_date, start_time)
);
CREATE INDEX IF NOT EXISTS idx_slots_lookup ON slots(scenario_id, slot_date, status);

-- ---------------------------------------------------------------------------
-- Reservations
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS reservations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    code            TEXT    NOT NULL UNIQUE,       -- public booking reference
    scenario_id     INTEGER NOT NULL REFERENCES scenarios(id) ON DELETE RESTRICT,
    slot_id         INTEGER NOT NULL REFERENCES slots(id) ON DELETE RESTRICT,
    customer_id     INTEGER NOT NULL REFERENCES customers(id) ON DELETE RESTRICT,
    participants    INTEGER NOT NULL CHECK (participants > 0),
    unit_price      INTEGER NOT NULL CHECK (unit_price >= 0),
    total_price     INTEGER NOT NULL CHECK (total_price >= 0),
    status          TEXT    NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','awaiting_confirmation','confirmed',
                                      'awaiting_payment','paid','cancelled','completed','no_show')),
    source          TEXT    NOT NULL DEFAULT 'web',
    customer_note   TEXT,
    internal_note   TEXT,
    slot_date       TEXT    NOT NULL,             -- denormalised for fast search
    start_time      TEXT    NOT NULL,
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    confirmed_at    TEXT,
    completed_at    TEXT,
    cancelled_at    TEXT,
    cancel_reason   TEXT
);
CREATE INDEX IF NOT EXISTS idx_reservations_slot   ON reservations(slot_id, status);
CREATE INDEX IF NOT EXISTS idx_reservations_date   ON reservations(slot_date, start_time);
CREATE INDEX IF NOT EXISTS idx_reservations_status ON reservations(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_reservations_customer ON reservations(customer_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_reservations_code ON reservations(code);

-- One active reservation per customer per slot (prevents duplicate submits).
CREATE UNIQUE INDEX IF NOT EXISTS idx_reservations_unique_active
    ON reservations (slot_id, customer_id)
    WHERE status NOT IN ('cancelled');

CREATE TABLE IF NOT EXISTS reservation_history (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    reservation_id INTEGER NOT NULL REFERENCES reservations(id) ON DELETE CASCADE,
    from_status    TEXT,
    to_status      TEXT    NOT NULL,
    admin_id       INTEGER REFERENCES admins(id) ON DELETE SET NULL,
    actor          TEXT    NOT NULL DEFAULT 'system',
    note           TEXT,
    created_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_res_history_reservation ON reservation_history(reservation_id, created_at);

-- ---------------------------------------------------------------------------
-- Reviews (verified only, one per reservation+scenario)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS reviews (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    reservation_id INTEGER NOT NULL REFERENCES reservations(id) ON DELETE CASCADE,
    scenario_id    INTEGER NOT NULL REFERENCES scenarios(id) ON DELETE CASCADE,
    customer_id    INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    rating         INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    title          TEXT    NOT NULL DEFAULT '',
    body           TEXT    NOT NULL DEFAULT '',
    status         TEXT    NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending','approved','rejected','hidden')),
    admin_reply    TEXT,
    replied_at     TEXT,
    reported       INTEGER NOT NULL DEFAULT 0,
    report_reason  TEXT,
    created_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE (reservation_id, scenario_id)
);
CREATE INDEX IF NOT EXISTS idx_reviews_status  ON reviews(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_reviews_scenario ON reviews(scenario_id, status);

-- ---------------------------------------------------------------------------
-- Settings / CMS content (homepage hero, about, rules, contact, footer…)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,          -- JSON-encoded value
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_by  INTEGER REFERENCES admins(id) ON DELETE SET NULL
);

-- ---------------------------------------------------------------------------
-- Notifications (SMS-ready, works fully with the mock provider)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS notification_templates (
    event_type  TEXT PRIMARY KEY,
    body        TEXT NOT NULL,
    is_active   INTEGER NOT NULL DEFAULT 1,
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS notifications (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type     TEXT    NOT NULL,
    reservation_id INTEGER REFERENCES reservations(id) ON DELETE SET NULL,
    booking_code   TEXT,
    recipient      TEXT    NOT NULL,
    provider       TEXT    NOT NULL DEFAULT 'mock',
    status         TEXT    NOT NULL DEFAULT 'queued'
                   CHECK (status IN ('queued','sent','failed','skipped')),
    attempts       INTEGER NOT NULL DEFAULT 0,
    last_error     TEXT,
    payload        TEXT,
    dedupe_key     TEXT    NOT NULL UNIQUE,   -- prevents duplicate sends on retry
    created_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    sent_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_notifications_status ON notifications(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notifications_res    ON notifications(reservation_id);

-- ---------------------------------------------------------------------------
-- Payments (provider interface; never fakes success)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS payment_transactions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    reservation_id INTEGER NOT NULL REFERENCES reservations(id) ON DELETE CASCADE,
    provider       TEXT    NOT NULL DEFAULT 'mock',
    reference      TEXT,
    amount         INTEGER NOT NULL DEFAULT 0,
    currency       TEXT    NOT NULL DEFAULT 'IRR',
    status         TEXT    NOT NULL DEFAULT 'created'
                   CHECK (status IN ('created','pending','paid','failed','refunded')),
    raw            TEXT,
    created_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    verified_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_payments_res ON payment_transactions(reservation_id);

-- ---------------------------------------------------------------------------
-- Uploaded media library
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS media (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    filename     TEXT    NOT NULL,
    path         TEXT    NOT NULL,
    mime         TEXT    NOT NULL,
    size_bytes   INTEGER NOT NULL DEFAULT 0,
    uploaded_by  INTEGER REFERENCES admins(id) ON DELETE SET NULL,
    created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_media_created ON media(created_at DESC);

-- ---------------------------------------------------------------------------
-- Phone verification codes (customer self sign-up)
--
-- One row per phone that is mid-sign-up. The code is never stored in clear:
-- only a salted SHA-256 of it, so a leaked database dump cannot be used to
-- verify a number. Epoch seconds are used for the timestamps because every
-- comparison is a numeric one ("has this expired?").
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS signup_codes (
    phone          TEXT    PRIMARY KEY,
    code_salt      TEXT    NOT NULL,
    code_hash      TEXT    NOT NULL,
    expires_at     REAL    NOT NULL,
    attempts       INTEGER NOT NULL DEFAULT 0,
    sends          INTEGER NOT NULL DEFAULT 0,
    last_sent_at   REAL    NOT NULL DEFAULT 0,
    verified_until REAL,
    created_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- ---------------------------------------------------------------------------
-- Schema migrations (repeatable initialisation process)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    applied_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

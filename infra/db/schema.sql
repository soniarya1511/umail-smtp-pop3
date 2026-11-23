-- Schema for one mail domain. Applied to each domain database by init.sh.
-- (The original project used SQLite at /root/db/data.db with the same tables;
-- its schema was never committed, this is a reconstruction from the code.)

CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- crypt()/gen_salt('bf') for bcrypt seeds

-- Local mailbox owners. password_hash is bcrypt.
CREATE TABLE users (
    id            SERIAL PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL
);

-- Host names of trusted mail user agents (webmail). A HELO from one of these,
-- with matching A and PTR records, is authenticated without AUTH.
CREATE TABLE mua (
    id   SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

-- Every message this server accepted. sender_id is NULL for mail relayed in
-- from another server.
CREATE TABLE emails (
    id        SERIAL PRIMARY KEY,
    subject   TEXT NOT NULL DEFAULT '',
    body      TEXT NOT NULL,
    sender_id INTEGER REFERENCES users(id),
    sender    TEXT NOT NULL,
    recipient TEXT NOT NULL,
    timestamp TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc')
);

-- Local deliveries, read by POP3.
CREATE TABLE inbox (
    id           SERIAL PRIMARY KEY,
    email_id     INTEGER NOT NULL REFERENCES emails(id) ON DELETE CASCADE,
    recipient_id INTEGER NOT NULL REFERENCES users(id),
    is_deleted   BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX inbox_recipient_idx ON inbox (recipient_id) WHERE NOT is_deleted;

-- Delivery queue. Local mail is written as 'sent' at once; remote mail stays
-- 'pending'/'failed' until the outbox worker relays it (max 3 attempts).
CREATE TABLE outbox (
    id           SERIAL PRIMARY KEY,
    email_id     INTEGER NOT NULL REFERENCES emails(id) ON DELETE CASCADE,
    status       TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'failed', 'sent')),
    retry_count  INTEGER NOT NULL DEFAULT 0,
    last_attempt TIMESTAMP
);
CREATE INDEX outbox_queue_idx ON outbox (status, retry_count);

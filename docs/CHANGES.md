# Changes from the original (2025) code

The rewrite keeps the original design: the same ports, the same command set,
the DNS-based MUA and MTA trust, MX lookup plus VRFY before accepting a remote
recipient, the outbox worker with 3 retries, a persistent SMTP/POP3
connection per webmail user with a NOOP heartbeat, and the same webmail pages.
This file lists what changed.

## Structure and deployment

| Before | After |
|---|---|
| Containers created by hand, code copied in with `docker cp` and a `sed` rewrite of the domain name (`cp.sh`) | `docker compose up` builds and starts 7 Alpine containers. The domain is an environment variable |
| SQLite file `/root/db/data.db` inside each container, schema never written down | PostgreSQL container with `schema.sql` and an init script that seeds demo users |
| SMTP and POP3 in one process | One image, two roles: `python -m mailserver smtp` and `python -m mailserver pop3`, each in its own container |
| Hard-coded IPs, host names and paths in many files | `Settings` dataclass built from the environment |
| `from x import *` everywhere, numbered command codes (`which()` returning 0..10) | Packages, explicit imports, a verb → method dispatch table |
| dnsmasq `address=` (no PTR record, so the MTA check failed) | `host-record=` (A + PTR), plus SRV records for POP3 discovery |
| Webmail stored Django sessions in `db.sqlite3` | No database. Signed cookie sessions |

## Protocol fixes

| Original behaviour | Now |
|---|---|
| `reader.read(100)` assumed one command per packet | Line-based reading (`readline`), so large or split packets work |
| Two reply codes sent as two lines (`503 ...\n214 ...`), clients read with one `recv(4096)` | RFC 5321 continuation lines (`503-...`, `503 ...`), RFC 1939 `.` terminators. Python's stock `smtplib`/`poplib` now work as clients |
| `AUTH PLAIN` waited for credentials without a prompt | Sends `334 ` (or accepts the initial response on the same line) |
| `RSET` logged the SMTP client out and left the MAIL/RCPT counter stuck | `RSET` aborts only the current transaction (RFC 5321). The webmail can recover from a rejected recipient |
| POP3 `RSET` logged out, `TOP` did nothing | `RSET` unmarks deletions and `TOP` is implemented (RFC 1939) |
| POP3 deletions applied when the Python object was garbage collected | Applied on `QUIT` only (UPDATE state). Deleted messages disappear from `LIST`/`STAT` at once |
| `DATA` ended at any chunk ending in `.` | Ends at a line holding only `.`, with dot-stuffing on both sides |
| Relay port accepted any recipient and tried to forward it | Port 25 accepts local recipients only (`550 Relay access denied`) |

## Bugs fixed

- **SQL injection.** `client.py` and `mailBox.py` built SQL with f-strings. All
  queries now use bound parameters.
- **POP3 accepted a wrong password.** `PASS` sent `-ERR` and then
  authenticated anyway, because a `return` was missing.
- **Local delivery marked the whole outbox as sent.** `UPDATE outbox SET
  status='sent'` had no `WHERE` clause.
- **Failed DATA during relay was retried forever.** A failure there did not
  increment `retry_count`.
- **The webmail sent dates as `YYYY-DD-MM`.** The server expects `YYYY-MM-DD`,
  so mail was rejected on most days of the month.
- **The POP3 idle timeout replied with an SMTP code** (`221`).
- **Domain checks used `endswith("posthawk.com")`,** which also matched
  `evilposthawk.com`. They now compare the exact domain.
- **`UserConnection.close()` called `.close()` on a list.**
- **The webmail opened two connections per login** (once in the manager, once
  in the view).
- **New mail only appeared after logging out of the webmail.** The dashboard
  now re-syncs POP3 on every load.

## Security

- `DJANGO_SECRET_KEY` comes from the environment. The old committed key is
  not reused.
- `DEBUG` is off by default and `ALLOWED_HOSTS` is set.
- CSRF protection is back on the send endpoint (`@csrf_exempt` removed).
- Delete is POST-only.
- Containers run as non-root users.
- The webmail is published on `127.0.0.1` only. The mail ports stay inside the
  Docker network.

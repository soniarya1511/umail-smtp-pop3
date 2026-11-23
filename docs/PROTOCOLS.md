# Protocol reference

Commands are case-insensitive. Lines may end in CRLF or LF. An idle session
is closed after 180 s (`221 Connection timeout` for SMTP,
`-ERR Connection timeout` for POP3).

## SMTP (submission on 578, relay on 25)

Greeting: `220 <hostname> ESMTP umail`

Multi-line replies use the RFC 5321 form: `250-first line`, ..., `250 last line`.

| Command | Needs | Replies |
|---|---|---|
| `HELO <name>` | - | `250-<host> Hello <name>` / `250 AUTH PLAIN` (normal client), `235` (trusted MUA, 578), `250` (verified MTA, 25), `535` (DNS check failed), `503` already done, `501` syntax |
| `AUTH PLAIN [base64]` | HELO | Without the argument the server answers `334 ` and reads the next line. `235` success, `535` bad credentials or non-local user, `503` already authenticated, `504` other mechanism |
| `MAIL FROM:<addr>` | HELO + auth | `250 OK`; `550` a user sending as someone else; `551` the MUA named an unknown local sender; `503` transaction already open |
| `RCPT TO:<addr>` | MAIL FROM | `250 User <addr> exists`; `551 Email not found` (local miss, or remote VRFY failed); `551 MX record not found`; `550 Relay access denied` (port 25, non-local); `503` second recipient |
| `DATA` | RCPT TO | `354`, then message lines up to a line holding only `.`; `250 OK: Queued for delivery, ID: <n>` or `500 Data not proper: <reason>` |
| `RSET` | HELO + auth | `250 OK`, aborts the current MAIL/RCPT/DATA transaction |
| `VRFY <addr>` | HELO + auth | `250 User <addr> exists` / `551 Email not found` (local addresses only) |
| `NOOP` | - | `250 OK` |
| `HELP` | - | `214-...` command list |
| `QUIT` | - | `221 <host> closing connection` |
| anything else | - | `500 Command not recognized` |

The order is enforced. A command used too early gets `503 Initial handshake
not done` (before HELO), `503 Client not authorized` (before auth) or
`503 Bad sequence of commands`.

### Message format (DATA body)

```
Subject: <text>
From: <same address as MAIL FROM>
To: <same address as RCPT TO>
Date: YYYY-MM-DD HH:MM:SS
                                  <- blank line
<body lines>
.
```

A body line that starts with `.` is sent as `..` (dot-stuffing) and the
server removes the extra dot. The server stores its own timestamp. It checks
the `Date` header's format but does not store the value.

## POP3 (995)

Greeting: `+OK POP3 server ready`

Multi-line replies end with a line holding only `.`. Lines starting with `.`
are dot-stuffed.

| Command | State | Replies |
|---|---|---|
| `USER <email>` | auth | `+OK User <email> exists` / `-ERR No such user` |
| `PASS <password>` | after USER | `+OK User auth success` / `-ERR Invalid password` |
| `STAT` | transaction | `+OK <count> <octets>` |
| `LIST [n]` | transaction | `+OK <count> messages (<octets> octets)` then `<id> <size>` lines; with `n`, `+OK <id> <size>` |
| `RETR n` | transaction | `+OK <size> octets` then the message (same layout as DATA) |
| `TOP n lines` | transaction | Headers, blank line and the first `lines` body lines |
| `DELE n` | transaction | `+OK Marked for deletion` / `-ERR Not found` |
| `RSET` | transaction | Unmarks every deletion, `+OK <count> messages` |
| `NOOP` | any | `+OK` |
| `HELP` | any | `+OK Help text follows`, command list, `.` |
| `QUIT` | any | Applies the DELE marks (UPDATE state), `+OK Closing connection` |

Message numbers are the inbox row ids, so they stay stable across sessions.
Deletions are only applied on `QUIT`. A dropped connection keeps all mail.

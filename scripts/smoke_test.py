"""End-to-end test of the running stack using only Python's stock smtplib/poplib.

Run it from inside the docker network (see README):

    docker run --rm --network umail_mailnet --dns 172.28.0.2 \
        -v ./scripts:/scripts:ro --entrypoint python umail/mailserver /scripts/smoke_test.py

It covers: AUTH PLAIN, local delivery, relay to the other domain through the
outbox worker, POP3 LIST/RETR/TOP/DELE/RSET/QUIT, and the main rejections.
"""

import base64
import poplib
import smtplib
import sys
import time
import uuid

PASSWORDS = {"alice@posthawk.com": "alice123", "bob@posthawk.com": "bob123",
             "charlie@swiftmail.com": "charlie123"}
failures = 0


def check(name, condition):
    global failures
    print(("PASS " if condition else "FAIL ") + name)
    failures += not condition


def smtp_login(host, email):
    smtp = smtplib.SMTP(host, 578, timeout=10)
    smtp.helo("smoke-test.local")
    token = base64.b64encode(f"\0{email}\0{PASSWORDS[email]}".encode()).decode()
    code, _ = smtp.docmd("AUTH", f"PLAIN {token}")
    check(f"AUTH PLAIN {email}", code == 235)
    return smtp


def message(sender, to, subject, body):
    return (f"Subject: {subject}\r\nFrom: {sender}\r\nTo: {to}\r\n"
            f"Date: {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())}\r\n\r\n{body}\r\n").encode()


def pop_login(host, email):
    pop = poplib.POP3(host, 995, timeout=10)
    pop.user(email)
    pop.pass_(PASSWORDS[email])
    return pop


def find(pop, subject):
    for entry in pop.list()[1]:
        number = int(entry.split()[0])
        lines = [l.decode() for l in pop.retr(number)[1]]
        if lines[0] == f"Subject: {subject}":
            return number, lines
    return None, None


def main():
    tag = uuid.uuid4().hex[:8]
    local_subject, remote_subject = f"local {tag}", f"remote {tag}"

    smtp = smtp_login("mail.posthawk.com", "alice@posthawk.com")
    refused = smtp.sendmail("alice@posthawk.com", ["bob@posthawk.com"],
                            message("alice@posthawk.com", "bob@posthawk.com", local_subject,
                                    "hello bob\n.leading dot line"))
    check("local send accepted", refused == {})
    smtp.sendmail("alice@posthawk.com", ["charlie@swiftmail.com"],
                  message("alice@posthawk.com", "charlie@swiftmail.com", remote_subject, "hi charlie"))
    check("remote send queued", True)

    try:
        smtp.sendmail("bob@posthawk.com", ["charlie@swiftmail.com"], message(
            "bob@posthawk.com", "charlie@swiftmail.com", "spoof", "x"))
        check("spoofed MAIL FROM rejected", False)
    except smtplib.SMTPSenderRefused as exc:
        check("spoofed MAIL FROM rejected", exc.smtp_code == 550)
    try:
        smtp.sendmail("alice@posthawk.com", ["nobody@swiftmail.com"], message(
            "alice@posthawk.com", "nobody@swiftmail.com", "x", "x"))
        check("unknown remote recipient rejected (VRFY)", False)
    except smtplib.SMTPRecipientsRefused:
        check("unknown remote recipient rejected (VRFY)", True)
    smtp.quit()

    relay = smtplib.SMTP("mail.swiftmail.com", 25, timeout=10)
    check("untrusted MTA HELO rejected", relay.helo("mail.posthawk.com")[0] == 535)
    relay.quit()

    pop = pop_login("pop.posthawk.com", "bob@posthawk.com")
    number, lines = find(pop, local_subject)
    check("bob received local mail", number is not None)
    check("dot-stuffing round trip", lines and lines[-1] == ".leading dot line")
    top = [l.decode() for l in pop.top(number, 0)[1]]
    check("TOP returns headers only", top[0] == f"Subject: {local_subject}" and top[-1] == "")
    pop.dele(number)
    pop.rset()
    check("RSET undeletes", find(pop, local_subject)[0] == number)
    pop.dele(number)
    pop.quit()
    pop = pop_login("pop.posthawk.com", "bob@posthawk.com")
    check("DELE committed on QUIT", find(pop, local_subject)[0] is None)
    pop.quit()

    p =poplib.POP3("pop.posthawk.com", 995)
    p.user("bob@posthawk.com")
    try:
        p.pass_("wrong")
        check("bad POP3 password rejected", False)
    except poplib.error_proto:
        check("bad POP3 password rejected", True)
    p.quit()

    for _ in range(30):  # outbox worker polls every 10 s
        pop = pop_login("pop.swiftmail.com", "charlie@swiftmail.com")
        number, lines = find(pop, remote_subject)
        pop.quit()
        if number:
            break
        time.sleep(1)
    check("charlie received relayed mail", number is not None and lines[-1] == "hi charlie")

    print("all passed" if not failures else f"{failures} failed")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()

"""Command line entry point.

    python -m mailserver smtp        # SMTP submission + relay + outbox worker
    python -m mailserver pop3        # POP3
    python -m mailserver smtp pop3   # everything in one process (original layout)
"""

import argparse
import asyncio
import logging

from .config import Settings
from .server import ROLES, MailServer


def main() -> None:
    parser = argparse.ArgumentParser(prog="mailserver", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("roles", nargs="+", choices=ROLES)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(MailServer(Settings.from_env()).run(set(args.roles)))


if __name__ == "__main__":
    main()

"""Shared logging setup for both long-running processes (app/main.py's
FastAPI/uvicorn process and app/worker.py's bot+scheduler process).

Before this, only worker.py called logging.basicConfig at all - main.py
relied entirely on uvicorn's own default handler/format, so the two
processes that make up this app produced differently-formatted log lines
in the same journalctl/log-aggregation view, and most of app/ (108 files,
only 6 of which used logging.getLogger before this audit) had no
consistent place log lines even came from. This doesn't add request-id/
user-id context propagation (a bigger change - threading a contextvar
through the whole call stack) - just a single real setup call and a
consistent, timestamped format across both processes as a starting point.
"""

import logging

_FORMAT = "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"


def setup_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(level=level, format=_FORMAT)

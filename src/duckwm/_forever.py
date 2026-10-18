"""Running a config for good: connect, set up, serve; when duckwm goes away,
wait for it and do it all again."""

import logging
import time
from collections.abc import Callable

from ._wire import Closed
from .client import Client, Rejected

log = logging.getLogger("duckwm")


def forever(setup: Callable[[Client], object], path: str | None = None) -> None:
    """Run setup(c) on every connection, then serve.

    Everything setup registers dies with the connection on duckwm's side, so it's
    simply redone on the next one. A config that fails (an exception, or duckwm
    rejecting it) is retried with a growing delay instead of hammering duckwm.
    Returns on Ctrl+C.
    """
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    delay = 1.0
    waiting = False
    try:
        while True:
            try:
                c = Client(path)
            except (FileNotFoundError, ConnectionRefusedError):
                if not waiting:
                    log.info("waiting for duckwm")
                    waiting = True
                time.sleep(1)
                continue
            waiting = False
            log.info("connected")
            with c:
                try:
                    setup(c)
                    delay = 1.0
                    c.serve()
                except Closed:
                    log.info("disconnected")
                    continue
                except Rejected as e:
                    log.error("duckwm rejected the config: %s", e)
                except Exception:  # noqa: BLE001 - a broken config must not end the service
                    log.exception("config failed")
            # Broken config: back off until it's fixed (or duckwm restarts).
            time.sleep(delay)
            delay = min(delay * 2, 30)
    except KeyboardInterrupt:
        pass

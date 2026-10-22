"""Compatibility entrypoint for the durable outbox worker."""

from outbox.worker import *  # noqa: F401,F403
from outbox.worker import main


if __name__ == "__main__":
    main()

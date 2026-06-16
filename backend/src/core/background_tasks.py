import asyncio
import logging
from typing import Any, Awaitable, Optional


def create_logged_task(
    coro: Awaitable[Any],
    *,
    logger: logging.Logger,
    failure_message: str,
    task_name: Optional[str] = None,
) -> asyncio.Task:
    task = asyncio.create_task(coro, name=task_name)

    def _log_failure(done_task: asyncio.Task) -> None:
        try:
            done_task.result()
        except Exception as exc:
            logger.exception("%s: %s", failure_message, exc)

    task.add_done_callback(_log_failure)
    return task

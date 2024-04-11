from typing import Any

from ape.exceptions import ApeException
from ape.logging import logger

from .types import TaskType


class ImportFromStringError(Exception):
    pass


class InvalidContainerTypeError(Exception):
    def __init__(self, container: Any):
        super().__init__(f"Invalid container type: {container.__class__}")


class ContainerTypeMismatchError(Exception):
    def __init__(self, task_type: TaskType, container: Any):
        super().__init__(f"Invalid container type for '{task_type}': {container.__class__}")


class NoWebsocketAvailableError(Exception):
    def __init__(self):
        super().__init__(
            "Attempted to a use WebsocketRunner without a websocket-compatible provider."
        )


class SilverbackException(ApeException):
    """Base Exception for any Silverback runtime faults."""


class Halt(SilverbackException):
    def __init__(self):
        super().__init__("App halted, must restart manually")


class CircuitBreaker(SilverbackException):
    """Custom exception (created by user) that will trigger an application shutdown."""

    def __init__(self, message: str):
        logger.error(message)
        super().__init__(message)

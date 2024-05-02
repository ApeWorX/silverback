from typing import Any, Sequence

from ape.exceptions import ApeException

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


# TODO: `ExceptionGroup` added in Python 3.11
class StartupFailure(SilverbackException):
    def __init__(self, *exceptions: Sequence[Exception]):
        if error_str := "\n".join(str(e) for e in exceptions):
            super().__init__(f"Startup failure(s):\n{error_str}")
        else:
            super().__init__("Startup failure(s) detected. See logs for details.")


class NoTasksAvailableError(SilverbackException):
    def __init__(self):
        super().__init__("No tasks to execute")


class Halt(SilverbackException):
    def __init__(self):
        super().__init__("App halted, must restart manually")


class CircuitBreaker(Halt):
    """Custom exception (created by user) that will trigger an application shutdown."""

    def __init__(self, message: str):
        super(SilverbackException, self).__init__(message)

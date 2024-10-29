def __getattr__(name: str):
    if name == "CircuitBreaker":
        from .exceptions import CircuitBreaker

        return CircuitBreaker

    elif name == "SilverbackException":
        from .exceptions import SilverbackException

        return SilverbackException

    elif name == "SilverbackBot":
        from .main import SilverbackBot

        return SilverbackBot

    elif name == "StateSnapshot":
        from .state import StateSnapshot

        return StateSnapshot


__all__ = [
    "StateSnapshot",
    "CircuitBreaker",
    "SilverbackBot",
    "SilverbackException",
]

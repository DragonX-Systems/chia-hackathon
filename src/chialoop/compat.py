"""CHIA function compatibility surface with a local fallback.

On a CHIA cluster this uses ``@ChiaFunction`` / ``get``. Without chialoops
installed, the same call sites run in-process — which is also a supported
CHIA mode (local driver call).
"""

from __future__ import annotations

from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])

try:
    from chia.base.ChiaFunction import ChiaFunction, get  # type: ignore
except ImportError:  # pragma: no cover - exercised when CHIA is absent

    def ChiaFunction(*args: Any, **_kwargs: Any):
        def _decorate(fn: F) -> F:
            def chia_remote(*a: Any, **k: Any):
                return fn(*a, **k)

            fn.chia_remote = chia_remote  # type: ignore[attr-defined]
            fn.chia_remote_blocking = fn  # type: ignore[attr-defined]
            return fn

        if args and callable(args[0]) and len(args) == 1:
            return _decorate(args[0])
        return _decorate

    def get(value: Any) -> Any:
        return value


def dispatch(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    remote = getattr(fn, "chia_remote", None)
    if remote is None:
        return fn(*args, **kwargs)
    return get(remote(*args, **kwargs))

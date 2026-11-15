"""Homebound: a wormhole mapper for EVE Online."""
import concurrent.futures
from importlib.metadata import PackageNotFoundError, version

# Python 3.15 imports concurrent.futures' executors lazily (PEP 810). Once PySide6 6.12 (shiboken) is loaded,
# reading such a name as a module attribute returns the unresolved lazy object instead of the class, so
# asyncio.to_thread fails with "'lazy_import' object is not callable" (the server tests, which load both). A name
# resolved before that stays resolved, so resolve it here, before any part of Homebound imports PySide6.
concurrent.futures.ThreadPoolExecutor  # noqa: B018 - the attribute read is the point


def app_version() -> str:
    """Return Homebound's version from the package metadata (``pyproject.toml``), or "unknown" without it."""
    try:
        return version("homebound")
    except PackageNotFoundError:
        return "unknown"

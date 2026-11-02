"""Homebound: a wormhole mapper for EVE Online."""
from importlib.metadata import PackageNotFoundError, version


def app_version() -> str:
    """Return Homebound's version from the package metadata (``pyproject.toml``), or "unknown" without it."""
    try:
        return version("homebound")
    except PackageNotFoundError:
        return "unknown"

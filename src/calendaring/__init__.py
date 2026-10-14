"""One Python API for calendars and task lists."""

try:
    from ._version import __version__
except ImportError:
    from importlib.metadata import PackageNotFoundError, version

    try:
        __version__ = version("calendaring")
    except PackageNotFoundError:
        __version__ = "0+unknown"

__all__ = ["__version__"]

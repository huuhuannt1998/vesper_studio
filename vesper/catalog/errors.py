"""Errors raised by the device catalog. None of them is ever swallowed (spec §5 Errors)."""


class CatalogError(Exception):
    """Base class for catalog failures."""


class SnapshotError(CatalogError):
    """A snapshot is missing, incomplete, unsafe, or no longer matches its recorded hashes."""


class IngestError(CatalogError):
    """An upstream source could not be fetched, verified or parsed."""

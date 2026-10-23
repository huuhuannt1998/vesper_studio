"""Validate VDS records against the versioned JSON Schema (spec §2, `vesper/catalog/schema`)."""
from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files

from jsonschema import Draft202012Validator

VDS_VERSION = 1


@lru_cache(maxsize=1)
def vds_schema() -> dict:
    return json.loads(files("vesper.catalog").joinpath("vds.schema.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    schema = vds_schema()
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def validate_vds(record: dict) -> list[str]:
    """Human-readable violations, each prefixed with its JSON path; [] means the record is valid."""
    errors = sorted(_validator().iter_errors(record), key=lambda e: [str(p) for p in e.absolute_path])
    return [f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}" for e in errors]

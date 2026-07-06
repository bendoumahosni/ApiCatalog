"""Dependency providers.

Heavy objects (the converter, which parses the ontology, and the SHACL
validator, which parses ontology + shapes) are built once at startup and reused.
"""

from __future__ import annotations

from pathlib import Path

from app.config import Settings, get_settings
from app.converter.json_to_ttl import JsonToGraphConverter
from app.validation.json_contract import JsonContractValidator
from app.validation.shacl_validator import ShaclValidator

BASE_DIR = Path(__file__).resolve().parent.parent
ONTOLOGY_PATH = BASE_DIR / "ontology" / "tmf-service-core.ttl"
SHAPES_PATH = BASE_DIR / "shapes" / "shapes.ttl"

_converter: JsonToGraphConverter | None = None
_shacl: ShaclValidator | None = None
_json_contract: JsonContractValidator | None = None


def init_singletons() -> None:
    global _converter, _shacl, _json_contract
    _converter = JsonToGraphConverter(ONTOLOGY_PATH)
    _shacl = ShaclValidator(SHAPES_PATH, ONTOLOGY_PATH)
    _json_contract = JsonContractValidator()


def get_converter() -> JsonToGraphConverter:
    if _converter is None:
        raise RuntimeError("Converter not initialised.")
    return _converter


def get_shacl() -> ShaclValidator:
    if _shacl is None:
        raise RuntimeError("SHACL validator not initialised.")
    return _shacl


def get_json_contract() -> JsonContractValidator:
    if _json_contract is None:
        raise RuntimeError("JSON contract validator not initialised.")
    return _json_contract


def settings_dep() -> Settings:
    return get_settings()


def get_ingestion_service():
    from app.graphdb.client import get_client
    from app.services.ingestion import IngestionService
    return IngestionService(
        get_settings(), get_client(), get_converter(), get_shacl(), get_json_contract()
    )

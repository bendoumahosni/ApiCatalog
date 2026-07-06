"""JSON Schemas for the raw TMF input documents.

These validate the JSON **exactly as the user will send it** — using the real
JSON keys (``category``, ``serviceCandidate``, ``serviceSpecification``...) —
never the ontology/TTL predicate names (``organizedInto``, ``exposes``...). That
distinction is the whole point of this module: SHACL (``shapes/shapes.ttl``)
validates the *converted RDF graph* and necessarily speaks in TTL predicates;
this module validates the *JSON contract* and speaks in JSON keys, so a
malformed or misnamed input field is caught **before** conversion, rather than
relying on the converter accidentally producing something SHACL happens to
reject (or, worse, not rejecting it at all — e.g. a ``serviceSpecification``
object missing its ``id``, which the converter tolerates silently).

Two deliberate leniencies, tuned for bulk/automated ingestion of external
catalogues (OpenSlice):

* **Unknown keys are tolerated.** Producers carry provider-specific extra
  fields (``uuid`` is the canonical example) that this contract does not
  declare. Rejecting them would block valid documents for no safety benefit, so
  ``additionalProperties`` is ``True``. The one exception, kept strict, is a
  TTL/ontology predicate used as an input key (``organizedInto`` instead of
  ``category``): that is a real mistake and is still rejected with a precise
  message — see ``_close`` and ``_FORBIDDEN_TTL_KEYS``.

* **Optional fields accept ``null``.** Producers emit ``"href": null``,
  ``"@schemaLocation": null`` etc. for optional fields they have no value for.
  A null on an optional field is treated like an absent field. Required fields
  (``id``, ``name``) stay strict — a null id is a genuine problem. This is
  applied recursively to nested objects and array items by
  ``_nullable_optionals``.
"""

from __future__ import annotations

from typing import Any

# Ontology/TTL predicate names that must NEVER appear as JSON input keys. They
# are produced BY the converter from the real JSON keys below (organizedInto
# <- category, contains <- serviceCandidate, exposes <- serviceSpecification,
# specializes/dependsOn/requires <- serviceSpecRelationship[].relationshipType).
# Listing them as "not": {"required": [...]} turns a misnamed input (e.g. a
# document that writes "organizedInto" instead of "category") into an explicit,
# fail-fast JSON contract violation instead of a silently-ignored extra key.
_FORBIDDEN_TTL_KEYS = [
    "organizedInto", "contains", "exposes", "hasCategory", "hasSubCategory",
    "specializes", "dependsOn", "requires", "hasServiceSpecRelationship",
    "hasCharacteristic", "hasCharacteristicValue", "hasRelatedParty",
    "requireResourceSpecification", "hasServiceLevelSpecification", "hasConstraint",
]


def _make_type_nullable(type_value: Any) -> Any:
    """Return a JSON-schema ``type`` value that also permits null."""
    if isinstance(type_value, str):
        return [type_value, "null"] if type_value != "null" else type_value
    if isinstance(type_value, list):
        return type_value if "null" in type_value else [*type_value, "null"]
    return type_value


def _nullable_optionals(schema: Any) -> Any:
    """Recursively allow ``null`` for every OPTIONAL property in an object
    schema (a property not listed in that object's ``required``).

    Why: external producers (OpenSlice) routinely emit ``"href": null``,
    ``"@schemaLocation": null``, ``"version": null`` etc. for optional fields
    they have no value for. A strict ``"type": "string"`` rejects that null,
    even though an absent field would have been accepted -- a meaningless
    distinction for ingestion. Required fields (``id``, ``name``) are left
    strict on purpose: a null id IS a real problem.

    The transform walks nested objects and array ``items`` so the rule applies
    at every depth (references, validFor, characteristics, ...). Properties
    that already permit null, or use composition (anyOf/oneOf) or an empty
    schema ``{}``, are left untouched.
    """
    if not isinstance(schema, dict):
        return schema

    schema = dict(schema)
    required = set(schema.get("required", []))
    props = schema.get("properties")
    if isinstance(props, dict):
        new_props: dict[str, Any] = {}
        for key, subschema in props.items():
            subschema = _nullable_optionals(subschema)
            if (
                key not in required
                and isinstance(subschema, dict)
                and "type" in subschema
            ):
                subschema = dict(subschema)
                subschema["type"] = _make_type_nullable(subschema["type"])
            new_props[key] = subschema
        schema["properties"] = new_props

    if isinstance(schema.get("items"), dict):
        schema["items"] = _nullable_optionals(schema["items"])

    return schema


def _close(schema: dict[str, Any]) -> dict[str, Any]:
    """Allow unknown keys, but still explicitly forbid TTL predicate names.

    Bulk/automated ingestion (e.g. mirroring an external OpenSlice catalogue)
    routinely carries provider-specific extra fields the contract does not
    declare -- ``uuid`` is the canonical example. Rejecting those would block
    perfectly valid documents for no safety benefit, so unknown keys are
    tolerated (``additionalProperties: True``).

    The one thing we still refuse is a TTL/ontology predicate name used as an
    input key (``organizedInto`` instead of ``category``, ``exposes`` instead
    of ``serviceSpecification``, ...): that is almost certainly a producer
    sending ontology-side vocabulary by mistake, and silently ignoring it would
    drop the relationship. Those keys are kept forbidden via ``not: required``,
    which both rejects them and yields a precise message.
    """
    schema = dict(schema)
    schema["additionalProperties"] = True
    forbidden = [{"not": {"required": [key]}} for key in _FORBIDDEN_TTL_KEYS]
    existing_all_of = schema.get("allOf", [])
    schema["allOf"] = existing_all_of + forbidden
    return _nullable_optionals(schema)

# --- Reusable fragments -----------------------------------------------------

_REF = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "minLength": 1},
        "name": {"type": "string"},
        "version": {"type": "string"},
        "href": {"type": "string"},
    },
    "required": ["id"],
}

_SPEC_REF = {
    "type": "object",
    "description": (
        "Reference to a ServiceSpecification, as used by "
        "ServiceCandidate.serviceSpecification. Must carry both the id and the "
        "name of the specification."
    ),
    "properties": {
        "id": {"type": "string", "minLength": 1},
        "name": {"type": "string", "minLength": 1},
        "version": {"type": "string"},
        "href": {"type": "string"},
        "@referredType": {"type": "string"},
        "@type": {"type": "string"},
    },
    "required": ["id", "name"],
}

_VALID_FOR = {
    "type": "object",
    "properties": {
        "startDateTime": {"type": "string"},
        "endDateTime": {"type": "string"},
    },
}

_RELATED_PARTY = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "name": {"type": "string"},
            "role": {"type": "string"},
        },
        "required": ["id", "name"],
    },
}

# --- ServiceCatalog ----------------------------------------------------------

SERVICE_CATALOG_SCHEMA: dict[str, Any] = _close({
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "ServiceCatalog (input JSON contract)",
    "type": "object",
    "properties": {
        "id": {"type": "string", "minLength": 1},
        "name": {"type": "string", "minLength": 1},
        "description": {"type": "string"},
        "version": {"type": "string"},
        "href": {"type": "string"},
        "lastUpdate": {"type": "string"},
        "lifecycleStatus": {"type": "string"},
        "validFor": _VALID_FOR,
        "relatedParty": _RELATED_PARTY,
        # JSON key is "category" -- this is converted to tmf:organizedInto in
        # the TTL, but the JSON contract must never ask for "organizedInto".
        "category": {"type": "array", "items": _REF},
        "@type": {"type": "string"},
        "@schemaLocation": {"type": "string"},
        "@baseType": {"type": "string"},
    },
    "required": ["id", "name"],
})

# --- ServiceCategory -----------------------------------------------------

SERVICE_CATEGORY_SCHEMA: dict[str, Any] = _close({
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "ServiceCategory (input JSON contract)",
    "type": "object",
    "properties": {
        "id": {"type": "string", "minLength": 1},
        "name": {"type": "string", "minLength": 1},
        "description": {"type": "string"},
        "version": {"type": "string"},
        "href": {"type": "string"},
        "lastUpdate": {"type": "string"},
        "lifecycleStatus": {"type": "string"},
        "isRoot": {"type": "boolean"},
        "validFor": _VALID_FOR,
        # sub-categories
        "category": {"type": "array", "items": _REF},
        # JSON key is "serviceCandidate" -- converted to tmf:contains in TTL.
        "serviceCandidate": {"type": "array", "items": _REF},
        "@type": {"type": "string"},
        "@schemaLocation": {"type": "string"},
        "@baseType": {"type": "string"},
    },
    "required": ["id", "name"],
})

# --- ServiceCandidate ---------------------------------------------------

SERVICE_CANDIDATE_SCHEMA: dict[str, Any] = _close({
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "ServiceCandidate (input JSON contract)",
    "type": "object",
    "properties": {
        "id": {"type": "string", "minLength": 1},
        "name": {"type": "string", "minLength": 1},
        "description": {"type": "string"},
        "version": {"type": "string"},
        "href": {"type": "string"},
        "lastUpdate": {"type": "string"},
        "lifecycleStatus": {"type": "string"},
        "validFor": _VALID_FOR,
        "category": {
            "type": "array",
            "items": _REF,
            "minItems": 1,
            "description": "Categories this candidate is classified into.",
        },
        # JSON key is "serviceSpecification" -- converted to tmf:exposes in
        # TTL. Business rule: a candidate exposes EXACTLY one spec, and that
        # spec reference MUST carry both id and name (the user's requirement).
        "serviceSpecification": _SPEC_REF,
        "@type": {"type": "string"},
        "@schemaLocation": {"type": "string"},
        "@baseType": {"type": "string"},
    },
    "required": ["id", "name", "serviceSpecification"],
})

# --- ServiceSpecification (Generic/Concrete CFSS, RFSS) ------------------

_SERVICE_SPEC_RELATIONSHIP = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "name": {"type": "string"},
            "relationshipType": {"type": "string", "minLength": 1},
            "role": {"type": "string"},
            "serviceSpec": _SPEC_REF,
            "serviceSpecification": _SPEC_REF,
            "targetServiceSpecification": _SPEC_REF,
        },
        "required": ["relationshipType"],
        # A relationship must identify its target. Two accepted shapes:
        #  * nested target object: serviceSpec / serviceSpecification /
        #    targetServiceSpecification (classic TMF / our own templates);
        #  * flat / self-describing target: the target id (and name) carried
        #    directly on the relationship object, with no nested wrapper. This
        #    is the form emitted by OpenSlice
        #    (@type = "ServiceSpecificationRelationship"), where the target id,
        #    name and href live on the relationship itself.
        "anyOf": [
            {"required": ["serviceSpec"]},
            {"required": ["serviceSpecification"]},
            {"required": ["targetServiceSpecification"]},
            {"required": ["id"]},
        ],
    },
}

_CHAR_VALUE = {
    "type": "object",
    "properties": {
        "isDefault": {"type": ["boolean", "null"]},
        # The following are descriptive/optional TMF633 fields, not structural
        # references. They are routinely null or absent in real payloads (e.g.
        # a characteristic value that only carries a label, with no numeric
        # range). Being strict here would block perfectly valid ingestions for
        # no safety benefit, so all of them accept null.
        "rangeInterval": {"type": ["string", "null"]},
        "unitOfMeasure": {"type": ["string", "null"]},
        "valueFrom": {"type": ["number", "null"]},
        "valueTo": {"type": ["number", "null"]},
        "valueType": {"type": ["string", "null"]},
        "regex": {"type": ["string", "null"]},
        "value": {},
        "validFor": {"anyOf": [_VALID_FOR, {"type": "null"}]},
    },
}

_CHARACTERISTIC = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "minLength": 1},
        "description": {"type": "string"},
        "configurable": {"type": ["boolean", "null"]},
        "extensible": {"type": ["boolean", "null"]},
        "isUnique": {"type": ["boolean", "null"]},
        "maxCardinality": {"type": ["integer", "null"]},
        "minCardinality": {"type": ["integer", "null"]},
        "regex": {"type": ["string", "null"]},
        "valueType": {"type": ["string", "null"]},
        "serviceSpecCharRelationship": {"type": "array"},
        "serviceSpecCharacteristicValue": {"type": "array", "items": _CHAR_VALUE},
    },
    "required": ["name"],
}

SERVICE_SPECIFICATION_SCHEMA: dict[str, Any] = _close({
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "ServiceSpecification (GenericCFSS / ConcreteCFSS / RFSS) input JSON contract",
    "type": "object",
    "properties": {
        "id": {"type": "string", "minLength": 1},
        "name": {"type": "string", "minLength": 1},
        "description": {"type": "string"},
        "version": {"type": "string"},
        "href": {"type": "string"},
        "isBundle": {"type": "boolean"},
        "lifecycleStatus": {"type": "string"},
        "lastUpdate": {"type": "string"},
        "validFor": _VALID_FOR,
        "attachment": {"type": "array"},
        "relatedParty": _RELATED_PARTY,
        "constraint": {"type": "array"},
        "resourceSpecification": {"type": "array"},
        "serviceLevelSpecification": {"type": "array"},
        "serviceSpecCharacteristic": {"type": "array", "items": _CHARACTERISTIC},
        "serviceSpecRelationship": _SERVICE_SPEC_RELATIONSHIP,
        "targetServiceSchema": {},
        "@type": {"type": "string"},
        "@referredType": {"type": "string"},
        "@baseType": {"type": "string"},
        "@schemaLocation": {"type": "string"},
    },
    "required": ["id", "name"],
})

# --- Registry ----------------------------------------------------------------

from app.config import EntityType  # noqa: E402  (avoid import cycle at module load)

SCHEMA_BY_ENTITY_TYPE: dict[EntityType, dict[str, Any]] = {
    EntityType.SERVICE_CATALOG: SERVICE_CATALOG_SCHEMA,
    EntityType.SERVICE_CATEGORY: SERVICE_CATEGORY_SCHEMA,
    EntityType.SERVICE_CANDIDATE: SERVICE_CANDIDATE_SCHEMA,
    EntityType.CONCRETE_CFSS: SERVICE_SPECIFICATION_SCHEMA,
    EntityType.GENERIC_CFSS: SERVICE_SPECIFICATION_SCHEMA,
    EntityType.CONCRETE_RFSS: SERVICE_SPECIFICATION_SCHEMA,
    EntityType.PROFILE_MANAGEMENT: SERVICE_SPECIFICATION_SCHEMA,
}
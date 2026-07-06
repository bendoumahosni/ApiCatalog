"""Validate the raw input JSON against its contract schema, before conversion.

This is the layer that enforces "the key in the JSON must be `category` /
`serviceSpecification`, not `organizedInto` / `exposes`" — i.e. it checks the
document the user actually sends, in its own vocabulary, rather than the RDF
produced afterwards.
"""

from __future__ import annotations

from typing import Any, Dict

from jsonschema import Draft7Validator

from app.config import EntityType
from app.core.exceptions import ValidationError
from app.validation.json_schemas import SCHEMA_BY_ENTITY_TYPE


class JsonContractValidator:
    def __init__(self) -> None:
        self._validators = {
            entity_type: Draft7Validator(schema)
            for entity_type, schema in SCHEMA_BY_ENTITY_TYPE.items()
        }

    def validate(self, data: Dict[str, Any], entity_type: EntityType) -> None:
        validator = self._validators[entity_type]
        errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
        if not errors:
            return

        details: list[dict[str, str]] = []
        for err in errors:
            field = "/".join(str(p) for p in err.path) or "(root)"
            message = err.message

            # A forbidden TTL/ontology predicate key (organizedInto, exposes,
            # ...) is encoded as `not: {required: [key]}`, whose raw jsonschema
            # message echoes the whole document. Rewrite it into a precise,
            # actionable message naming the offending key and its JSON
            # counterpart. (Unknown keys are now allowed, so this is the only
            # signal for a misnamed-relationship key.)
            ttl_key = self._forbidden_key_in(err)
            if ttl_key is not None:
                details.append({
                    "field": ttl_key,
                    "message": (
                        f"'{ttl_key}' is an ontology/TTL predicate name and must "
                        f"not be used as an input key. Use the corresponding JSON "
                        f"key instead (e.g. 'category' not 'organizedInto', "
                        f"'serviceSpecification' not 'exposes')."
                    ),
                })
                continue

            details.append({"field": field, "message": message})

        # Deduplicate while preserving order (the same TTL key can surface from
        # both the top-level allOf clause and a nested one).
        seen: set[tuple[str, str]] = set()
        deduped = [
            d for d in details
            if (d["field"], d["message"]) not in seen and not seen.add((d["field"], d["message"]))
        ]

        raise ValidationError(
            f"Input JSON does not match the expected '{entity_type.value}' contract.",
            details=deduped,
        )

    @staticmethod
    def _forbidden_key_in(err) -> str | None:
        """If this error is a forbidden-TTL-key clause, return the key name."""
        validator_value = getattr(err, "validator_value", None)
        # err.validator == "not", err.validator_value == {"required": [key]}
        if getattr(err, "validator", None) == "not" and isinstance(validator_value, dict):
            req = validator_value.get("required")
            if isinstance(req, list) and len(req) == 1:
                return req[0]
        return None

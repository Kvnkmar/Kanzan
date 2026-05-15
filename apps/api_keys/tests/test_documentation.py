"""
Tests that drf-spectacular emits a schema reflecting the API-key feature.

What we assert:
- ``components.securitySchemes.ApiKeyAuth`` is present and shaped correctly
  (``type: apiKey, in: header, name: Authorization``) — proves
  ``apps.api_keys.extensions.APIKeyAuthScheme`` is discovered.
- ``/api/v1/api-keys/`` is documented as both GET (list) and POST (create);
  the create 201 response references the cleartext-bearing serializer
  (``APIKeyCreatedResponseSerializer`` with the ``key`` field).
- The ``API Keys`` and ``Tickets`` tags are present in the global ``tags``
  list — proves A3's ``extend_schema(tags=[...])`` decorations made it into
  the spec.

Schema is fetched via the Django test client against ``/api/schema/`` with
JSON format (drf-spectacular defaults to YAML; we force JSON for parsing
convenience). ``/api/schema/`` is exempt from TenantMiddleware so no Host
header juggling is required.
"""

from __future__ import annotations

import json

import pytest
from django.test import Client


pytestmark = pytest.mark.django_db


SCHEMA_URL = "/api/schema/"


def _fetch_schema() -> dict:
    """
    Fetch the OpenAPI schema via the bare-domain Django test client and parse
    it as JSON. drf-spectacular emits YAML by default; ``?format=json`` flips
    it to JSON, which is trivially parseable from the stdlib.
    """
    client = Client()
    resp = client.get(SCHEMA_URL, {"format": "json"})
    assert resp.status_code == 200, (
        f"Schema endpoint returned {resp.status_code}: {resp.content[:200]!r}"
    )
    body = resp.content.decode("utf-8")
    return json.loads(body)


# ── Auth scheme registration ─────────────────────────────────────────


def test_schema_includes_api_key_auth_scheme():
    """
    ``components.securitySchemes.ApiKeyAuth`` is registered and points at
    the Authorization header.
    """
    schema = _fetch_schema()
    schemes = (
        schema.get("components", {}).get("securitySchemes", {})
    )
    assert "ApiKeyAuth" in schemes, (
        f"ApiKeyAuth missing from securitySchemes. Found: {list(schemes.keys())}"
    )
    api_key_auth = schemes["ApiKeyAuth"]
    assert api_key_auth.get("type") == "apiKey"
    assert api_key_auth.get("in") == "header"
    assert api_key_auth.get("name") == "Authorization"


# ── Endpoint documentation ───────────────────────────────────────────


def test_schema_includes_api_keys_endpoints():
    """
    ``/api/v1/api-keys/`` should be present with both GET and POST. The
    POST 201 response should reference a schema mentioning ``key`` (the
    one-time cleartext field).
    """
    schema = _fetch_schema()
    paths = schema.get("paths", {})
    api_keys_path = paths.get("/api/v1/api-keys/")
    assert api_keys_path is not None, (
        f"/api/v1/api-keys/ not in paths. Paths: {sorted(paths.keys())[:20]}…"
    )

    assert "get" in api_keys_path, "GET /api/v1/api-keys/ undocumented"
    assert "post" in api_keys_path, "POST /api/v1/api-keys/ undocumented"

    # The create 201 response should reference the cleartext-bearing
    # serializer (key field) somewhere in its schema. drf-spectacular
    # resolves nested $refs to component schemas; we follow the ref.
    post_op = api_keys_path["post"]
    responses = post_op.get("responses", {})
    assert "201" in responses, (
        f"POST /api/v1/api-keys/ missing 201 response: {list(responses.keys())}"
    )

    # Either the response schema inlines `key`, or it $refs a component that does.
    response_201 = responses["201"]
    schema_blob = json.dumps(response_201)

    # Walk the components if a $ref is used.
    if "$ref" in schema_blob:
        component_schemas = schema.get("components", {}).get("schemas", {})
        # APIKeyCreatedResponseSerializer is the expected component.
        candidate_names = [
            n for n in component_schemas
            if "ApiKeyCreatedResponse" in n or "APIKeyCreatedResponse" in n
        ]
        # Inline check covers either the component-ref or inline schema cases.
        haystack = schema_blob + "".join(
            json.dumps(component_schemas[name]) for name in candidate_names
        )
        assert '"key"' in haystack, (
            "Cleartext `key` field not documented in 201 response. "
            f"Components found: {candidate_names}"
        )
    else:
        assert '"key"' in schema_blob, (
            f"Cleartext `key` field not in inline 201 response: {response_201}"
        )


# ── Tag presence ─────────────────────────────────────────────────────


def _tag_names(schema: dict) -> list[str]:
    """
    Collect every tag name referenced in the schema — both the global
    ``tags`` array and any operation-level ``tags`` field.
    """
    names: set[str] = set()
    for tag in schema.get("tags", []) or []:
        if isinstance(tag, dict) and tag.get("name"):
            names.add(tag["name"])
        elif isinstance(tag, str):
            names.add(tag)

    for path_item in schema.get("paths", {}).values():
        if not isinstance(path_item, dict):
            continue
        for method, op in path_item.items():
            if method in ("parameters",) or not isinstance(op, dict):
                continue
            for tag in op.get("tags", []) or []:
                names.add(tag)
    return sorted(names)


def test_schema_includes_tickets_tag():
    """The Tickets tag (added by A3 extend_schema decoration) appears."""
    schema = _fetch_schema()
    tags = _tag_names(schema)
    assert "Tickets" in tags, f"'Tickets' tag missing. Got: {tags}"


def test_schema_includes_api_keys_tag():
    """The API Keys tag is present (set by the APIKeyViewSet decoration)."""
    schema = _fetch_schema()
    tags = _tag_names(schema)
    assert "API Keys" in tags, f"'API Keys' tag missing. Got: {tags}"

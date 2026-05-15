"""
drf-spectacular extension that documents :class:`APIKeyAuthentication`
in the generated OpenAPI 3.0 schema as the ``ApiKeyAuth`` security scheme.

Once imported, drf-spectacular auto-discovers this subclass and Swagger UI's
"Authorize" button gains an Api-Key option alongside the existing JWT bearer.
"""

from __future__ import annotations

from drf_spectacular.extensions import OpenApiAuthenticationExtension


class APIKeyAuthScheme(OpenApiAuthenticationExtension):
    target_class = "apps.api_keys.authentication.APIKeyAuthentication"
    name = "ApiKeyAuth"
    match_subclasses = False
    priority = 0

    def get_security_definition(self, auto_schema):
        return {
            "type": "apiKey",
            "in": "header",
            "name": "Authorization",
            "description": (
                "Tenant-scoped API key. Format: `Api-Key kz_live_<slug>_<secret>`. "
                "Generate keys at Settings → Developer & Integrations → API Keys "
                "(admin-only)."
            ),
        }

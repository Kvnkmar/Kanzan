# Protected media serving

User-uploaded files under `MEDIA_ROOT` (ticket/comment attachments, call
recordings, customer email attachments) are **tenant-private**. They are served
through `apps.attachments.media_views.serve_protected_media`, which authorizes
every request by authentication + tenant ownership before any bytes go out.

`/media/` is **no longer** a static directory. In production it must be proxied
to Django so the authorization runs.

## Dev / tests

No configuration needed. `USE_X_ACCEL_REDIRECT` defaults to `False`, so Django
streams the file via `FileResponse` after authorizing.

## Production (nginx + X-Accel-Redirect)

Set in the environment:

```
USE_X_ACCEL_REDIRECT=True
X_ACCEL_MEDIA_PREFIX=/protected_media/
```

Django then authorizes the request and returns an empty `200` with an
`X-Accel-Redirect: /protected_media/<path>` header; nginx serves the actual
bytes from an **internal** location (zero Python in the byte path):

```nginx
# Public requests for /media/ go to Django for authorization.
location /media/ {
    proxy_pass http://kanzen_app;            # gunicorn/uvicorn upstream
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
}

# Internal location nginx uses to actually serve the file after Django says OK.
# `internal` makes it unreachable directly from the outside.
location /protected_media/ {
    internal;
    alias /srv/kanzen/media/;                 # = MEDIA_ROOT, with trailing slash
}
```

Public assets (tenant logos under `tenants/logos/`, KB portal images under
`tenants/knowledge/`) are served without auth by the view so login / landing /
public-KB pages keep working.

> Follow-up: the legacy raw-`/media/` location block (if any) must be removed
> from the nginx config so it can no longer bypass Django.

## Deploy-time safety checks

Run this as a blocking step in the production deploy pipeline:

```
python manage.py check --deploy --fail-level ERROR
```

It enforces the custom checks in `main/checks.py`:

* `kanzen.E001` (Error) — `DEBUG` must be `False` in production.
* `kanzen.W001` (Warning) — nudges enabling `USE_X_ACCEL_REDIRECT` for media.

plus Django's built-in deployment checks (secure cookies, HSTS, etc.).

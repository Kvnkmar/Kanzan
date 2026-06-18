from .base import *  # noqa: F401, F403

import environ
import os

env = environ.Env()

# Load environment-specific settings.
# SECURITY: default is False so an UNSET DJANGO_DEBUG fails safe to prod.py.
# A missing env var in production must never silently load dev.py (which sets
# DEBUG=True and ALLOWED_HOSTS=["*"]). This aligns with base.py's DEBUG default.
if env.bool("DJANGO_DEBUG", default=False):
    try:
        from .dev import *  # noqa: F401, F403
    except ImportError:
        pass
else:
    try:
        from .prod import *  # noqa: F401, F403
    except ImportError:
        pass

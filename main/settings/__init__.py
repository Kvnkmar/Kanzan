from .base import *  # noqa: F401, F403

import environ
import os

env = environ.Env()

# Load environment-specific settings
if env.bool("DJANGO_DEBUG", default=True):
    try:
        from .dev import *  # noqa: F401, F403
    except ImportError:
        pass
else:
    try:
        from .prod import *  # noqa: F401, F403
    except ImportError:
        pass

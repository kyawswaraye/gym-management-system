from .base import *  # noqa: F403

DEBUG = True
# Password login is enough on a developer machine. Owner and platform
# step-up checks follow the same switch. This module is not production.
MFA_ENFORCED = False

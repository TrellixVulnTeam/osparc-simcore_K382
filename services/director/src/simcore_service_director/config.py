"""Director service configuration
"""

import logging
import os
from distutils.util import strtobool
from typing import Dict, Optional

from servicelib.client_session import (  # pylint: disable=no-name-in-module
    APP_CLIENT_SESSION_KEY,
)

LOGLEVEL_STR = os.environ.get("LOGLEVEL", "WARNING").upper()
log_level = getattr(logging, LOGLEVEL_STR)
logging.basicConfig(
    level=log_level,
    format="%(levelname)s:%(name)s-%(lineno)d: %(message)s",
)
logging.root.setLevel(log_level)

# TODO: debug mode is define by the LOG-LEVEL and not the other way around. I leave it like that for the moment ...
DEBUG_MODE = log_level == logging.DEBUG

API_VERSION: str = "v0"
API_ROOT: str = "api"


def _from_env_with_default(env: str, python_type, default):
    env_value = python_type(os.environ.get(env, default))

    return default if env_value <= 0 else env_value


# NOTE: these settings must be in sync with settings-library: comp_services.py (since the director is frozen)
DEFAULT_MAX_NANO_CPUS: int = _from_env_with_default(
    "DEFAULT_MAX_NANO_CPUS", int, 1 * pow(10, 9)
)
DEFAULT_MAX_MEMORY: int = _from_env_with_default(
    "DEFAULT_MAX_MEMORY", int, 2 * pow(1024, 3)
)  # 2 GiB

SERVICE_RUNTIME_SETTINGS: str = "simcore.service.settings"
SERVICE_REVERSE_PROXY_SETTINGS: str = "simcore.service.reverse-proxy-settings"
SERVICE_RUNTIME_BOOTSETTINGS: str = "simcore.service.bootsettings"

ORG_LABELS_TO_SCHEMA_LABELS = {
    "org.label-schema.build-date": "build_date",
    "org.label-schema.vcs-ref": "vcs_ref",
    "org.label-schema.vcs-url": "vcs_url",
}

DIRECTOR_REGISTRY_CACHING: bool = strtobool(
    os.environ.get("DIRECTOR_REGISTRY_CACHING", "True")
)
DIRECTOR_REGISTRY_CACHING_TTL: int = int(
    os.environ.get("DIRECTOR_REGISTRY_CACHING_TTL", 15 * 60)
)

DIRECTOR_SERVICES_CUSTOM_CONSTRAINTS: str = os.environ.get(
    "DIRECTOR_SERVICES_CUSTOM_CONSTRAINTS", ""
)

# for passing self-signed certificate to spawned services
DIRECTOR_SELF_SIGNED_SSL_SECRET_ID: str = os.environ.get(
    "DIRECTOR_SELF_SIGNED_SSL_SECRET_ID", ""
)
DIRECTOR_SELF_SIGNED_SSL_SECRET_NAME: str = os.environ.get(
    "DIRECTOR_SELF_SIGNED_SSL_SECRET_NAME", ""
)
DIRECTOR_SELF_SIGNED_SSL_FILENAME: str = os.environ.get(
    "DIRECTOR_SELF_SIGNED_SSL_FILENAME", ""
)

DIRECTOR_SERVICES_RESTART_POLICY_MAX_ATTEMPTS: int = int(
    os.environ.get("DIRECTOR_SERVICES_RESTART_POLICY_MAX_ATTEMPTS", 10)
)
DIRECTOR_SERVICES_RESTART_POLICY_DELAY_S: int = int(
    os.environ.get("DIRECTOR_SERVICES_RESTART_POLICY_DELAY_S", 12)
)

DIRECTOR_SERVICES_STATE_MONITOR_S: int = int(
    os.environ.get("DIRECTOR_SERVICES_STATE_MONITOR_S", 8)
)

TRAEFIK_SIMCORE_ZONE: str = os.environ.get(
    "TRAEFIK_SIMCORE_ZONE", "internal_simcore_stack"
)
APP_REGISTRY_CACHE_DATA_KEY: str = __name__ + "_registry_cache_data"

REGISTRY_AUTH: bool = strtobool(os.environ.get("REGISTRY_AUTH", "False"))
REGISTRY_USER: str = os.environ.get("REGISTRY_USER", "")
REGISTRY_PW: str = os.environ.get("REGISTRY_PW", "")
REGISTRY_URL: str = os.environ.get("REGISTRY_URL", "")
REGISTRY_PATH: str = os.environ.get("REGISTRY_PATH", None) or os.environ.get(
    "REGISTRY_URL", ""
)  # This is useful in case of a local registry, where the registry url (path) is relative to the host docker engine
REGISTRY_SSL: bool = strtobool(os.environ.get("REGISTRY_SSL", "True"))

EXTRA_HOSTS_SUFFIX: str = os.environ.get("EXTRA_HOSTS_SUFFIX", "undefined")

# these are the envs passed to the dynamic services by default
SERVICES_DEFAULT_ENVS: Dict[str, str] = {
    "POSTGRES_ENDPOINT": os.environ.get(
        "POSTGRES_ENDPOINT", "undefined postgres endpoint"
    ),
    "POSTGRES_USER": os.environ.get("POSTGRES_USER", "undefined postgres user"),
    "POSTGRES_PASSWORD": os.environ.get(
        "POSTGRES_PASSWORD", "undefined postgres password"
    ),
    "POSTGRES_DB": os.environ.get("POSTGRES_DB", "undefined postgres db"),
    "STORAGE_ENDPOINT": os.environ.get(
        "STORAGE_ENDPOINT", "undefined storage endpoint"
    ),
}

# some services need to know the published host to be functional (paraview)
# TODO: please review if needed
PUBLISHED_HOST_NAME: str = os.environ.get("PUBLISHED_HOST_NAME", "")

SWARM_STACK_NAME: str = os.environ.get("SWARM_STACK_NAME", "undefined-please-check")

# used when in devel mode vs release mode
NODE_SCHEMA_LOCATION: str = os.environ.get(
    "NODE_SCHEMA_LOCATION", f"{API_ROOT}/{API_VERSION}/schemas/node-meta-v0.0.1.json"
)
# used to find the right network name
SIMCORE_SERVICES_NETWORK_NAME: Optional[str] = os.environ.get(
    "SIMCORE_SERVICES_NETWORK_NAME"
)
# useful when developing with an alternative registry namespace
SIMCORE_SERVICES_PREFIX: str = os.environ.get(
    "SIMCORE_SERVICES_PREFIX", "simcore/services"
)

# monitoring
# NOTE: keep disabled for unit-testing otherwise mocks will not hold
MONITORING_ENABLED: bool = strtobool(os.environ.get("MONITORING_ENABLED", "False"))

# tracing
TRACING_ENABLED: bool = strtobool(os.environ.get("TRACING_ENABLED", "True"))
TRACING_ZIPKIN_ENDPOINT: str = os.environ.get(
    "TRACING_ZIPKIN_ENDPOINT", "http://jaeger:9411"
)

# resources: not taken from servicelib.resources since the director uses a fixed hash of that library
CPU_RESOURCE_LIMIT_KEY = "SIMCORE_NANO_CPUS_LIMIT"
MEM_RESOURCE_LIMIT_KEY = "SIMCORE_MEMORY_BYTES_LIMIT"

__all__ = ["APP_CLIENT_SESSION_KEY"]

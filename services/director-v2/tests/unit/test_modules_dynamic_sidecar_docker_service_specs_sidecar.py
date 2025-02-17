# pylint: disable=redefined-outer-name
# pylint: disable=unused-argument
# pylint: disable=unused-variable

from typing import Any

from pytest import MonkeyPatch
from simcore_service_director_v2.core.settings import AppSettings
from simcore_service_director_v2.models.schemas.dynamic_services.scheduler import (
    SchedulerData,
)
from simcore_service_director_v2.modules.dynamic_sidecar.docker_service_specs.sidecar import (
    _get_environment_variables,
)

# PLEASE keep alphabetical to simplify debugging
EXPECTED_DYNAMIC_SIDECAR_ENV_VAR_NAMES = {
    "DY_SIDECAR_NODE_ID",
    "DY_SIDECAR_PATH_INPUTS",
    "DY_SIDECAR_PATH_OUTPUTS",
    "DY_SIDECAR_PROJECT_ID",
    "DY_SIDECAR_RUN_ID",
    "DY_SIDECAR_STATE_EXCLUDE",
    "DY_SIDECAR_STATE_PATHS",
    "DY_SIDECAR_USER_ID",
    "DYNAMIC_SIDECAR_COMPOSE_NAMESPACE",
    "DYNAMIC_SIDECAR_LOG_LEVEL",
    "POSTGRES_DB",
    "POSTGRES_ENDPOINT",
    "POSTGRES_HOST",
    "POSTGRES_PASSWORD",
    "POSTGRES_PORT",
    "POSTGRES_USER",
    "R_CLONE_ENABLED",
    "R_CLONE_PROVIDER",
    "RABBIT_CHANNELS",
    "RABBIT_HOST",
    "RABBIT_PASSWORD",
    "RABBIT_PORT",
    "RABBIT_USER",
    "REGISTRY_AUTH",
    "REGISTRY_PATH",
    "REGISTRY_PW",
    "REGISTRY_SSL",
    "REGISTRY_URL",
    "REGISTRY_USER",
    "S3_ACCESS_KEY",
    "S3_BUCKET_NAME",
    "S3_ENDPOINT",
    "S3_SECRET_KEY",
    "S3_SECURE",
    "SC_BOOT_MODE",
    "SIMCORE_HOST_NAME",
    "STORAGE_HOST",
    "STORAGE_PORT",
}


def test_dynamic_sidecar_env_vars(
    monkeypatch: MonkeyPatch,
    scheduler_data_from_http_request: SchedulerData,
    project_env_devel_environment: dict[str, Any],
):
    app_settings = AppSettings.create_from_envs()

    dynamic_sidecar_env_vars = _get_environment_variables(
        "compose_namespace", scheduler_data_from_http_request, app_settings
    )
    print("dynamic_sidecar_env_vars:", dynamic_sidecar_env_vars)

    assert set(dynamic_sidecar_env_vars) == EXPECTED_DYNAMIC_SIDECAR_ENV_VAR_NAMES

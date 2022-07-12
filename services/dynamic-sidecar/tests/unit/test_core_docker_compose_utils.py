# pylint: disable=redefined-outer-name
# pylint: disable=unused-argument
# pylint: disable=unused-variable

from typing import Any

import pytest
import yaml
from faker import Faker
from pytest_simcore.helpers.typing_env import EnvVarsDict
from simcore_service_dynamic_sidecar.core.docker_compose_utils import (
    docker_compose_config,
    docker_compose_down,
    docker_compose_restart,
    docker_compose_rm,
    docker_compose_up,
)
from simcore_service_dynamic_sidecar.core.settings import DynamicSidecarSettings
from simcore_service_dynamic_sidecar.core.utils import CommandResult

COMPOSE_SPEC_SAMPLE = {
    "version": "3.8",
    "services": {
        "my-test-container": {
            "environment": [
                "DY_SIDECAR_PATH_INPUTS=/work/inputs",
                "DY_SIDECAR_PATH_OUTPUTS=/work/outputs",
                'DY_SIDECAR_STATE_PATHS=["/work/workspace"]',
            ],
            "working_dir": "/work",
            "image": "busybox",
            "command": f"sh -c \"echo 'setup {__name__}'; sleep 60; echo 'teardown {__name__}'\"",
        }
    },
}


@pytest.fixture
def compose_spec_yaml(faker: Faker) -> str:
    return yaml.safe_dump(COMPOSE_SPEC_SAMPLE, indent=1)


@pytest.mark.parametrize("with_restart", (True, False))
async def test_docker_compose_workflow(
    compose_spec_yaml: str, mock_environment: EnvVarsDict, with_restart: bool
):
    settings = DynamicSidecarSettings.create_from_envs()

    def _print_result(r: CommandResult):
        assert r.elapsed and r.elapsed > 0
        print(f"{r.command:*^100}", "\nELAPSED:", r.elapsed)

    compose_spec: dict[str, Any] = yaml.safe_load(compose_spec_yaml)
    print("compose_spec:\n", compose_spec)

    # validates specs
    r = await docker_compose_config(
        compose_spec_yaml,
        settings,
        10,
    )
    _print_result(r)
    assert r.success, r.message

    # removes all stopped containers from specs
    r = await docker_compose_rm(
        compose_spec_yaml,
        settings,
    )
    _print_result(r)
    assert r.success, r.message

    # creates and starts in detached mode
    r = await docker_compose_up(
        compose_spec_yaml,
        settings,
        10,
    )
    _print_result(r)
    assert r.success, r.message

    if with_restart:
        # restarts
        r = await docker_compose_restart(
            compose_spec_yaml,
            settings,
            10,
        )
        _print_result(r)
        assert r.success, r.message

    # stops and removes
    r = await docker_compose_down(
        compose_spec_yaml,
        settings,
        10,
    )

    _print_result(r)
    assert r.success, r.message

    # full cleanup
    r = await docker_compose_rm(
        compose_spec_yaml,
        settings,
    )

    _print_result(r)
    assert r.success, r.message

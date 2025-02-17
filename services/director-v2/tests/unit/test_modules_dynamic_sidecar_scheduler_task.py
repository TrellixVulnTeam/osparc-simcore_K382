# pylint: disable=redefined-outer-name
# pylint: disable=unused-argument
# pylint: disable=protected-access

import asyncio
import re
from dataclasses import dataclass
from typing import Final, Iterator

import httpx
import pytest
import respx
from _pytest.monkeypatch import MonkeyPatch
from fastapi import FastAPI
from pytest import FixtureRequest
from pytest_mock import MockerFixture
from pytest_simcore.helpers.typing_env import EnvVarsDict
from pytest_simcore.helpers.utils_envs import setenvs_from_dict
from respx.router import MockRouter
from simcore_service_director_v2.models.schemas.dynamic_services.scheduler import (
    SchedulerData,
)
from simcore_service_director_v2.modules.dynamic_sidecar.api_client._public import (
    DynamicSidecarClient,
)
from simcore_service_director_v2.modules.dynamic_sidecar.scheduler import _utils, task
from simcore_service_director_v2.modules.dynamic_sidecar.scheduler.events import (
    REGISTERED_EVENTS,
    DynamicSchedulerEvent,
)
from simcore_service_director_v2.modules.dynamic_sidecar.scheduler.task import (
    DynamicSidecarsScheduler,
)

SCHEDULER_INTERVAL_SECONDS: Final[float] = 0.1


@pytest.fixture
def mock_env(
    mock_env: EnvVarsDict,
    monkeypatch: MonkeyPatch,
    simcore_services_network_name: str,
    docker_swarm: None,
    mock_docker_api: None,
) -> None:
    disabled_services_envs = {
        "S3_ENDPOINT": "",
        "S3_ACCESS_KEY": "",
        "S3_SECRET_KEY": "",
        "S3_BUCKET_NAME": "",
        "POSTGRES_HOST": "",
        "POSTGRES_USER": "",
        "POSTGRES_PASSWORD": "",
        "POSTGRES_DB": "",
    }
    setenvs_from_dict(monkeypatch, disabled_services_envs)

    monkeypatch.setenv("DIRECTOR_V2_DYNAMIC_SCHEDULER_ENABLED", "true")
    monkeypatch.setenv(
        "DIRECTOR_V2_DYNAMIC_SCHEDULER_INTERVAL_SECONDS",
        f"{SCHEDULER_INTERVAL_SECONDS}",
    )


@pytest.fixture
def scheduler_data(scheduler_data_from_http_request: SchedulerData) -> SchedulerData:
    scheduler_data_from_http_request.docker_node_id = "test_docker_node_id"
    return scheduler_data_from_http_request


@pytest.fixture
def mock_containers_docker_status(
    scheduler_data: SchedulerData,
) -> Iterator[MockRouter]:
    service_endpoint = scheduler_data.endpoint
    with respx.mock as mock:
        mock.get(
            re.compile(
                rf"^http://{scheduler_data.service_name}:{scheduler_data.port}/v1/containers\?only_status=true"
            ),
            name="containers_docker_status",
        ).mock(httpx.Response(200, json={}))
        mock.get(f"{service_endpoint}/health", name="is_healthy").respond(
            json=dict(is_healthy=True)
        )

        yield mock


@pytest.fixture
def mock_dynamic_sidecar_client(mocker: MockerFixture) -> None:
    mocker.patch.object(DynamicSidecarClient, "push_service_output_ports")
    mocker.patch.object(DynamicSidecarClient, "save_service_state")
    mocker.patch.object(DynamicSidecarClient, "stop_service")


@pytest.fixture
def mock_is_dynamic_sidecar_stack_missing(mocker: MockerFixture) -> None:
    async def _return_false(*args, **kwargs) -> bool:
        return False

    mocker.patch.object(
        task, "is_dynamic_sidecar_stack_missing", side_effect=_return_false
    )


@pytest.fixture
def scheduler(
    mock_containers_docker_status: MockRouter,
    mock_dynamic_sidecar_client: None,
    mock_is_dynamic_sidecar_stack_missing: None,
    minimal_app: FastAPI,
) -> DynamicSidecarsScheduler:
    return minimal_app.state.dynamic_sidecar_scheduler


class ACounter:
    def __init__(self, start: int = 0) -> None:
        self.start = start
        self.count = start

    def increment(self) -> None:
        self.count += 1


@pytest.fixture(params=[True, False])
def error_raised_by_saving_state(request: FixtureRequest) -> bool:
    return request.param  # type: ignore


@dataclass
class UseCase:
    can_save: bool
    wait_for_manual_intervention_after_error: bool
    outcome_service_removed: bool


@pytest.fixture(
    params=[
        UseCase(
            can_save=False,
            wait_for_manual_intervention_after_error=False,
            outcome_service_removed=True,
        ),
        UseCase(
            can_save=False,
            wait_for_manual_intervention_after_error=True,
            outcome_service_removed=False,
        ),
        UseCase(
            can_save=True,
            wait_for_manual_intervention_after_error=False,
            outcome_service_removed=True,
        ),
        UseCase(
            can_save=True,
            wait_for_manual_intervention_after_error=True,
            outcome_service_removed=False,
        ),
    ]
)
def use_case(request) -> UseCase:
    return request.param  # type: ignore


@pytest.fixture
def mocked_dynamic_scheduler_events(
    error_raised_by_saving_state: bool, use_case: UseCase
) -> ACounter:
    counter = ACounter()

    class AlwaysTriggersDynamicSchedulerEvent(DynamicSchedulerEvent):
        @classmethod
        async def will_trigger(
            cls, app: FastAPI, scheduler_data: SchedulerData
        ) -> bool:
            return True

        @classmethod
        async def action(cls, app: FastAPI, scheduler_data: SchedulerData) -> None:
            counter.increment()
            if error_raised_by_saving_state:
                # emulate the error was generated while saving the state
                scheduler_data.dynamic_sidecar.service_removal_state.can_save = (
                    use_case.can_save
                )
                scheduler_data.dynamic_sidecar.wait_for_manual_intervention_after_error = (
                    use_case.wait_for_manual_intervention_after_error
                )
            raise RuntimeError("Failed as planned")

    test_defined_scheduler_events: list[type[DynamicSchedulerEvent]] = [
        AlwaysTriggersDynamicSchedulerEvent
    ]

    # replace REGISTERED EVENTS
    REGISTERED_EVENTS.clear()
    for event in test_defined_scheduler_events:
        REGISTERED_EVENTS.append(event)

    return counter


@pytest.fixture
def mock_remove_calls(mocker: MockerFixture) -> None:
    mocker.patch.object(_utils, "remove_volumes_from_node")


async def test_skip_observation_cycle_after_error(
    docker_swarm: None,
    minimal_app: FastAPI,
    scheduler: DynamicSidecarsScheduler,
    scheduler_data: SchedulerData,
    mocked_dynamic_scheduler_events: ACounter,
    error_raised_by_saving_state: bool,
    use_case: UseCase,
    mock_remove_calls: None,
):
    # add a task, emulate an error make sure no observation cycle is
    # being triggered again
    assert mocked_dynamic_scheduler_events.count == 0
    await scheduler.add_service(scheduler_data)
    # check it is being tracked
    assert scheduler_data.node_uuid in scheduler._inverse_search_mapping

    # ensure observation cycle triggers a lot
    await asyncio.sleep(SCHEDULER_INTERVAL_SECONDS * 10)
    # only expect the event to be triggered once, when it raised
    # an error and no longer trigger again
    assert mocked_dynamic_scheduler_events.count == 1

    # check if service was properly removed or is still kept for manual interventions
    if error_raised_by_saving_state:
        if use_case.outcome_service_removed:
            assert scheduler_data.node_uuid not in scheduler._inverse_search_mapping
        else:
            assert scheduler_data.node_uuid in scheduler._inverse_search_mapping
    else:
        assert scheduler_data.node_uuid not in scheduler._inverse_search_mapping

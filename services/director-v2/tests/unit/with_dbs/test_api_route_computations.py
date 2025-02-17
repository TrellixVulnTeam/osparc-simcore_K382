# pylint: disable=no-value-for-parameter
# pylint: disable=protected-access
# pylint: disable=redefined-outer-name
# pylint: disable=too-many-arguments
# pylint: disable=unused-argument
# pylint: disable=unused-variable

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest
import respx
from faker import Faker
from fastapi import FastAPI
from models_library.clusters import DEFAULT_CLUSTER_ID
from models_library.projects import ProjectAtDB
from models_library.projects_nodes import NodeID, NodeState
from models_library.projects_pipeline import PipelineDetails
from models_library.projects_state import RunningState
from models_library.services import ServiceDockerData
from models_library.utils.fastapi_encoders import jsonable_encoder
from pydantic import AnyHttpUrl, parse_obj_as
from pytest_mock.plugin import MockerFixture
from pytest_simcore.helpers.typing_env import EnvVarsDict
from simcore_postgres_database.models.comp_pipeline import StateType
from simcore_postgres_database.models.comp_tasks import NodeClass
from simcore_service_director_v2.models.domains.comp_pipelines import CompPipelineAtDB
from simcore_service_director_v2.models.domains.comp_runs import CompRunsAtDB
from simcore_service_director_v2.models.domains.comp_tasks import CompTaskAtDB
from simcore_service_director_v2.models.schemas.comp_tasks import (
    ComputationCreate,
    ComputationGet,
)
from simcore_service_director_v2.models.schemas.services import ServiceExtras
from starlette import status

pytest_simcore_core_services_selection = [
    "postgres",
]
pytest_simcore_ops_services_selection = [
    "adminer",
]


@pytest.fixture()
def mocked_rabbit_mq_client(mocker: MockerFixture):
    mocker.patch(
        "simcore_service_director_v2.core.application.rabbitmq.RabbitMQClient",
        autospec=True,
    )


@pytest.fixture()
def minimal_configuration(
    mock_env: EnvVarsDict,
    postgres_host_config: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    mocked_rabbit_mq_client: None,
):
    monkeypatch.setenv("DIRECTOR_V2_DYNAMIC_SIDECAR_ENABLED", "false")
    monkeypatch.setenv("DIRECTOR_V2_POSTGRES_ENABLED", "1")
    monkeypatch.setenv("COMPUTATIONAL_BACKEND_DASK_CLIENT_ENABLED", "1")
    monkeypatch.setenv("COMPUTATIONAL_BACKEND_ENABLED", "1")
    monkeypatch.setenv("R_CLONE_PROVIDER", "MINIO")
    monkeypatch.setenv("S3_ENDPOINT", "endpoint")
    monkeypatch.setenv("S3_ACCESS_KEY", "access_key")
    monkeypatch.setenv("S3_SECRET_KEY", "secret_key")
    monkeypatch.setenv("S3_BUCKET_NAME", "bucket_name")
    monkeypatch.setenv("S3_SECURE", "false")


@pytest.fixture(scope="session")
def fake_service_details(mocks_dir: Path) -> ServiceDockerData:
    fake_service_path = mocks_dir / "fake_service.json"
    assert fake_service_path.exists()
    fake_service_data = json.loads(fake_service_path.read_text())
    return ServiceDockerData(**fake_service_data)


@pytest.fixture
def fake_service_extras() -> ServiceExtras:
    extra_example = ServiceExtras.Config.schema_extra["examples"][2]
    random_extras = ServiceExtras(**extra_example)
    assert random_extras is not None
    return random_extras


@pytest.fixture
def mocked_director_service_fcts(
    minimal_app: FastAPI,
    fake_service_details: ServiceDockerData,
    fake_service_extras: ServiceExtras,
):
    # pylint: disable=not-context-manager
    with respx.mock(
        base_url=minimal_app.state.settings.DIRECTOR_V0.endpoint,
        assert_all_called=False,
        assert_all_mocked=True,
    ) as respx_mock:
        respx_mock.get(
            re.compile(
                r"/services/(simcore)%2F(services)%2F(comp|dynamic|frontend)%2F.+/(.+)"
            ),
            name="get_service",
        ).respond(json={"data": [fake_service_details.dict(by_alias=True)]})

        respx_mock.get(
            re.compile(
                r"/service_extras/(simcore)%2F(services)%2F(comp|dynamic|frontend)%2F.+/(.+)"
            ),
            name="get_service_extras",
        ).respond(json={"data": fake_service_extras.dict(by_alias=True)})

        yield respx_mock


@pytest.fixture
def mocked_catalog_service_fcts(
    minimal_app: FastAPI,
    fake_service_details: ServiceDockerData,
    fake_service_extras: ServiceExtras,
):
    # pylint: disable=not-context-manager
    with respx.mock(
        base_url=minimal_app.state.settings.DIRECTOR_V2_CATALOG.api_base_url,
        assert_all_called=False,
        assert_all_mocked=True,
    ) as respx_mock:
        respx_mock.get(
            re.compile(
                r"services/(simcore)%2F(services)%2F(comp|dynamic|frontend)%2F.+/(.+)"
            ),
            name="get_service",
        ).respond(json=fake_service_details.dict(by_alias=True))

        yield respx_mock


@pytest.fixture
def mocked_catalog_service_fcts_deprecated(
    minimal_app: FastAPI,
    fake_service_details: ServiceDockerData,
    fake_service_extras: ServiceExtras,
):
    # pylint: disable=not-context-manager
    with respx.mock(
        base_url=minimal_app.state.settings.DIRECTOR_V2_CATALOG.api_base_url,
        assert_all_called=False,
        assert_all_mocked=True,
    ) as respx_mock:
        respx_mock.get(
            re.compile(
                r"services/(simcore)%2F(services)%2F(comp|dynamic|frontend)%2F.+/(.+)"
            ),
            name="get_service",
        ).respond(
            json=fake_service_details.copy(
                update={
                    "deprecated": (datetime.utcnow() - timedelta(days=1)).isoformat()
                }
            ).dict(by_alias=True)
        )

        yield respx_mock


@pytest.fixture
def product_name(faker: Faker) -> str:
    return faker.name()


async def test_create_computation(
    minimal_configuration: None,
    mocked_director_service_fcts,
    mocked_catalog_service_fcts,
    product_name: str,
    fake_workbench_without_outputs: dict[str, Any],
    registered_user: Callable[..., dict[str, Any]],
    project: Callable[..., ProjectAtDB],
    async_client: httpx.AsyncClient,
):
    user = registered_user()
    proj = project(user, workbench=fake_workbench_without_outputs)
    create_computation_url = httpx.URL("/v2/computations")
    response = await async_client.post(
        create_computation_url,
        json=jsonable_encoder(
            ComputationCreate(
                user_id=user["id"],
                project_id=proj.uuid,
            )
        ),
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text


async def test_start_computation_without_product_fails(
    minimal_configuration: None,
    mocked_director_service_fcts,
    mocked_catalog_service_fcts,
    product_name: str,
    fake_workbench_without_outputs: dict[str, Any],
    registered_user: Callable[..., dict[str, Any]],
    project: Callable[..., ProjectAtDB],
    async_client: httpx.AsyncClient,
):
    user = registered_user()
    proj = project(user, workbench=fake_workbench_without_outputs)
    create_computation_url = httpx.URL("/v2/computations")
    response = await async_client.post(
        create_computation_url,
        json={
            "user_id": f"{user['id']}",
            "project_id": f"{proj.uuid}",
            "start_pipeline": f"{True}",
        },
    )
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY, response.text


async def test_start_computation(
    minimal_configuration: None,
    mocked_director_service_fcts,
    mocked_catalog_service_fcts,
    product_name: str,
    fake_workbench_without_outputs: dict[str, Any],
    registered_user: Callable[..., dict[str, Any]],
    project: Callable[..., ProjectAtDB],
    async_client: httpx.AsyncClient,
):
    user = registered_user()
    proj = project(user, workbench=fake_workbench_without_outputs)
    create_computation_url = httpx.URL("/v2/computations")
    response = await async_client.post(
        create_computation_url,
        json=jsonable_encoder(
            ComputationCreate(
                user_id=user["id"],
                project_id=proj.uuid,
                start_pipeline=True,
                product_name=product_name,
            )
        ),
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text


async def test_start_computation_with_deprecated_services_raises_406(
    minimal_configuration: None,
    mocked_director_service_fcts,
    mocked_catalog_service_fcts_deprecated,
    product_name: str,
    fake_workbench_without_outputs: dict[str, Any],
    fake_workbench_adjacency: dict[str, Any],
    registered_user: Callable[..., dict[str, Any]],
    project: Callable[..., ProjectAtDB],
    async_client: httpx.AsyncClient,
):
    user = registered_user()
    proj = project(user, workbench=fake_workbench_without_outputs)
    create_computation_url = httpx.URL("/v2/computations")
    response = await async_client.post(
        create_computation_url,
        json=jsonable_encoder(
            ComputationCreate(
                user_id=user["id"],
                project_id=proj.uuid,
                start_pipeline=True,
                product_name=product_name,
            )
        ),
    )
    assert response.status_code == status.HTTP_406_NOT_ACCEPTABLE, response.text


async def test_get_computation_from_empty_project(
    minimal_configuration: None,
    fake_workbench_without_outputs: dict[str, Any],
    fake_workbench_adjacency: dict[str, Any],
    registered_user: Callable[..., dict[str, Any]],
    project: Callable[..., ProjectAtDB],
    pipeline: Callable[..., CompPipelineAtDB],
    tasks: Callable[..., list[CompTaskAtDB]],
    faker: Faker,
    async_client: httpx.AsyncClient,
):
    user = registered_user()
    get_computation_url = httpx.URL(
        f"/v2/computations/{faker.uuid4()}?user_id={user['id']}"
    )
    # the project exists but there is no pipeline yet
    response = await async_client.get(get_computation_url)
    assert response.status_code == status.HTTP_404_NOT_FOUND, response.text
    # create the project
    proj = project(user, workbench=fake_workbench_without_outputs)
    get_computation_url = httpx.URL(
        f"/v2/computations/{proj.uuid}?user_id={user['id']}"
    )
    response = await async_client.get(get_computation_url)
    assert response.status_code == status.HTTP_404_NOT_FOUND, response.text
    # create an empty pipeline
    pipeline(
        project_id=proj.uuid,
    )
    response = await async_client.get(get_computation_url)
    assert response.status_code == status.HTTP_200_OK, response.text
    returned_computation = ComputationGet.parse_obj(response.json())
    assert returned_computation
    expected_computation = ComputationGet(
        id=proj.uuid,
        state=RunningState.UNKNOWN,
        pipeline_details=PipelineDetails(adjacency_list={}, node_states={}),
        url=parse_obj_as(
            AnyHttpUrl, f"{async_client.base_url.join(get_computation_url)}"
        ),
        stop_url=None,
        result=None,
        iteration=None,
        cluster_id=None,
    )
    assert returned_computation.dict() == expected_computation.dict()


async def test_get_computation_from_not_started_computation_task(
    minimal_configuration: None,
    fake_workbench_without_outputs: dict[str, Any],
    fake_workbench_adjacency: dict[str, Any],
    registered_user: Callable[..., dict[str, Any]],
    project: Callable[..., ProjectAtDB],
    pipeline: Callable[..., CompPipelineAtDB],
    tasks: Callable[..., list[CompTaskAtDB]],
    faker: Faker,
    async_client: httpx.AsyncClient,
):
    user = registered_user()
    proj = project(user, workbench=fake_workbench_without_outputs)
    get_computation_url = httpx.URL(
        f"/v2/computations/{proj.uuid}?user_id={user['id']}"
    )
    pipeline(
        project_id=proj.uuid,
        dag_adjacency_list=fake_workbench_adjacency,
    )
    # create no task this should trigger an exception
    response = await async_client.get(get_computation_url)
    assert response.status_code == status.HTTP_409_CONFLICT, response.text

    # now create the expected tasks and the state is good again
    comp_tasks = tasks(user=user, project=proj)
    response = await async_client.get(get_computation_url)
    assert response.status_code == status.HTTP_200_OK, response.text
    returned_computation = ComputationGet.parse_obj(response.json())
    assert returned_computation
    expected_computation = ComputationGet(
        id=proj.uuid,
        state=RunningState.NOT_STARTED,
        pipeline_details=PipelineDetails(
            adjacency_list=parse_obj_as(
                dict[NodeID, list[NodeID]], fake_workbench_adjacency
            ),
            node_states={
                t.node_id: NodeState(
                    modified=True,
                    currentStatus=RunningState.NOT_STARTED,
                    dependencies={
                        NodeID(node)
                        for node, next_nodes in fake_workbench_adjacency.items()
                        if f"{t.node_id}" in next_nodes
                    },
                )
                for t in comp_tasks
                if t.node_class == NodeClass.COMPUTATIONAL
            },
        ),
        url=parse_obj_as(
            AnyHttpUrl, f"{async_client.base_url.join(get_computation_url)}"
        ),
        stop_url=None,
        result=None,
        iteration=None,
        cluster_id=None,
    )

    assert returned_computation.dict() == expected_computation.dict()


async def test_get_computation_from_published_computation_task(
    minimal_configuration: None,
    fake_workbench_without_outputs: dict[str, Any],
    fake_workbench_adjacency: dict[str, Any],
    registered_user: Callable[..., dict[str, Any]],
    project: Callable[..., ProjectAtDB],
    pipeline: Callable[..., CompPipelineAtDB],
    tasks: Callable[..., list[CompTaskAtDB]],
    runs: Callable[..., CompRunsAtDB],
    async_client: httpx.AsyncClient,
):
    user = registered_user()
    proj = project(user, workbench=fake_workbench_without_outputs)
    pipeline(
        project_id=proj.uuid,
        dag_adjacency_list=fake_workbench_adjacency,
    )
    comp_tasks = tasks(user=user, project=proj, state=StateType.PUBLISHED)
    comp_runs = runs(user=user, project=proj, result=StateType.PUBLISHED)
    get_computation_url = httpx.URL(
        f"/v2/computations/{proj.uuid}?user_id={user['id']}"
    )
    response = await async_client.get(get_computation_url)
    assert response.status_code == status.HTTP_200_OK, response.text
    returned_computation = ComputationGet.parse_obj(response.json())
    assert returned_computation
    expected_stop_url = async_client.base_url.join(
        f"/v2/computations/{proj.uuid}:stop?user_id={user['id']}"
    )
    expected_computation = ComputationGet(
        id=proj.uuid,
        state=RunningState.PUBLISHED,
        pipeline_details=PipelineDetails(
            adjacency_list=parse_obj_as(
                dict[NodeID, list[NodeID]], fake_workbench_adjacency
            ),
            node_states={
                t.node_id: NodeState(
                    modified=True,
                    currentStatus=RunningState.PUBLISHED,
                    dependencies={
                        NodeID(node)
                        for node, next_nodes in fake_workbench_adjacency.items()
                        if f"{t.node_id}" in next_nodes
                    },
                )
                for t in comp_tasks
                if t.node_class == NodeClass.COMPUTATIONAL
            },
        ),
        url=parse_obj_as(
            AnyHttpUrl, f"{async_client.base_url.join(get_computation_url)}"
        ),
        stop_url=parse_obj_as(AnyHttpUrl, f"{expected_stop_url}"),
        result=None,
        iteration=1,
        cluster_id=DEFAULT_CLUSTER_ID,
    )

    assert returned_computation.dict() == expected_computation.dict()

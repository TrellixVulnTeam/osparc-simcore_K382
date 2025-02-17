""" Covers user stories for ISAN : #501, #712, #730

"""
# pylint:disable=unused-variable
# pylint:disable=unused-argument
# pylint:disable=redefined-outer-name


import asyncio
import logging
import re
from copy import deepcopy
from pathlib import Path
from pprint import pprint
from typing import AsyncIterator, Callable

import pytest
from aiohttp import ClientResponse, ClientSession, web
from aiohttp.test_utils import TestClient
from aioresponses import aioresponses
from models_library.projects_state import ProjectLocked, ProjectStatus
from pytest_mock.plugin import MockerFixture
from pytest_simcore.helpers.utils_assert import assert_status
from pytest_simcore.helpers.utils_dict import ConfigDict
from pytest_simcore.helpers.utils_login import UserRole
from pytest_simcore.helpers.utils_projects import NewProject, delete_all_projects
from servicelib.aiohttp.long_running_tasks.client import LRTask
from servicelib.aiohttp.long_running_tasks.server import TaskProgress
from servicelib.aiohttp.rest_responses import unwrap_envelope
from settings_library.redis import RedisSettings
from simcore_service_webserver.log import setup_logging
from simcore_service_webserver.projects.project_models import ProjectDict
from simcore_service_webserver.projects.projects_api import submit_delete_project_task
from simcore_service_webserver.users_api import delete_user, get_user_role

SHARED_STUDY_UUID = "e2e38eee-c569-4e55-b104-70d159e49c87"


@pytest.fixture
def app_cfg(
    default_app_cfg: ConfigDict,
    unused_tcp_port_factory: Callable,
    redis_service: RedisSettings,
):
    """App's configuration used for every test in this module

    NOTE: Overrides services/web/server/tests/unit/with_dbs/conftest.py::app_cfg to influence app setup
    """
    cfg = deepcopy(default_app_cfg)

    cfg["main"]["port"] = unused_tcp_port_factory()
    cfg["main"]["studies_access_enabled"] = True

    exclude = {
        "tracing",
        "director",
        "smtp",
        "storage",
        "activity",
        "diagnostics",
        "groups",
        "tags",
        "publications",
        "catalog",
        "computation",
        "clusters",
    }
    include = {
        "db",
        "rest",
        "projects",
        "login",
        "socketio",
        "resource_manager",
        "users",
        "products",
    }

    assert include.intersection(exclude) == set()

    for section in include:
        cfg[section]["enabled"] = True
    for section in exclude:
        cfg[section]["enabled"] = False

    # NOTE: To see logs, use pytest -s --log-cli-level=DEBUG
    setup_logging(level=logging.DEBUG)

    # Enforces smallest GC in the background task
    cfg["resource_manager"]["garbage_collection_interval_seconds"] = 1

    return cfg


@pytest.fixture
async def published_project(
    client: TestClient,
    fake_project: ProjectDict,
    tests_data_dir: Path,
    osparc_product_name: str,
) -> AsyncIterator[dict]:
    project_data = deepcopy(fake_project)
    project_data["name"] = "Published project"
    project_data["uuid"] = SHARED_STUDY_UUID
    project_data["published"] = True

    async with NewProject(
        project_data,
        client.app,
        user_id=None,
        product_name=osparc_product_name,
        clear_all=True,
        tests_data_dir=tests_data_dir,
    ) as template_project:
        yield template_project


@pytest.fixture
async def unpublished_project(
    client: TestClient,
    fake_project: ProjectDict,
    tests_data_dir: Path,
    osparc_product_name: str,
):
    project_data = deepcopy(fake_project)
    project_data["name"] = "Template Unpublished project"
    project_data["uuid"] = "b134a337-a74f-40ff-a127-b36a1ccbede6"
    project_data["published"] = False

    async with NewProject(
        project_data,
        client.app,
        user_id=None,
        product_name=osparc_product_name,
        clear_all=True,
        tests_data_dir=tests_data_dir,
        as_template=True,
    ) as template_project:
        yield template_project


@pytest.fixture
async def director_v2_mock(director_v2_service_mock) -> AsyncIterator[aioresponses]:
    yield director_v2_service_mock


async def _get_user_projects(client):
    url = client.app.router["list_projects"].url_for()
    resp = await client.get(url.with_query(type="user"))

    payload = await resp.json()
    assert resp.status == 200, payload

    projects, error = unwrap_envelope(payload)
    assert not error, pprint(error)

    return projects


def _assert_same_projects(got: dict, expected: dict):
    # TODO: validate using api/specs/webserver/v0/components/schemas/project-v0.0.1.json
    # TODO: validate workbench!
    exclude = {
        "creationDate",
        "lastChangeDate",
        "prjOwner",
        "uuid",
        "workbench",
        "accessRights",
        "ui",
    }
    for key in expected.keys():
        if key not in exclude:
            assert got[key] == expected[key], "Failed in %s" % key


def is_user_authenticated(session: ClientSession) -> bool:
    return "osparc.WEBAPI_SESSION" in [c.key for c in session.cookie_jar]


async def assert_redirected_to_study(
    resp: ClientResponse, session: ClientSession
) -> str:

    # https://docs.aiohttp.org/en/stable/client_advanced.html#redirection-history
    assert len(resp.history) == 1, "Is a re-direction"

    content = await resp.text()
    assert resp.status == web.HTTPOk.status_code, f"Got {content}"

    # Expects redirection to osparc web
    assert resp.url.path == "/"
    assert (
        "OSPARC-SIMCORE" in content
    ), "Expected front-end rendering workbench's study, got %s" % str(content)

    # Expects auth cookie for current user
    assert is_user_authenticated(session)

    # Expects fragment to indicate client where to find newly created project
    m = re.match(r"/study/([\d\w-]+)", resp.real_url.fragment)
    assert m, f"Expected /study/uuid, got {resp.real_url.fragment}"

    # returns newly created project
    redirected_project_id = m.group(1)
    return redirected_project_id


@pytest.fixture
def mocks_on_projects_api(mocker) -> None:
    """
    All projects in this module are UNLOCKED
    """
    mocker.patch(
        "simcore_service_webserver.projects.projects_api._get_project_lock_state",
        return_value=ProjectLocked(value=False, status=ProjectStatus.CLOSED),
    )


@pytest.fixture
async def storage_subsystem_mock(storage_subsystem_mock, mocker: MockerFixture):
    """
    Mocks functions that require storage client
    """
    # Overrides + extends fixture in services/web/server/tests/unit/with_dbs/conftest.py
    # SEE https://docs.pytest.org/en/stable/fixture.html#override-a-fixture-on-a-folder-conftest-level

    # Mocks copy_data_folders_from_project BUT under studies_access
    mock = mocker.patch(
        "simcore_service_webserver.studies_dispatcher._studies_access.copy_data_folders_from_project",
        autospec=True,
    )

    async def _mock_copy_data_from_project(app, src_prj, dst_prj, nodes_map, user_id):
        print(
            f"MOCK copying data project {src_prj['uuid']} -> {dst_prj['uuid']} "
            f"with {len(nodes_map)} s3 objects by user={user_id}"
        )

        yield LRTask(TaskProgress(message="pytest mocked fct, started"))

        async def _mock_result():
            return None

        yield LRTask(
            TaskProgress(message="pytest mocked fct, finished", percent=1.0),
            _result=_mock_result(),
        )

    mock.side_effect = _mock_copy_data_from_project


async def test_access_to_invalid_study(client, published_project):
    resp = await client.get("/study/SOME_INVALID_UUID")
    content = await resp.text()

    assert resp.status == web.HTTPNotFound.status_code, str(content)


async def test_access_to_forbidden_study(client, unpublished_project):
    app = client.app

    valid_but_not_sharable = unpublished_project["uuid"]

    resp = await client.get("/study/valid_but_not_sharable")
    content = await resp.text()

    assert (
        resp.status == web.HTTPNotFound.status_code
    ), f"STANDARD studies are NOT sharable: {content}"


@pytest.mark.flaky(max_runs=3)
async def test_access_study_anonymously(
    client,
    published_project,
    storage_subsystem_mock,
    catalog_subsystem_mock,
    director_v2_mock,
    mocks_on_projects_api,
    redis_locks_client,  # needed to cleanup the locks between parametrizations
):
    catalog_subsystem_mock([published_project])
    assert not is_user_authenticated(client.session), "Is anonymous"

    study_url = client.app.router["study"].url_for(id=published_project["uuid"])

    resp = await client.get(study_url)

    expected_prj_id = await assert_redirected_to_study(resp, client.session)

    # has auto logged in as guest?
    me_url = client.app.router["get_my_profile"].url_for()
    resp = await client.get(me_url)

    data, _ = await assert_status(resp, web.HTTPOk)
    assert data["login"].endswith("guest-at-osparc.io")
    assert data["gravatar_id"]
    assert data["role"].upper() == UserRole.GUEST.name

    # guest user only a copy of the template project
    projects = await _get_user_projects(client)
    assert len(projects) == 1
    guest_project = projects[0]

    assert expected_prj_id == guest_project["uuid"]
    _assert_same_projects(guest_project, published_project)

    assert guest_project["prjOwner"] == data["login"]


@pytest.fixture
async def auto_delete_projects(client) -> AsyncIterator[None]:
    yield
    await delete_all_projects(client.app)


@pytest.mark.parametrize("user_role", [UserRole.USER, UserRole.TESTER])
async def test_access_study_by_logged_user(
    client,
    logged_user,
    published_project,
    storage_subsystem_mock,
    catalog_subsystem_mock,
    director_v2_mock,
    mocks_on_projects_api,
    auto_delete_projects,
    redis_locks_client,  # needed to cleanup the locks between parametrizations
):
    catalog_subsystem_mock([published_project])
    assert is_user_authenticated(client.session), "Is already logged-in"

    study_url = client.app.router["study"].url_for(id=published_project["uuid"])
    resp = await client.get(study_url)
    await assert_redirected_to_study(resp, client.session)

    # user has a copy of the template project
    projects = await _get_user_projects(client)
    assert len(projects) == 1
    user_project = projects[0]

    # heck redirects to /#/study/{uuid}
    assert resp.real_url.fragment.endswith("/study/%s" % user_project["uuid"])
    _assert_same_projects(user_project, published_project)

    assert user_project["prjOwner"] == logged_user["email"]


async def test_access_cookie_of_expired_user(
    client,
    published_project,
    storage_subsystem_mock,
    catalog_subsystem_mock,
    director_v2_mock,
    mocks_on_projects_api,
    redis_locks_client,  # needed to cleanup the locks between parametrizations
):
    catalog_subsystem_mock([published_project])
    # emulates issue #1570
    app: web.Application = client.app

    study_url = app.router["study"].url_for(id=published_project["uuid"])
    resp = await client.get(study_url)

    await assert_redirected_to_study(resp, client.session)

    # Expects valid cookie and GUEST access
    me_url = app.router["get_my_profile"].url_for()
    resp = await client.get(me_url)

    data, _ = await assert_status(resp, web.HTTPOk)
    assert await get_user_role(app, data["id"]) == UserRole.GUEST

    async def enforce_garbage_collect_guest(uid):
        # TODO: can be replaced now by actual GC
        # Emulates garbage collector:
        #   - GUEST user expired, cleaning it up
        #   - client still holds cookie with its identifier nonetheless
        #
        assert await get_user_role(app, uid) == UserRole.GUEST
        projects = await _get_user_projects(client)
        assert len(projects) == 1

        prj_id = projects[0]["uuid"]

        delete_task = await submit_delete_project_task(app, prj_id, uid)
        await delete_task

        await delete_user(app, uid)
        return uid

    user_id = await enforce_garbage_collect_guest(uid=data["id"])
    user_email = data["login"]

    # Now this should be non -authorized
    resp = await client.get(me_url)
    await assert_status(resp, web.HTTPUnauthorized)

    # But still can access as a new user
    resp = await client.get(study_url)
    await assert_redirected_to_study(resp, client.session)

    # as a guest user
    resp = await client.get(me_url)
    data, _ = await assert_status(resp, web.HTTPOk)
    assert await get_user_role(app, data["id"]) == UserRole.GUEST

    # But I am another user
    assert data["id"] != user_id
    assert data["login"] != user_email


@pytest.mark.parametrize("number_of_simultaneous_requests", [1, 2, 64])
async def test_guest_user_is_not_garbage_collected(
    number_of_simultaneous_requests,
    web_server,
    aiohttp_client,
    published_project,
    storage_subsystem_mock,
    catalog_subsystem_mock,
    director_v2_mock,
    mocks_on_projects_api,
    redis_locks_client,  # needed to cleanup the locks between parametrizations
):
    catalog_subsystem_mock([published_project])
    ## NOTE: use pytest -s --log-cli-level=DEBUG  to see GC logs

    async def _test_guest_user_workflow(request_index):
        print("request #", request_index, "-" * 10)

        # TODO: heartbeat is missing here!
        # TODO: reduce GC activation period to 0.1 secs
        # every guest uses different client to preserve it's own authorization/authentication cookies
        client: TestClient = await aiohttp_client(web_server)
        assert client.app
        study_url = client.app.router["study"].url_for(id=published_project["uuid"])

        # clicks link to study
        resp = await client.get(f"{study_url}")

        expected_prj_id = await assert_redirected_to_study(resp, client.session)

        # has auto logged in as guest?
        me_url = client.app.router["get_my_profile"].url_for()
        resp = await client.get(f"{me_url}")

        data, _ = await assert_status(resp, web.HTTPOk)
        assert data["login"].endswith("guest-at-osparc.io")
        assert data["gravatar_id"]
        assert data["role"].upper() == UserRole.GUEST.name

        # guest user only a copy of the template project
        projects = await _get_user_projects(client)
        assert len(projects) == 1
        guest_project = projects[0]

        assert expected_prj_id == guest_project["uuid"]
        _assert_same_projects(guest_project, published_project)

        assert guest_project["prjOwner"] == data["login"]
        print("request #", request_index, "DONE", "-" * 10)

    # N concurrent requests
    request_tasks = [
        asyncio.create_task(_test_guest_user_workflow(n))
        for n in range(number_of_simultaneous_requests)
    ]

    await asyncio.gather(*request_tasks)

    # and now the garbage collector shall delete our users since we are done...

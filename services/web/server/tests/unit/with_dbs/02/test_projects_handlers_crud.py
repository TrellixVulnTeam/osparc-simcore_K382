# pylint: disable=redefined-outer-name
# pylint: disable=too-many-arguments
# pylint: disable=unused-argument
# pylint: disable=unused-variable


import uuid as uuidlib
from copy import deepcopy
from math import ceil
from typing import Any, Awaitable, Callable, Iterator, Optional, Union

import pytest
import sqlalchemy as sa
from _helpers import ExpectedResponse, MockedStorageSubsystem, standard_role_response
from aiohttp import web
from aiohttp.test_utils import TestClient
from aioresponses import aioresponses
from models_library.projects_state import ProjectState
from pytest_simcore.helpers.utils_assert import assert_status
from simcore_postgres_database.models.products import products
from simcore_postgres_database.models.projects_to_products import projects_to_products
from simcore_service_webserver._constants import X_PRODUCT_NAME_HEADER
from simcore_service_webserver._meta import api_version_prefix
from simcore_service_webserver.db_models import UserRole
from simcore_service_webserver.projects.project_models import ProjectDict
from simcore_service_webserver.utils import to_datetime
from yarl import URL

API_PREFIX = "/" + api_version_prefix


def assert_replaced(current_project, update_data):
    def _extract(dikt, keys):
        return {k: dikt[k] for k in keys}

    modified = [
        "lastChangeDate",
    ]
    keep = [k for k in update_data.keys() if k not in modified]

    assert _extract(current_project, keep) == _extract(update_data, keep)

    k = "lastChangeDate"
    assert to_datetime(update_data[k]) < to_datetime(current_project[k])


async def _list_projects(
    client,
    expected: type[web.HTTPException],
    query_parameters: Optional[dict] = None,
    headers: Optional[dict] = None,
    expected_error_msg: Optional[str] = None,
    expected_error_code: Optional[str] = None,
) -> tuple[list[dict], dict[str, Any], dict[str, Any]]:
    if not query_parameters:
        query_parameters = {}
    # GET /v0/projects
    url = client.app.router["list_projects"].url_for()
    assert str(url) == API_PREFIX + "/projects"
    if query_parameters:
        url = url.with_query(**query_parameters)

    if headers is None:
        headers = {}

    resp = await client.get(url, headers=headers)
    data, errors, meta, links = await assert_status(
        resp,
        expected,
        expected_msg=expected_error_msg,
        expected_error_code=expected_error_code,
        include_meta=True,
        include_links=True,
    )
    if data is not None:
        assert meta is not None
        # see [api/specs/webserver/openapi-projects.yaml] for defaults
        exp_offset = max(int(query_parameters.get("offset", 0)), 0)
        exp_limit = max(1, min(int(query_parameters.get("limit", 20)), 50))
        assert meta["offset"] == exp_offset
        assert meta["limit"] == exp_limit
        exp_last_page = ceil(meta["total"] / meta["limit"] - 1)
        assert links is not None
        complete_url = client.make_url(url)
        assert links["self"] == str(
            URL(complete_url).update_query({"offset": exp_offset, "limit": exp_limit})
        )
        assert links["first"] == str(
            URL(complete_url).update_query({"offset": 0, "limit": exp_limit})
        )
        assert links["last"] == str(
            URL(complete_url).update_query(
                {"offset": exp_last_page * exp_limit, "limit": exp_limit}
            )
        )
        if exp_offset <= 0:
            assert links["prev"] == None
        else:
            assert links["prev"] == str(
                URL(complete_url).update_query(
                    {"offset": max(exp_offset - exp_limit, 0), "limit": exp_limit}
                )
            )
        if exp_offset >= (exp_last_page * exp_limit):
            assert links["next"] == None
        else:
            assert links["next"] == str(
                URL(complete_url).update_query(
                    {
                        "offset": min(
                            exp_offset + exp_limit, exp_last_page * exp_limit
                        ),
                        "limit": exp_limit,
                    }
                )
            )
    else:
        assert meta is None
        assert links is None
    return data, meta, links


async def _assert_get_same_project(
    client,
    project: dict,
    expected: type[web.HTTPException],
) -> dict:
    # GET /v0/projects/{project_id}

    # with a project owned by user
    url = client.app.router["get_project"].url_for(project_id=project["uuid"])
    assert str(url) == f"{API_PREFIX}/projects/{project['uuid']}"
    resp = await client.get(url)
    data, error = await assert_status(resp, expected)

    if not error:
        project_state = data.pop("state")
        assert data == project
        assert ProjectState(**project_state)
    return data


async def _replace_project(
    client, project_update: dict, expected: type[web.HTTPException]
) -> dict:
    # PUT /v0/projects/{project_id}
    url = client.app.router["replace_project"].url_for(
        project_id=project_update["uuid"]
    )
    assert str(url) == f"{API_PREFIX}/projects/{project_update['uuid']}"
    resp = await client.put(url, json=project_update)
    data, error = await assert_status(resp, expected)
    if not error:
        assert_replaced(current_project=data, update_data=project_update)
    return data


@pytest.mark.parametrize(
    "user_role,expected",
    [
        (UserRole.ANONYMOUS, web.HTTPUnauthorized),
        (UserRole.GUEST, web.HTTPOk),
        (UserRole.USER, web.HTTPOk),
        (UserRole.TESTER, web.HTTPOk),
    ],
)
async def test_list_projects(
    client: TestClient,
    logged_user: dict[str, Any],
    user_project: dict[str, Any],
    template_project: dict[str, Any],
    expected: type[web.HTTPException],
    catalog_subsystem_mock: Callable[[Optional[Union[list[dict], dict]]], None],
    director_v2_service_mock: aioresponses,
):
    catalog_subsystem_mock([user_project, template_project])
    data, *_ = await _list_projects(client, expected)

    if data:
        assert len(data) == 2

        project_state = data[0].pop("state")
        assert data[0] == template_project
        assert not ProjectState(
            **project_state
        ).locked.value, "Templates are not locked"

        project_state = data[1].pop("state")
        assert data[1] == user_project
        assert ProjectState(**project_state)

    # GET /v0/projects?type=user
    data, *_ = await _list_projects(client, expected, {"type": "user"})
    if data:
        assert len(data) == 1
        project_state = data[0].pop("state")
        assert data[0] == user_project
        assert not ProjectState(
            **project_state
        ).locked.value, "Single user does not lock"

    # GET /v0/projects?type=template
    # instead /v0/projects/templates ??
    data, *_ = await _list_projects(client, expected, {"type": "template"})
    if data:
        assert len(data) == 1
        project_state = data[0].pop("state")
        assert data[0] == template_project
        assert not ProjectState(
            **project_state
        ).locked.value, "Templates are not locked"


@pytest.fixture(scope="session")
def s4l_product_name() -> str:
    return "s4l"


@pytest.fixture
def s4l_products_db_name(
    postgres_db: sa.engine.Engine, s4l_product_name: str
) -> Iterator[str]:
    postgres_db.execute(
        products.insert().values(
            name=s4l_product_name, host_regex="pytest", display_name="pytest"
        )
    )
    yield s4l_product_name
    postgres_db.execute(products.delete().where(products.c.name == s4l_product_name))


@pytest.fixture
def s4l_product_headers(s4l_products_db_name: str) -> dict[str, str]:
    return {X_PRODUCT_NAME_HEADER: s4l_products_db_name}


@pytest.mark.parametrize(
    "user_role,expected",
    [
        (UserRole.USER, web.HTTPOk),
    ],
)
async def test_list_projects_with_innaccessible_services(
    s4l_products_db_name: str,
    client: TestClient,
    logged_user: dict[str, Any],
    user_project: dict[str, Any],
    template_project: dict[str, Any],
    expected: type[web.HTTPException],
    catalog_subsystem_mock: Callable[[Optional[Union[list[dict], dict]]], None],
    director_v2_service_mock: aioresponses,
    postgres_db: sa.engine.Engine,
    s4l_product_headers: dict[str, Any],
):
    # use-case 1: calling with correct product name returns 2 projects
    # projects are linked to osparc
    data, *_ = await _list_projects(client, expected)
    assert len(data) == 2
    # use-case 2: calling with another product name returns 0 projects
    # because projects are linked to osparc product in projects_to_products table
    data, *_ = await _list_projects(client, expected, headers=s4l_product_headers)
    assert len(data) == 0
    # use-case 3: remove the links to products
    # shall still return 0 because the user has no access to the services
    postgres_db.execute(projects_to_products.delete())
    data, *_ = await _list_projects(client, expected, headers=s4l_product_headers)
    assert len(data) == 0
    data, *_ = await _list_projects(client, expected)
    assert len(data) == 0
    # use-case 4: give user access to services
    # shall return the projects for any product
    catalog_subsystem_mock([user_project, template_project])
    data, *_ = await _list_projects(client, expected, headers=s4l_product_headers)
    assert len(data) == 2
    data, *_ = await _list_projects(client, expected)
    assert len(data) == 2


@pytest.mark.parametrize(
    "user_role,expected",
    [
        (UserRole.ANONYMOUS, web.HTTPUnauthorized),
        (UserRole.GUEST, web.HTTPOk),
        (UserRole.USER, web.HTTPOk),
        (UserRole.TESTER, web.HTTPOk),
    ],
)
async def test_get_project(
    client,
    logged_user,
    user_project,
    template_project,
    expected,
    catalog_subsystem_mock,
):
    catalog_subsystem_mock([user_project, template_project])

    # standard project
    await _assert_get_same_project(client, user_project, expected)

    # with a template
    await _assert_get_same_project(client, template_project, expected)


# POST --------
@pytest.mark.parametrize(*standard_role_response())
async def test_new_project(
    client,
    logged_user,
    primary_group,
    expected,
    storage_subsystem_mock,
    project_db_cleaner,
    request_create_project: Callable[..., Awaitable[ProjectDict]],
):
    await request_create_project(
        client, expected.accepted, expected.created, logged_user, primary_group
    )


@pytest.mark.parametrize(*standard_role_response())
async def test_new_project_from_template(
    client,
    logged_user,
    primary_group: dict[str, str],
    template_project,
    expected,
    storage_subsystem_mock,
    project_db_cleaner,
    request_create_project: Callable[..., Awaitable[ProjectDict]],
):
    new_project = await request_create_project(
        client,
        expected.accepted,
        expected.created,
        logged_user,
        primary_group,
        from_study=template_project,
    )

    if new_project:
        # check uuid replacement
        for node_name in new_project["workbench"]:
            try:
                uuidlib.UUID(node_name)
            except ValueError:
                pytest.fail(f"Invalid uuid in workbench node {node_name}")


@pytest.mark.parametrize(*standard_role_response())
async def test_new_project_from_other_study(
    client,
    logged_user,
    primary_group: dict[str, str],
    user_project,
    expected,
    storage_subsystem_mock,
    catalog_subsystem_mock,
    project_db_cleaner,
    request_create_project: Callable[..., Awaitable[ProjectDict]],
):
    catalog_subsystem_mock([user_project])
    new_project = await request_create_project(
        client,
        expected.accepted,
        expected.created,
        logged_user,
        primary_group,
        from_study=user_project,
    )

    if new_project:
        # check uuid replacement
        assert new_project["name"].endswith("(Copy)")
        for node_name in new_project["workbench"]:
            try:
                uuidlib.UUID(node_name)
            except ValueError:
                pytest.fail(f"Invalid uuid in workbench node {node_name}")


@pytest.mark.parametrize(*standard_role_response())
async def test_new_project_from_template_with_body(
    client,
    logged_user,
    primary_group: dict[str, str],
    standard_groups: list[dict[str, str]],
    template_project,
    expected,
    storage_subsystem_mock,
    project_db_cleaner,
    request_create_project: Callable[..., Awaitable[ProjectDict]],
):
    predefined = {
        "uuid": "",
        "name": "Sleepers8",
        "description": "Some lines from user",
        "thumbnail": "",
        "prjOwner": "",
        "creationDate": "2019-06-03T09:59:31.987Z",
        "lastChangeDate": "2019-06-03T09:59:31.987Z",
        "accessRights": {
            str(standard_groups[0]["gid"]): {
                "read": True,
                "write": True,
                "delete": False,
            }
        },
        "workbench": {},
        "tags": [],
        "classifiers": [],
    }
    project = await request_create_project(
        client,
        expected.accepted,
        expected.created,
        logged_user,
        primary_group,
        project=predefined,
        from_study=template_project,
    )

    if project:
        # uses predefined
        assert project["name"] == predefined["name"]
        assert project["description"] == predefined["description"]

        # different uuids for project and nodes!?
        assert project["uuid"] != template_project["uuid"]

        # check uuid replacement
        for node_name in project["workbench"]:
            try:
                uuidlib.UUID(node_name)
            except ValueError:
                pytest.fail(f"Invalid uuid in workbench node {node_name}")


@pytest.mark.parametrize(*standard_role_response())
async def test_new_template_from_project(
    client: TestClient,
    logged_user: dict[str, Any],
    primary_group: dict[str, str],
    all_group: dict[str, str],
    user_project: dict[str, Any],
    expected: ExpectedResponse,
    storage_subsystem_mock: MockedStorageSubsystem,
    catalog_subsystem_mock: Callable,
    project_db_cleaner: None,
    request_create_project: Callable[..., Awaitable[ProjectDict]],
):
    assert client.app
    new_template_prj = await request_create_project(
        client,
        expected.accepted,
        expected.created,
        logged_user,
        primary_group,
        from_study=user_project,
        as_template=True,
    )

    if new_template_prj:
        template_project = new_template_prj
        catalog_subsystem_mock([template_project])

        templates, *_ = await _list_projects(client, web.HTTPOk, {"type": "template"})

        assert len(templates) == 1
        assert templates[0] == template_project

        assert template_project["name"] == user_project["name"]
        assert template_project["description"] == user_project["description"]
        assert template_project["prjOwner"] == logged_user["email"]
        assert template_project["accessRights"] == user_project["accessRights"]

        # different timestamps
        assert to_datetime(user_project["creationDate"]) < to_datetime(
            template_project["creationDate"]
        )
        assert to_datetime(user_project["lastChangeDate"]) < to_datetime(
            template_project["lastChangeDate"]
        )

        # different uuids for project and nodes!?
        assert template_project["uuid"] != user_project["uuid"]

        # check uuid replacement
        for node_name in template_project["workbench"]:
            try:
                uuidlib.UUID(node_name)
            except ValueError:
                pytest.fail(f"Invalid uuid in workbench node {node_name}")

    # do the same with a body
    predefined = {
        "uuid": "",
        "name": "My super duper new template",
        "description": "Some lines from user",
        "thumbnail": "",
        "prjOwner": "",
        "creationDate": "2019-06-03T09:59:31.987Z",
        "lastChangeDate": "2019-06-03T09:59:31.987Z",
        "workbench": {},
        "accessRights": {
            str(all_group["gid"]): {"read": True, "write": False, "delete": False},
        },
        "tags": [],
        "classifiers": [],
    }
    new_template_prj = await request_create_project(
        client,
        expected.accepted,
        expected.created,
        logged_user,
        primary_group,
        project=predefined,
        from_study=user_project,
        as_template=True,
    )

    if new_template_prj:
        template_project = new_template_prj

        # uses predefined
        assert template_project["name"] == predefined["name"]
        assert template_project["description"] == predefined["description"]
        assert template_project["prjOwner"] == logged_user["email"]
        # the logged in user access rights are added by default
        predefined["accessRights"].update(
            {str(primary_group["gid"]): {"read": True, "write": True, "delete": True}}
        )
        assert template_project["accessRights"] == predefined["accessRights"]

        # different ownership
        assert template_project["prjOwner"] == logged_user["email"]
        assert template_project["prjOwner"] == user_project["prjOwner"]

        # different timestamps
        assert to_datetime(user_project["creationDate"]) < to_datetime(
            template_project["creationDate"]
        )
        assert to_datetime(user_project["lastChangeDate"]) < to_datetime(
            template_project["lastChangeDate"]
        )

        # different uuids for project and nodes!?
        assert template_project["uuid"] != user_project["uuid"]

        # check uuid replacement
        for node_name in template_project["workbench"]:
            try:
                uuidlib.UUID(node_name)
            except ValueError:
                pytest.fail(f"Invalid uuid in workbench node {node_name}")


# PUT --------
@pytest.mark.parametrize(
    "user_role,expected,expected_change_access",
    [
        (UserRole.ANONYMOUS, web.HTTPUnauthorized, web.HTTPUnauthorized),
        (UserRole.GUEST, web.HTTPOk, web.HTTPForbidden),
        (UserRole.USER, web.HTTPOk, web.HTTPOk),
        (UserRole.TESTER, web.HTTPOk, web.HTTPOk),
    ],
)
async def test_replace_project(
    client,
    logged_user,
    user_project,
    expected,
    expected_change_access,
    all_group,
    ensure_run_in_sequence_context_is_empty,
):
    project_update = deepcopy(user_project)
    project_update["description"] = "some updated from original project!!!"
    await _replace_project(client, project_update, expected)

    # replacing the owner access is not possible, it will keep the owner as well
    project_update["accessRights"].update(
        {str(all_group["gid"]): {"read": True, "write": True, "delete": True}}
    )
    await _replace_project(client, project_update, expected_change_access)


@pytest.mark.parametrize(
    "user_role,expected",
    [
        (UserRole.ANONYMOUS, web.HTTPUnauthorized),
        (UserRole.GUEST, web.HTTPOk),
        (UserRole.USER, web.HTTPOk),
        (UserRole.TESTER, web.HTTPOk),
    ],
)
async def test_replace_project_updated_inputs(
    client, logged_user, user_project, expected, ensure_run_in_sequence_context_is_empty
):
    project_update = deepcopy(user_project)
    #
    # "inputAccess": {
    #    "Na": "ReadAndWrite", <--------
    #    "Kr": "ReadOnly",
    #    "BCL": "ReadAndWrite",
    #    "NBeats": "ReadOnly",
    #    "Ligand": "Invisible",
    #    "cAMKII": "Invisible"
    #  },
    project_update["workbench"]["5739e377-17f7-4f09-a6ad-62659fb7fdec"]["inputs"][
        "Na"
    ] = 55
    await _replace_project(client, project_update, expected)


@pytest.mark.parametrize(
    "user_role,expected",
    [
        (UserRole.ANONYMOUS, web.HTTPUnauthorized),
        (UserRole.GUEST, web.HTTPOk),
        (UserRole.USER, web.HTTPOk),
        (UserRole.TESTER, web.HTTPOk),
    ],
)
async def test_replace_project_updated_readonly_inputs(
    client, logged_user, user_project, expected, ensure_run_in_sequence_context_is_empty
):
    project_update = deepcopy(user_project)
    project_update["workbench"]["5739e377-17f7-4f09-a6ad-62659fb7fdec"]["inputs"][
        "Na"
    ] = 55
    project_update["workbench"]["5739e377-17f7-4f09-a6ad-62659fb7fdec"]["inputs"][
        "Kr"
    ] = 5
    await _replace_project(client, project_update, expected)

from pathlib import Path
from typing import Awaitable, Callable

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient
from faker import Faker
from models_library.api_schemas_storage import DatasetMetaDataGet, FileMetaDataGet
from models_library.projects import ProjectID
from models_library.projects_nodes_io import SimcoreS3FileID
from models_library.users import UserID
from pydantic import ByteSize, parse_obj_as
from pytest_simcore.helpers.utils_assert import assert_status
from pytest_simcore.helpers.utils_parametrizations import byte_size_ids
from tests.helpers.file_utils import parametrized_file_size

pytest_simcore_core_services_selection = ["postgres"]
pytest_simcore_ops_services_selection = ["adminer"]


async def test_get_files_metadata_dataset_with_no_files_returns_empty_array(
    client: TestClient,
    user_id: UserID,
    project_id: ProjectID,
    location_id: int,
):
    assert client.app
    url = (
        client.app.router["get_files_metadata_dataset"]
        .url_for(location_id=f"{location_id}", dataset_id=f"{project_id}")
        .with_query(user_id=user_id)
    )
    response = await client.get(f"{url}")
    data, error = await assert_status(response, web.HTTPOk)
    assert data == []
    assert not error


@pytest.mark.parametrize(
    "file_size",
    [parametrized_file_size("100Mib")],
    ids=byte_size_ids,
)
async def test_get_files_metadata_dataset(
    upload_file: Callable[[ByteSize, str], Awaitable[tuple[Path, SimcoreS3FileID]]],
    client: TestClient,
    user_id: UserID,
    project_id: ProjectID,
    location_id: int,
    file_size: ByteSize,
    faker: Faker,
):
    assert client.app
    NUM_FILES = 3
    for n in range(NUM_FILES):
        file, file_id = await upload_file(file_size, faker.file_name())
        url = (
            client.app.router["get_files_metadata_dataset"]
            .url_for(location_id=f"{location_id}", dataset_id=f"{project_id}")
            .with_query(user_id=user_id)
        )
        response = await client.get(f"{url}")
        data, error = await assert_status(response, web.HTTPOk)
        assert data
        assert not error
        list_fmds = parse_obj_as(list[FileMetaDataGet], data)
        assert len(list_fmds) == (n + 1)
        fmd = list_fmds[n]
        assert fmd.file_name == file.name
        assert fmd.file_id == file_id
        assert fmd.file_uuid == file_id
        assert fmd.file_size == file.stat().st_size


async def test_get_datasets_metadata(
    client: TestClient,
    user_id: UserID,
    location_id: int,
    project_id: ProjectID,
):
    assert client.app

    url = (
        client.app.router["get_datasets_metadata"]
        .url_for(location_id=f"{location_id}")
        .with_query(user_id=f"{user_id}")
    )

    response = await client.get(f"{url}")
    data, error = await assert_status(response, web.HTTPOk)
    assert data
    assert not error
    list_datasets = parse_obj_as(list[DatasetMetaDataGet], data)
    assert len(list_datasets) == 1
    dataset = list_datasets[0]
    assert dataset.dataset_id == project_id

# pylint:disable=unused-variable
# pylint:disable=unused-argument
# pylint:disable=redefined-outer-name

from filecmp import cmpfiles
from pathlib import Path
from shutil import copy, make_archive, unpack_archive
from typing import Callable, Iterator

import pytest
from simcore_sdk.node_data import data_manager
from simcore_sdk.node_ports_common.constants import SIMCORE_LOCATION


@pytest.fixture
def create_files() -> Iterator[Callable[..., list[Path]]]:
    created_files = []

    def _create_files(number: int, folder: Path) -> list[Path]:

        for i in range(number):
            file_path = folder / f"{i}.test"
            file_path.write_text(f"I am test file number {i}")
            assert file_path.exists()
            created_files.append(file_path)
        return created_files

    yield _create_files
    # cleanup
    for file_path in created_files:
        file_path.unlink()


async def test_push_folder(
    user_id: int,
    project_id: str,
    node_uuid: str,
    mocker,
    tmpdir: Path,
    create_files: Callable,
):
    # create some files
    assert tmpdir.exists()

    # create a folder to compress from
    test_folder = Path(tmpdir) / "test_folder"
    test_folder.mkdir()
    assert test_folder.exists()

    # create a folder to compress in
    test_compression_folder = Path(tmpdir) / "test_compression_folder"
    test_compression_folder.mkdir()
    assert test_compression_folder.exists()

    # mocks
    mock_filemanager = mocker.patch(
        "simcore_sdk.node_data.data_manager.filemanager", spec=True
    )
    mock_filemanager.upload_file.return_value = ""
    mock_temporary_directory = mocker.patch(
        "simcore_sdk.node_data.data_manager.TemporaryDirectory"
    )
    mock_temporary_directory.return_value.__enter__.return_value = (
        test_compression_folder
    )

    files_number = 10
    create_files(files_number, test_folder)
    assert len(list(test_folder.glob("**/*"))) == files_number
    for file_path in test_folder.glob("**/*"):
        assert file_path.exists()

    await data_manager.push(
        user_id, project_id, node_uuid, test_folder, io_log_redirect_cb=None
    )

    mock_temporary_directory.assert_called_once()
    mock_filemanager.upload_file.assert_called_once_with(
        file_to_upload=(test_compression_folder / f"{test_folder.stem}.zip"),
        r_clone_settings=None,
        io_log_redirect_cb=None,
        s3_object=f"{project_id}/{node_uuid}/{test_folder.stem}.zip",
        store_id=SIMCORE_LOCATION,
        store_name=None,
        user_id=user_id,
    )

    archive_file = test_compression_folder / f"{test_folder.stem}.zip"
    assert archive_file.exists()

    # create control folder
    control_folder = Path(tmpdir) / "control_folder"
    control_folder.mkdir()
    assert control_folder.exists()
    unpack_archive(f"{archive_file}", extract_dir=control_folder)
    matchs, mismatchs, errors = cmpfiles(
        test_folder, control_folder, [x.name for x in test_folder.glob("**/*")]
    )
    assert len(matchs) == files_number
    assert not mismatchs
    assert not errors


async def test_push_file(
    user_id: int,
    project_id: str,
    node_uuid: str,
    mocker,
    tmpdir: Path,
    create_files: Callable,
):
    mock_filemanager = mocker.patch(
        "simcore_sdk.node_data.data_manager.filemanager", spec=True
    )
    mock_filemanager.upload_file.return_value = ""
    mock_temporary_directory = mocker.patch(
        "simcore_sdk.node_data.data_manager.TemporaryDirectory"
    )

    file_path = create_files(1, Path(tmpdir))[0]
    assert file_path.exists()

    # test push file by file
    await data_manager.push(
        user_id, project_id, node_uuid, file_path, io_log_redirect_cb=None
    )
    mock_temporary_directory.assert_not_called()
    mock_filemanager.upload_file.assert_called_once_with(
        r_clone_settings=None,
        io_log_redirect_cb=None,
        file_to_upload=file_path,
        s3_object=f"{project_id}/{node_uuid}/{file_path.name}",
        store_id=SIMCORE_LOCATION,
        store_name=None,
        user_id=user_id,
    )
    mock_filemanager.reset_mock()


async def test_pull_folder(
    user_id: int,
    project_id: str,
    node_uuid: str,
    mocker,
    tmpdir: Path,
    create_files: Callable,
):
    assert tmpdir.exists()
    # create a folder to compress from
    test_control_folder = Path(tmpdir) / "test_control_folder"
    test_control_folder.mkdir()
    assert test_control_folder.exists()
    # create the test folder
    test_folder = Path(tmpdir) / "test_folder"
    test_folder.mkdir()
    assert test_folder.exists()
    # create a folder to compress in
    test_compression_folder = Path(tmpdir) / "test_compression_folder"
    test_compression_folder.mkdir()
    assert test_compression_folder.exists()
    # create the zip file
    files_number = 12
    create_files(files_number, test_control_folder)
    compressed_file_name = test_compression_folder / test_folder.stem
    archive_file = make_archive(
        f"{compressed_file_name}", "zip", root_dir=test_control_folder
    )
    assert Path(archive_file).exists()
    # create mock downloaded folder
    test_download_folder = Path(tmpdir) / "test_download_folder"
    test_download_folder.mkdir()
    assert test_download_folder.exists()
    fake_zipped_folder = test_download_folder / Path(archive_file).name
    copy(archive_file, fake_zipped_folder)

    mock_filemanager = mocker.patch(
        "simcore_sdk.node_data.data_manager.filemanager", spec=True
    )
    mock_filemanager.download_file_from_s3.return_value = fake_zipped_folder
    mock_temporary_directory = mocker.patch(
        "simcore_sdk.node_data.data_manager.TemporaryDirectory"
    )
    mock_temporary_directory.return_value.__enter__.return_value = (
        test_compression_folder
    )

    await data_manager.pull(
        user_id, project_id, node_uuid, test_folder, io_log_redirect_cb=None
    )
    mock_temporary_directory.assert_called_once()
    mock_filemanager.download_file_from_s3.assert_called_once_with(
        local_folder=test_compression_folder,
        s3_object=f"{project_id}/{node_uuid}/{test_folder.stem}.zip",
        store_id=SIMCORE_LOCATION,
        store_name=None,
        user_id=user_id,
        io_log_redirect_cb=None,
    )

    matchs, mismatchs, errors = cmpfiles(
        test_folder,
        test_control_folder,
        [x.name for x in test_control_folder.glob("**/*")],
    )
    assert len(matchs) == files_number
    assert not mismatchs
    assert not errors


async def test_pull_file(
    user_id: int,
    project_id: str,
    node_uuid: str,
    mocker,
    tmpdir: Path,
    create_files: Callable,
):
    file_path = create_files(1, Path(tmpdir))[0]
    assert file_path.exists()
    assert file_path.is_file()

    fake_download_folder = Path(tmpdir) / "download_folder"
    fake_download_folder.mkdir()
    fake_downloaded_file = fake_download_folder / file_path.name
    copy(file_path, fake_downloaded_file)

    mock_filemanager = mocker.patch(
        "simcore_sdk.node_data.data_manager.filemanager", spec=True
    )
    mock_filemanager.download_file_from_s3.return_value = fake_downloaded_file
    mock_temporary_directory = mocker.patch(
        "simcore_sdk.node_data.data_manager.TemporaryDirectory"
    )

    await data_manager.pull(
        user_id, project_id, node_uuid, file_path, io_log_redirect_cb=None
    )
    mock_temporary_directory.assert_not_called()
    mock_filemanager.download_file_from_s3.assert_called_once_with(
        local_folder=file_path.parent,
        s3_object=f"{project_id}/{node_uuid}/{file_path.name}",
        store_id=SIMCORE_LOCATION,
        store_name=None,
        user_id=user_id,
        io_log_redirect_cb=None,
    )

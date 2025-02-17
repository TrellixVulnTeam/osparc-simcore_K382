import logging
from contextlib import contextmanager
from typing import Callable, Optional

import asyncpg.exceptions
from aiohttp import web
from aiopg.sa.result import RowProxy
from models_library.users import GroupID, UserID
from simcore_postgres_database.errors import DatabaseError

from . import users_exceptions
from .db_models import GroupType
from .groups_api import get_group_from_gid
from .projects.projects_db import APP_PROJECT_DBAPI, ProjectAccessRights
from .projects.projects_exceptions import ProjectNotFoundError
from .users_api import get_user, get_user_id_from_gid
from .users_exceptions import UserNotFoundError
from .users_to_groups_api import get_users_for_gid

logger = logging.getLogger(__name__)


async def _fetch_new_project_owner_from_groups(
    app: web.Application, standard_groups: dict, user_id: UserID
) -> Optional[UserID]:
    """Iterate over all the users in a group and if the users exists in the db
    return its gid
    """

    # fetch all users in the group and then get their gid to put in here
    # go through user_to_groups table and fetch all uid for matching gid
    for group_gid in standard_groups.keys():
        # remove the current owner from the bunch
        target_group_users = await get_users_for_gid(app=app, gid=group_gid) - {user_id}
        logger.info("Found group users '%s'", target_group_users)

        for possible_user_id in target_group_users:
            # check if the possible_user is still present in the db
            try:
                possible_user = await get_user(app=app, user_id=possible_user_id)
                return int(possible_user["primary_gid"])
            except users_exceptions.UserNotFoundError:
                logger.warning(
                    "Could not find new owner '%s' will try a new one",
                    possible_user_id,
                )

        return None


async def get_new_project_owner_gid(
    app: web.Application,
    project_uuid: str,
    user_id: UserID,
    user_primary_gid: GroupID,
    project: dict,
) -> Optional[GroupID]:
    """Goes through the access rights and tries to find a new suitable owner.
    The first viable user is selected as a new owner.
    In order to become a new owner the user must have write access right.
    """

    access_rights = project["accessRights"]
    # A Set[str] is prefered over Set[int] because access_writes
    # is a Dict with only key,valus in {str, None}
    other_users_access_rights: set[str] = set(access_rights.keys()) - {
        f"{user_primary_gid}"
    }
    logger.debug(
        "Processing other user and groups access rights '%s'",
        other_users_access_rights,
    )

    # Selecting a new project owner
    # divide permissions between types of groups
    standard_groups = {}  # groups of users, multiple users can be part of this
    primary_groups = {}  # each individual user has a unique primary group
    for other_gid in other_users_access_rights:
        group: Optional[RowProxy] = await get_group_from_gid(
            app=app, gid=int(other_gid)
        )

        # only process for users and groups with write access right
        if group is None:
            continue
        if access_rights[other_gid]["write"] is not True:
            continue

        if group.type == GroupType.STANDARD:
            standard_groups[other_gid] = access_rights[other_gid]
        elif group.type == GroupType.PRIMARY:
            primary_groups[other_gid] = access_rights[other_gid]

    logger.debug(
        "Possible new owner groups: standard='%s', primary='%s'",
        standard_groups,
        primary_groups,
    )

    new_project_owner_gid = None
    # the primary group contains the users which which the project was directly shared
    if len(primary_groups) > 0:
        # fetch directly from the direct users with which the project is shared with
        new_project_owner_gid = int(list(primary_groups.keys())[0])
    # fallback to the groups search if the user does not exist
    if len(standard_groups) > 0 and new_project_owner_gid is None:
        new_project_owner_gid = await _fetch_new_project_owner_from_groups(
            app=app,
            standard_groups=standard_groups,
            user_id=user_id,
        )

    logger.info(
        "Will move project '%s' to user with gid '%s', if user exists",
        project_uuid,
        new_project_owner_gid,
    )

    return new_project_owner_gid


async def replace_current_owner(
    app: web.Application,
    project_uuid: str,
    user_primary_gid: GroupID,
    new_project_owner_gid: GroupID,
    project: dict,
) -> None:
    try:
        new_project_owner_id = await get_user_id_from_gid(
            app=app, primary_gid=new_project_owner_gid
        )

    except (
        DatabaseError,
        asyncpg.exceptions.PostgresError,
        ProjectNotFoundError,
        UserNotFoundError,
    ):
        logger.exception(
            "Could not recover new user id from gid %s", new_project_owner_gid
        )
        return

    # the result might me none
    if new_project_owner_id is None:
        logger.warning(
            "Could not recover a new user id from gid %s", new_project_owner_gid
        )
        return

    # unseting the project owner and saving the project back
    project["prj_owner"] = int(new_project_owner_id)
    # removing access rights entry
    del project["accessRights"][f"{user_primary_gid}"]
    project["accessRights"][
        f"{new_project_owner_gid}"
    ] = ProjectAccessRights.OWNER.value
    logger.info("Syncing back project %s", project)

    # syncing back project data
    try:
        await app[APP_PROJECT_DBAPI].update_project_without_checking_permissions(
            project_data=project,
            project_uuid=project_uuid,
        )
    except (
        DatabaseError,
        asyncpg.exceptions.PostgresError,
        ProjectNotFoundError,
        UserNotFoundError,
    ):
        logger.exception(
            "Could not remove old owner and replaced it with user %s",
            new_project_owner_id,
        )


@contextmanager
def log_context(log: Callable, message: str):
    """Logs entering/existing context as start/done and informs if exited with error"""
    # NOTE: could have been done with a LoggerAdapter but wonder if it would work
    # with a global logger and asyncio context switches
    # PC: I still do not find useful enought to move it to e.g. servicelib.logging_utils
    try:
        log("%s [STARTING]", message)

        yield

        log("%s [DONE w/ SUCCESS]", message)

    except Exception as e:  # pylint: disable=broad-except
        log("%s [DONE w/ ERROR %s]", message, type(e))
        raise

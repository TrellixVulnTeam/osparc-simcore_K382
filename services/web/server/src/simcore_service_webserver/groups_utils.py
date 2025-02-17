import logging
from typing import Any, Dict, Mapping, Optional, TypedDict

from aiopg.sa.result import RowProxy

from .groups_exceptions import UserInsufficientRightsError
from .users_utils import convert_user_db_to_schema

logger = logging.getLogger(__name__)

GROUPS_SCHEMA_TO_DB = {
    "gid": "gid",
    "label": "name",
    "description": "description",
    "thumbnail": "thumbnail",
    "accessRights": "access_rights",
    "inclusionRules": "inclusion_rules",
}


class AccessRightsDict(TypedDict):
    read: bool
    write: bool
    delete: bool


def check_group_permissions(
    group: RowProxy, user_id: int, gid: int, permission: str
) -> None:
    if not group.access_rights[permission]:
        raise UserInsufficientRightsError(
            f"User {user_id} has insufficient rights for {permission} access to group {gid}"
        )


def convert_groups_db_to_schema(
    db_row: RowProxy, *, prefix: Optional[str] = "", **kwargs
) -> Dict:
    converted_dict = {
        k: db_row[f"{prefix}{v}"]
        for k, v in GROUPS_SCHEMA_TO_DB.items()
        if f"{prefix}{v}" in db_row
    }
    converted_dict.update(**kwargs)
    return converted_dict


def convert_groups_schema_to_db(schema: Dict) -> Dict:
    return {
        v: schema[k]
        for k, v in GROUPS_SCHEMA_TO_DB.items()
        if k in schema and k != "gid"
    }


def convert_user_in_group_to_schema(user: Mapping[str, Any]) -> Dict[str, str]:
    group_user = convert_user_db_to_schema(user)
    group_user.pop("role")
    group_user["accessRights"] = user["access_rights"]
    group_user["gid"] = user["primary_gid"]
    return group_user

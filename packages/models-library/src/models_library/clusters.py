from typing import Dict, Final, Literal, Optional, Union

from pydantic import AnyUrl, BaseModel, Extra, Field, HttpUrl, SecretStr, root_validator
from pydantic.types import NonNegativeInt
from simcore_postgres_database.models.clusters import ClusterType

from .users import GroupID


class ClusterAccessRights(BaseModel):
    read: bool = Field(..., description="allows to run pipelines on that cluster")
    write: bool = Field(..., description="allows to modify the cluster")
    delete: bool = Field(..., description="allows to delete a cluster")

    class Config:
        extra = Extra.forbid


CLUSTER_ADMIN_RIGHTS = ClusterAccessRights(read=True, write=True, delete=True)
CLUSTER_MANAGER_RIGHTS = ClusterAccessRights(read=True, write=True, delete=False)
CLUSTER_USER_RIGHTS = ClusterAccessRights(read=True, write=False, delete=False)
CLUSTER_NO_RIGHTS = ClusterAccessRights(read=False, write=False, delete=False)


class BaseAuthentication(BaseModel):
    type: str

    class Config:
        extra = Extra.forbid


class SimpleAuthentication(BaseAuthentication):
    type: Literal["simple"] = "simple"
    username: str
    password: SecretStr

    class Config(BaseAuthentication.Config):
        schema_extra = {
            "examples": [
                {
                    "type": "simple",
                    "username": "someuser",
                    "password": "somepassword",
                },
            ]
        }


class KerberosAuthentication(BaseAuthentication):
    type: Literal["kerberos"] = "kerberos"
    # NOTE: the entries here still need to be defined
    class Config(BaseAuthentication.Config):
        schema_extra = {
            "examples": [
                {
                    "type": "kerberos",
                },
            ]
        }


class JupyterHubTokenAuthentication(BaseAuthentication):
    type: Literal["jupyterhub"] = "jupyterhub"
    api_token: str

    class Config(BaseAuthentication.Config):
        schema_extra = {
            "examples": [
                {"type": "jupyterhub", "api_token": "some_jupyterhub_token"},
            ]
        }


class NoAuthentication(BaseAuthentication):
    type: Literal["none"] = "none"


InternalClusterAuthentication = NoAuthentication
ExternalClusterAuthentication = Union[
    SimpleAuthentication, KerberosAuthentication, JupyterHubTokenAuthentication
]
ClusterAuthentication = Union[
    ExternalClusterAuthentication,
    InternalClusterAuthentication,
]


class BaseCluster(BaseModel):
    name: str = Field(..., description="The human readable name of the cluster")
    description: Optional[str] = None
    type: ClusterType
    owner: GroupID
    thumbnail: Optional[HttpUrl] = Field(
        None,
        description="url to the image describing this cluster",
        examples=["https://placeimg.com/171/96/tech/grayscale/?0.jpg"],
    )
    endpoint: AnyUrl
    authentication: ClusterAuthentication = Field(
        ..., description="Dask gateway authentication"
    )
    access_rights: Dict[GroupID, ClusterAccessRights] = Field(default_factory=dict)

    class Config:
        extra = Extra.forbid
        use_enum_values = True


ClusterID = NonNegativeInt
DEFAULT_CLUSTER_ID: Final[NonNegativeInt] = 0


class Cluster(BaseCluster):
    id: ClusterID = Field(..., description="The cluster ID")

    class Config(BaseCluster.Config):
        schema_extra = {
            "examples": [
                {
                    "id": DEFAULT_CLUSTER_ID,
                    "name": "The default cluster",
                    "type": ClusterType.ON_PREMISE,
                    "owner": 1456,
                    "endpoint": "tcp://default-dask-scheduler:8786",
                    "authentication": {
                        "type": "simple",
                        "username": "someuser",
                        "password": "somepassword",
                    },
                },
                {
                    "id": 432,
                    "name": "My awesome cluster",
                    "type": ClusterType.ON_PREMISE,
                    "owner": 12,
                    "endpoint": "https://registry.osparc-development.fake.dev",
                    "authentication": {
                        "type": "simple",
                        "username": "someuser",
                        "password": "somepassword",
                    },
                },
                {
                    "id": 432546,
                    "name": "My AWS cluster",
                    "description": "a AWS cluster administered by me",
                    "type": ClusterType.AWS,
                    "owner": 154,
                    "endpoint": "https://registry.osparc-development.fake.dev",
                    "authentication": {"type": "kerberos"},
                    "access_rights": {
                        154: CLUSTER_ADMIN_RIGHTS,
                        12: CLUSTER_MANAGER_RIGHTS,
                        7899: CLUSTER_USER_RIGHTS,
                    },
                },
                {
                    "id": 325436,
                    "name": "My AWS cluster",
                    "description": "a AWS cluster administered by me",
                    "type": ClusterType.AWS,
                    "owner": 2321,
                    "endpoint": "https://registry.osparc-development.fake2.dev",
                    "authentication": {
                        "type": "jupyterhub",
                        "api_token": "some_fake_token",
                    },
                    "access_rights": {
                        154: CLUSTER_ADMIN_RIGHTS,
                        12: CLUSTER_MANAGER_RIGHTS,
                        7899: CLUSTER_USER_RIGHTS,
                    },
                },
            ]
        }

    @root_validator(pre=True)
    @classmethod
    def check_owner_has_access_rights(cls, values):
        is_default_cluster = bool(values["id"] == DEFAULT_CLUSTER_ID)
        owner_gid = values["owner"]

        # check owner is in the access rights, if not add it
        access_rights = values.get("access_rights", values.get("accessRights", {}))
        if owner_gid not in access_rights:
            access_rights[owner_gid] = (
                CLUSTER_USER_RIGHTS if is_default_cluster else CLUSTER_ADMIN_RIGHTS
            )
        # check owner has the expected access
        if access_rights[owner_gid] != (
            CLUSTER_USER_RIGHTS if is_default_cluster else CLUSTER_ADMIN_RIGHTS
        ):
            raise ValueError(
                f"the cluster owner access rights are incorrectly set: {access_rights[owner_gid]}"
            )
        values["access_rights"] = access_rights
        return values

from aiodocker.exceptions import DockerError
from models_library.projects_nodes import NodeID

from ...core.errors import DirectorException


class DynamicSidecarError(DirectorException):
    pass


class GenericDockerError(DynamicSidecarError):
    """Generic docker library error"""

    def __init__(self, msg: str, original_exception: DockerError):
        super().__init__(msg + f": {original_exception.message}")
        self.original_exception = original_exception


class DynamicSidecarNotFoundError(DirectorException):
    """Dynamic sidecar was not found"""

    def __init__(self, node_uuid: NodeID):
        super().__init__(f"node {node_uuid} not found")


class EntrypointContainerNotFoundError(DirectorException):
    """Raised while the entrypoint container was nto yet started"""


class LegacyServiceIsNotSupportedError(DirectorException):
    """This API is not implemented by the director-v0"""

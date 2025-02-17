""" Free functions to inject dependencies in routes handlers
"""


from fastapi import Depends, FastAPI, Request
from fastapi.datastructures import State

from ..core.rabbitmq import RabbitMQ
from ..core.settings import ApplicationSettings
from ..models.schemas.application_health import ApplicationHealth
from ..models.shared_store import SharedStore
from ..modules.mounted_fs import MountedVolumes


def get_application(request: Request) -> FastAPI:
    return request.app


def get_app_state(request: Request) -> State:
    return request.app.state


def get_application_health(
    app_state: State = Depends(get_app_state),
) -> ApplicationHealth:
    return app_state.application_health  # type: ignore


def get_settings(app_state: State = Depends(get_app_state)) -> ApplicationSettings:
    return app_state.settings  # type: ignore


def get_shared_store(app_state: State = Depends(get_app_state)) -> SharedStore:
    return app_state.shared_store  # type: ignore


def get_rabbitmq(app_state: State = Depends(get_app_state)) -> RabbitMQ:
    return app_state.rabbitmq  # type: ignore


def get_mounted_volumes(app_state: State = Depends(get_app_state)) -> MountedVolumes:
    return app_state.mounted_volumes  # type: ignore

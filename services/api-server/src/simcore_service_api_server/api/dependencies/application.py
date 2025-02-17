from typing import Any, Callable

from fastapi import Request

from ...core.settings import ApplicationSettings


def get_reverse_url_mapper(request: Request) -> Callable:
    def reverse_url_mapper(name: str, **path_params: Any) -> str:
        return request.url_for(name, **path_params)

    return reverse_url_mapper


def get_settings(request: Request) -> ApplicationSettings:
    return request.app.state.settings

import asyncio
import functools
import inspect
import logging
from typing import Any, Awaitable, Callable, Optional

from httpx import AsyncClient, ConnectError, HTTPError, PoolTimeout, Response
from httpx._types import TimeoutTypes, URLTypes
from tenacity import RetryCallState
from tenacity._asyncio import AsyncRetrying
from tenacity.before_sleep import before_sleep_log
from tenacity.retry import retry_if_exception_type
from tenacity.stop import stop_after_attempt
from tenacity.wait import wait_exponential

from ._errors import ClientHttpError, UnexpectedStatusError, _WrongReturnType

logger = logging.getLogger(__name__)


def _log_requests_in_pool(client: AsyncClient, event_name: str) -> None:
    # pylint: disable=protected-access
    logger.warning(
        "Requests while event '%s': %s",
        event_name.upper(),
        [
            (r.request.method, r.request.url, r.request.headers)
            for r in client._transport._pool._requests
        ],
    )


def _after_log(
    log: logging.Logger, max_retries: int
) -> Callable[[RetryCallState], None]:
    def log_it(retry_state: RetryCallState) -> None:
        # pylint: disable=protected-access

        assert retry_state.outcome  # nosec
        e = retry_state.outcome.exception()
        assert isinstance(e, HTTPError)  # nosec
        log.error(
            "Unexpected error with '%s': %s, (attempt [%s/%s])",
            f"{e.request=}",
            f"{e=}",
            retry_state.attempt_number,
            max_retries,
        )

    return log_it


def retry_on_errors(
    request_func: Callable[..., Awaitable[Response]]
) -> Callable[..., Awaitable[Response]]:
    """
    Will retry the request on `ConnectError` and `PoolTimeout`.
    Also wraps `httpx.HTTPError`
    raises:
    - `ClientHttpError`
    """
    assert asyncio.iscoroutinefunction(request_func)

    RETRY_ERRORS = (ConnectError, PoolTimeout)

    @functools.wraps(request_func)
    async def request_wrapper(zelf: "BaseThinClient", *args, **kwargs) -> Response:
        # pylint: disable=protected-access
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(zelf._request_max_retries),
                wait=wait_exponential(min=1),
                retry=retry_if_exception_type(RETRY_ERRORS),
                before_sleep=before_sleep_log(logger, logging.WARNING),
                after=_after_log(logger, zelf._request_max_retries),
                reraise=True,
            ):
                with attempt:
                    r: Response = await request_func(zelf, *args, **kwargs)
                    return r
        except HTTPError as e:
            if isinstance(e, PoolTimeout):
                _log_requests_in_pool(zelf._client, "pool timeout")
            raise ClientHttpError(e) from e

    return request_wrapper


def expect_status(expected_code: int):
    """
    raises an `UnexpectedStatusError` if the request's status is different
    from `expected_code`
    NOTE: always apply after `retry_on_errors`

    raises:
    - `UnexpectedStatusError`
    - `ClientHttpError`
    """

    def decorator(
        request_func: Callable[..., Awaitable[Response]]
    ) -> Callable[..., Awaitable[Response]]:
        assert asyncio.iscoroutinefunction(request_func)

        @functools.wraps(request_func)
        async def request_wrapper(zelf: "BaseThinClient", *args, **kwargs) -> Response:
            response = await request_func(zelf, *args, **kwargs)
            if response.status_code != expected_code:
                raise UnexpectedStatusError(response, expected_code)

            return response

        return request_wrapper

    return decorator


class BaseThinClient:
    SKIP_METHODS: set[str] = {"close"}

    def __init__(
        self,
        *,
        request_max_retries: int,
        base_url: Optional[URLTypes] = None,
        timeout: Optional[TimeoutTypes] = None,
    ) -> None:
        self._request_max_retries: int = request_max_retries

        client_args: dict[str, Any] = {}
        if base_url:
            client_args["base_url"] = base_url
        if timeout:
            client_args["timeout"] = timeout
        self._client = AsyncClient(**client_args)

        # ensure all user defined public methods return `httpx.Response`
        # NOTE: ideally these checks should be ran at import time!
        public_methods = [
            t[1]
            for t in inspect.getmembers(self, predicate=inspect.ismethod)
            if not (t[0].startswith("_") or t[0] in self.SKIP_METHODS)
        ]

        for method in public_methods:
            signature = inspect.signature(method)
            if signature.return_annotation != Response:
                raise _WrongReturnType(method, signature.return_annotation)

    async def close(self) -> None:
        _log_requests_in_pool(self._client, "closing")
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_t, exc_v, exc_tb):
        await self.close()

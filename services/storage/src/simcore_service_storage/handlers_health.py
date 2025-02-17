"""

    - Checks connectivity with other services in the backend

"""
import logging

from aiohttp.web import Request, RouteTableDef
from models_library.api_schemas_storage import HealthCheck, S3BucketName
from models_library.app_diagnostics import AppStatusCheck
from servicelib.aiohttp.rest_utils import extract_and_validate
from simcore_service_storage.constants import APP_CONFIG_KEY

from ._meta import api_version, api_version_prefix, app_name
from .db import get_engine_state
from .db import is_service_responsive as is_pg_responsive
from .exceptions import S3AccessError, S3BucketInvalidError
from .s3 import get_s3_client
from .settings import Settings

log = logging.getLogger(__name__)

routes = RouteTableDef()


@routes.get(f"/{api_version_prefix}/", name="health_check")  # type: ignore
async def get_health(request: Request):
    """Service health-check endpoint
    Some general information on the API and state of the service behind
    """
    log.debug("CHECK HEALTH INCOMING PATH %s", request.path)
    await extract_and_validate(request)

    return HealthCheck.parse_obj(
        {"name": app_name, "version": api_version, "api_version": api_version}
    ).dict(exclude_unset=True)


@routes.get(f"/{api_version_prefix}/status", name="get_status")  # type: ignore
async def get_status(request: Request):
    # NOTE: all calls here must NOT raise
    assert request.app  # nosec
    app_settings: Settings = request.app[APP_CONFIG_KEY]
    s3_state = "disabled"
    if app_settings.STORAGE_S3:
        try:
            await get_s3_client(request.app).check_bucket_connection(
                S3BucketName(app_settings.STORAGE_S3.S3_BUCKET_NAME)
            )
            s3_state = "connected"
        except S3BucketInvalidError:
            s3_state = "no access to S3 bucket"
        except S3AccessError:
            s3_state = "failed"

    postgres_state = "disabled"
    if app_settings.STORAGE_POSTGRES:
        postgres_state = (
            "connected" if await is_pg_responsive(request.app) else "failed"
        )

    status = AppStatusCheck.parse_obj(
        {
            "app_name": app_name,
            "version": api_version,
            "services": {
                "postgres": {
                    "healthy": postgres_state,
                    "pool": get_engine_state(request.app),
                },
                "s3": {"healthy": s3_state},
            },
        }
    )

    return status.dict(exclude_unset=True)

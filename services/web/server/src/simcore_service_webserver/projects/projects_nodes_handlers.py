""" Handlers for CRUD operations on /projects/{*}/nodes/{*}

"""

import json
import logging
from typing import Optional, Union

from aiohttp import web
from models_library.projects_nodes import NodeID
from models_library.services import ServiceKey, ServiceVersion

#
# projects/*/nodes COLLECTION -------------------------
#
from pydantic import BaseModel
from servicelib.aiohttp.requests_validation import (
    parse_request_body_as,
    parse_request_path_parameters_as,
)
from servicelib.json_serialization import json_dumps
from servicelib.mimetype_constants import MIMETYPE_APPLICATION_JSON

from .. import director_v2_api
from .._meta import api_version_prefix as VTAG
from ..login.decorators import login_required
from ..security_decorators import permission_required
from . import projects_api
from .projects_exceptions import NodeNotFoundError, ProjectNotFoundError
from .projects_handlers_crud import ProjectPathParams, RequestContext

log = logging.getLogger(__name__)


routes = web.RouteTableDef()


class _CreateNodeBody(BaseModel):
    service_key: ServiceKey
    service_version: ServiceVersion
    service_id: Optional[str] = None


@routes.post(f"/{VTAG}/projects/{{project_id}}/nodes")
@login_required
@permission_required("project.node.create")
async def create_node(request: web.Request) -> web.Response:
    req_ctx = RequestContext.parse_obj(request)
    path_params = parse_request_path_parameters_as(ProjectPathParams, request)
    body = await parse_request_body_as(_CreateNodeBody, request)

    if await projects_api.is_service_deprecated(
        request.app,
        req_ctx.user_id,
        body.service_key,
        body.service_version,
        req_ctx.product_name,
    ):
        raise web.HTTPNotAcceptable(
            reason=f"Service {body.service_key}:{body.service_version} is deprecated"
        )
    try:
        # ensure the project exists
        project_data = await projects_api.get_project_for_user(
            request.app,
            project_uuid=f"{path_params.project_id}",
            user_id=req_ctx.user_id,
            include_templates=True,
        )
        data = {
            "node_id": await projects_api.add_project_node(
                request,
                project_data,
                req_ctx.user_id,
                req_ctx.product_name,
                body.service_key,
                body.service_version,
                body.service_id,
            )
        }
        return web.json_response(
            {"data": data}, status=web.HTTPCreated.status_code, dumps=json_dumps
        )
    except ProjectNotFoundError as exc:
        raise web.HTTPNotFound(
            reason=f"Project {path_params.project_id} not found"
        ) from exc


class _NodePathParams(ProjectPathParams):
    node_id: NodeID


@routes.get(f"/{VTAG}/projects/{{project_id}}/nodes/{{node_id}}")
@login_required
@permission_required("project.node.read")
async def get_node(request: web.Request) -> web.Response:
    req_ctx = RequestContext.parse_obj(request)
    path_params = parse_request_path_parameters_as(_NodePathParams, request)

    try:
        # ensure the project exists
        project = await projects_api.get_project_for_user(
            request.app,
            project_uuid=f"{path_params.project_id}",
            user_id=req_ctx.user_id,
            include_templates=True,
        )

        if await projects_api.is_project_node_deprecated(
            request.app,
            req_ctx.user_id,
            project,
            path_params.node_id,
            req_ctx.product_name,
        ):
            project_node = project["workbench"][f"{path_params.node_id}"]
            raise web.HTTPNotAcceptable(
                reason=f"Service {project_node['key']}:{project_node['version']} is deprecated!"
            )

        # NOTE: for legacy services a redirect to director-v0 is made
        service_state: Union[
            dict, list
        ] = await director_v2_api.get_dynamic_service_state(
            app=request.app, node_uuid=f"{path_params.node_id}"
        )

        if "data" not in service_state:
            # dynamic-service NODE STATE
            return web.json_response({"data": service_state}, dumps=json_dumps)

        # LEGACY-service NODE STATE
        return web.json_response({"data": service_state["data"]}, dumps=json_dumps)
    except ProjectNotFoundError as exc:
        raise web.HTTPNotFound(
            reason=f"Project {path_params.project_id} not found"
        ) from exc
    except NodeNotFoundError as exc:
        raise web.HTTPNotFound(reason=f"Node {path_params.node_id} not found") from exc


@login_required
@permission_required("project.node.delete")
async def delete_node(request: web.Request) -> web.Response:
    req_ctx = RequestContext.parse_obj(request)
    path_params = parse_request_path_parameters_as(_NodePathParams, request)

    try:
        # ensure the project exists

        await projects_api.get_project_for_user(
            request.app,
            project_uuid=f"{path_params.project_id}",
            user_id=req_ctx.user_id,
            include_templates=True,
        )

        await projects_api.delete_project_node(
            request,
            f"{path_params.project_id}",
            req_ctx.user_id,
            f"{path_params.node_id}",
        )

        raise web.HTTPNoContent(content_type=MIMETYPE_APPLICATION_JSON)
    except ProjectNotFoundError as exc:
        raise web.HTTPNotFound(
            reason=f"Project {path_params.project_id} not found"
        ) from exc


@routes.post(f"/{VTAG}/projects/{{project_id}}/nodes/{{node_id}}:retrieve")
@login_required
@permission_required("project.node.read")
async def retrieve_node(request: web.Request) -> web.Response:
    """Has only effect on nodes associated to dynamic services"""
    path_params = parse_request_path_parameters_as(_NodePathParams, request)

    try:
        data = await request.json()
        port_keys = data.get("port_keys", [])
    except json.JSONDecodeError as exc:
        raise web.HTTPBadRequest(reason=f"Invalid request body: {exc}") from exc

    return web.json_response(
        await director_v2_api.retrieve(
            request.app, f"{path_params.node_id}", port_keys
        ),
        dumps=json_dumps,
    )


@routes.post(f"/{VTAG}/projects/{{project_id}}/nodes/{{node_id}}:restart")
@login_required
@permission_required("project.node.read")
async def restart_node(request: web.Request) -> web.Response:
    """Has only effect on nodes associated to dynamic services"""

    path_params = parse_request_path_parameters_as(_NodePathParams, request)

    await director_v2_api.restart_dynamic_service(request.app, f"{path_params.node_id}")

    return web.HTTPNoContent()


#
# projects/*/nodes/*/resources  COLLECTION -------------------------
#


@routes.get(f"/{VTAG}/projects/{{project_id}}/nodes/{{node_id}}/resources")
@login_required
@permission_required("project.node.read")
async def get_node_resources(request: web.Request) -> web.Response:
    req_ctx = RequestContext.parse_obj(request)
    path_params = parse_request_path_parameters_as(_NodePathParams, request)

    try:
        # ensure the project exists
        project = await projects_api.get_project_for_user(
            request.app,
            project_uuid=f"{path_params.project_id}",
            user_id=req_ctx.user_id,
            include_templates=True,
        )

        resources = await projects_api.get_project_node_resources(
            request.app, project=project, node_id=path_params.node_id
        )
        return web.json_response({"data": resources}, dumps=json_dumps)

    except ProjectNotFoundError as exc:
        raise web.HTTPNotFound(
            reason=f"Project {path_params.project_id} not found"
        ) from exc
    except NodeNotFoundError as exc:
        raise web.HTTPNotFound(
            reason=f"Node {path_params.node_id} not found in project"
        ) from exc


@routes.put(f"/{VTAG}/projects/{{project_id}}/nodes/{{node_id}}/resources")
@login_required
@permission_required("project.node.update")
async def replace_node_resources(request: web.Request) -> web.Response:
    req_ctx = RequestContext.parse_obj(request)
    path_params = parse_request_path_parameters_as(_NodePathParams, request)

    try:
        _body = await request.json()
    except json.JSONDecodeError as exc:
        raise web.HTTPBadRequest(reason=f"Invalid request body: {exc}") from exc

    try:

        # ensure the project exists
        _project = await projects_api.get_project_for_user(
            request.app,
            project_uuid=f"{path_params.project_id}",
            user_id=req_ctx.user_id,
            include_templates=True,
        )
        raise web.HTTPNotImplemented(reason="Not yet implemented!")
        # new_node_resources = await projects_api.set_project_node_resources(
        #     request.app, project=project, node_id=node_id
        # )

        # return web.json_response({"data": new_node_resources}, dumps=json_dumps)

    except ProjectNotFoundError as exc:
        raise web.HTTPNotFound(
            reason=f"Project {path_params.project_id} not found"
        ) from exc
    except NodeNotFoundError as exc:
        raise web.HTTPNotFound(
            reason=f"Node {path_params.node_id} not found in project"
        ) from exc

"""this module creates a background task that monitors changes in the database.
First a procedure is registered in postgres that gets triggered whenever the outputs
of a record in comp_task table is changed.
"""
import asyncio
import json
import logging
from contextlib import suppress
from pprint import pformat
from typing import Optional

from aiohttp import web
from aiopg.sa import Engine
from aiopg.sa.connection import SAConnection
from models_library.errors import ErrorDict
from models_library.projects_state import RunningState
from pydantic.types import PositiveInt
from servicelib.aiohttp.application_keys import APP_DB_ENGINE_KEY
from servicelib.logging_utils import log_decorator
from simcore_postgres_database.webserver_models import DB_CHANNEL_NAME, projects
from sqlalchemy.sql import select

from .computation_utils import convert_state_from_db
from .projects import projects_api, projects_exceptions
from .projects.projects_nodes_utils import update_node_outputs

log = logging.getLogger(__name__)


@log_decorator(logger=log)
async def _get_project_owner(conn: SAConnection, project_uuid: str) -> PositiveInt:
    the_project_owner = await conn.scalar(
        select([projects.c.prj_owner]).where(projects.c.uuid == project_uuid)
    )
    if not the_project_owner:
        raise projects_exceptions.ProjectOwnerNotFoundError(project_uuid)
    return the_project_owner


@log_decorator(logger=log)
async def _update_project_state(
    app: web.Application,
    user_id: PositiveInt,
    project_uuid: str,
    node_uuid: str,
    new_state: RunningState,
    node_errors: Optional[list[ErrorDict]],
) -> None:
    project = await projects_api.update_project_node_state(
        app, user_id, project_uuid, node_uuid, new_state
    )
    await projects_api.notify_project_node_update(app, project, node_uuid, node_errors)
    await projects_api.notify_project_state_update(app, project)


async def listen(app: web.Application, db_engine: Engine):
    listen_query = f"LISTEN {DB_CHANNEL_NAME};"
    _LISTENING_TASK_BASE_SLEEPING_TIME_S = 1
    async with db_engine.acquire() as conn:
        await conn.execute(listen_query)

        while True:
            # NOTE: instead of using await get() we check first if the connection was closed
            # since aiopg does not reset the await in such a case (if DB was restarted or so)
            # see aiopg issue: https://github.com/aio-libs/aiopg/pull/559#issuecomment-826813082
            if conn.closed:
                raise ConnectionError("connection with database is closed!")
            if conn.connection.notifies.empty():
                await asyncio.sleep(_LISTENING_TASK_BASE_SLEEPING_TIME_S)
                continue
            notification = conn.connection.notifies.get_nowait()
            log.debug(
                "received update from database: %s", pformat(notification.payload)
            )
            # get the data and the info on what changed
            payload: dict = json.loads(notification.payload)

            # FIXME: all this should move to rabbitMQ instead of this
            task_data = payload.get("data", {})
            task_changes = payload.get("changes", [])

            if not task_data:
                log.error("task data invalid: %s", pformat(payload))
                continue

            if not task_changes:
                log.error("no changes but still triggered: %s", pformat(payload))
                continue

            project_uuid = task_data.get("project_id", "undefined")
            node_uuid = task_data.get("node_id", "undefined")

            # FIXME: we do not know who triggered these changes. we assume the user had the rights to do so
            # therefore we'll use the prj_owner user id. This should be fixed when the new sidecar comes in
            # and comp_tasks/comp_pipeline get deprecated.
            try:
                # find the user(s) linked to that project
                the_project_owner = await _get_project_owner(conn, project_uuid)

                if any(f in task_changes for f in ["outputs", "run_hash"]):
                    new_outputs = task_data.get("outputs", {})
                    new_run_hash = task_data.get("run_hash", None)

                    await update_node_outputs(
                        app,
                        the_project_owner,
                        project_uuid,
                        node_uuid,
                        new_outputs,
                        new_run_hash,
                        node_errors=task_data.get("errors", None),
                        ui_changed_keys=None,
                    )

                if "state" in task_changes:
                    new_state = convert_state_from_db(task_data["state"]).value
                    await _update_project_state(
                        app,
                        the_project_owner,
                        project_uuid,
                        node_uuid,
                        new_state,
                        node_errors=task_data.get("errors", None),
                    )

            except projects_exceptions.ProjectNotFoundError as exc:
                log.warning(
                    "Project %s was not found and cannot be updated. Maybe was it deleted?",
                    exc.project_uuid,
                )
                continue
            except projects_exceptions.ProjectOwnerNotFoundError as exc:
                log.warning(
                    "Project owner of project %s could not be found, is the project valid?",
                    exc.project_uuid,
                )
                continue
            except projects_exceptions.NodeNotFoundError as exc:
                log.warning(
                    "Node %s of project %s not found and cannot be updated. Maybe was it deleted?",
                    exc.node_uuid,
                    exc.project_uuid,
                )
                continue


async def comp_tasks_listening_task(app: web.Application) -> None:
    log.info("starting comp_task db listening task...")
    while True:
        try:
            # create a special connection here
            db_engine = app[APP_DB_ENGINE_KEY]
            log.info("listening to comp_task events...")
            await listen(app, db_engine)
        except asyncio.CancelledError:
            # we are closing the app..
            log.info("cancelled comp_tasks events")
            raise
        except Exception:  # pylint: disable=broad-except
            log.exception(
                "caught unhandled comp_task db listening task exception, restarting...",
                exc_info=True,
            )
            # wait a bit and try restart the task
            await asyncio.sleep(3)


async def create_comp_tasks_listening_task(app: web.Application):
    task = asyncio.create_task(
        comp_tasks_listening_task(app), name="computation db listener"
    )
    log.debug("comp_tasks db listening task created %s", f"{task=}")

    yield

    log.debug("cancelling comp_tasks db listening %s task...", f"{task=}")
    task.cancel()
    log.debug("waiting for comp_tasks db listening %s to stop", f"{task=}")
    with suppress(asyncio.CancelledError):
        await task
    log.debug("waiting for comp_tasks db listening %s to stop completed", f"{task=}")

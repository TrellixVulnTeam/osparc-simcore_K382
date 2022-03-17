import logging

from aiohttp import web
from servicelib.aiohttp.application_setup import ModuleCategory, app_module_setup

from .garbage_collector_task import run_background_task
from .projects.plugin import setup_projects_db, setup_projects_model_schema

logger = logging.getLogger(__name__)


@app_module_setup(
    __name__,
    ModuleCategory.ADDON,
    settings_name="WEBSERVER_GARBAGE_COLLECTOR",
    logger=logger,
)
def setup_garbage_collector(app: web.Application):

    ## needs a partial init of projects plugin since this plugin uses projects-api
    # - project-api needs access to db
    setup_projects_db(app)
    # - projects-api needs access to schema
    setup_projects_model_schema(app)

    app.cleanup_ctx.append(run_background_task)

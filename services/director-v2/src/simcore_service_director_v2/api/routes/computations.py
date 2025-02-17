""" CRUD operations on a "computation" resource

A computation is a resource that represents a running pipeline of computational services in a give project
Therefore,
 - creating a computation will run the project's pipeline
 - the computation ID is the same as the associated project uuid


A task is computation sub-resource that respresents a running computational service in the pipeline described above
Therefore,
 - the task ID is the same as the associated node uuid

"""
# pylint: disable=too-many-arguments


import contextlib
import logging
from typing import Any, Optional

import networkx as nx
from fastapi import APIRouter, Depends, HTTPException
from models_library.clusters import DEFAULT_CLUSTER_ID
from models_library.projects import ProjectAtDB, ProjectID
from models_library.services import ServiceKeyVersion
from models_library.users import UserID
from models_library.utils.fastapi_encoders import jsonable_encoder
from pydantic import AnyHttpUrl, parse_obj_as
from servicelib.async_utils import run_sequentially_in_context
from starlette import status
from starlette.requests import Request
from tenacity import retry
from tenacity.before_sleep import before_sleep_log
from tenacity.retry import retry_if_result
from tenacity.stop import stop_after_delay
from tenacity.wait import wait_random

from ...core.errors import (
    ComputationalRunNotFoundError,
    ProjectNotFoundError,
    SchedulerError,
)
from ...models.domains.comp_pipelines import CompPipelineAtDB
from ...models.domains.comp_runs import CompRunsAtDB
from ...models.domains.comp_tasks import CompTaskAtDB
from ...models.schemas.comp_tasks import (
    ComputationCreate,
    ComputationDelete,
    ComputationGet,
    ComputationStop,
)
from ...modules.catalog import CatalogClient
from ...modules.comp_scheduler.base_scheduler import BaseCompScheduler
from ...modules.db.repositories.comp_pipelines import CompPipelinesRepository
from ...modules.db.repositories.comp_runs import CompRunsRepository
from ...modules.db.repositories.comp_tasks import CompTasksRepository
from ...modules.db.repositories.projects import ProjectsRepository
from ...modules.director_v0 import DirectorV0Client
from ...utils.computations import (
    find_deprecated_tasks,
    get_pipeline_state_from_task_states,
    is_pipeline_running,
    is_pipeline_stopped,
)
from ...utils.dags import (
    compute_pipeline_details,
    create_complete_dag,
    create_complete_dag_from_tasks,
    create_minimal_computational_graph_based_on_selection,
    find_computational_node_cycles,
)
from ..dependencies.catalog import get_catalog_client
from ..dependencies.database import get_repository
from ..dependencies.director_v0 import get_director_v0_client
from ..dependencies.scheduler import get_scheduler
from .computations_tasks import analyze_pipeline

PIPELINE_ABORT_TIMEOUT_S = 10

log = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "",
    summary="Create and optionally start a new computation",
    response_model=ComputationGet,
    status_code=status.HTTP_201_CREATED,
)
# NOTE: in case of a burst of calls to that endpoint, we might end up in a weird state.
@run_sequentially_in_context(target_args=["computation.project_id"])
async def create_computation(
    computation: ComputationCreate,
    request: Request,
    project_repo: ProjectsRepository = Depends(get_repository(ProjectsRepository)),
    comp_pipelines_repo: CompPipelinesRepository = Depends(
        get_repository(CompPipelinesRepository)
    ),
    comp_tasks_repo: CompTasksRepository = Depends(get_repository(CompTasksRepository)),
    comp_runs_repo: CompRunsRepository = Depends(get_repository(CompRunsRepository)),
    director_client: DirectorV0Client = Depends(get_director_v0_client),
    scheduler: BaseCompScheduler = Depends(get_scheduler),
    catalog_client: CatalogClient = Depends(get_catalog_client),
) -> ComputationGet:
    log.debug(
        "User %s is creating a new computation from project %s",
        f"{computation.user_id=}",
        f"{computation.project_id=}",
    )
    try:
        # get the project
        project: ProjectAtDB = await project_repo.get_project(computation.project_id)

        # FIXME: this could not be valid anymore if the user deletes the project in between right?

        # check if current state allow to modify the computation
        comp_tasks: list[CompTaskAtDB] = await comp_tasks_repo.get_comp_tasks(
            computation.project_id
        )
        pipeline_state = get_pipeline_state_from_task_states(comp_tasks)
        if is_pipeline_running(pipeline_state):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Project {computation.project_id} already started, current state is {pipeline_state}",
            )

        # create the complete DAG graph
        complete_dag = create_complete_dag(project.workbench)
        # find the minimal viable graph to be run
        minimal_computational_dag: nx.DiGraph = (
            await create_minimal_computational_graph_based_on_selection(
                complete_dag=complete_dag,
                selected_nodes=computation.subgraph or [],
                force_restart=computation.force_restart or False,
            )
        )

        if computation.start_pipeline:
            assert computation.product_name  # nosec
            if deprecated_tasks := await find_deprecated_tasks(
                computation.user_id,
                computation.product_name,
                [
                    ServiceKeyVersion(key=node[1]["key"], version=node[1]["version"])
                    for node in minimal_computational_dag.nodes.data()
                ],
                catalog_client,
            ):
                raise HTTPException(
                    status_code=status.HTTP_406_NOT_ACCEPTABLE,
                    detail=f"Project {computation.project_id} cannot run since it contains deprecated tasks {jsonable_encoder( deprecated_tasks)}",
                )
        # ok so put the tasks in the db
        await comp_pipelines_repo.upsert_pipeline(
            project.uuid,
            minimal_computational_dag,
            publish=computation.start_pipeline or False,
        )
        inserted_comp_tasks = await comp_tasks_repo.upsert_tasks_from_project(
            project,
            director_client,
            published_nodes=list(minimal_computational_dag.nodes())
            if computation.start_pipeline
            else [],
        )

        if computation.start_pipeline:
            if not minimal_computational_dag.nodes():
                # 2 options here: either we have cycles in the graph or it's really done
                list_of_cycles = find_computational_node_cycles(complete_dag)
                if list_of_cycles:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"Project {computation.project_id} contains cycles with computational services which are currently not supported! Please remove them.",
                    )
                # there is nothing else to be run here, so we are done
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Project {computation.project_id} has no computational services",
                )

            await scheduler.run_new_pipeline(
                computation.user_id,
                computation.project_id,
                computation.cluster_id or DEFAULT_CLUSTER_ID,
            )

        # filter the tasks by the effective pipeline
        filtered_tasks = [
            t
            for t in inserted_comp_tasks
            if f"{t.node_id}" in set(minimal_computational_dag.nodes())
        ]
        pipeline_state = get_pipeline_state_from_task_states(filtered_tasks)

        # get run details if any
        last_run: Optional[CompRunsAtDB] = None
        with contextlib.suppress(ComputationalRunNotFoundError):
            last_run = await comp_runs_repo.get(
                user_id=computation.user_id, project_id=computation.project_id
            )

        return ComputationGet(
            id=computation.project_id,
            state=pipeline_state,
            pipeline_details=await compute_pipeline_details(
                complete_dag, minimal_computational_dag, inserted_comp_tasks
            ),
            url=parse_obj_as(
                AnyHttpUrl,
                f"{request.url}/{computation.project_id}?user_id={computation.user_id}",
            ),
            stop_url=parse_obj_as(
                AnyHttpUrl,
                f"{request.url}/{computation.project_id}:stop?user_id={computation.user_id}",
            )
            if computation.start_pipeline
            else None,
            iteration=last_run.iteration if last_run else None,
            cluster_id=last_run.cluster_id if last_run else None,
            result=None,
        )

    except ProjectNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{e}") from e


@router.get(
    "/{project_id}",
    summary="Returns a computation pipeline state",
    response_model=ComputationGet,
    status_code=status.HTTP_200_OK,
)
async def get_computation(
    user_id: UserID,
    project_id: ProjectID,
    request: Request,
    project_repo: ProjectsRepository = Depends(get_repository(ProjectsRepository)),
    comp_pipelines_repo: CompPipelinesRepository = Depends(
        get_repository(CompPipelinesRepository)
    ),
    comp_tasks_repo: CompTasksRepository = Depends(get_repository(CompTasksRepository)),
    comp_runs_repo: CompRunsRepository = Depends(get_repository(CompRunsRepository)),
) -> ComputationGet:
    log.debug(
        "User %s getting computation status for project %s",
        f"{user_id=}",
        f"{project_id=}",
    )

    # check that project actually exists
    await project_repo.get_project(project_id)

    pipeline_dag, all_tasks, filtered_tasks = await analyze_pipeline(
        project_id, comp_pipelines_repo, comp_tasks_repo
    )

    pipeline_state = get_pipeline_state_from_task_states(filtered_tasks)

    log.debug(
        "Computational task status by %s for %s has %s",
        f"{user_id=}",
        f"{project_id=}",
        f"{pipeline_state=}",
    )

    # create the complete DAG graph
    complete_dag = create_complete_dag_from_tasks(all_tasks)
    pipeline_details = await compute_pipeline_details(
        complete_dag, pipeline_dag, all_tasks
    )

    # get run details if any
    last_run: Optional[CompRunsAtDB] = None
    with contextlib.suppress(ComputationalRunNotFoundError):
        last_run = await comp_runs_repo.get(user_id=user_id, project_id=project_id)

    self_url = request.url.remove_query_params("user_id")
    task_out = ComputationGet(
        id=project_id,
        state=pipeline_state,
        pipeline_details=pipeline_details,
        url=parse_obj_as(AnyHttpUrl, f"{request.url}"),
        stop_url=parse_obj_as(AnyHttpUrl, f"{self_url}:stop?user_id={user_id}")
        if pipeline_state.is_running()
        else None,
        iteration=last_run.iteration if last_run else None,
        cluster_id=last_run.cluster_id if last_run else None,
        result=None,
    )
    return task_out


@router.post(
    "/{project_id}:stop",
    summary="Stops a computation pipeline",
    response_model=ComputationGet,
    status_code=status.HTTP_202_ACCEPTED,
)
async def stop_computation(
    computation_stop: ComputationStop,
    project_id: ProjectID,
    request: Request,
    project_repo: ProjectsRepository = Depends(get_repository(ProjectsRepository)),
    comp_pipelines_repo: CompPipelinesRepository = Depends(
        get_repository(CompPipelinesRepository)
    ),
    comp_tasks_repo: CompTasksRepository = Depends(get_repository(CompTasksRepository)),
    comp_runs_repo: CompRunsRepository = Depends(get_repository(CompRunsRepository)),
    scheduler: BaseCompScheduler = Depends(get_scheduler),
) -> ComputationGet:
    log.debug(
        "User %s stopping computation for project %s",
        computation_stop.user_id,
        project_id,
    )
    try:
        # check the project exists
        await project_repo.get_project(project_id)
        # get the project pipeline
        pipeline_at_db: CompPipelineAtDB = await comp_pipelines_repo.get_pipeline(
            project_id
        )
        pipeline_dag: nx.DiGraph = pipeline_at_db.get_graph()
        # get the project task states
        tasks: list[CompTaskAtDB] = await comp_tasks_repo.get_all_tasks(project_id)
        # create the complete DAG graph
        complete_dag = create_complete_dag_from_tasks(tasks)
        # filter the tasks by the effective pipeline
        filtered_tasks = [
            t for t in tasks if f"{t.node_id}" in set(pipeline_dag.nodes())
        ]
        pipeline_state = get_pipeline_state_from_task_states(filtered_tasks)

        if is_pipeline_running(pipeline_state):
            await scheduler.stop_pipeline(computation_stop.user_id, project_id)

        # get run details if any
        last_run: Optional[CompRunsAtDB] = None
        with contextlib.suppress(ComputationalRunNotFoundError):
            last_run = await comp_runs_repo.get(
                user_id=computation_stop.user_id, project_id=project_id
            )

        return ComputationGet(
            id=project_id,
            state=pipeline_state,
            pipeline_details=await compute_pipeline_details(
                complete_dag, pipeline_dag, tasks
            ),
            url=parse_obj_as(AnyHttpUrl, f"{request.url}"),
            stop_url=None,
            iteration=last_run.iteration if last_run else None,
            cluster_id=last_run.cluster_id if last_run else None,
            result=None,
        )

    except ProjectNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{e}") from e
    except SchedulerError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{e}") from e


@router.delete(
    "/{project_id}",
    summary="Deletes a computation pipeline",
    response_model=None,
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_computation(
    computation_stop: ComputationDelete,
    project_id: ProjectID,
    project_repo: ProjectsRepository = Depends(get_repository(ProjectsRepository)),
    comp_pipelines_repo: CompPipelinesRepository = Depends(
        get_repository(CompPipelinesRepository)
    ),
    comp_tasks_repo: CompTasksRepository = Depends(get_repository(CompTasksRepository)),
    scheduler: BaseCompScheduler = Depends(get_scheduler),
) -> None:
    try:
        # get the project
        project: ProjectAtDB = await project_repo.get_project(project_id)
        # check if current state allow to stop the computation
        comp_tasks: list[CompTaskAtDB] = await comp_tasks_repo.get_comp_tasks(
            project_id
        )
        pipeline_state = get_pipeline_state_from_task_states(comp_tasks)
        if is_pipeline_running(pipeline_state):
            if not computation_stop.force:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Projet {project_id} is currently running and cannot be deleted, current state is {pipeline_state}",
                )
            # abort the pipeline first
            try:
                await scheduler.stop_pipeline(computation_stop.user_id, project_id)
            except SchedulerError as e:
                log.warning(
                    "Project %s could not be stopped properly.\n reason: %s",
                    project_id,
                    e,
                )

            def return_last_value(retry_state: Any) -> Any:
                """return the result of the last call attempt"""
                return retry_state.outcome.result()

            @retry(
                stop=stop_after_delay(PIPELINE_ABORT_TIMEOUT_S),
                wait=wait_random(0, 2),
                retry_error_callback=return_last_value,
                retry=retry_if_result(lambda result: result is False),
                reraise=False,
                before_sleep=before_sleep_log(log, logging.INFO),
            )
            async def check_pipeline_stopped() -> bool:
                comp_tasks: list[CompTaskAtDB] = await comp_tasks_repo.get_comp_tasks(
                    project_id
                )
                pipeline_state = get_pipeline_state_from_task_states(
                    comp_tasks,
                )
                return is_pipeline_stopped(pipeline_state)

            # wait for the pipeline to be stopped
            if not await check_pipeline_stopped():
                log.error(
                    "pipeline %s could not be stopped properly after %ss",
                    project_id,
                    PIPELINE_ABORT_TIMEOUT_S,
                )

        # delete the pipeline now
        await comp_tasks_repo.delete_tasks_from_project(project)
        await comp_pipelines_repo.delete_pipeline(project_id)

    except ProjectNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{e}") from e

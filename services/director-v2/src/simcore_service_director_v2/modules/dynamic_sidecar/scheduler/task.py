"""Dynamic services scheduler

This scheduler takes care of scheduling the dynamic services life cycle.

self._to_observe is a list containing all the dynamic services to schedule (from creation to deletion)
self._to_observe is protected by an asyncio Lock
1. a background task runs every X seconds and adds all the current scheduled services in an asyncio.Queue
2. a second background task processes the entries in the Queue and starts a task per service
  a. if the service is already under "observation" then it will skip this cycle
3. a third background task dealing with ensuring no `volumes removal services`
    remain in the system in case the director-v2:
    - is restarted before while the service is running
    - an error occurs while removing one such services
"""

import asyncio
import contextlib
import functools
import logging
from asyncio import Lock, Queue, Task, sleep
from copy import deepcopy
from dataclasses import dataclass, field
from math import floor
from typing import Optional, Union
from uuid import UUID

from fastapi import FastAPI
from models_library.projects_networks import DockerNetworkAlias
from models_library.projects_nodes_io import NodeID
from models_library.service_settings_labels import RestartPolicy
from pydantic import AnyHttpUrl
from servicelib.error_codes import create_error_code

from ....core.settings import DynamicServicesSchedulerSettings, DynamicSidecarSettings
from ....models.domains.dynamic_services import RetrieveDataOutEnveloped
from ....models.schemas.dynamic_services import (
    DynamicSidecarStatus,
    RunningDynamicServiceDetails,
    SchedulerData,
    ServiceName,
)
from ..api_client import (
    ClientHttpError,
    DynamicSidecarClient,
    get_dynamic_sidecar_client,
)
from ..docker_api import (
    get_dynamic_sidecar_state,
    get_dynamic_sidecars_to_observe,
    is_dynamic_sidecar_stack_missing,
    remove_pending_volume_removal_services,
    update_scheduler_data_label,
)
from ..docker_states import ServiceState, extract_containers_minimim_statuses
from ..errors import (
    DynamicSidecarError,
    DynamicSidecarNotFoundError,
    GenericDockerError,
)
from ._task_utils import apply_observation_cycle
from ._utils import attempt_pod_removal_and_data_saving

logger = logging.getLogger(__name__)

ServiceName = str

_DISABLED_MARK = object()


def _trigger_every_30_seconds(observation_counter: int, wait_interval: float) -> bool:
    # divisor to figure out if 30 seconds have passed based on the cycle count
    modulo_divisor = max(1, int(floor(30 / wait_interval)))
    return observation_counter % modulo_divisor == 0


@dataclass
class DynamicSidecarsScheduler:  # pylint: disable=too-many-instance-attributes
    app: FastAPI

    _lock: Lock = field(default_factory=Lock)
    _to_observe: dict[ServiceName, SchedulerData] = field(default_factory=dict)
    _service_observation_task: dict[
        ServiceName, Optional[Union[asyncio.Task, object]]
    ] = field(default_factory=dict)
    _keep_running: bool = False
    _inverse_search_mapping: dict[UUID, str] = field(default_factory=dict)
    _scheduler_task: Optional[Task] = None
    _cleanup_volume_removal_services_task: Optional[Task] = None
    _trigger_observation_queue_task: Optional[Task] = None
    _trigger_observation_queue: Queue = field(default_factory=Queue)
    _observation_counter: int = 0

    def toggle_observation_cycle(self, node_uuid: NodeID, disable: bool) -> bool:
        """
        returns True if it managed to toggle the current state

        raises DynamicSidecarNotFoundError
        """
        if node_uuid not in self._inverse_search_mapping:
            raise DynamicSidecarNotFoundError(node_uuid)
        service_name = self._inverse_search_mapping[node_uuid]

        service_task = self._service_observation_task.get(service_name)

        if isinstance(service_task, asyncio.Task):
            return False

        if disable:
            self._service_observation_task[service_name] = _DISABLED_MARK
        else:
            self._service_observation_task.pop(service_name, None)

        return True

    async def add_service(self, scheduler_data: SchedulerData) -> None:
        """Invoked before the service is started

        Because we do not have all items require to compute the service_name the node_uuid is used to
        keep track of the service for faster searches.
        """
        async with self._lock:
            if scheduler_data.service_name in self._to_observe:
                logger.warning(
                    "Service %s is already being observed", scheduler_data.service_name
                )
                return

            if scheduler_data.node_uuid in self._inverse_search_mapping:
                raise DynamicSidecarError(
                    "node_uuids at a global level collided. A running "
                    f"service for node {scheduler_data.node_uuid} already exists. "
                    "Please checkout other projects which may have this issue."
                )

            self._inverse_search_mapping[
                scheduler_data.node_uuid
            ] = scheduler_data.service_name
            self._to_observe[scheduler_data.service_name] = scheduler_data
            self._enqueue_observation_from_service_name(scheduler_data.service_name)
            logger.debug("Added service '%s' to observe", scheduler_data.service_name)

    async def mark_service_for_removal(
        self, node_uuid: NodeID, can_save: Optional[bool]
    ) -> None:
        """Marks service for removal, causing RemoveMarkedService to trigger"""
        async with self._lock:
            if node_uuid not in self._inverse_search_mapping:
                raise DynamicSidecarNotFoundError(node_uuid)

            service_name = self._inverse_search_mapping[node_uuid]
            if service_name not in self._to_observe:
                return

            current: SchedulerData = self._to_observe[service_name]
            current.dynamic_sidecar.service_removal_state.mark_to_remove(can_save)
            await update_scheduler_data_label(current)

        logger.debug("Service '%s' marked for removal from scheduler", service_name)

    async def remove_service_from_observation(self, node_uuid: NodeID) -> None:
        """
        directly invoked from RemoveMarkedService once it's finished
        and removes the service from the observation cycle
        """
        async with self._lock:
            if node_uuid not in self._inverse_search_mapping:
                raise DynamicSidecarNotFoundError(node_uuid)

            service_name = self._inverse_search_mapping[node_uuid]
            if service_name not in self._to_observe:
                return

            del self._to_observe[service_name]
            del self._inverse_search_mapping[node_uuid]

        logger.debug("Removed service '%s' from scheduler", service_name)

    def get_scheduler_data(self, node_uuid: NodeID) -> SchedulerData:
        """

        raises DynamicSidecarNotFoundError
        """
        if node_uuid not in self._inverse_search_mapping:
            raise DynamicSidecarNotFoundError(node_uuid)
        service_name = self._inverse_search_mapping[node_uuid]
        return self._to_observe[service_name]

    async def get_stack_status(self, node_uuid: NodeID) -> RunningDynamicServiceDetails:
        """

        raises DynamicSidecarNotFoundError
        """
        if node_uuid not in self._inverse_search_mapping:
            raise DynamicSidecarNotFoundError(node_uuid)
        service_name = self._inverse_search_mapping[node_uuid]

        scheduler_data: SchedulerData = self._to_observe[service_name]

        # check if there was an error picked up by the scheduler
        # and marked this service as failing
        if scheduler_data.dynamic_sidecar.status.current != DynamicSidecarStatus.OK:
            return RunningDynamicServiceDetails.from_scheduler_data(
                node_uuid=node_uuid,
                scheduler_data=scheduler_data,
                service_state=ServiceState.FAILED,
                service_message=scheduler_data.dynamic_sidecar.status.info,
            )

        service_state, service_message = await get_dynamic_sidecar_state(
            # the service_name is unique and will not collide with other names
            # it can be used in place of the service_id here, as the docker API accepts both
            service_id=scheduler_data.service_name
        )

        # while the dynamic-sidecar state is not RUNNING report it's state
        if service_state != ServiceState.RUNNING:
            return RunningDynamicServiceDetails.from_scheduler_data(
                node_uuid=node_uuid,
                scheduler_data=scheduler_data,
                service_state=service_state,
                service_message=service_message,
            )

        dynamic_sidecar_client: DynamicSidecarClient = get_dynamic_sidecar_client(
            self.app
        )

        try:
            docker_statuses: Optional[
                dict[str, dict[str, str]]
            ] = await dynamic_sidecar_client.containers_docker_status(
                dynamic_sidecar_endpoint=scheduler_data.endpoint
            )
        except ClientHttpError:
            # error fetching docker_statues, probably someone should check
            return RunningDynamicServiceDetails.from_scheduler_data(
                node_uuid=node_uuid,
                scheduler_data=scheduler_data,
                service_state=ServiceState.STARTING,
                service_message="There was an error while trying to fetch the stautes form the contianers",
            )

        # wait for containers to start
        if len(docker_statuses) == 0:
            # marks status as waiting for containers
            return RunningDynamicServiceDetails.from_scheduler_data(
                node_uuid=node_uuid,
                scheduler_data=scheduler_data,
                service_state=ServiceState.STARTING,
                service_message="",
            )

        # compute composed containers states
        container_state, container_message = extract_containers_minimim_statuses(
            docker_statuses
        )
        return RunningDynamicServiceDetails.from_scheduler_data(
            node_uuid=node_uuid,
            scheduler_data=scheduler_data,
            service_state=container_state,
            service_message=container_message,
        )

    async def retrieve_service_inputs(
        self, node_uuid: NodeID, port_keys: list[str]
    ) -> RetrieveDataOutEnveloped:
        """Pulls data from input ports for the service"""
        if node_uuid not in self._inverse_search_mapping:
            raise DynamicSidecarNotFoundError(node_uuid)

        service_name = self._inverse_search_mapping[node_uuid]
        scheduler_data: SchedulerData = self._to_observe[service_name]
        dynamic_sidecar_endpoint: AnyHttpUrl = scheduler_data.endpoint
        dynamic_sidecar_client: DynamicSidecarClient = get_dynamic_sidecar_client(
            self.app
        )

        transferred_bytes = await dynamic_sidecar_client.pull_service_input_ports(
            dynamic_sidecar_endpoint, port_keys
        )

        if scheduler_data.restart_policy == RestartPolicy.ON_INPUTS_DOWNLOADED:
            logger.info("Will restart containers")
            await dynamic_sidecar_client.restart_containers(dynamic_sidecar_endpoint)

        return RetrieveDataOutEnveloped.from_transferred_bytes(transferred_bytes)

    async def attach_project_network(
        self, node_id: NodeID, project_network: str, network_alias: DockerNetworkAlias
    ) -> None:
        if node_id not in self._inverse_search_mapping:
            return

        service_name = self._inverse_search_mapping[node_id]
        scheduler_data = self._to_observe[service_name]

        dynamic_sidecar_client: DynamicSidecarClient = get_dynamic_sidecar_client(
            self.app
        )

        await dynamic_sidecar_client.attach_service_containers_to_project_network(
            dynamic_sidecar_endpoint=scheduler_data.endpoint,
            dynamic_sidecar_network_name=scheduler_data.dynamic_sidecar_network_name,
            project_network=project_network,
            project_id=scheduler_data.project_id,
            network_alias=network_alias,
        )

    async def detach_project_network(
        self, node_id: NodeID, project_network: str
    ) -> None:
        if node_id not in self._inverse_search_mapping:
            return

        service_name = self._inverse_search_mapping[node_id]
        scheduler_data = self._to_observe[service_name]

        dynamic_sidecar_client: DynamicSidecarClient = get_dynamic_sidecar_client(
            self.app
        )

        await dynamic_sidecar_client.detach_service_containers_from_project_network(
            dynamic_sidecar_endpoint=scheduler_data.endpoint,
            project_network=project_network,
            project_id=scheduler_data.project_id,
        )

    async def restart_containers(self, node_uuid: NodeID) -> None:
        """Restarts containers without saving or restoring the state or I/O ports"""
        if node_uuid not in self._inverse_search_mapping:
            raise DynamicSidecarNotFoundError(node_uuid)

        service_name = self._inverse_search_mapping[node_uuid]
        scheduler_data: SchedulerData = self._to_observe[service_name]

        dynamic_sidecar_client: DynamicSidecarClient = get_dynamic_sidecar_client(
            self.app
        )

        await dynamic_sidecar_client.restart_containers(scheduler_data.endpoint)

    def _enqueue_observation_from_service_name(self, service_name: str) -> None:
        self._trigger_observation_queue.put_nowait(service_name)

    async def _run_trigger_observation_queue_task(self) -> None:
        """generates events at regular time interval"""
        dynamic_sidecar_settings: DynamicSidecarSettings = (
            self.app.state.settings.DYNAMIC_SERVICES.DYNAMIC_SIDECAR
        )
        dynamic_scheduler: DynamicServicesSchedulerSettings = (
            self.app.state.settings.DYNAMIC_SERVICES.DYNAMIC_SCHEDULER
        )

        async def _observing_single_service(service_name: str) -> None:
            scheduler_data: SchedulerData = self._to_observe[service_name]

            if (
                scheduler_data.dynamic_sidecar.status.current
                == DynamicSidecarStatus.FAILING
            ):
                # potential use-cases:
                # 1. service failed on start -> it can be removed safely
                # 2. service must be deleted -> it can be removed safely
                # 3. service started and failed while running (either
                #   dy-sidecar, dy-proxy, or containers) -> it cannot be removed safely
                # 4. service started, and failed on closing -> it cannot be removed safely

                if (
                    scheduler_data.dynamic_sidecar.wait_for_manual_intervention_after_error
                ):
                    # use-cases: 3, 4
                    # Since user data is important and must be saved, take no further
                    # action and wait for manual intervention from support.

                    # After manual intervention service can now be removed
                    # from tracking.

                    if (
                        # NOTE: do not change below order, reduces pressure on the
                        # docker swarm engine API.
                        _trigger_every_30_seconds(
                            self._observation_counter,
                            dynamic_scheduler.DIRECTOR_V2_DYNAMIC_SCHEDULER_INTERVAL_SECONDS,
                        )
                        and await is_dynamic_sidecar_stack_missing(
                            scheduler_data.node_uuid, dynamic_sidecar_settings
                        )
                    ):
                        # if both proxy and sidecar ar missing at this point it
                        # is safe to assume that user manually removed them from
                        # Portainer after cleaning up.

                        # NOTE: saving will fail since there is no dy-sidecar,
                        # and the save was taken care of by support. Disabling it.
                        scheduler_data.dynamic_sidecar.service_removal_state.can_save = (
                            False
                        )
                        await attempt_pod_removal_and_data_saving(
                            self.app, scheduler_data
                        )

                    return

                # use-cases: 1, 2
                # Cleanup all resources related to the dynamic-sidecar.
                await attempt_pod_removal_and_data_saving(self.app, scheduler_data)
                return

            scheduler_data_copy: SchedulerData = deepcopy(scheduler_data)
            try:
                await apply_observation_cycle(self.app, self, scheduler_data)
                logger.debug("completed observation cycle of %s", f"{service_name=}")
            except asyncio.CancelledError:  # pylint: disable=try-except-raise
                raise  # pragma: no cover
            except Exception as e:  # pylint: disable=broad-except
                service_name = scheduler_data.service_name

                # With unhandled errors, let's generate and ID and send it to the end-user
                # so that we can trace the logs and debug the issue.

                error_code = create_error_code(e)
                logger.exception(
                    "Observation of %s unexpectedly failed [%s]",
                    f"{service_name=} ",
                    f"{error_code}",
                    extra={"error_code": error_code},
                )
                scheduler_data.dynamic_sidecar.status.update_failing_status(
                    # This message must be human-friendly
                    f"Upss! This service ({service_name}) unexpectedly failed",
                    error_code,
                )
            finally:
                if scheduler_data_copy != scheduler_data:
                    try:
                        await update_scheduler_data_label(scheduler_data)
                    except GenericDockerError as e:
                        logger.warning(
                            "Skipped labels update, please check:\n %s", f"{e}"
                        )

        service_name: str
        while service_name := await self._trigger_observation_queue.get():
            logger.info("Handling observation for %s", service_name)

            if service_name not in self._to_observe:
                logger.warning(
                    "%s is missing from list of services to observe", f"{service_name=}"
                )
                continue

            if self._service_observation_task.get(service_name) is None:
                self._service_observation_task[
                    service_name
                ] = observation_task = asyncio.create_task(
                    _observing_single_service(service_name),
                    name=f"observe_{service_name}",
                )
                observation_task.add_done_callback(
                    functools.partial(
                        lambda s, _: self._service_observation_task.pop(s, None),
                        service_name,
                    )
                )
                logger.debug(
                    "created %s for %s", f"{observation_task=}", f"{service_name=}"
                )

        logger.info("Scheduler 'trigger observation queue task' was shut down")

    async def _run_scheduler_task(self) -> None:
        settings: DynamicServicesSchedulerSettings = (
            self.app.state.settings.DYNAMIC_SERVICES.DYNAMIC_SCHEDULER
        )
        logger.debug(
            "dynamic-sidecars observation interval %s",
            settings.DIRECTOR_V2_DYNAMIC_SCHEDULER_INTERVAL_SECONDS,
        )

        while self._keep_running:
            logger.debug("Observing dynamic-sidecars %s", list(self._to_observe.keys()))

            try:
                # prevent access to self._to_observe
                async with self._lock:
                    for service_name in self._to_observe:
                        self._enqueue_observation_from_service_name(service_name)
            except asyncio.CancelledError:  # pragma: no cover
                logger.info("Stopped dynamic scheduler")
                raise
            except Exception:  # pylint: disable=broad-except
                logger.exception(
                    "Unexpected error while scheduling sidecars observation"
                )

            await sleep(settings.DIRECTOR_V2_DYNAMIC_SCHEDULER_INTERVAL_SECONDS)
            self._observation_counter += 1

    async def _discover_running_services(self) -> None:
        """discover all services which were started before and add them to the scheduler"""
        dynamic_sidecar_settings: DynamicSidecarSettings = (
            self.app.state.settings.DYNAMIC_SERVICES.DYNAMIC_SIDECAR
        )
        services_to_observe: list[
            SchedulerData
        ] = await get_dynamic_sidecars_to_observe(dynamic_sidecar_settings)

        logger.info(
            "The following services need to be observed: %s", services_to_observe
        )

        for scheduler_data in services_to_observe:
            await self.add_service(scheduler_data)

    async def _cleanup_volume_removal_services(self) -> None:
        settings: DynamicServicesSchedulerSettings = (
            self.app.state.settings.DYNAMIC_SERVICES.DYNAMIC_SCHEDULER
        )
        dynamic_sidecar_settings: DynamicSidecarSettings = (
            self.app.state.settings.DYNAMIC_SERVICES.DYNAMIC_SIDECAR
        )

        logger.debug(
            "dynamic-sidecars cleanup pending volume removal services every %s seconds",
            settings.DIRECTOR_V2_DYNAMIC_SCHEDULER_PENDING_VOLUME_REMOVAL_INTERVAL_S,
        )
        while await asyncio.sleep(
            settings.DIRECTOR_V2_DYNAMIC_SCHEDULER_PENDING_VOLUME_REMOVAL_INTERVAL_S,
            True,
        ):
            logger.debug("Removing pending volume removal services...")

            try:
                await remove_pending_volume_removal_services(dynamic_sidecar_settings)
            except asyncio.CancelledError:
                logger.info("Stopped pending volume removal services task")
                raise
            except Exception:  # pylint: disable=broad-except
                logger.exception(
                    "Unexpected error while cleaning up pending volume removal services"
                )

    async def start(self) -> None:
        # run as a background task
        logger.info("Starting dynamic-sidecar scheduler")
        self._keep_running = True
        self._scheduler_task = asyncio.create_task(
            self._run_scheduler_task(), name="dynamic-scheduler"
        )
        self._trigger_observation_queue_task = asyncio.create_task(
            self._run_trigger_observation_queue_task(),
            name="dynamic-scheduler-trigger-obs-queue",
        )

        self._cleanup_volume_removal_services_task = asyncio.create_task(
            self._cleanup_volume_removal_services(),
            name="dynamic-scheduler-cleanup-volume-removal-services",
        )
        await self._discover_running_services()

    async def shutdown(self):
        logger.info("Shutting down dynamic-sidecar scheduler")
        self._keep_running = False
        self._inverse_search_mapping = {}
        self._to_observe = {}

        if self._cleanup_volume_removal_services_task is not None:
            self._cleanup_volume_removal_services_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._cleanup_volume_removal_services_task
            self._cleanup_volume_removal_services_task = None

        if self._scheduler_task is not None:
            self._scheduler_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._scheduler_task
            self._scheduler_task = None

        if self._trigger_observation_queue_task is not None:
            await self._trigger_observation_queue.put(None)

            self._trigger_observation_queue_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._trigger_observation_queue_task
            self._trigger_observation_queue_task = None
            self._trigger_observation_queue = Queue()

        # let's properly cleanup remaining observation tasks
        running_tasks = self._service_observation_task.values()
        for task in running_tasks:
            task.cancel()
        try:
            MAX_WAIT_TIME_SECONDS = 5
            results = await asyncio.wait_for(
                asyncio.gather(*running_tasks, return_exceptions=True),
                timeout=MAX_WAIT_TIME_SECONDS,
            )
            if bad_results := list(filter(lambda r: isinstance(r, Exception), results)):
                logger.error(
                    "Following observation tasks completed with an unexpected error:%s",
                    f"{bad_results}",
                )
        except asyncio.TimeoutError:
            logger.error(
                "Timed-out waiting for %s to complete. Action: Check why this is blocking",
                f"{running_tasks=}",
            )

    def is_service_tracked(self, node_uuid: NodeID) -> bool:
        return node_uuid in self._inverse_search_mapping


async def setup_scheduler(app: FastAPI):
    scheduler = DynamicSidecarsScheduler(app)
    app.state.dynamic_sidecar_scheduler = scheduler
    assert isinstance(scheduler, DynamicSidecarsScheduler)  # nosec

    settings: DynamicServicesSchedulerSettings = (
        app.state.settings.DYNAMIC_SERVICES.DYNAMIC_SCHEDULER
    )
    if not settings.DIRECTOR_V2_DYNAMIC_SCHEDULER_ENABLED:
        logger.warning("dynamic-sidecar scheduler will not be started!!!")
        return

    await scheduler.start()


async def shutdown_scheduler(app: FastAPI):
    scheduler: DynamicSidecarsScheduler = app.state.dynamic_sidecar_scheduler
    assert isinstance(scheduler, DynamicSidecarsScheduler)  # nosec

    # FIXME: PC->ANE: if not started, should it be shutdown?
    await scheduler.shutdown()


__all__: tuple[str, ...] = (
    "DynamicSidecarsScheduler",
    "setup_scheduler",
    "shutdown_scheduler",
)

# pylint:disable=unused-variable
# pylint:disable=unused-argument
# pylint:disable=redefined-outer-name
# pylint: disable=too-many-branches

import asyncio
import json
import logging
import subprocess
from contextlib import suppress
from pathlib import Path
from typing import Any, Iterator

import docker
import pytest
import yaml
from docker.errors import APIError
from tenacity import Retrying, TryAgain, retry
from tenacity.before_sleep import before_sleep_log
from tenacity.retry import retry_if_exception_type
from tenacity.stop import stop_after_delay
from tenacity.wait import wait_fixed, wait_random_exponential

from .helpers.constants import HEADER_STR, MINUTE
from .helpers.typing_env import EnvVarsDict
from .helpers.utils_dict import copy_from_dict
from .helpers.utils_docker import get_localhost_ip

log = logging.getLogger(__name__)


class _ResourceStillNotRemoved(Exception):
    pass


def _is_docker_swarm_init(docker_client: docker.client.DockerClient) -> bool:
    try:
        docker_client.swarm.reload()
        inspect_result = docker_client.swarm.attrs
        assert type(inspect_result) == dict
    except APIError as error:
        return False
    return True


@retry(
    wait=wait_fixed(5),
    stop=stop_after_delay(8 * MINUTE),
    before_sleep=before_sleep_log(log, logging.WARNING),
    reraise=True,
)
def assert_service_is_running(service):
    """Checks that a number of tasks of this service are in running state"""

    def _get(obj: dict[str, Any], dotted_key: str, default=None) -> Any:
        keys = dotted_key.split(".")
        value = obj
        for key in keys[:-1]:
            value = value.get(key, {})
        return value.get(keys[-1], default)

    service_name = service.name
    num_replicas_specified = _get(
        service.attrs, "Spec.Mode.Replicated.Replicas", default=1
    )

    log.info(
        "Waiting for service_name='%s' to have num_replicas_specified=%s ...",
        service_name,
        num_replicas_specified,
    )

    tasks = list(service.tasks())
    assert tasks

    #
    # NOTE: We have noticed using the 'last updated' task is not necessarily
    # the most actual of the tasks. It dependends e.g. on the restart policy.
    # We explored the possibility of determining success by using the condition
    # "DesiredState" == "Status.State" but realized that "DesiredState" is not
    # part of the specs but can be updated by the swarm at runtime.
    # Finally, the decision was to use the state 'running' understanding that
    # the swarms flags this state to the service when it is up and healthy.
    #
    # SEE https://docs.docker.com/engine/swarm/how-swarm-mode-works/swarm-task-states/

    tasks_current_state = [_get(task, "Status.State") for task in tasks]
    num_running = sum(current == "running" for current in tasks_current_state)

    assert num_running == num_replicas_specified, (
        f"service_name='{service_name}'  has tasks_current_state={tasks_current_state}, "
        f"but expected at least num_replicas_specified='{num_replicas_specified}' running"
    )

    print(f"--> {service_name} is up and running!!")


def _fetch_and_print_services(
    docker_client: docker.client.DockerClient, extra_title: str
) -> None:
    print(HEADER_STR.format(f"docker services running {extra_title}"))

    for service_obj in docker_client.services.list():

        tasks = {}
        service = {}
        with suppress(Exception):
            # trims dicts (more info in dumps)
            assert service_obj.attrs
            service = copy_from_dict(
                service_obj.attrs,
                include={
                    "ID": ...,
                    "CreatedAt": ...,
                    "UpdatedAt": ...,
                    "Spec": {"Name", "Labels", "Mode"},
                },
            )

            tasks = [
                copy_from_dict(
                    task,
                    include={
                        "ID": ...,
                        "CreatedAt": ...,
                        "UpdatedAt": ...,
                        "Spec": {"ContainerSpec": {"Image", "Labels", "Env"}},
                        "Status": {"Timestamp", "State"},
                        "DesiredState": ...,
                        "ServiceID": ...,
                        "NodeID": ...,
                        "Slot": ...,
                    },
                )
                for task in service_obj.tasks()  # type: ignore
            ]

        print(HEADER_STR.format(service_obj.name))  # type: ignore
        print(json.dumps({"service": service, "tasks": tasks}, indent=1))


@pytest.fixture(scope="session")
def docker_client() -> Iterator[docker.client.DockerClient]:
    client = docker.from_env()
    yield client
    client.close()


@pytest.fixture(scope="session")
def keep_docker_up(request) -> bool:
    return request.config.getoption("--keep-docker-up")


@pytest.fixture(scope="module")
def docker_swarm(
    docker_client: docker.client.DockerClient, keep_docker_up: Iterator[bool]
) -> Iterator[None]:
    """inits docker swarm"""

    for attempt in Retrying(
        wait=wait_fixed(2), stop=stop_after_delay(15), reraise=True
    ):
        with attempt:
            if not _is_docker_swarm_init(docker_client):
                print("--> initializing docker swarm...")
                docker_client.swarm.init(advertise_addr=get_localhost_ip())
                print("--> docker swarm initialized.")

            # if still not in swarm, raise an error to try and initialize again
            assert _is_docker_swarm_init(docker_client)

    yield

    if not keep_docker_up:
        print("<-- leaving docker swarm...")
        assert docker_client.swarm.leave(force=True)
        print("<-- docker swarm left.")

    assert _is_docker_swarm_init(docker_client) is keep_docker_up


@pytest.fixture(scope="module")
def docker_stack(
    docker_swarm: None,
    docker_client: docker.client.DockerClient,
    core_docker_compose_file: Path,
    ops_docker_compose_file: Path,
    keep_docker_up: bool,
    testing_environ_vars: EnvVarsDict,
) -> Iterator[dict]:
    """deploys core and ops stacks and returns as soon as all are running"""

    # WARNING: keep prefix "pytest-" in stack names
    core_stack_name = testing_environ_vars["SWARM_STACK_NAME"]
    ops_stack_name = "pytest-ops"

    assert core_stack_name
    assert core_stack_name.startswith("pytest-")
    stacks = [
        (
            "core",
            core_stack_name,
            core_docker_compose_file,
        ),
        (
            "ops",
            ops_stack_name,
            ops_docker_compose_file,
        ),
    ]

    # NOTE: if the migration service was already running prior to this call it must
    # be force updated so that it does its job. else it remains and tests will fail
    migration_service_was_running_before = any(
        filter(
            lambda s: "migration" in s.name, docker_client.services.list()  # type: ignore
        )
    )
    for migration_service in filter(
        lambda s: "migration" in s.name, docker_client.services.list()  # type: ignore
    ):
        print(
            "WARNING: migration service detected before updating stack, it will be force-updated"
        )
        migration_service.force_update()  # type: ignore
        print(f"forced updated {migration_service.name}.")  # type: ignore

    # make up-version
    stacks_deployed: dict[str, dict] = {}
    for key, stack_name, compose_file in stacks:
        for attempt in Retrying(
            stop=stop_after_delay(60),
            wait=wait_random_exponential(max=5),
            retry=retry_if_exception_type(TryAgain),
            reraise=True,
        ):
            with attempt:
                try:
                    subprocess.run(
                        [
                            "docker",
                            "stack",
                            "deploy",
                            "--with-registry-auth",
                            "--compose-file",
                            f"{compose_file.name}",
                            f"{stack_name}",
                        ],
                        check=True,
                        cwd=compose_file.parent,
                        capture_output=True,
                    )
                except subprocess.CalledProcessError as err:
                    if b"update out of sequence" in err.stderr:
                        raise TryAgain from err
                    print(
                        "docker_stack failed",
                        f"{' '.join(err.cmd)}",
                        f"returncode={err.returncode}",
                        f"stdout={err.stdout}",
                        f"stderr={err.stderr}",
                        "\nTIP: frequent failure is due to a corrupt .env file: Delete .env and .env.bak",
                    )
                    raise

        stacks_deployed[key] = {
            "name": stack_name,
            "compose": yaml.safe_load(compose_file.read_text()),
        }

    # All SELECTED services ready
    # - notice that the timeout is set for all services in both stacks
    # - TODO: the time to deploy will depend on the number of services selected
    try:

        async def _check_all_services_are_running():
            done, pending = await asyncio.wait(
                [
                    asyncio.get_event_loop().run_in_executor(
                        None, assert_service_is_running, service
                    )
                    for service in docker_client.services.list()
                ],
                return_when=asyncio.FIRST_EXCEPTION,
            )
            assert done, f"no services ready, they all failed! [{pending}]"
            assert not pending, f"some service did not start correctly [{pending}]"

        asyncio.run(_check_all_services_are_running())

    finally:
        _fetch_and_print_services(docker_client, "[BEFORE TEST]")

    yield {
        "stacks": stacks_deployed,
        "services": [service.name for service in docker_client.services.list()],  # type: ignore
    }

    ## TEAR DOWN ----------------------

    _fetch_and_print_services(docker_client, "[AFTER TEST]")

    if keep_docker_up:
        # skip bringing the stack down
        return

    # clean up. Guarantees that all services are down before creating a new stack!
    #
    # WORKAROUND https://github.com/moby/moby/issues/30942#issue-207070098
    #
    #   docker stack rm services
    #   until [ -z "$(docker service ls --filter label=com.docker.stack.namespace=services -q)" ] || [ "$limit" -lt 0 ]; do
    #   sleep 1;
    #   done
    #   until [ -z "$(docker network ls --filter label=com.docker.stack.namespace=services -q)" ] || [ "$limit" -lt 0 ]; do
    #   sleep 1;
    #   done

    # make down
    # NOTE: remove them in reverse order since stacks share common networks

    stacks.reverse()
    for _, stack, _ in stacks:

        try:
            subprocess.run(
                f"docker stack remove {stack}".split(" "),
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as err:
            log.warning(
                "Ignoring failure while executing '%s' (returned code %d):\n%s\n%s\n%s\n%s\n",
                err.cmd,
                err.returncode,
                HEADER_STR.format("stdout"),
                err.stdout.decode("utf8") if err.stdout else "",
                HEADER_STR.format("stderr"),
                err.stderr.decode("utf8") if err.stderr else "",
            )

        # Waits that all resources get removed or force them
        # The check order is intentional because some resources depend on others to be removed
        # e.g. cannot remove networks/volumes used by running containers
        for resource_name in ("services", "containers", "volumes", "networks"):
            resource_client = getattr(docker_client, resource_name)

            for attempt in Retrying(
                wait=wait_fixed(2),
                stop=stop_after_delay(3 * MINUTE),
                before_sleep=before_sleep_log(log, logging.WARNING),
                reraise=True,
            ):
                with attempt:
                    pending = resource_client.list(
                        filters={"label": f"com.docker.stack.namespace={stack}"}
                    )
                    if pending:
                        if resource_name in ("volumes",):
                            # WARNING: rm volumes on this stack migh be a problem when shared between different stacks
                            # NOTE: volumes are removed to avoid mixing configs (e.g. postgres db credentials)
                            for resource in pending:
                                resource.remove(force=True)

                        raise _ResourceStillNotRemoved(
                            f"Waiting for {len(pending)} {resource_name} to shutdown: {pending}."
                        )

    _fetch_and_print_services(docker_client, "[AFTER REMOVED]")

"""
:mod:`alchemiscale.interface.api` --- user-facing API components
================================================================

"""

from typing import Any, Dict, List, Optional, Union
import os
import json
from collections import Counter

from fastapi import FastAPI, APIRouter, Body, Depends, HTTPException
from fastapi import status as http_status
from fastapi.middleware.gzip import GZipMiddleware
from gufe import AlchemicalNetwork, ChemicalSystem, Transformation
from gufe.protocols import ProtocolDAGResult
from gufe.tokenization import GufeTokenizable, JSON_HANDLER

from ..base.api import (
    GufeJSONResponse,
    QueryGUFEHandler,
    scope_params,
    get_token_data_depends,
    get_n4js_depends,
    get_s3os_depends,
    base_router,
    get_cred_entity,
    validate_scopes,
    validate_scopes_query,
    _check_store_connectivity,
    gufe_to_json,
    GzipRoute,
)
from ..settings import get_api_settings
from ..settings import get_base_api_settings, get_api_settings
from ..storage.statestore import Neo4jStore
from ..storage.objectstore import S3ObjectStore
from ..storage.models import ProtocolDAGResultRef, TaskStatusEnum
from ..models import Scope, ScopedKey
from ..security.auth import get_token_data, oauth2_scheme
from ..security.models import Token, TokenData, CredentialedUserIdentity
from ..keyedchain import KeyedChain


app = FastAPI(title="AlchemiscaleAPI")
app.dependency_overrides[get_base_api_settings] = get_api_settings
app.include_router(base_router)
app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=5)


def get_cred_user():
    return CredentialedUserIdentity


app.dependency_overrides[get_cred_entity] = get_cred_user

router = APIRouter(
    dependencies=[Depends(get_token_data_depends)],
)
router.route_class = GzipRoute


@app.get("/ping")
def ping():
    return {"api": "AlchemiscaleAPI"}


@router.get("/info")
def info():
    return {"message": "nothing yet"}


@router.get("/check")
def check(
    n4js: Neo4jStore = Depends(get_n4js_depends),
    s3os: S3ObjectStore = Depends(get_s3os_depends),
):
    # check connectivity of storage components
    # if no exception raised, all good
    _check_store_connectivity(n4js, s3os)


@router.get("/identities/{identity_identifier}/scopes")
def list_scopes(
    *,
    identity_identifier,
    n4js: Neo4jStore = Depends(get_n4js_depends),
    token: TokenData = Depends(get_token_data_depends),
) -> List[str]:
    scopes = n4js.list_scopes(identity_identifier, CredentialedUserIdentity)
    return [str(scope) for scope in scopes]


### inputs


@router.get("/exists/{scoped_key}")
def check_existence(
    scoped_key,
    *,
    n4js: Neo4jStore = Depends(get_n4js_depends),
    token: TokenData = Depends(get_token_data_depends),
):
    sk = ScopedKey.from_str(scoped_key)
    validate_scopes(sk.scope, token)

    return n4js.check_existence(scoped_key=sk)


@router.post("/networks", response_model=ScopedKey)
def create_network(
    *,
    network: List = Body(...),
    scope: Scope,
    n4js: Neo4jStore = Depends(get_n4js_depends),
    token: TokenData = Depends(get_token_data_depends),
):
    validate_scopes(scope, token)

    an = KeyedChain(network).to_gufe()

    try:
        an_sk = n4js.create_network(network=an, scope=scope)
    except ValueError as e:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=e.args[0],
        )

    # create taskhub for this network
    n4js.create_taskhub(an_sk)

    return an_sk


@router.get("/networks")
def query_networks(
    *,
    name: str = None,
    scope: Scope = Depends(scope_params),
    n4js: Neo4jStore = Depends(get_n4js_depends),
    token: TokenData = Depends(get_token_data_depends),
):
    # Intersect query scopes with accessible scopes in the token
    query_scopes = validate_scopes_query(scope, token)

    # query each scope
    # loop might be removable in the future with a Union like operator on scopes
    results = []
    for single_query_scope in query_scopes:
        results.extend(
            n4js.query_networks(
                name=name,
                scope=single_query_scope,
            )
        )

    return [str(sk) for sk in results]


@router.get("/transformations")
def query_transformations(
    *,
    name: str = None,
    scope: Scope = Depends(scope_params),
    n4js: Neo4jStore = Depends(get_n4js_depends),
    token: TokenData = Depends(get_token_data_depends),
):
    # Intersect query scopes with accessible scopes in the token
    query_scopes = validate_scopes_query(scope, token)

    # query each scope
    # loop might be removable in the future with a Union like operator on scopes
    results = []
    for single_query_scope in query_scopes:
        results.extend(n4js.query_transformations(name=name, scope=single_query_scope))

    return [str(sk) for sk in results]


@router.get("/chemicalsystems")
def query_chemicalsystems(
    *,
    name: str = None,
    scope: Scope = Depends(scope_params),
    n4js: Neo4jStore = Depends(get_n4js_depends),
    token: TokenData = Depends(get_token_data_depends),
):
    # Intersect query scopes with accessible scopes in the token
    query_scopes = validate_scopes_query(scope, token)

    # query each scope
    # loop might be removable in the future with a Union like operator on scopes
    results = []
    for single_query_scope in query_scopes:
        results.extend(n4js.query_chemicalsystems(name=name, scope=single_query_scope))

    return [str(sk) for sk in results]


@router.get("/networks/{network_scoped_key}/weight")
def get_network_weight(
    network_scoped_key,
    *,
    n4js: Neo4jStore = Depends(get_n4js_depends),
    token: TokenData = Depends(get_token_data_depends),
) -> float:
    sk = ScopedKey.from_str(network_scoped_key)
    validate_scopes(sk.scope, token)
    return n4js.get_taskhub_weight(sk)


@router.get("/networks/{network_scoped_key}/transformations")
def get_network_transformations(
    network_scoped_key,
    *,
    n4js: Neo4jStore = Depends(get_n4js_depends),
    token: TokenData = Depends(get_token_data_depends),
):
    sk = ScopedKey.from_str(network_scoped_key)
    validate_scopes(sk.scope, token)

    return [str(sk) for sk in n4js.get_network_transformations(network=sk)]


@router.get("/transformations/{transformation_scoped_key}/networks")
def get_transformation_networks(
    transformation_scoped_key,
    *,
    n4js: Neo4jStore = Depends(get_n4js_depends),
    token: TokenData = Depends(get_token_data_depends),
):
    sk = ScopedKey.from_str(transformation_scoped_key)
    validate_scopes(sk.scope, token)

    return [str(sk) for sk in n4js.get_transformation_networks(transformation=sk)]


@router.get("/networks/{network_scoped_key}/chemicalsystems")
def get_network_chemicalsystems(
    network_scoped_key,
    *,
    n4js: Neo4jStore = Depends(get_n4js_depends),
    token: TokenData = Depends(get_token_data_depends),
):
    sk = ScopedKey.from_str(network_scoped_key)
    validate_scopes(sk.scope, token)

    return [str(sk) for sk in n4js.get_network_chemicalsystems(network=sk)]


@router.get("/chemicalsystems/{chemicalsystem_scoped_key}/networks")
def get_chemicalsystem_networks(
    chemicalsystem_scoped_key,
    *,
    n4js: Neo4jStore = Depends(get_n4js_depends),
    token: TokenData = Depends(get_token_data_depends),
):
    sk = ScopedKey.from_str(chemicalsystem_scoped_key)
    validate_scopes(sk.scope, token)

    return [str(sk) for sk in n4js.get_chemicalsystem_networks(chemicalsystem=sk)]


@router.get("/transformations/{transformation_scoped_key}/chemicalsystems")
def get_transformation_chemicalsystems(
    transformation_scoped_key,
    *,
    n4js: Neo4jStore = Depends(get_n4js_depends),
    token: TokenData = Depends(get_token_data_depends),
):
    sk = ScopedKey.from_str(transformation_scoped_key)
    validate_scopes(sk.scope, token)

    return [
        str(sk) for sk in n4js.get_transformation_chemicalsystems(transformation=sk)
    ]


@router.get("/chemicalsystems/{chemicalsystem_scoped_key}/transformations")
def get_chemicalsystem_transformations(
    chemicalsystem_scoped_key,
    *,
    n4js: Neo4jStore = Depends(get_n4js_depends),
    token: TokenData = Depends(get_token_data_depends),
):
    sk = ScopedKey.from_str(chemicalsystem_scoped_key)
    validate_scopes(sk.scope, token)

    return [
        str(sk) for sk in n4js.get_chemicalsystem_transformations(chemicalsystem=sk)
    ]


@router.get("/networks/{network_scoped_key}")
def get_network(
    network_scoped_key,
    *,
    n4js: Neo4jStore = Depends(get_n4js_depends),
    token: TokenData = Depends(get_token_data_depends),
):
    sk = ScopedKey.from_str(network_scoped_key)
    validate_scopes(sk.scope, token)

    network = n4js.get_gufe(scoped_key=sk)
    return GufeJSONResponse(network)


@router.get("/transformations/{transformation_scoped_key}")
def get_transformation(
    transformation_scoped_key,
    *,
    n4js: Neo4jStore = Depends(get_n4js_depends),
    token: TokenData = Depends(get_token_data_depends),
):
    sk = ScopedKey.from_str(transformation_scoped_key)
    validate_scopes(sk.scope, token)

    transformation = n4js.get_gufe(scoped_key=sk)
    return GufeJSONResponse(transformation)


@router.get("/chemicalsystems/{chemicalsystem_scoped_key}")
def get_chemicalsystem(
    chemicalsystem_scoped_key,
    *,
    n4js: Neo4jStore = Depends(get_n4js_depends),
    token: TokenData = Depends(get_token_data_depends),
):
    sk = ScopedKey.from_str(chemicalsystem_scoped_key)
    validate_scopes(sk.scope, token)

    chemicalsystem = n4js.get_gufe(scoped_key=sk)
    return GufeJSONResponse(chemicalsystem)


### compute


@router.post("/networks/{scoped_key}/strategy")
def set_strategy(scoped_key: str, *, strategy: Dict = Body(...), scope: Scope):
    ...


@router.post("/transformations/{transformation_scoped_key}/tasks")
def create_tasks(
    transformation_scoped_key,
    *,
    extends: Optional[ScopedKey] = None,
    count: int = Body(...),
    n4js: Neo4jStore = Depends(get_n4js_depends),
    token: TokenData = Depends(get_token_data_depends),
) -> List[str]:
    sk = ScopedKey.from_str(transformation_scoped_key)
    validate_scopes(sk.scope, token)

    task_sks = []
    for i in range(count):
        task_sks.append(
            n4js.create_task(transformation=sk, extends=extends, creator=token.entity)
        )

    return [str(sk) for sk in task_sks]


@router.get("/tasks")
def query_tasks(
    *,
    status: str = None,
    scope: Scope = Depends(scope_params),
    n4js: Neo4jStore = Depends(get_n4js_depends),
    token: TokenData = Depends(get_token_data_depends),
):
    # Intersect query scopes with accessible scopes in the token
    query_scopes = validate_scopes_query(scope, token)

    # query each scope
    # loop might be removable in the future with a Union like operator on scopes
    results = []
    for single_query_scope in query_scopes:
        results.extend(n4js.query_tasks(status=status, scope=single_query_scope))

    return [str(sk) for sk in results]


@router.get("/networks/{network_scoped_key}/tasks")
def get_network_tasks(
    network_scoped_key,
    *,
    status: str = None,
    n4js: Neo4jStore = Depends(get_n4js_depends),
    token: TokenData = Depends(get_token_data_depends),
):
    # Get scope from scoped key provided by user, uniquely identifying the network
    sk = ScopedKey.from_str(network_scoped_key)
    validate_scopes(sk.scope, token)

    if status is not None:
        status = TaskStatusEnum(status)

    return [str(sk) for sk in n4js.get_network_tasks(network=sk, status=status)]


@router.get("/tasks/{task_scoped_key}/networks")
def get_task_networks(
    task_scoped_key,
    *,
    n4js: Neo4jStore = Depends(get_n4js_depends),
    token: TokenData = Depends(get_token_data_depends),
):
    sk = ScopedKey.from_str(task_scoped_key)
    validate_scopes(sk.scope, token)

    return [str(sk) for sk in n4js.get_task_networks(task=sk)]


@router.get("/transformations/{transformation_scoped_key}/tasks")
def get_transformation_tasks(
    transformation_scoped_key,
    *,
    extends: str = None,
    return_as: str = "list",
    status: str = None,
    n4js: Neo4jStore = Depends(get_n4js_depends),
    token: TokenData = Depends(get_token_data_depends),
):
    sk = ScopedKey.from_str(transformation_scoped_key)
    validate_scopes(sk.scope, token)

    if status is not None:
        status = TaskStatusEnum(status)

    task_sks = n4js.get_transformation_tasks(
        sk, extends=extends, return_as=return_as, status=status
    )

    if return_as == "list":
        return [str(sk) for sk in task_sks]
    elif return_as == "graph":
        return {
            str(sk): str(extends) if extends is not None else None
            for sk, extends in task_sks.items()
        }
    else:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"`return_as` takes 'list' or 'graph', not '{return_as}'",
        )


@router.get("/scopes/{scope}/status")
def get_scope_status(
    scope,
    *,
    n4js: Neo4jStore = Depends(get_n4js_depends),
    token: TokenData = Depends(get_token_data_depends),
):
    scope = Scope.from_str(scope)
    scope_space = validate_scopes_query(scope, token)

    status_counts = Counter()
    for single_scope in scope_space:
        status_counts.update(n4js.get_scope_status(single_scope))

    return dict(status_counts)


@router.get("/networks/{network_scoped_key}/status")
def get_network_status(
    network_scoped_key,
    *,
    n4js: Neo4jStore = Depends(get_n4js_depends),
    token: TokenData = Depends(get_token_data_depends),
):
    sk = ScopedKey.from_str(network_scoped_key)
    validate_scopes(sk.scope, token)

    status_counts = n4js.get_network_status(network_scoped_key)

    return status_counts


@router.get("/transformations/{transformation_scoped_key}/status")
def get_transformation_status(
    transformation_scoped_key,
    *,
    n4js: Neo4jStore = Depends(get_n4js_depends),
    token: TokenData = Depends(get_token_data_depends),
):
    sk = ScopedKey.from_str(transformation_scoped_key)
    validate_scopes(sk.scope, token)

    status_counts = n4js.get_transformation_status(transformation_scoped_key)

    return status_counts


@router.post("/networks/{network_scoped_key}/tasks/actioned")
def get_network_actioned_tasks(
    network_scoped_key,
    *,
    task_weights: bool = Body(embed=True),
    n4js: Neo4jStore = Depends(get_n4js_depends),
    token: TokenData = Depends(get_token_data_depends),
) -> Union[Dict[str, float], List[str]]:
    network_sk = ScopedKey.from_str(network_scoped_key)
    validate_scopes(network_sk.scope, token)

    taskhub_sk = n4js.get_taskhub(network_sk)
    task_sks = n4js.get_taskhub_actioned_tasks(taskhub_sk)

    if task_weights:
        return {str(task_sk): weight for task_sk, weight in task_sks.items()}

    return [str(task_sk) for task_sk in task_sks]


@router.post("/tasks/{task_scoped_key}/networks/actioned")
def get_task_actioned_networks(
    task_scoped_key,
    *,
    task_weights: bool = Body(embed=True),
    n4js: Neo4jStore = Depends(get_n4js_depends),
    token: TokenData = Depends(get_token_data_depends),
) -> Union[Dict[str, float], List[str]]:
    task_sk = ScopedKey.from_str(task_scoped_key)
    validate_scopes(task_sk.scope, token)

    network_sks = n4js.get_task_actioned_networks(task_sk)

    if task_weights:
        return {str(network_sk): weight for network_sk, weight in network_sks.items()}

    return [str(network_sk) for network_sk in network_sks]


@router.post("/networks/{network_scoped_key}/tasks/action")
def action_tasks(
    network_scoped_key,
    *,
    tasks: List[ScopedKey] = Body(embed=True),
    weight: Optional[Union[float, List[float]]] = Body(None, embed=True),
    n4js: Neo4jStore = Depends(get_n4js_depends),
    token: TokenData = Depends(get_token_data_depends),
) -> List[Union[str, None]]:
    sk = ScopedKey.from_str(network_scoped_key)
    validate_scopes(sk.scope, token)

    taskhub_sk = n4js.get_taskhub(sk)
    actioned_sks = n4js.action_tasks(tasks, taskhub_sk)

    try:
        if isinstance(weight, float):
            n4js.set_task_weights(tasks, taskhub_sk, weight)
        elif isinstance(weight, list):
            if len(weight) != len(tasks):
                detail = "weight (when in a list) must have the same length as tasks"
                raise HTTPException(
                    status_code=http_status.HTTP_400_BAD_REQUEST,
                    detail=detail,
                )

            n4js.set_task_weights(
                {task: weight for task, weight in zip(tasks, weight)}, taskhub_sk, None
            )
    except ValueError as e:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(e))

    return [str(sk) if sk is not None else None for sk in actioned_sks]


@router.post("/networks/{network_scoped_key}/weight")
def set_network_weight(
    network_scoped_key,
    *,
    weight: float = Body(),
    n4js: Neo4jStore = Depends(get_n4js_depends),
    token: TokenData = Depends(get_token_data_depends),
) -> None:
    sk = ScopedKey.from_str(network_scoped_key)
    validate_scopes(sk.scope, token)

    try:
        n4js.set_taskhub_weight(sk, weight)
    except ValueError as e:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/networks/{network_scoped_key}/tasks/cancel")
def cancel_tasks(
    network_scoped_key,
    *,
    tasks: List[ScopedKey] = Body(embed=True),
    n4js: Neo4jStore = Depends(get_n4js_depends),
    token: TokenData = Depends(get_token_data_depends),
) -> List[Union[str, None]]:
    sk = ScopedKey.from_str(network_scoped_key)
    validate_scopes(sk.scope, token)

    taskhub_sk = n4js.get_taskhub(sk)
    canceled_sks = n4js.cancel_tasks(tasks, taskhub_sk)

    return [str(sk) if sk is not None else None for sk in canceled_sks]


@router.post("/bulk/tasks/priority/get")
def tasks_priority_get(
    *,
    tasks: List[ScopedKey] = Body(embed=True),
    n4js: Neo4jStore = Depends(get_n4js_depends),
    token: TokenData = Depends(get_token_data_depends),
) -> List[int]:
    valid_tasks = []
    for task_sk in tasks:
        try:
            validate_scopes(task_sk.scope, token)
            valid_tasks.append(task_sk)
        except HTTPException:
            valid_tasks.append(None)

    priorities = n4js.get_task_priority(valid_tasks)

    return priorities


@router.post("/bulk/tasks/priority/set")
def tasks_priority_set(
    *,
    tasks: List[ScopedKey] = Body(embed=True),
    priority: int = Body(embed=True),
    n4js: Neo4jStore = Depends(get_n4js_depends),
    token: TokenData = Depends(get_token_data_depends),
) -> List[Union[str, None]]:
    valid_tasks = []
    for task_sk in tasks:
        try:
            validate_scopes(task_sk.scope, token)
            valid_tasks.append(task_sk)
        except HTTPException:
            valid_tasks.append(None)

    try:
        tasks_updated = n4js.set_task_priority(valid_tasks, priority)
    except ValueError as e:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(e))

    return [str(t) if t is not None else None for t in tasks_updated]


@router.post("/bulk/tasks/status/get")
def tasks_status_get(
    *,
    tasks: List[ScopedKey] = Body(embed=True),
    n4js: Neo4jStore = Depends(get_n4js_depends),
    token: TokenData = Depends(get_token_data_depends),
) -> List[Union[str, None]]:
    valid_tasks = []
    for task_sk in tasks:
        try:
            validate_scopes(task_sk.scope, token)
            valid_tasks.append(task_sk)
        except HTTPException:
            valid_tasks.append(None)

    statuses = n4js.get_task_status(valid_tasks)

    return [status.value if status is not None else None for status in statuses]


@router.post("/bulk/tasks/status/set")
def tasks_status_set(
    *,
    tasks: List[ScopedKey] = Body(),
    status: str = Body(),
    n4js: Neo4jStore = Depends(get_n4js_depends),
    token: TokenData = Depends(get_token_data_depends),
) -> List[Union[str, None]]:
    status = TaskStatusEnum(status)
    if status not in (
        TaskStatusEnum.waiting,
        TaskStatusEnum.invalid,
        TaskStatusEnum.deleted,
    ):
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot set status to '{status}', must be one of 'waiting', 'invalid', 'deleted'",
        )

    valid_tasks = []
    for task_sk in tasks:
        try:
            validate_scopes(task_sk.scope, token)
            valid_tasks.append(task_sk)
        except HTTPException:
            valid_tasks.append(None)

    tasks_updated = n4js.set_task_status(valid_tasks, status)

    return [str(t) if t is not None else None for t in tasks_updated]


@router.post("/tasks/{task_scoped_key}/status")
def set_task_status(
    task_scoped_key,
    status: str = Body(),
    n4js: Neo4jStore = Depends(get_n4js_depends),
    token: TokenData = Depends(get_token_data_depends),
):
    status = TaskStatusEnum(status)
    if status not in (
        TaskStatusEnum.waiting,
        TaskStatusEnum.invalid,
        TaskStatusEnum.deleted,
    ):
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot set status to '{status}', must be one of 'waiting', 'invalid', 'deleted'",
        )
    task_sk = ScopedKey.from_str(task_scoped_key)
    validate_scopes(task_sk.scope, token)
    tasks_statused = n4js.set_task_status([task_sk], status)
    return [str(t) if t is not None else None for t in tasks_statused][0]


@router.get("/tasks/{task_scoped_key}/status")
def get_task_status(
    task_scoped_key,
    *,
    n4js: Neo4jStore = Depends(get_n4js_depends),
    token: TokenData = Depends(get_token_data_depends),
):
    task_sk = ScopedKey.from_str(task_scoped_key)
    validate_scopes(task_sk.scope, token)

    status = n4js.get_task_status([task_sk])

    return status[0].value


@router.get("/tasks/{task_scoped_key}/transformation")
def get_task_transformation(
    task_scoped_key,
    *,
    n4js: Neo4jStore = Depends(get_n4js_depends),
    token: TokenData = Depends(get_token_data_depends),
):
    sk = ScopedKey.from_str(task_scoped_key)
    validate_scopes(sk.scope, token)

    transformation: ScopedKey

    transformation, protocoldagresultref = n4js.get_task_transformation(
        task=task_scoped_key,
        return_gufe=False,
    )

    return str(transformation)


### results


@router.get("/transformations/{transformation_scoped_key}/results")
def get_transformation_results(
    transformation_scoped_key,
    *,
    n4js: Neo4jStore = Depends(get_n4js_depends),
    token: TokenData = Depends(get_token_data_depends),
):
    sk = ScopedKey.from_str(transformation_scoped_key)
    validate_scopes(sk.scope, token)

    return [str(sk) for sk in n4js.get_transformation_results(sk)]


@router.get("/transformations/{transformation_scoped_key}/failures")
def get_transformation_failures(
    transformation_scoped_key,
    *,
    n4js: Neo4jStore = Depends(get_n4js_depends),
    token: TokenData = Depends(get_token_data_depends),
):
    sk = ScopedKey.from_str(transformation_scoped_key)
    validate_scopes(sk.scope, token)

    return [str(sk) for sk in n4js.get_transformation_failures(sk)]


@router.get(
    "/transformations/{transformation_scoped_key}/{route}/{protocoldagresultref_scoped_key}"
)
def get_protocoldagresult(
    protocoldagresultref_scoped_key,
    route,
    transformation_scoped_key,
    *,
    n4js: Neo4jStore = Depends(get_n4js_depends),
    s3os: S3ObjectStore = Depends(get_s3os_depends),
    token: TokenData = Depends(get_token_data_depends),
) -> List[str]:
    if route == "results":
        ok = True
    elif route == "failures":
        ok = False
    else:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"`route` takes 'results' or 'failures', not '{route}'",
        )

    sk = ScopedKey.from_str(protocoldagresultref_scoped_key)
    transformation_sk = ScopedKey.from_str(transformation_scoped_key)

    validate_scopes(sk.scope, token)
    validate_scopes(transformation_sk.scope, token)

    protocoldagresultref = n4js.get_gufe(scoped_key=sk)
    pdr_sk = ScopedKey(gufe_key=protocoldagresultref.obj_key, **sk.scope.dict())

    # we leave each ProtocolDAGResult in string form to avoid
    # deserializing/reserializing here; just passing through to client
    try:
        pdr: str = s3os.pull_protocoldagresult(
            pdr_sk, transformation_sk, return_as="json", ok=ok
        )
    except:
        # if we fail to get the object with the above, fall back to
        # location-based retrieval
        pdr: str = s3os.pull_protocoldagresult(
            location=protocoldagresultref.location,
            return_as="json",
            ok=ok,
        )

    return [pdr]


@router.get("/tasks/{task_scoped_key}/results")
def get_task_results(
    task_scoped_key,
    *,
    n4js: Neo4jStore = Depends(get_n4js_depends),
    token: TokenData = Depends(get_token_data_depends),
):
    sk = ScopedKey.from_str(task_scoped_key)
    validate_scopes(sk.scope, token)

    return [str(sk) for sk in n4js.get_task_results(sk)]


@router.get("/tasks/{task_scoped_key}/failures")
def get_task_failures(
    task_scoped_key,
    *,
    n4js: Neo4jStore = Depends(get_n4js_depends),
    token: TokenData = Depends(get_token_data_depends),
):
    sk = ScopedKey.from_str(task_scoped_key)
    validate_scopes(sk.scope, token)

    return [str(sk) for sk in n4js.get_task_failures(sk)]


### add router

app.include_router(router)

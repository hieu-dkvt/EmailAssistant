"""API cấu hình dự án cho web (spec 2026-10-31-project-settings-design mục 6).

Xác thực bằng phiên web (WebUserId; request ghi cần header X-PMM-Request). Bộ lọc và tên dự án
có thể chứa tên khách: log chỉ có id, không ghi giá trị.
"""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Request, Response, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.deps import get_engine, read_body
from app.api.errors import ApiError, validation_error_response
from app.api.web_deps import WebUserId, json_body
from app.db.session import user_session
from app.projects import (
    ProjectError,
    create_project,
    delete_project,
    list_projects,
    update_project,
)
from app.schemas import MAX_BIGINT
from app.web_schemas import ErrorBody, Project, ProjectInput, ProjectList

log = logging.getLogger("app.api")
router = APIRouter(prefix="/api/v1")

Engine = Annotated[AsyncEngine, Depends(get_engine)]
ProjectId = Annotated[int, Path(gt=0, le=MAX_BIGINT)]
UNAUTHORIZED: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorBody, "description": "Chưa đăng nhập"}
}
WRITE: dict[int | str, dict[str, Any]] = {
    **UNAUTHORIZED,
    403: {"model": ErrorBody, "description": "Thiếu header X-PMM-Request"},
    404: {"model": ErrorBody, "description": "project_not_found"},
    409: {"model": ErrorBody, "description": "project_name_taken, project_limit"},
}


def _api_error(e: ProjectError) -> ApiError:
    return ApiError(e.status_code, e.detail, e.code)


async def _parse(request: Request) -> ProjectInput:
    raw = await read_body(request)
    try:
        return ProjectInput.model_validate_json(raw)
    except ValidationError as e:
        raise validation_error_response(e.errors()) from None


@router.get("/projects", response_model=ProjectList, responses=UNAUTHORIZED)
async def get_projects(user_id: WebUserId, engine: Engine) -> ProjectList:
    async with user_session(engine, user_id) as s:
        return await list_projects(s, user_id)


@router.post(
    "/projects",
    response_model=Project,
    status_code=status.HTTP_201_CREATED,
    responses=WRITE,
    openapi_extra=json_body(ProjectInput),
)
async def post_project(request: Request, user_id: WebUserId, engine: Engine) -> Project:
    req = await _parse(request)
    try:
        async with user_session(engine, user_id) as s:
            project = await create_project(s, user_id, req)
    except ProjectError as e:
        raise _api_error(e) from None
    log.info("project-create user=%s project=%s", user_id, project.id)
    return project


@router.put(
    "/projects/{project_id}",
    response_model=Project,
    responses=WRITE,
    openapi_extra=json_body(ProjectInput),
)
async def put_project(
    project_id: ProjectId, request: Request, user_id: WebUserId, engine: Engine
) -> Project:
    req = await _parse(request)
    try:
        async with user_session(engine, user_id) as s:
            project = await update_project(s, user_id, project_id, req)
    except ProjectError as e:
        raise _api_error(e) from None
    log.info("project-update user=%s project=%s", user_id, project_id)
    return project


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT, responses=WRITE)
async def remove_project(project_id: ProjectId, user_id: WebUserId, engine: Engine) -> Response:
    try:
        async with user_session(engine, user_id) as s:
            await delete_project(s, user_id, project_id)
    except ProjectError as e:
        raise _api_error(e) from None
    log.info("project-delete user=%s project=%s", user_id, project_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

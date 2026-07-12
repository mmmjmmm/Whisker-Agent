from typing import Optional

from fastapi import APIRouter, Body, Depends, File, UploadFile

from app.application.services.skill_service import SkillService
from app.interfaces.schemas.base import Response
from app.interfaces.schemas.skill import (
    ListSkillsResponse,
    SkillDetail,
    SkillListItem,
)
from app.interfaces.service_dependencies import get_skill_service

router = APIRouter(prefix="/app-config/skills", tags=["设置模块"])


@router.get("", response_model=Response[ListSkillsResponse])
async def list_skills(
    service: SkillService = Depends(get_skill_service),
) -> Response[ListSkillsResponse]:
    skills = await service.list_skills()
    return Response.success(
        data=ListSkillsResponse(
            skills=[SkillListItem.model_validate(skill) for skill in skills]
        )
    )


@router.post("", response_model=Response[SkillListItem])
async def upload_skill(
    file: UploadFile = File(...),
    service: SkillService = Depends(get_skill_service),
) -> Response[SkillListItem]:
    skill = await service.upload(file)
    return Response.success(
        msg="Skill 上传成功",
        data=SkillListItem.model_validate(skill),
    )


@router.get("/{skill_id}", response_model=Response[SkillDetail])
async def get_skill(
    skill_id: str,
    service: SkillService = Depends(get_skill_service),
) -> Response[SkillDetail]:
    skill = await service.get_skill(skill_id)
    return Response.success(data=SkillDetail.model_validate(skill))


@router.post("/{skill_id}/enabled", response_model=Response[SkillListItem])
async def set_skill_enabled(
    skill_id: str,
    enabled: bool = Body(..., embed=True),
    service: SkillService = Depends(get_skill_service),
) -> Response[SkillListItem]:
    skill = await service.set_enabled(skill_id, enabled)
    return Response.success(
        msg="Skill 启用状态更新成功",
        data=SkillListItem.model_validate(skill),
    )


@router.post(
    "/{skill_id}/delete",
    response_model=Response[Optional[dict]],
)
async def delete_skill(
    skill_id: str,
    service: SkillService = Depends(get_skill_service),
) -> Response[Optional[dict]]:
    await service.delete_skill(skill_id)
    return Response.success(msg="Skill 删除成功")

"""Public, non-sensitive feature flags for the mini-app."""

from fastapi import APIRouter
from pydantic import BaseModel

from src.core.config import test_mode_enabled


router = APIRouter(prefix="/service", tags=["service"])


class ServiceConfig(BaseModel):
    test_mode: bool


@router.get("/config", response_model=ServiceConfig)
async def get_service_config() -> ServiceConfig:
    return ServiceConfig(test_mode=test_mode_enabled())

from fastapi import APIRouter, Depends
from app.auth.dependencies import get_current_user
from app.auth.fixtures import FIXTURE_ORGS, FIXTURE_USERS
from app.schemas import UserResponse
from app.models import User

router = APIRouter(prefix="/auth", tags=["Authentication & Identities"])

@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    """Returns the authenticated user's identity, role, and organization."""
    return current_user

@router.get("/fixtures")
async def get_fixtures():
    """Development fixture listing seeded organizations, users, and API keys."""
    return {
        "organizations": FIXTURE_ORGS,
        "sample_identities": FIXTURE_USERS,
        "auth_methods": [
            "Authorization: Bearer <api_key>",
            "X-API-Key: <api_key>",
            "X-User-Id: <user_id>"
        ]
    }

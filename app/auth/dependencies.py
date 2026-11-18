from typing import List, Optional
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials, APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models import User, Organization

security_bearer = HTTPBearer(auto_error=False)
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
user_id_header = APIKeyHeader(name="X-User-Id", auto_error=False)

async def get_current_user(
    bearer: Optional[HTTPAuthorizationCredentials] = Security(security_bearer),
    api_key: Optional[str] = Security(api_key_header),
    user_id: Optional[str] = Security(user_id_header),
    db: AsyncSession = Depends(get_db)
) -> User:
    token: Optional[str] = None
    if bearer:
        token = bearer.credentials
    elif api_key:
        token = api_key

    query = select(User)
    if token:
        query = query.where(User.api_key == token)
    elif user_id:
        query = query.where(User.id == user_id)
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Authentication required. Provide Bearer token, X-API-Key, or X-User-Id header."}
        )

    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Invalid authentication credentials."}
        )
    return user


def require_role(allowed_roles: List[str]):
    def role_checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "FORBIDDEN",
                    "message": f"Operation requires one of the roles: {', '.join(allowed_roles)}. Current role: {current_user.role}"
                }
            )
        return current_user
    return role_checker


def verify_organization(current_user: User, organization_id: str):
    if current_user.organization_id != organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "CROSS_ORGANIZATION_FORBIDDEN",
                "message": "Access denied: cannot access or modify resources belonging to another organization."
            }
        )

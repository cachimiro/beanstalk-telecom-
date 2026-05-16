"""Admin users CRUD API."""
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_

from api.auth import get_current_admin
from api.db.base import get_db
from api.models.user import User
from api.services.email import send_test_email

router = APIRouter(dependencies=[Depends(get_current_admin)])


class UserCreate(BaseModel):
    full_name: str
    email: str
    extension: str
    active: bool = True


class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    email: Optional[str] = None
    extension: Optional[str] = None
    active: Optional[bool] = None


class UserOut(BaseModel):
    id: str
    full_name: str
    email: str
    extension: str
    active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


def _user_out(u: User) -> dict:
    return {
        "id": str(u.id),
        "full_name": u.full_name,
        "email": u.email,
        "extension": u.extension,
        "active": u.active,
        "created_at": u.created_at.isoformat(),
        "updated_at": u.updated_at.isoformat(),
    }


@router.get("")
async def list_users(
    search: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    q = select(User).order_by(User.created_at.desc())
    if search:
        term = f"%{search}%"
        q = q.where(
            or_(
                User.full_name.ilike(term),
                User.email.ilike(term),
                User.extension.ilike(term),
            )
        )
    q = q.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(q)
    users = result.scalars().all()

    count_q = select(func.count()).select_from(User)
    if search:
        term = f"%{search}%"
        count_q = count_q.where(
            or_(
                User.full_name.ilike(term),
                User.email.ilike(term),
                User.extension.ilike(term),
            )
        )
    total = (await db.execute(count_q)).scalar()

    return {"total": total, "page": page, "page_size": page_size, "items": [_user_out(u) for u in users]}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_user(body: UserCreate, db: AsyncSession = Depends(get_db)):
    # Check extension uniqueness among active users
    if body.active:
        existing = await db.execute(
            select(User).where(User.extension == body.extension, User.active == True)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"An active user with extension {body.extension} already exists",
            )

    user = User(
        full_name=body.full_name,
        email=body.email,
        extension=body.extension,
        active=body.active,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return _user_out(user)


@router.get("/{user_id}")
async def get_user(user_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return _user_out(user)


@router.put("/{user_id}")
async def update_user(user_id: str, body: UserUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check extension conflict if changing extension or activating
    new_ext = body.extension or user.extension
    new_active = body.active if body.active is not None else user.active
    if new_active and new_ext != user.extension:
        existing = await db.execute(
            select(User).where(
                User.extension == new_ext,
                User.active == True,
                User.id != user.id,
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"An active user with extension {new_ext} already exists",
            )

    if body.full_name is not None:
        user.full_name = body.full_name
    if body.email is not None:
        user.email = body.email
    if body.extension is not None:
        user.extension = body.extension
    if body.active is not None:
        user.active = body.active
    user.updated_at = datetime.utcnow()

    await db.commit()
    await db.refresh(user)
    return _user_out(user)


@router.patch("/{user_id}/toggle")
async def toggle_user(user_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # If activating, check for extension conflict
    if not user.active:
        existing = await db.execute(
            select(User).where(
                User.extension == user.extension,
                User.active == True,
                User.id != user.id,
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot activate: extension {user.extension} is already in use by another active user",
            )

    user.active = not user.active
    user.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(user)
    return _user_out(user)


@router.post("/{user_id}/test-email")
async def test_email(user_id: str, db: AsyncSession = Depends(get_db)):
    import asyncio
    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    try:
        loop = asyncio.get_event_loop()
        message_id = await loop.run_in_executor(
            None, send_test_email, user.email, user.full_name
        )
        return {"status": "sent", "message_id": message_id, "recipient": user.email}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Email delivery failed: {exc}")

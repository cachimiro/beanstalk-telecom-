"""Match a parsed recording to a registered user by extension or name.

Priority:
  1. folder_extension → user.extension
  2. filename_extension → user.extension
  3. user_name (case-insensitive) → user.full_name
  4. No match → return None
"""
import logging
import unicodedata
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from api.models.user import User
from api.services.parser import ParsedRecording

logger = logging.getLogger(__name__)


def _normalise(text: str) -> str:
    """Lowercase and strip accents for fuzzy name matching."""
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
    return ascii_str.lower().strip()


async def match_user(db: AsyncSession, parsed: ParsedRecording) -> Optional[User]:
    # 1. Match by folder extension
    user = await _find_by_extension(db, parsed.folder_extension)
    if user:
        logger.info("Matched by folder_extension=%s → user=%s", parsed.folder_extension, user.email)
        return user

    # 2. Match by filename extension (may differ from folder)
    if parsed.filename_extension != parsed.folder_extension:
        user = await _find_by_extension(db, parsed.filename_extension)
        if user:
            logger.info(
                "Matched by filename_extension=%s → user=%s", parsed.filename_extension, user.email
            )
            return user

    # 3. Match by bracketed name (normalised)
    user = await _find_by_name(db, parsed.user_name)
    if user:
        logger.info("Matched by name=%s → user=%s", parsed.user_name, user.email)
        return user

    logger.warning(
        "No user matched for extension=%s name=%s", parsed.folder_extension, parsed.user_name
    )
    return None


async def _find_by_extension(db: AsyncSession, extension: str) -> Optional[User]:
    result = await db.execute(
        select(User).where(User.extension == extension, User.active == True)
    )
    return result.scalar_one_or_none()


async def _find_by_name(db: AsyncSession, name: str) -> Optional[User]:
    normalised = _normalise(name)
    result = await db.execute(select(User).where(User.active == True))
    users = result.scalars().all()
    for user in users:
        if _normalise(user.full_name) == normalised:
            return user
    return None

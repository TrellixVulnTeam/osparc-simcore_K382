from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from .utils_migration import get_current_head


async def get_pg_engine_stateinfo(engine: AsyncEngine) -> dict[str, Any]:
    return {
        "current pool connections": f"{engine.pool.checkedin()=},{engine.pool.checkedout()=}",
    }


class DBMigrationError(RuntimeError):
    pass


async def raise_if_migration_not_ready(engine: AsyncEngine):
    """Ensures db migration is complete

    :raises DBMigrationError
    """
    async with engine.connect() as conn:
        version_num = await conn.scalar(
            sa.DDL('SELECT "version_num" FROM "alembic_version"')
        )
        head_version_num = get_current_head()
        if version_num != head_version_num:
            raise DBMigrationError(
                f"Migration is incomplete, expected {head_version_num} but got {version_num}"
            )

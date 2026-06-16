from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from src.core.config import settings

engine_options = {
    "echo": False,
    "future": True,
    "pool_pre_ping": True,
}

if not settings.async_database_url.startswith("sqlite"):
    engine_options.update(
        pool_size=max(1, settings.DB_POOL_SIZE),
        max_overflow=max(0, settings.DB_MAX_OVERFLOW),
        pool_recycle=max(30, settings.DB_POOL_RECYCLE_SECONDS),
    )

# Create async engine using asyncpg driver url
engine = create_async_engine(
    settings.async_database_url,
    **engine_options,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False
)

Base = declarative_base()

async def get_db():
    """FastAPI DB Session Dependency"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def get_read_db():
    """Read-only DB session dependency for hot GET paths."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

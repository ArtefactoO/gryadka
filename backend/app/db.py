from __future__ import annotations

from app.config import settings
from app.services.registry_repository import RegistryRepository


def is_postgres_configured() -> bool:
    return all(
        [
            settings.postgres_host,
            settings.postgres_db,
            settings.postgres_user,
        ]
    )


def run_schema_migrations() -> None:
    if not is_postgres_configured():
        return
    RegistryRepository().ensure_schema()

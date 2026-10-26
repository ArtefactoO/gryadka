"""
Models for MongoDB backup status reported by mongo-backup-exporter.
"""
from typing import Optional
from pydantic import BaseModel


class MongoBackupFile(BaseModel):
    file: str
    backup_date: Optional[str] = None
    mtime: Optional[str] = None
    size_bytes: Optional[int] = None
    active: bool = False


class MongoDatabaseBackup(BaseModel):
    database: str
    status: str
    last_completed: Optional[MongoBackupFile] = None
    running: Optional[MongoBackupFile] = None
    files_count: int = 0

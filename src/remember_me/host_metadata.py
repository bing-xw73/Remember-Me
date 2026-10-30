# SPDX-License-Identifier: CPAL-1.0
"""Host-only provenance, stored separately from ombre-brain-assets-v1."""

from dataclasses import dataclass
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from remember_me.core import InvalidMetadata
from remember_me.core.normalization import (
    normalize_description, normalize_tags, normalize_title,
)


@dataclass(frozen=True)
class UploadMetadata:
    title: str = ""
    description: str = ""
    tags: tuple[str, ...] = ()
    occurred_at: str = ""
    source: str = ""


def upload_metadata(title="", description="", tags=(), occurred_at="", source=""):
    occurred = ""
    if occurred_at:
        try:
            parsed = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("timezone required")
            occurred = parsed.astimezone(timezone.utc).isoformat()
        except (TypeError, ValueError, AttributeError):
            raise InvalidMetadata() from None
    return UploadMetadata(
        normalize_title(title), normalize_description(description),
        normalize_tags(tags), occurred, normalize_title(source),
    )


class HostMetadataStore:
    """Sidecar DB backed up alongside the unmodified Core DB and CAS."""

    def __init__(self, data_root):
        self.path = Path(data_root) / "host-metadata.sqlite3"
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS provenance ("
                       "asset_id TEXT PRIMARY KEY, occurred_at TEXT NOT NULL, "
                       "source TEXT NOT NULL)")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def record_new(self, asset_id, metadata):
        with self._connect() as db:
            db.execute("INSERT OR IGNORE INTO provenance VALUES (?, ?, ?)",
                       (asset_id, metadata.occurred_at, metadata.source))

    def get(self, asset_id):
        with self._connect() as db:
            row = db.execute("SELECT occurred_at, source FROM provenance WHERE asset_id = ?",
                             (asset_id,)).fetchone()
        return {"occurred_at": row[0], "source": row[1]} if row else {"occurred_at": "", "source": ""}

    def delete(self, asset_id):
        with self._connect() as db:
            db.execute("DELETE FROM provenance WHERE asset_id = ?", (asset_id,))

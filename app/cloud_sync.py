"""
Optional cloud database synchronisation.

Syncs the local SQLite database file to any S3-compatible object storage
provider (Cloudflare R2, AWS S3, Backblaze B2, etc.).

Design principles
-----------------
- Completely optional: all functions no-op when cloud sync is not configured.
- Offline-first: every operation degrades gracefully on network failure.
- Single-writer locking: a lock file in the bucket prevents two sessions from
  writing concurrently. The lock is held for the lifetime of the process and
  cleared only on clean shutdown. There is no TTL — see the crash recovery docs
  in AGENTS.md for how to handle a stale lock.

Lock file
---------
Stored at ``{CLOUD_SYNC_OBJECT_KEY}.lock`` (e.g. ``trades.db.lock``).
Content (JSON):
    {
        "session_id": "<uuid4>",
        "hostname":   "machine-a.local",
        "pid":        12345,
        "locked_at":  "2025-01-01T10:00:00Z"
    }

Session identity
----------------
On lock acquisition the session_id is written to
``~/.local/share/tui-trader/cloud_sync.session``.  On startup, if a cloud
lock is found, this file is read and compared to determine whether the current
process is the rightful owner (crash recovery Path A).
"""

import json
import logging
import os
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import uuid4

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _cfg():
    """Lazy import of config to avoid circular imports and import-time side effects."""
    from app import config

    return config


def _db_path() -> Path:
    return _cfg().DATABASE_PATH


def _lock_key() -> str:
    return f"{_cfg().CLOUD_SYNC_OBJECT_KEY}.lock"


def _session_file() -> Path:
    return _cfg().DATA_DIR / "cloud_sync.session"


def _get_client():
    """Build and return a boto3 S3 client from config."""
    import boto3  # local import — only needed when cloud sync is active

    cfg = _cfg()
    kwargs = {
        "aws_access_key_id": cfg.CLOUD_SYNC_KEY_ID,
        "aws_secret_access_key": cfg.CLOUD_SYNC_KEY_SECRET,
    }
    if cfg.CLOUD_SYNC_ENDPOINT_URL:
        kwargs["endpoint_url"] = cfg.CLOUD_SYNC_ENDPOINT_URL
    return boto3.client("s3", **kwargs)


# ---------------------------------------------------------------------------
# Public API — configuration check
# ---------------------------------------------------------------------------


def is_configured() -> bool:
    """
    Return True only if cloud sync is enabled and all required vars are set.
    When False, all other functions in this module are no-ops.
    """
    cfg = _cfg()
    return bool(
        cfg.CLOUD_SYNC_ENABLED
        and cfg.CLOUD_SYNC_BUCKET
        and cfg.CLOUD_SYNC_KEY_ID
        and cfg.CLOUD_SYNC_KEY_SECRET
        and cfg.CLOUD_SYNC_OBJECT_KEY
    )


# ---------------------------------------------------------------------------
# Session identity
# ---------------------------------------------------------------------------


def load_local_session_id() -> Optional[str]:
    """Read the session ID written during the last lock acquisition, or None."""
    p = _session_file()
    try:
        return p.read_text().strip() or None
    except FileNotFoundError:
        return None
    except Exception as e:
        log.warning("cloud_sync: could not read session file: %s", e)
        return None


def save_local_session_id(session_id: str) -> None:
    """Persist the session ID to the local data directory."""
    try:
        _session_file().write_text(session_id)
    except Exception as e:
        log.warning("cloud_sync: could not write session file: %s", e)


def clear_local_session_id() -> None:
    """Delete the local session ID file."""
    try:
        _session_file().unlink(missing_ok=True)
    except Exception as e:
        log.warning("cloud_sync: could not delete session file: %s", e)


# ---------------------------------------------------------------------------
# Lock management
# ---------------------------------------------------------------------------


def check_lock() -> Optional[dict]:
    """
    Read the cloud lock file.

    Returns the parsed lock dict if a lock exists, or None if there is no lock
    or the bucket is unreachable.
    """
    if not is_configured():
        return None
    cfg = _cfg()
    try:
        client = _get_client()
        resp = client.get_object(Bucket=cfg.CLOUD_SYNC_BUCKET, Key=_lock_key())
        return json.loads(resp["Body"].read().decode())
    except Exception as e:
        # Safely extract the S3 error code from a boto3 ClientError.
        # Non-boto3 exceptions (network errors, TLS failures, etc.) have no
        # "response" attribute — the original getattr chain returned None which
        # then raised AttributeError on .get(), silently swallowing the error
        # and causing lock detection to fail.
        try:
            code = e.response["Error"]["Code"]  # type: ignore[attr-defined]
        except (AttributeError, KeyError, TypeError):
            code = ""
        if code != "NoSuchKey":
            log.warning("cloud_sync: could not read lock file: %s", e)
        return None


def acquire_lock(session_id: str) -> None:
    """
    Write the lock file to the bucket and persist the session ID locally.
    Logs a warning on failure but does not raise.
    """
    if not is_configured():
        return
    cfg = _cfg()
    lock = {
        "session_id": session_id,
        "hostname": socket.gethostname(),
        "pid": os.getpid(),
        "locked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    try:
        client = _get_client()
        client.put_object(
            Bucket=cfg.CLOUD_SYNC_BUCKET,
            Key=_lock_key(),
            Body=json.dumps(lock).encode(),
            ContentType="application/json",
        )
        save_local_session_id(session_id)
        log.info("cloud_sync: lock acquired (session %s)", session_id)
    except Exception as e:
        log.warning("cloud_sync: could not acquire lock: %s", e)


def release_lock(session_id: str) -> None:
    """
    Delete the cloud lock file, but only if we own it.
    Logs a warning on failure but does not raise.
    """
    if not is_configured():
        return
    cfg = _cfg()
    # Confirm we still own the lock before deleting
    current = check_lock()
    if current is None:
        return  # already gone
    if current.get("session_id") != session_id:
        log.warning(
            "cloud_sync: will not release lock — owned by a different session (%s)",
            current.get("session_id"),
        )
        return
    try:
        client = _get_client()
        client.delete_object(Bucket=cfg.CLOUD_SYNC_BUCKET, Key=_lock_key())
        log.info("cloud_sync: lock released (session %s)", session_id)
    except Exception as e:
        log.warning("cloud_sync: could not release lock: %s", e)


def force_clear_lock() -> None:
    """
    Delete the cloud lock file unconditionally.
    Used by the --force-unlock recovery path only.
    """
    if not is_configured():
        return
    cfg = _cfg()
    try:
        client = _get_client()
        client.delete_object(Bucket=cfg.CLOUD_SYNC_BUCKET, Key=_lock_key())
        log.info("cloud_sync: lock force-cleared")
    except Exception as e:
        log.warning("cloud_sync: could not force-clear lock: %s", e)


# ---------------------------------------------------------------------------
# Database sync
# ---------------------------------------------------------------------------


def sync_down() -> bool:
    """
    Download the database from the bucket if the remote copy is newer than
    the local file (or if no local file exists).

    Returns True if the local file was replaced, False otherwise.
    Silently returns False on any network or configuration error.
    """
    if not is_configured():
        return False
    cfg = _cfg()
    db_path = _db_path()
    try:
        client = _get_client()
        head = client.head_object(
            Bucket=cfg.CLOUD_SYNC_BUCKET, Key=cfg.CLOUD_SYNC_OBJECT_KEY
        )
        remote_mtime: datetime = head["LastModified"]

        if db_path.exists():
            local_mtime = datetime.fromtimestamp(
                db_path.stat().st_mtime, tz=timezone.utc
            )
            if local_mtime >= remote_mtime:
                log.info("cloud_sync: local DB is up to date, skipping download")
                return False

        log.info("cloud_sync: downloading DB from bucket")
        resp = client.get_object(
            Bucket=cfg.CLOUD_SYNC_BUCKET, Key=cfg.CLOUD_SYNC_OBJECT_KEY
        )
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db_path.write_bytes(resp["Body"].read())
        log.info("cloud_sync: DB downloaded successfully")
        return True

    except Exception as e:
        try:
            code = e.response["Error"]["Code"]  # type: ignore[attr-defined]
        except (AttributeError, KeyError, TypeError):
            code = ""
        if code in ("404", "NoSuchKey"):
            log.info("cloud_sync: no remote DB found, using local")
        else:
            log.warning("cloud_sync: sync_down failed: %s", e)
        return False


def sync_up() -> bool:
    """
    Flush the SQLite WAL and upload the local database to the bucket.

    Returns True on success, False on any error.
    Silently returns False when cloud sync is not configured.
    """
    if not is_configured():
        return False
    cfg = _cfg()
    db_path = _db_path()
    if not db_path.exists():
        log.warning("cloud_sync: sync_up called but DB file does not exist")
        return False
    try:
        # Flush WAL into the main DB file before copying
        import sqlite3

        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()

        client = _get_client()
        with db_path.open("rb") as fh:
            client.put_object(
                Bucket=cfg.CLOUD_SYNC_BUCKET,
                Key=cfg.CLOUD_SYNC_OBJECT_KEY,
                Body=fh,
            )
        log.info("cloud_sync: DB uploaded successfully")
        return True
    except Exception as e:
        log.warning("cloud_sync: sync_up failed: %s", e)
        return False

"""Pull activities straight from Garmin Connect.

Convenience, not a foundation. This talks to the same private endpoints the phone app
uses, which is a grey area under Garmin's terms and will break again eventually. Keep the
official export as your fallback and nothing is lost when it does.

Your password is used once, to obtain tokens, and is never written to disk. Tokens live in
~/.wattif/garmin with owner-only permissions.
"""

from __future__ import annotations

import io
import os
import time
import zipfile
from getpass import getpass
from pathlib import Path

from garminconnect import Garmin

TOKEN_DIR = Path.home() / ".wattif" / "garmin"
CYCLING_TYPES = {"cycling", "gravel_cycling", "road_biking", "mountain_biking", "virtual_ride"}
FIT_MAGIC = b".FIT"

# Garmin rate-limits by IP and answers 429 when pushed. A whole history is hundreds of
# requests, so pace them. Downloads are resumable — anything already on disk is skipped —
# so a 429 costs time, never progress.
PAGE_SIZE = 100
DOWNLOAD_PAUSE_S = 0.7


def _prompt_mfa() -> str:
    return input("Garmin MFA code: ").strip()


def _tokens_dir() -> Path:
    TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_DIR.chmod(0o700)
    return TOKEN_DIR


def _login_with_stored_tokens(store: Path) -> Garmin | None:
    """Try the saved tokens. Returns None when a fresh login is needed.

    `login()` reports a required re-authentication by *returning* a truthy first element
    rather than raising, so the return value has to be checked or an unauthenticated
    client sails on and fails later with something unhelpful.
    """
    if not any(store.iterdir()):
        return None

    client = Garmin(prompt_mfa=_prompt_mfa)
    try:
        needs_mfa, _ = client.login(tokenstore=str(store))
    except Exception as exc:
        print(f"Stored Garmin tokens unusable ({type(exc).__name__}), logging in again.")
        return None
    return None if needs_mfa else client


def connect(email: str | None = None, password: str | None = None) -> Garmin:
    """Return a logged-in client, reusing stored tokens when they are still good."""
    store = _tokens_dir()

    client = _login_with_stored_tokens(store)
    if client is not None:
        return client

    email = email or os.environ.get("GARMIN_EMAIL") or input("Garmin email: ").strip()
    password = password or os.environ.get("GARMIN_PASSWORD") or getpass("Garmin password: ")

    client = Garmin(email=email, password=password, prompt_mfa=_prompt_mfa)
    client.login()
    client.client.dump(str(store))
    for token in store.iterdir():
        if token.is_file():
            token.chmod(0o600)
    return client


def _fit_bytes(payload: bytes) -> bytes | None:
    """Garmin hands back the original as a zip; older ones come through bare."""
    if payload[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            for member in archive.namelist():
                if member.lower().endswith(".fit"):
                    return archive.read(member)
        return None
    return payload if payload[8:12] == FIT_MAGIC else None


class GarminConnect:
    """Downloads original FIT files for recent rides."""

    name = "garmin"

    def __init__(self, client: Garmin | None = None, cycling_only: bool = True):
        self.client = client or connect()
        self.cycling_only = cycling_only

    def _list_activities(self, limit: int | None) -> list[dict]:
        """Page through the activity list. `limit` of None means the whole history."""
        collected: list[dict] = []
        start = 0
        while limit is None or len(collected) < limit:
            want = PAGE_SIZE if limit is None else min(PAGE_SIZE, limit - len(collected))
            page = self.client.get_activities(start, want) or []
            if not page:
                break
            collected.extend(page)
            start += len(page)
            if len(page) < want:
                break
        return collected

    def fetch(self, dest: Path, limit: int | None = 50) -> list[Path]:
        dest.mkdir(parents=True, exist_ok=True)
        activities = self._list_activities(limit)
        rides = [
            a
            for a in activities
            if not self.cycling_only
            or (a.get("activityType") or {}).get("typeKey", "") in CYCLING_TYPES
        ]
        print(f"{len(activities)} activity(s) listed, {len(rides)} ride(s) to consider.")
        written: list[Path] = []

        for index, activity in enumerate(rides, 1):
            activity_id = str(activity.get("activityId"))
            target = dest / f"garmin-{activity_id}.fit"
            if target.exists():
                continue

            try:
                payload = self.client.download_activity(
                    activity_id, dl_fmt=Garmin.ActivityDownloadFormat.ORIGINAL
                )
            except Exception as exc:
                # A timeout on one ride should not discard the rest of the run.
                print(f"  could not download activity {activity_id}: {type(exc).__name__}")
                continue

            fit = _fit_bytes(payload)
            if fit is None:
                continue
            target.write_bytes(fit)
            written.append(target)

            if len(written) % 25 == 0:
                print(f"  {len(written)} downloaded ({index}/{len(rides)} considered)")
            time.sleep(DOWNLOAD_PAUSE_S)

        return written

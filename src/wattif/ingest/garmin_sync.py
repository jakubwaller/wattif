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
import zipfile
from getpass import getpass
from pathlib import Path

from garminconnect import Garmin

TOKEN_DIR = Path.home() / ".wattif" / "garmin"
CYCLING_TYPES = {"cycling", "gravel_cycling", "road_biking", "mountain_biking", "virtual_ride"}
FIT_MAGIC = b".FIT"


def _prompt_mfa() -> str:
    return input("Garmin MFA code: ").strip()


def _tokens_dir() -> Path:
    TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_DIR.chmod(0o700)
    return TOKEN_DIR


def connect(email: str | None = None, password: str | None = None) -> Garmin:
    """Return a logged-in client, reusing stored tokens when they are still good."""
    store = _tokens_dir()
    client = Garmin(prompt_mfa=_prompt_mfa)

    try:
        client.login(tokenstore=str(store))
        return client
    except Exception:
        pass  # No usable tokens: fall through to a fresh login.

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

    def fetch(self, dest: Path, limit: int | None = 50) -> list[Path]:
        dest.mkdir(parents=True, exist_ok=True)
        activities = self.client.get_activities(0, limit or 50) or []
        written: list[Path] = []

        for activity in activities:
            type_key = (activity.get("activityType") or {}).get("typeKey", "")
            if self.cycling_only and type_key not in CYCLING_TYPES:
                continue

            activity_id = str(activity.get("activityId"))
            target = dest / f"garmin-{activity_id}.fit"
            if target.exists():
                continue

            payload = self.client.download_activity(
                activity_id, dl_fmt=Garmin.ActivityDownloadFormat.ORIGINAL
            )
            fit = _fit_bytes(payload)
            if fit is None:
                continue
            target.write_bytes(fit)
            written.append(target)

        return written

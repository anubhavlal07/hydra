from __future__ import annotations

import dataclasses
import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path

from .config import AccountConfig, settings

log = logging.getLogger(__name__)

LABEL_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,22}[a-z0-9])?$")

MIN_ACCOUNTS = 1
MIN_ENABLED = 1


class AccountStoreError(RuntimeError):
    pass


CALLSIGNS = (
    "alpha",
    "bravo",
    "charlie",
    "delta",
    "echo",
    "foxtrot",
    "golf",
    "hotel",
    "india",
    "juliett",
    "kilo",
    "lima",
    "mike",
    "november",
    "oscar",
    "papa",
    "quebec",
    "romeo",
    "sierra",
    "tango",
    "uniform",
    "victor",
    "whiskey",
    "xray",
    "yankee",
    "zulu",
)


def derive_label(taken: set[str]) -> str:
    for callsign in CALLSIGNS:
        if callsign not in taken:
            return callsign
    for suffix in range(2, 100):
        for callsign in CALLSIGNS:
            candidate = f"{callsign}{suffix}"
            if candidate not in taken:
                return candidate
    raise AccountStoreError("could not find a free label")


def email_label_stem(username: str) -> str:
    local = username.partition("@")[0].lower()
    stem = re.sub(r"[^a-z0-9]", "", local)
    stem = re.sub(r"\d+$", "", stem) or stem or "account"
    return stem[:5] or "acct"


def is_email_label(label: str, username: str) -> bool:
    stem = email_label_stem(username)
    return label == stem or bool(re.fullmatch(rf"{re.escape(stem)}\d{{1,2}}", label))


def assign_callsigns(accounts: list[AccountConfig]) -> list[AccountConfig]:
    taken = {a.label for a in accounts if not is_email_label(a.label, a.username)}
    relabelled = []
    for account in accounts:
        if is_email_label(account.label, account.username):
            label = derive_label(taken)
            taken.add(label)
            account = dataclasses.replace(account, label=label)
        relabelled.append(account)
    return relabelled


class AccountStore:
    def __init__(self, seed: list[AccountConfig]) -> None:
        self._seed = list(seed)
        self._accounts: list[AccountConfig] = list(seed)
        self._loaded_from_file = False
        self.last_error = ""

    @property
    def configured(self) -> bool:
        return bool(settings.accounts_file)

    @property
    def accounts(self) -> list[AccountConfig]:
        return list(self._accounts)

    def get(self, label: str) -> AccountConfig | None:
        return next((a for a in self._accounts if a.label == label), None)

    @property
    def _path(self) -> Path | None:
        return Path(settings.accounts_file) if settings.accounts_file else None

    def load(self) -> list[AccountConfig]:
        path = self._path
        if path is None:
            return self.accounts

        if not path.exists():
            log.info("no account store at %s yet; seeding from HYDRA_ADGUARD_ACCOUNTS", path)
            self._write()
            self._loaded_from_file = True
            return self.accounts

        try:
            payload = json.loads(path.read_text())
            rows = payload.get("accounts") or []
            parsed = [
                AccountConfig(
                    label=str(row["label"]),
                    username=str(row["username"]),
                    password=str(row["password"]),
                    device_id=str(row.get("device_id") or ""),
                    doh_url=str(row.get("doh_url") or ""),
                    enabled=bool(row.get("enabled", True)),
                )
                for row in rows
                if row.get("label") and row.get("username")
            ]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            log.error(
                "account store at %s is unreadable (%s); falling back to "
                "HYDRA_ADGUARD_ACCOUNTS, and leaving the file alone",
                path,
                self.last_error,
            )
            return self.accounts

        if not parsed:
            log.error("account store at %s is empty; falling back to the seed list", path)
            self.last_error = "the stored account list was empty"
            return self.accounts

        relabelled = assign_callsigns(parsed)
        self._accounts = relabelled
        self._loaded_from_file = True
        self.last_error = ""
        if relabelled != parsed:
            renamed = [
                f"{a.label} -> {b.label}" for a, b in zip(parsed, relabelled, strict=True) if a != b
            ]
            log.info("account store: renamed %s", ", ".join(renamed))
            try:
                self._write()
            except OSError as exc:
                log.error("could not save the renamed account labels: %s", exc)
        log.info("account store: %s account(s) from %s", len(parsed), path)
        return self.accounts

    def _write(self) -> None:
        path = self._path
        if path is None:
            return

        payload = {
            "written_at": time.time(),
            "accounts": [
                {
                    "label": a.label,
                    "username": a.username,
                    "password": a.password,
                    "device_id": a.device_id,
                    "doh_url": a.doh_url,
                    "enabled": a.enabled,
                }
                for a in self._accounts
            ],
        }

        path.parent.mkdir(parents=True, exist_ok=True)
        handle_fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=".accounts-", suffix=".tmp")
        temp = Path(temp_name)
        try:
            with os.fdopen(handle_fd, "w") as handle:
                json.dump(payload, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp, 0o600)
            temp.replace(path)
        except OSError:
            temp.unlink(missing_ok=True)
            raise

    def add(self, username: str, password: str, label: str | None = None) -> AccountConfig:
        if not self.configured:
            raise AccountStoreError(
                "no account store is configured, so accounts can only be changed in "
                "HYDRA_ADGUARD_ACCOUNTS on the host"
            )

        username = username.strip().lower()
        if not username or "@" not in username:
            raise AccountStoreError("that does not look like an email address")
        if any(a.username.lower() == username for a in self._accounts):
            raise AccountStoreError(f"{username} is already in the pool")
        if not password:
            raise AccountStoreError("a password is required")

        taken = {a.label for a in self._accounts}
        if label:
            label = label.strip().lower()
            if not LABEL_PATTERN.match(label):
                raise AccountStoreError(
                    "a label must be lowercase letters, digits or dashes, 1-24 characters"
                )
            if label in taken:
                raise AccountStoreError(f"the label {label!r} is already used")
        else:
            label = derive_label(taken)

        account = AccountConfig(label=label, username=username, password=password)
        previous = list(self._accounts)
        self._accounts.append(account)
        try:
            self._write()
        except OSError as exc:
            self._accounts = previous
            raise AccountStoreError(f"could not save the account list: {exc}") from exc
        return account

    def remove(self, label: str) -> AccountConfig:
        if not self.configured:
            raise AccountStoreError(
                "no account store is configured, so accounts can only be changed in "
                "HYDRA_ADGUARD_ACCOUNTS on the host"
            )

        account = self.get(label)
        if account is None:
            raise AccountStoreError(f"no account is labelled {label!r}")
        if len(self._accounts) <= MIN_ACCOUNTS:
            raise AccountStoreError(
                "this is the only account left, and removing it would leave the resolver with "
                "no account to forward to. Add another one first."
            )
        if account.enabled and self.enabled_count <= MIN_ENABLED:
            raise AccountStoreError(
                "this is the only account still taking traffic, and removing it would leave "
                "the resolver with nothing to forward to. Turn another one on first."
            )

        previous = list(self._accounts)
        self._accounts = [a for a in self._accounts if a.label != label]
        try:
            self._write()
        except OSError as exc:
            self._accounts = previous
            raise AccountStoreError(f"could not save the account list: {exc}") from exc
        return account

    def set_enabled(self, label: str, enabled: bool) -> AccountConfig:
        if not self.configured:
            raise AccountStoreError(
                "no account store is configured, so accounts can only be changed in "
                "HYDRA_ADGUARD_ACCOUNTS on the host"
            )

        account = self.get(label)
        if account is None:
            raise AccountStoreError(f"no account is labelled {label!r}")
        if account.enabled == enabled:
            return account
        if not enabled and self.enabled_count <= MIN_ENABLED:
            raise AccountStoreError(
                "this is the only account still taking traffic, and turning it off would "
                "leave the resolver with nothing to forward to. Enable another one first."
            )

        updated = dataclasses.replace(account, enabled=enabled)
        previous = list(self._accounts)
        self._accounts = [updated if a.label == label else a for a in self._accounts]
        try:
            self._write()
        except OSError as exc:
            self._accounts = previous
            raise AccountStoreError(f"could not save the account list: {exc}") from exc
        return updated

    @property
    def enabled_count(self) -> int:
        return sum(1 for a in self._accounts if a.enabled)

    def snapshot(self) -> dict:
        return {
            "configured": self.configured,
            "editable": self.configured and not self.last_error,
            "count": len(self._accounts),
            "enabled_count": self.enabled_count,
            "from_file": self._loaded_from_file,
            "min_accounts": MIN_ACCOUNTS,
            "min_enabled": MIN_ENABLED,
            "last_error": self.last_error,
        }

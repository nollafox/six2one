from __future__ import annotations

import getpass
from dataclasses import dataclass
from typing import Any

from six2one.e621 import E621Client
from six2one.e621.errors import E621APIError, E621AuthError, E621PermissionError

from six2one._commands.config import SixTwoOneConfig
from six2one._commands.errors import CommandError

from .storage import AuthStore, StoredAuth


@dataclass(frozen=True, slots=True)
class AuthAccount:
    username: str
    user_id: int | None = None
    api_access: str = "ok"


@dataclass(frozen=True, slots=True)
class AuthResult:
    status: str
    storage: str
    path: str
    account: AuthAccount | None = None
    reason: str | None = None


@dataclass(slots=True)
class AuthCommand:
    config: SixTwoOneConfig
    username: str | None = None
    api_token: str | None = None
    test: bool = False
    remove: bool = False
    yes: bool = False
    e621: Any | None = None

    @classmethod
    def from_args(cls, args: Any) -> "AuthCommand":
        return cls(
            config=SixTwoOneConfig.from_args(args),
            username=getattr(args, "username", None),
            api_token=getattr(args, "api_token", None),
            test=bool(getattr(args, "test", False)),
            remove=bool(getattr(args, "remove", False)),
            yes=bool(getattr(args, "yes", False)),
        )

    def run(self) -> int:
        store = AuthStore(self.config)
        try:
            if self.remove:
                result = self._remove(store)
            elif self.test:
                result = self._test(store)
            else:
                result = self._configure(store)
        except CommandError as error:
            print(_format_auth_error(str(error)))
            return 1
        print(_format_auth_result(result))
        return 0

    def _configure(self, store: AuthStore) -> AuthResult:
        existing = store.load()
        username = self.username
        api_token = self.api_token

        if existing and username is None and api_token is None:
            action = self._existing_action(existing)
            if action == "keep":
                account = self._verify(existing.username, existing.api_token)
                return AuthResult("kept", existing.source, str(existing.path), account)
            if action == "remove":
                return self._remove(store)
            if action == "test":
                return self._test(store)

        if username is None:
            username = input("Username: ")
        if api_token is None:
            api_token = getpass.getpass("API token: ")

        account = self._verify(username, api_token)
        saved = store.save(username, api_token)
        return AuthResult("authenticated", saved.source, str(saved.path), account)

    def _test(self, store: AuthStore) -> AuthResult:
        credentials = store.load()
        if credentials is None:
            raise CommandError("No stored e621 credentials were found.")
        account = self._verify(credentials.username, credentials.api_token)
        return AuthResult("tested", credentials.source, str(credentials.path), account)

    def _remove(self, store: AuthStore) -> AuthResult:
        existing = store.load()
        if existing is None:
            raise CommandError("No stored e621 credentials were found.")
        if not self.yes:
            answer = input("Remove stored e621 credentials? [y/N] ").strip().lower()
            if answer not in {"y", "yes"}:
                return AuthResult("unchanged", existing.source, str(existing.path))
        path = store.delete()
        return AuthResult("removed", "auth.toml", str(path))

    def _existing_action(self, existing: StoredAuth) -> str:
        print(_format_existing(existing))
        answer = input("Choice [1]: ").strip()
        if answer in {"", "1"}:
            return "keep"
        if answer == "2":
            return "replace"
        if answer == "3":
            return "remove"
        if answer == "4":
            return "test"
        raise CommandError("Unsupported auth choice.")

    def _verify(self, username: str, api_token: str) -> AuthAccount:
        client = self.e621 or E621Client(auth=(username, api_token), user_agent=self.config.user_agent)
        try:
            user = client.me()
        except (E621AuthError, E621PermissionError) as error:
            raise CommandError("e621 rejected the username or API token.") from error
        except E621APIError as error:
            raise CommandError(f"Could not verify credentials with e621: {error}") from error
        user_id = getattr(user, "id", None)
        return AuthAccount(username=getattr(user, "name", username) or username, user_id=int(user_id) if user_id is not None else None)


def _format_existing(credentials: StoredAuth) -> str:
    return "\n".join(
        (
            "six2one auth",
            "",
            "Existing credentials found.",
            "",
            "Account",
            f"  Username        {credentials.username}",
            "",
            "What would you like to do?",
            "",
            "  1. Keep existing credentials",
            "  2. Replace credentials",
            "  3. Remove credentials",
            "  4. Test credentials",
            "",
        )
    )


def _format_auth_result(result: AuthResult) -> str:
    if result.status == "removed":
        return "\n".join(("Credentials removed.", "", "Unauthenticated fetches will still work for public queries."))
    if result.status == "unchanged":
        return "Nothing was changed."

    lines = ["six2one auth", ""]
    if result.status == "kept":
        lines.append("Existing credentials kept.")
    elif result.status == "tested":
        lines.append("Testing credentials...")
    else:
        lines.extend(["Verifying credentials...", "", "Authenticated."])
    if result.account:
        lines.extend(
            [
                "",
                "Account",
                f"  Username        {result.account.username}",
                f"  User ID         {result.account.user_id if result.account.user_id is not None else 'unknown'}",
                f"  API access      {result.account.api_access}",
            ]
        )
    lines.extend(["", "Config updated" if result.status == "authenticated" else "Storage", f"  Auth storage    {result.storage}", "  API host        https://e621.net"])
    return "\n".join(lines)


def _format_auth_error(reason: str) -> str:
    return "\n".join(
        (
            "six2one auth",
            "",
            "Authentication failed.",
            "",
            "Reason",
            f"  {reason}",
            "",
            "Nothing was changed.",
        )
    )

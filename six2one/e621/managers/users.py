"""User managers."""

from .base import GetSearchManager
from . import endpoints
from ..models import User


class UsersManager(GetSearchManager[User]):
    resource_name = "users"
    model_type = User
    index_endpoint = endpoints.USERS_INDEX
    show_endpoint = endpoints.USER_SHOW
    index_response_key = "users"
    show_response_key = "user"

    def me(self) -> User:
        """Fetch the authenticated viewer."""

        payload = self.client.transport.get_json(endpoints.USER_ME)
        return self._model(self._extract_one(payload, "user"))

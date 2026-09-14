"""Authentication backend.

V3 section 9 specifies the login route as ID/password. Accounts are keyed by the
institutional ID, which lives in ``username``, so the lookup is an exact match on
that column. Signing in with an email address is deliberately not accepted: two
different identifiers reaching the same account would make the duplicate-account
rules of section 8 harder to reason about.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class InstitutionalIdBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        UserModel = get_user_model()
        if username is None:
            username = kwargs.get(UserModel.USERNAME_FIELD)
        if username is None or password is None:
            return None

        institutional_id = username.strip()
        try:
            user = UserModel._default_manager.get(username=institutional_id)
        except UserModel.DoesNotExist:
            # Run the hasher once anyway so a missing account and a wrong password
            # take a comparable amount of time.
            UserModel().set_password(password)
            return None

        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None

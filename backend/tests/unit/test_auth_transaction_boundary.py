from __future__ import annotations

import inspect

from apps.users.views import LoginView, SelectGymView


def test_login_dispatch_is_non_atomic_and_post_opens_explicit_atomic() -> None:
    assert "default" in getattr(LoginView.dispatch, "_non_atomic_requests", set())
    source = inspect.getsource(LoginView.post)
    assert "with transaction.atomic():" in source
    assert source.index("with transaction.atomic():") < source.index("_complete_login")


def test_select_gym_jwt_path_opens_explicit_atomic() -> None:
    assert "default" in getattr(SelectGymView.dispatch, "_non_atomic_requests", set())
    source = inspect.getsource(SelectGymView.post)
    jwt_path = source.split("if ticket:", 1)[1]
    assert "with transaction.atomic():" in jwt_path
    assert jwt_path.index("with transaction.atomic():") < jwt_path.index(
        "StaffJWTAuthentication().authenticate"
    )

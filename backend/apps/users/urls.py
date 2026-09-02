from django.urls import path

from .views import (
    GymsView,
    LoginView,
    LogoutAllView,
    LogoutView,
    MeView,
    ProviderNeutralUnavailableView,
    RefreshView,
    SelectGymView,
)

urlpatterns = [
    path("login/", LoginView.as_view(), name="login"),
    path("refresh/", RefreshView.as_view(), name="refresh"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("logout-all/", LogoutAllView.as_view(), name="logout-all"),
    path("me/", MeView.as_view(), name="me"),
    path("gyms/", GymsView.as_view(), name="gyms"),
    path("select-gym/", SelectGymView.as_view(), name="select-gym"),
    path("password/forgot/", ProviderNeutralUnavailableView.as_view(), name="password-forgot"),
    path("password/reset/", ProviderNeutralUnavailableView.as_view(), name="password-reset"),
    path("mfa/challenge/", ProviderNeutralUnavailableView.as_view(), name="mfa-challenge"),
]

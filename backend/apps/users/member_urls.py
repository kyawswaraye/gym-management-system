from django.urls import path

from .member_views import (
    MemberGoogleLoginView,
    MemberLoginView,
    MemberLogoutView,
    MemberRefreshView,
)

urlpatterns = [
    path("login/", MemberLoginView.as_view(), name="member-auth-login"),
    path("google/", MemberGoogleLoginView.as_view(), name="member-auth-google"),
    path("refresh/", MemberRefreshView.as_view(), name="member-auth-refresh"),
    path("logout/", MemberLogoutView.as_view(), name="member-auth-logout"),
]

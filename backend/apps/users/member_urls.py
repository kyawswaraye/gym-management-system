from django.urls import path

from .member_views import MemberLoginView, MemberLogoutView, MemberRefreshView

urlpatterns = [
    path("login/", MemberLoginView.as_view(), name="member-auth-login"),
    path("refresh/", MemberRefreshView.as_view(), name="member-auth-refresh"),
    path("logout/", MemberLogoutView.as_view(), name="member-auth-logout"),
]

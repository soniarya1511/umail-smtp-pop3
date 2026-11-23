from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("login/", views.login_page, name="login"),
    path("logout/", views.logout_page, name="logout"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("send_email/", views.send_email, name="send_email"),
    path("delete_email/<int:id>/", views.delete_email, name="delete_email"),
]

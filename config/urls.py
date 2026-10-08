from django.urls import include, path

urlpatterns = [path("", include("attendance.urls"))]

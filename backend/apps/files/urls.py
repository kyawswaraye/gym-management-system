from django.urls import path

from apps.files.views import (
    CompleteUploadView,
    DeleteUploadView,
    DownloadUrlView,
    UploadIntentView,
)

urlpatterns = [
    path("files/upload-intents/", UploadIntentView.as_view(), name="file-upload-intent"),
    path("files/<uuid:id>/complete/", CompleteUploadView.as_view(), name="file-complete"),
    path("files/<uuid:id>/download-url/", DownloadUrlView.as_view(), name="file-download-url"),
    path("files/<uuid:id>/", DeleteUploadView.as_view(), name="file-delete"),
]

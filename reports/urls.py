from django.urls import path
from .views import UploadExcelFileView, ListUserReportsView, DeleteUserFileView, UploadVideoUrlView

urlpatterns = [
    path('upload/', UploadExcelFileView.as_view(), name='upload-excel'),
    path('my-files/', ListUserReportsView.as_view(), name='list-user-files'),
    path('delete/', DeleteUserFileView.as_view(), name='delete-user-file'),
    path('video-url/', UploadVideoUrlView.as_view(), name='upload-video-url'),
]

from django.urls import path
from .views import UploadExcelFileView, ListUserFilesView, DeleteUserFileView

urlpatterns = [
    path('upload/', UploadExcelFileView.as_view(), name='upload-excel'),
    path('my-files/', ListUserFilesView.as_view(), name='list-user-files'),
    path('delete/', DeleteUserFileView.as_view(), name='delete-user-file'),
]

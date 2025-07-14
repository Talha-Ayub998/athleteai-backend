from django.urls import path
from .views import UploadExcelFileView, ListUserReportsView, DeleteUserFileView

urlpatterns = [
    path('upload/', UploadExcelFileView.as_view(), name='upload-excel'),
    path('my-files/', ListUserReportsView.as_view(), name='list-user-files'),
    path('delete/', DeleteUserFileView.as_view(), name='delete-user-file'),
]

from django.urls import path
from .views import UploadExcelFileView, ListUserReportsView, \
                    DeleteUserFileView, UploadVideoUrlView, \
                    ListUserVideoUrlsView, ReportKPIsView, UploadVideoFileView, DeleteUserVideoView
from .annotation_views import (
    AnnotationEventCreateView,
    AnnotationEventDetailView,
    AnnotationFinalizeView,
    AnnotationMatchResultDetailView,
    AnnotationMatchResultListCreateView,
    AnnotationSessionDownloadXlsxView,
    AnnotationSessionDetailView,
    AnnotationSessionListCreateView,
)

urlpatterns = [
    path('upload/', UploadExcelFileView.as_view(), name='upload-excel'),
    path('my-files/', ListUserReportsView.as_view(), name='list-user-files'),
    path('delete/', DeleteUserFileView.as_view(), name='delete-user-file'),
    path('video-url/', UploadVideoUrlView.as_view(), name='upload-video-url'),
    path('video-upload/', UploadVideoFileView.as_view(), name='upload-video-file'),
    path('my-video-urls/', ListUserVideoUrlsView.as_view(), name='list-video-urls'),
    path('my-video-urls/<int:video_id>/', DeleteUserVideoView.as_view(), name='delete-video-url'),
    path('kpis/', ReportKPIsView.as_view(), name='report-kpis"'),
    path("annotation-sessions/", AnnotationSessionListCreateView.as_view(), name="annotation-session-list-create"),
    path("annotation-sessions/<int:session_id>/", AnnotationSessionDetailView.as_view(), name="annotation-session-detail"),
    path("annotation-sessions/<int:session_id>/events/", AnnotationEventCreateView.as_view(), name="annotation-event-create"),
    path(
        "annotation-sessions/<int:session_id>/events/<int:event_id>/",
        AnnotationEventDetailView.as_view(),
        name="annotation-event-detail",
    ),
    path(
        "annotation-sessions/<int:session_id>/match-results/",
        AnnotationMatchResultListCreateView.as_view(),
        name="annotation-match-result-list-create",
    ),
    path(
        "annotation-sessions/<int:session_id>/match-results/<int:result_id>/",
        AnnotationMatchResultDetailView.as_view(),
        name="annotation-match-result-detail",
    ),
    path(
        "annotation-sessions/<int:session_id>/finalize/",
        AnnotationFinalizeView.as_view(),
        name="annotation-finalize",
    ),
    path(
        "annotation-sessions/<int:session_id>/download-xlsx/",
        AnnotationSessionDownloadXlsxView.as_view(),
        name="annotation-session-download-xlsx",
    ),
]

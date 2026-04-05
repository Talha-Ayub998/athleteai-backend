from django.urls import path
from .views import UploadExcelFileView, ListUserReportsView, \
                    DeleteUserFileView, UploadVideoUrlView, \
                    ListUserVideoUrlsView, ReportKPIsView, UploadVideoFileView, DeleteUserVideoView, \
                    StartMultipartVideoUploadView, MultipartVideoUploadPartUrlView, \
                    CompleteMultipartVideoUploadView, AbortMultipartVideoUploadView, ListMultipartPartsView
from .multi_session_views import (
    MultiVideoSessionListCreateView,
    MultiVideoSessionDetailView,
    MultiVideoSessionStartVideoView,
    MultiVideoSessionCompleteVideoView,
    MultiVideoSessionRemoveVideoView,
    MultiVideoSessionFinalizeView,
    MultiVideoSessionDownloadXlsxView,
)
from .annotation_views import (
    AnnotationEventCreateView,
    AnnotationEventDetailView,
    AnnotationFinalizeView,
    AnnotationLatestSessionByVideoView,
    AnnotationMatchResultDetailView,
    AnnotationMatchResultListCreateView,
    AnnotationSessionDownloadXlsxView,
    AnnotationSessionReopenView,
    AnnotationSessionDetailView,
    AnnotationSessionListCreateView,
)

urlpatterns = [
    path('upload/', UploadExcelFileView.as_view(), name='upload-excel'),
    path('my-files/', ListUserReportsView.as_view(), name='list-user-files'),
    path('delete/', DeleteUserFileView.as_view(), name='delete-user-file'),
    path('video-url/', UploadVideoUrlView.as_view(), name='upload-video-url'),
    path('video-upload/', UploadVideoFileView.as_view(), name='upload-video-file'),
    path('video-upload/multipart/start/', StartMultipartVideoUploadView.as_view(), name='video-upload-multipart-start'),
    path('video-upload/multipart/part-url/', MultipartVideoUploadPartUrlView.as_view(), name='video-upload-multipart-part-url'),
    path('video-upload/multipart/complete/', CompleteMultipartVideoUploadView.as_view(), name='video-upload-multipart-complete'),
    path('video-upload/multipart/abort/', AbortMultipartVideoUploadView.as_view(), name='video-upload-multipart-abort'),
    path('video-upload/multipart/list-parts/', ListMultipartPartsView.as_view(), name='video-upload-multipart-list-parts'),
    path('my-video-urls/', ListUserVideoUrlsView.as_view(), name='list-video-urls'),
    path('my-video-urls/<int:video_id>/', DeleteUserVideoView.as_view(), name='delete-video-url'),
    path('kpis/', ReportKPIsView.as_view(), name='report-kpis"'),

    # Multi-video session
    path('multi-session/', MultiVideoSessionListCreateView.as_view(), name='multi-session-list-create'),
    path('multi-session/<int:session_id>/', MultiVideoSessionDetailView.as_view(), name='multi-session-detail'),
    path('multi-session/<int:session_id>/start-video/<int:item_id>/', MultiVideoSessionStartVideoView.as_view(), name='multi-session-start-video'),
    path('multi-session/<int:session_id>/complete-video/<int:item_id>/', MultiVideoSessionCompleteVideoView.as_view(), name='multi-session-complete-video'),
    path('multi-session/<int:session_id>/remove-video/<int:item_id>/', MultiVideoSessionRemoveVideoView.as_view(), name='multi-session-remove-video'),
    path('multi-session/<int:session_id>/finalize/', MultiVideoSessionFinalizeView.as_view(), name='multi-session-finalize'),
    path('multi-session/<int:session_id>/download-xlsx/', MultiVideoSessionDownloadXlsxView.as_view(), name='multi-session-download-xlsx'),
    path("annotation-sessions/", AnnotationSessionListCreateView.as_view(), name="annotation-session-list-create"),
    path(
        "annotation-sessions/latest-by-video/",
        AnnotationLatestSessionByVideoView.as_view(),
        name="annotation-latest-session-by-video",
    ),
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
    path(
        "annotation-sessions/<int:session_id>/reopen/",
        AnnotationSessionReopenView.as_view(),
        name="annotation-session-reopen",
    ),
]

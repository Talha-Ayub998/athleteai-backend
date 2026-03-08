from django.contrib import admin
from .models import (
    AnnotationEvent,
    AnnotationMatchResult,
    AnnotationSession,
    AthleteReport,
    VideoUrl,
)

@admin.register(AthleteReport)
class AthleteReportAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "filename", "file_size_mb", "uploaded_at")
    list_filter = ("uploaded_at", "user")
    search_fields = ("filename", "user__email")
    ordering = ("-uploaded_at",)
    readonly_fields = ("pdf_data",)

@admin.register(VideoUrl)
class VideoUrlAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "url", "s3_key", "file_name", "file_hash", "created_at")
    list_filter = ("created_at", "user")
    search_fields = ("url", "s3_key", "file_name", "file_hash", "user__email")
    ordering = ("-created_at",)


@admin.register(AnnotationSession)
class AnnotationSessionAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "status", "video_url", "generated_report", "created_at", "finalized_at")
    list_filter = ("status", "created_at")
    search_fields = ("user__email", "title", "video_url")
    ordering = ("-created_at",)


@admin.register(AnnotationEvent)
class AnnotationEventAdmin(admin.ModelAdmin):
    list_display = ("id", "session", "match_number", "timestamp_seconds", "player", "event_type", "move_name", "outcome")
    list_filter = ("event_type", "player", "match_number", "created_at")
    search_fields = ("session__user__email", "move_name", "note")
    ordering = ("session_id", "match_number", "timestamp_seconds")


@admin.register(AnnotationMatchResult)
class AnnotationMatchResultAdmin(admin.ModelAdmin):
    list_display = ("id", "session", "match_number", "result", "match_type", "referee_decision", "disqualified")
    list_filter = ("result", "match_type", "referee_decision", "disqualified", "created_at")
    search_fields = ("session__user__email", "opponent")
    ordering = ("session_id", "match_number")

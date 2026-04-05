from django.contrib import admin
from .models import (
    AnnotationEvent,
    AnnotationMatchResult,
    AnnotationSession,
    AthleteReport,
    MultiVideoSession,
    MultiVideoSessionItem,
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
    list_display = ("id", "user", "status", "video_id_value", "video", "video_url", "generated_report", "created_at", "finalized_at")
    list_filter = ("status", "created_at")
    search_fields = ("user__email", "title", "video_url", "video__file_name", "video__url")
    ordering = ("-created_at",)

    def video_id_value(self, obj):
        return obj.video_id

    video_id_value.short_description = "Video ID"


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


class MultiVideoSessionItemInline(admin.TabularInline):
    model = MultiVideoSessionItem
    extra = 0
    readonly_fields = ("annotation_session", "status", "is_removed", "created_at", "updated_at")
    fields = ("video", "annotation_session", "status", "is_removed", "created_at")


@admin.register(MultiVideoSession)
class MultiVideoSessionAdmin(admin.ModelAdmin):
    list_display = ("id", "created_by", "title", "status", "total_videos", "completed_videos", "finalized_at", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("created_by__email", "title")
    ordering = ("-created_at",)
    readonly_fields = ("finalized_at", "generated_report", "created_at", "updated_at")
    inlines = [MultiVideoSessionItemInline]

    def total_videos(self, obj):
        return obj.items.filter(is_removed=False).count()
    total_videos.short_description = "Total Videos"

    def completed_videos(self, obj):
        return obj.items.filter(is_removed=False, status=MultiVideoSessionItem.STATUS_COMPLETED).count()
    completed_videos.short_description = "Completed"


@admin.register(MultiVideoSessionItem)
class MultiVideoSessionItemAdmin(admin.ModelAdmin):
    list_display = ("id", "session", "video", "annotation_session", "status", "is_removed", "created_at")
    list_filter = ("status", "is_removed", "created_at")
    search_fields = ("session__created_by__email", "video__file_name")
    ordering = ("-created_at",)

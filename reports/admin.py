from django.contrib import admin
from .models import AthleteReport, VideoUrl

@admin.register(AthleteReport)
class AthleteReportAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "filename", "file_size_mb", "uploaded_at")
    list_filter = ("uploaded_at", "user")
    search_fields = ("filename", "user__email")
    ordering = ("-uploaded_at",)
    readonly_fields = ("pdf_data",)

@admin.register(VideoUrl)
class VideoUrlAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "url", "created_at")
    list_filter = ("created_at", "user")
    search_fields = ("url", "user__email")
    ordering = ("-created_at",)

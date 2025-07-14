from django.contrib import admin
from .models import AthleteReport

@admin.register(AthleteReport)
class AthleteReportAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "filename", "file_size_mb", "uploaded_at")
    list_filter = ("uploaded_at", "user")
    search_fields = ("filename", "user__email")
    ordering = ("-uploaded_at",)
    readonly_fields = ("pdf_data",)

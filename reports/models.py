from django.db import models
from users.models import CustomUser

class AthleteReport(models.Model):
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='athlete_reports')
    filename = models.CharField(max_length=255)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    pdf_data = models.JSONField()
    file_size_mb = models.FloatField(null=True, blank=True)
    file_hash = models.CharField(max_length=64, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        unique_together = ("user", "file_hash")  # prevent duplicates per user

    def __str__(self):
        return f"{self.user.email} - {self.filename}"

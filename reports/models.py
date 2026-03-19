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
    s3_key = models.CharField(max_length=500, blank=True, null=True)
    class Meta:
        unique_together = ("user", "file_hash")  # prevent duplicates per user

    def __str__(self):
        return f"{self.user.email} - {self.filename}"

class VideoUrl(models.Model):
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name="video_urls")
    url = models.URLField()
    s3_key = models.CharField(max_length=500, blank=True, null=True, db_index=True)
    file_name = models.CharField(max_length=255, blank=True, null=True)
    content_type = models.CharField(max_length=100, blank=True, null=True)
    file_size_bytes = models.BigIntegerField(blank=True, null=True)
    file_hash = models.CharField(max_length=64, blank=True, null=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "url")
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.user.email} - {self.url}"


class AnnotationSession(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_COMPLETED = "completed"
    STATUS_CHOICES = (
        (STATUS_DRAFT, "Draft"),
        (STATUS_COMPLETED, "Completed"),
    )

    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name="annotation_sessions")
    title = models.CharField(max_length=255, blank=True, null=True)
    video = models.ForeignKey(
        VideoUrl,
        on_delete=models.SET_NULL,
        related_name="annotation_sessions",
        blank=True,
        null=True,
    )
    video_url = models.URLField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    finalized_at = models.DateTimeField(blank=True, null=True)
    generated_report = models.ForeignKey(
        AthleteReport,
        on_delete=models.SET_NULL,
        related_name="annotation_sessions",
        blank=True,
        null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.user.email} - Session {self.id} ({self.status})"


class AnnotationEvent(models.Model):
    PLAYER_ME = "me"
    PLAYER_OPPONENT = "opponent"
    PLAYER_CHOICES = (
        (PLAYER_ME, "Me"),
        (PLAYER_OPPONENT, "Opponent"),
    )

    EVENT_POSITION = "position"
    EVENT_TRANSITION = "transition"
    EVENT_SUBMISSION = "submission"
    EVENT_NOTE = "note"
    EVENT_TYPE_CHOICES = (
        (EVENT_POSITION, "Position"),
        (EVENT_TRANSITION, "Transition"),
        (EVENT_SUBMISSION, "Submission"),
        (EVENT_NOTE, "Note"),
    )

    OUTCOME_SUCCESS = "success"
    OUTCOME_FAILED = "failed"
    OUTCOME_CHOICES = (
        (OUTCOME_SUCCESS, "Success"),
        (OUTCOME_FAILED, "Failed"),
    )

    session = models.ForeignKey(AnnotationSession, on_delete=models.CASCADE, related_name="events")
    match_number = models.PositiveIntegerField()
    timestamp_seconds = models.FloatField()
    start_time_seconds = models.FloatField(blank=True, null=True)
    end_time_seconds = models.FloatField(blank=True, null=True)
    player = models.CharField(max_length=20, choices=PLAYER_CHOICES)
    event_type = models.CharField(max_length=20, choices=EVENT_TYPE_CHOICES)
    move_name = models.CharField(max_length=255, blank=True, null=True)
    outcome = models.CharField(max_length=20, choices=OUTCOME_CHOICES, blank=True, null=True)
    note = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("match_number", "timestamp_seconds", "id")

    def __str__(self):
        return f"Session {self.session_id} | M{self.match_number} | {self.event_type}"


class AnnotationMatchResult(models.Model):
    RESULT_WIN = "Win"
    RESULT_LOST = "Lost"
    RESULT_DRAW = "Draw"
    RESULT_CHOICES = (
        (RESULT_WIN, "Win"),
        (RESULT_LOST, "Lost"),
        (RESULT_DRAW, "Draw"),
    )

    session = models.ForeignKey(AnnotationSession, on_delete=models.CASCADE, related_name="match_results")
    match_number = models.PositiveIntegerField()
    result = models.CharField(max_length=20, choices=RESULT_CHOICES, default=RESULT_WIN)
    match_type = models.CharField(max_length=100, default="No-GI Points")
    referee_decision = models.BooleanField(default=False)
    disqualified = models.BooleanField(default=False)
    opponent = models.CharField(max_length=255, default="Unknown Opponent")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("session", "match_number")
        ordering = ("match_number",)

    def __str__(self):
        return f"Session {self.session_id} | Match-{self.match_number} | {self.result}"

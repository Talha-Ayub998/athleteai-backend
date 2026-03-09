from rest_framework import serializers
from urllib.parse import urlparse
from .models import (
    VideoUrl,
    AnnotationSession,
    AnnotationEvent,
    AnnotationMatchResult,
)
from utils.s3_service import S3Service

# Serializer to validate input
class VideoUrlSerializer(serializers.Serializer):
    video_url = serializers.URLField(required=True)


class VideoUploadSerializer(serializers.Serializer):
    video = serializers.FileField(required=True)


class VideoUrlReadSerializer(serializers.ModelSerializer):
    playback_url = serializers.SerializerMethodField()
    session_id = serializers.IntegerField(source="latest_session_id", read_only=True, allow_null=True)
    session_status = serializers.CharField(source="latest_session_status", read_only=True, allow_null=True)
    session_updated_at = serializers.DateTimeField(source="latest_session_updated_at", read_only=True, allow_null=True)

    class Meta:
        model = VideoUrl
        fields = (
            "id",
            "url",
            "s3_key",
            "file_name",
            "content_type",
            "file_size_bytes",
            "file_hash",
            "session_id",
            "session_status",
            "session_updated_at",
            "playback_url",
            "created_at",
        )

    def get_playback_url(self, obj):
        raw_url = obj.url or ""
        if obj.s3_key:
            try:
                s3 = S3Service()
                return s3.generate_presigned_get_url(obj.s3_key) or raw_url
            except Exception:
                return raw_url

        parsed = urlparse(raw_url)
        if not parsed.netloc.endswith("amazonaws.com"):
            return raw_url
        key = parsed.path.lstrip("/")
        if not key:
            return raw_url
        try:
            s3 = S3Service()
            return s3.generate_presigned_get_url(key) or raw_url
        except Exception:
            return raw_url


class AnnotationSessionSerializer(serializers.ModelSerializer):
    events_count = serializers.SerializerMethodField()
    match_results_count = serializers.SerializerMethodField()
    video_id = serializers.PrimaryKeyRelatedField(
        source="video",
        queryset=VideoUrl.objects.all(),
        required=False,
        allow_null=True,
    )

    class Meta:
        model = AnnotationSession
        fields = (
            "id",
            "title",
            "video_id",
            "video_url",
            "status",
            "finalized_at",
            "generated_report",
            "events_count",
            "match_results_count",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("status", "finalized_at", "generated_report", "created_at", "updated_at")

    def validate_video(self, value):
        request = self.context.get("request")
        if request and value and value.user_id != request.user.id:
            raise serializers.ValidationError("Selected video does not belong to the authenticated user.")
        return value

    def validate(self, attrs):
        video = attrs.get("video", getattr(self.instance, "video", None))
        video_url = attrs.get("video_url", getattr(self.instance, "video_url", None))

        if video and video_url and str(video_url).strip() != str(video.url).strip():
            raise serializers.ValidationError(
                {"video_url": "video_url must match the selected video_id URL."}
            )

        # If frontend only sends video_id, keep the legacy URL snapshot in sync.
        if video and "video_url" not in attrs:
            attrs["video_url"] = video.url

        return attrs

    def get_events_count(self, obj):
        val = getattr(obj, "events_count", None)
        if val is not None:
            return int(val)
        return obj.events.count()

    def get_match_results_count(self, obj):
        val = getattr(obj, "match_results_count", None)
        if val is not None:
            return int(val)
        return obj.match_results.count()


class AnnotationEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = AnnotationEvent
        fields = (
            "id",
            "session",
            "match_number",
            "timestamp_seconds",
            "player",
            "event_type",
            "move_name",
            "outcome",
            "note",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("session", "created_at", "updated_at")

    def validate(self, attrs):
        event_type = attrs.get("event_type", getattr(self.instance, "event_type", None))
        player = attrs.get("player", getattr(self.instance, "player", None))
        move_name = attrs.get("move_name", getattr(self.instance, "move_name", None))
        outcome = attrs.get("outcome", getattr(self.instance, "outcome", None))
        note = attrs.get("note", getattr(self.instance, "note", None))
        match_number = attrs.get("match_number", getattr(self.instance, "match_number", None))
        timestamp = attrs.get("timestamp_seconds", getattr(self.instance, "timestamp_seconds", None))

        if match_number is None or int(match_number) < 1:
            raise serializers.ValidationError({"match_number": "Match number must be >= 1."})

        if timestamp is None or float(timestamp) < 0:
            raise serializers.ValidationError({"timestamp_seconds": "Timestamp must be >= 0."})

        if event_type == AnnotationEvent.EVENT_NOTE:
            if not note and not move_name:
                raise serializers.ValidationError({"note": "Note events require note text or move_name context."})
            return attrs

        if not move_name:
            raise serializers.ValidationError({"move_name": "move_name is required for non-note events."})

        if outcome not in {AnnotationEvent.OUTCOME_SUCCESS, AnnotationEvent.OUTCOME_FAILED}:
            raise serializers.ValidationError({"outcome": "Outcome must be success or failed for non-note events."})

        attrs["move_name"] = str(move_name).strip()
        return attrs


class AnnotationMatchResultSerializer(serializers.ModelSerializer):
    class Meta:
        model = AnnotationMatchResult
        fields = (
            "id",
            "session",
            "match_number",
            "result",
            "match_type",
            "referee_decision",
            "disqualified",
            "opponent",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("session", "created_at", "updated_at")

    def validate_match_number(self, value):
        if int(value) < 1:
            raise serializers.ValidationError("Match number must be >= 1.")
        return value

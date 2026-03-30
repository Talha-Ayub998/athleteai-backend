from rest_framework import serializers
from urllib.parse import parse_qs, urlparse
from django.db.models import Q
from .models import (
    VideoUrl,
    AnnotationSession,
    AnnotationEvent,
    AnnotationMatchResult,
)
from utils.s3_service import S3Service


YOUTUBE_ALLOWED_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
}


def _extract_youtube_video_id(raw_url):
    if not raw_url:
        return None

    parsed = urlparse(str(raw_url).strip())
    host = (parsed.netloc or "").lower().strip()
    path = (parsed.path or "").strip("/")
    if not host or host not in YOUTUBE_ALLOWED_HOSTS:
        return None

    video_id = None
    if host.endswith("youtu.be"):
        video_id = path.split("/")[0] if path else None
    else:
        path_parts = [p for p in path.split("/") if p]
        if path == "watch":
            video_id = parse_qs(parsed.query or "").get("v", [None])[0]
        elif len(path_parts) >= 2 and path_parts[0] in {"shorts", "embed", "live"}:
            video_id = path_parts[1]

    if not video_id:
        return None
    video_id = str(video_id).strip()
    if len(video_id) != 11:
        return None
    for ch in video_id:
        if not (ch.isalnum() or ch in {"-", "_"}):
            return None
    return video_id


def _normalize_youtube_url(raw_url):
    video_id = _extract_youtube_video_id(raw_url)
    if not video_id:
        return None, None
    return f"https://www.youtube.com/watch?v={video_id}", video_id


# Serializer to validate input
class VideoUrlSerializer(serializers.Serializer):
    video_url = serializers.URLField(required=True)

    def validate_video_url(self, value):
        normalized_url, _video_id = _normalize_youtube_url(value)
        if not normalized_url:
            raise serializers.ValidationError(
                "Only valid YouTube URLs are allowed (youtube.com or youtu.be)."
            )
        return normalized_url


class VideoUploadSerializer(serializers.Serializer):
    video = serializers.FileField(required=True)


class VideoUrlReadSerializer(serializers.ModelSerializer):
    playback_url = serializers.SerializerMethodField()
    is_youtube_link = serializers.SerializerMethodField()
    session_id = serializers.SerializerMethodField()
    session_status = serializers.SerializerMethodField()
    session_updated_at = serializers.SerializerMethodField()

    class Meta:
        model = VideoUrl
        fields = (
            "id",
            "url",
            "is_youtube_link",
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

    def get_is_youtube_link(self, obj):
        return bool(_extract_youtube_video_id(obj.url or ""))

    def get_playback_url(self, obj):
        raw_url = obj.url or ""
        if _extract_youtube_video_id(raw_url):
            return raw_url
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

    def _get_latest_session_for_video(self, obj):
        cache_attr = "_latest_session_cache"
        if hasattr(obj, cache_attr):
            return getattr(obj, cache_attr)

        latest_id = getattr(obj, "latest_session_id", None)
        latest_status = getattr(obj, "latest_session_status", None)
        latest_updated_at = getattr(obj, "latest_session_updated_at", None)
        if latest_id is not None or latest_status is not None or latest_updated_at is not None:
            cached = {
                "id": latest_id,
                "status": latest_status,
                "updated_at": latest_updated_at,
            }
            setattr(obj, cache_attr, cached)
            return cached

        latest = (
            AnnotationSession.objects
            .filter(user_id=obj.user_id)
            .filter(Q(video_id=obj.id) | Q(video_id__isnull=True, video_url=obj.url))
            .order_by("-created_at", "-id")
            .values("id", "status", "updated_at")
            .first()
        )
        cached = latest or {"id": None, "status": None, "updated_at": None}
        setattr(obj, cache_attr, cached)
        return cached

    def get_session_id(self, obj):
        return self._get_latest_session_for_video(obj).get("id")

    def get_session_status(self, obj):
        return self._get_latest_session_for_video(obj).get("status")

    def get_session_updated_at(self, obj):
        return self._get_latest_session_for_video(obj).get("updated_at")


class AnnotationSessionSerializer(serializers.ModelSerializer):
    events_count = serializers.SerializerMethodField()
    match_results_count = serializers.SerializerMethodField()
    report_owner_user_id = serializers.SerializerMethodField()
    report_owner_email = serializers.SerializerMethodField()
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
            "report_owner_user_id",
            "report_owner_email",
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

    def get_report_owner_user_id(self, obj):
        report = getattr(obj, "generated_report", None)
        if not report:
            return None
        return getattr(report, "user_id", None)

    def get_report_owner_email(self, obj):
        report = getattr(obj, "generated_report", None)
        if not report:
            return None
        try:
            return getattr(report.user, "email", None)
        except Exception:
            return None


class AnnotationEventSerializer(serializers.ModelSerializer):
    # Frontend can send UI categorization labels; backend normalizes to model choices.
    event_type = serializers.CharField(max_length=50)
    DEFAULT_CLIP_BEFORE_SECONDS = 2.0
    DEFAULT_CLIP_AFTER_SECONDS = 2.0
    EVENT_TYPE_ALIASES = {
        "position": AnnotationEvent.EVENT_POSITION,
        "neutral position": AnnotationEvent.EVENT_POSITION,
        "top position": AnnotationEvent.EVENT_POSITION,
        "bottom position": AnnotationEvent.EVENT_POSITION,
        "chest to chest": AnnotationEvent.EVENT_POSITION,
        "chest to back": AnnotationEvent.EVENT_POSITION,
        "transition": AnnotationEvent.EVENT_TRANSITION,
        "takedown": AnnotationEvent.EVENT_TRANSITION,
        "sweep": AnnotationEvent.EVENT_TRANSITION,
        "passing": AnnotationEvent.EVENT_TRANSITION,
        "back take": AnnotationEvent.EVENT_TRANSITION,
        "leg entry": AnnotationEvent.EVENT_TRANSITION,
        "submission": AnnotationEvent.EVENT_SUBMISSION,
        "note": AnnotationEvent.EVENT_NOTE,
    }

    class Meta:
        model = AnnotationEvent
        fields = (
            "id",
            "session",
            "match_number",
            "timestamp_seconds",
            "start_time_seconds",
            "end_time_seconds",
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
        event_type_raw = attrs.get("event_type", getattr(self.instance, "event_type", None))
        player = attrs.get("player", getattr(self.instance, "player", None))
        move_name = attrs.get("move_name", getattr(self.instance, "move_name", None))
        outcome = attrs.get("outcome", getattr(self.instance, "outcome", None))
        note = attrs.get("note", getattr(self.instance, "note", None))
        match_number = attrs.get("match_number", getattr(self.instance, "match_number", None))
        timestamp = attrs.get("timestamp_seconds", getattr(self.instance, "timestamp_seconds", None))
        start_time = attrs.get("start_time_seconds", getattr(self.instance, "start_time_seconds", None))
        end_time = attrs.get("end_time_seconds", getattr(self.instance, "end_time_seconds", None))

        event_type_key = str(event_type_raw or "").strip().lower()
        event_type = self.EVENT_TYPE_ALIASES.get(event_type_key)
        if not event_type:
            allowed = ", ".join(sorted(set(self.EVENT_TYPE_ALIASES.keys())))
            raise serializers.ValidationError(
                {"event_type": f'Unsupported event_type "{event_type_raw}". Allowed: {allowed}'}
            )
        attrs["event_type"] = event_type

        if match_number is None or int(match_number) < 1:
            raise serializers.ValidationError({"match_number": "Match number must be >= 1."})

        if timestamp is None or float(timestamp) < 0:
            raise serializers.ValidationError({"timestamp_seconds": "Timestamp must be >= 0."})
        timestamp = float(timestamp)

        if start_time is None:
            start_time = max(0.0, timestamp - self.DEFAULT_CLIP_BEFORE_SECONDS)
        if end_time is None:
            end_time = timestamp + self.DEFAULT_CLIP_AFTER_SECONDS

        start_time = float(start_time)
        end_time = float(end_time)
        if start_time < 0:
            raise serializers.ValidationError({"start_time_seconds": "start_time_seconds must be >= 0."})
        if end_time < start_time:
            raise serializers.ValidationError({"end_time_seconds": "end_time_seconds must be >= start_time_seconds."})
        if timestamp < start_time or timestamp > end_time:
            raise serializers.ValidationError(
                {"timestamp_seconds": "timestamp_seconds must be between start_time_seconds and end_time_seconds."}
            )

        attrs["timestamp_seconds"] = timestamp
        attrs["start_time_seconds"] = start_time
        attrs["end_time_seconds"] = end_time

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

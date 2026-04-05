from rest_framework import serializers

from reports.models import MultiVideoSession, MultiVideoSessionItem
from reports.serializers import AnnotationSessionSerializer, VideoUrlReadSerializer


class MultiVideoSessionItemSerializer(serializers.ModelSerializer):
    video_detail = VideoUrlReadSerializer(source="video", read_only=True)
    annotation_session_id = serializers.IntegerField(source="annotation_session.id", read_only=True, allow_null=True)

    class Meta:
        model = MultiVideoSessionItem
        fields = [
            "id",
            "video",
            "video_detail",
            "annotation_session_id",
            "status",
            "is_removed",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "annotation_session_id", "status", "is_removed", "created_at", "updated_at"]


class MultiVideoSessionSerializer(serializers.ModelSerializer):
    items = serializers.SerializerMethodField()
    total_videos = serializers.SerializerMethodField()
    completed_videos = serializers.SerializerMethodField()
    report_id = serializers.IntegerField(source="generated_report.id", read_only=True, allow_null=True)

    class Meta:
        model = MultiVideoSession
        fields = [
            "id",
            "title",
            "status",
            "report_id",
            "total_videos",
            "completed_videos",
            "finalized_at",
            "created_at",
            "updated_at",
            "items",
        ]
        read_only_fields = ["id", "status", "report_id", "finalized_at", "created_at", "updated_at"]

    def get_items(self, obj):
        items = obj.items.filter(is_removed=False).order_by("created_at")
        return MultiVideoSessionItemSerializer(items, many=True).data

    def get_total_videos(self, obj):
        return obj.items.filter(is_removed=False).count()

    def get_completed_videos(self, obj):
        return obj.items.filter(is_removed=False, status=MultiVideoSessionItem.STATUS_COMPLETED).count()

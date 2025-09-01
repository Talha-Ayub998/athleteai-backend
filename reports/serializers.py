
from rest_framework import serializers
from .models import VideoUrl

# Serializer to validate input
class VideoUrlSerializer(serializers.Serializer):
    video_url = serializers.URLField(required=True)

class VideoUrlReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = VideoUrl
        fields = ("id", "url", "created_at")
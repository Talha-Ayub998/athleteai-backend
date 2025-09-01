
from rest_framework import serializers

# Serializer to validate input
class VideoUrlSerializer(serializers.Serializer):
    video_url = serializers.URLField(required=True)
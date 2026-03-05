from urllib.parse import urlparse

from django.core.management.base import BaseCommand

from reports.models import VideoUrl


def _extract_s3_key(raw_url: str):
    if not raw_url:
        return None
    parsed = urlparse(raw_url)
    if not parsed.netloc.endswith("amazonaws.com"):
        return None
    key = (parsed.path or "").lstrip("/")
    return key or None


class Command(BaseCommand):
    help = "Backfill VideoUrl.s3_key from existing S3 URLs."

    def handle(self, *args, **options):
        updated = 0
        skipped = 0

        for video in VideoUrl.objects.filter(s3_key__isnull=True).iterator():
            key = _extract_s3_key(video.url)
            if not key:
                skipped += 1
                continue
            video.s3_key = key
            video.save(update_fields=["s3_key"])
            updated += 1

        self.stdout.write(self.style.SUCCESS(f"Backfill complete. updated={updated}, skipped={skipped}"))

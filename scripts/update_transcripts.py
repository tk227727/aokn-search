import json
import time
import urllib.error
import urllib.request
from pathlib import Path

VIDEOS_FILE = Path("data/videos.json")
OUTPUT_FILE = Path("data/transcript_status.json")
MAX_VIDEOS_PER_RUN = 5
TRANSCRIPT_URL = "https://youtube-transcript.ai/transcript/{}.txt"


def load_json(path, default):
    if not path.exists():
        return default

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def fetch_transcript(video_id):
    url = TRANSCRIPT_URL.format(video_id)

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 Chrome/140.0 Safari/537.36"
        },
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=30,
        ) as response:
            text = response.read().decode(
                "utf-8",
                errors="replace",
            ).strip()

        if not text:
            return {
                "status": "no_transcript",
                "length": 0,
            }

        return {
            "status": "success",
            "length": len(text),
        }

    except urllib.error.HTTPError as e:
        return {
            "status": "http_error",
            "httpCode": e.code,
        }

    except Exception as e:
        return {
            "status": "error",
            "message": str(e)[:300],
        }


def main():
    if not VIDEOS_FILE.exists():
        raise RuntimeError(
            "data/videos.json がありません。"
            "先に update_videos.py を実行してください。"
        )

    videos_data = load_json(VIDEOS_FILE, {})
    videos = videos_data.get("videos", [])

    old_data = load_json(
        OUTPUT_FILE,
        {
            "version": 1,
            "videos": {},
        },
    )

    current_video_ids = {
        video.get("videoId")
        for video in videos
        if video.get("videoId")
    }

    statuses = {
        video_id: status
        for video_id, status
        in old_data.get("videos", {}).items()
        if video_id in current_video_ids
    }

    # まだ一度も確認していない動画を最優先する。
    new_videos = [
        video
        for video in videos
        if video.get("videoId") not in statuses
    ]

    # 一時エラーだった動画は、未確認動画の後で再試行する。
    retry_videos = [
        video
        for video in videos
        if statuses.get(
            video.get("videoId"), {}
        ).get("status") in (
            "http_error",
            "error",
        )
    ]

    pending = new_videos + retry_videos
    selected = pending[:MAX_VIDEOS_PER_RUN]

    print(f"Total videos: {len(videos)}")
    print(f"Already checked: {len(statuses)}")
    print(f"New videos: {len(new_videos)}")
    print(f"Retry videos: {len(retry_videos)}")
    print(f"Checking this run: {len(selected)}")

    for index, video in enumerate(
        selected,
        start=1,
    ):
        video_id = video.get("videoId")
        title = video.get("title", "")
        channel = video.get("channel", "")

        print()
        print(
            f"[{index}/{len(selected)}] "
            f"{channel} / {title}"
        )
        print(f"Video ID: {video_id}")

        result = fetch_transcript(video_id)

        statuses[video_id] = {
            "title": title,
            "channel": channel,
            **result,
        }

        print(f"Result: {result}")

        if index < len(selected):
            time.sleep(3)

    output = {
        "version": 1,
        "totalVideos": len(videos),
        "checkedVideos": len(statuses),
        "videos": statuses,
    }

    save_json(OUTPUT_FILE, output)

    print()
    print(f"Saved: {OUTPUT_FILE}")
    print(f"Checked videos: {len(statuses)}")


if __name__ == "__main__":
    main()

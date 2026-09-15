import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

API_KEY = os.environ.get("YOUTUBE_API_KEY")
VIDEOS_FILE = Path("data/videos.json")

if not API_KEY:
    raise RuntimeError("YOUTUBE_API_KEY が設定されていません")

BASE_URL = "https://www.googleapis.com/youtube/v3"

# 火威青・音乃瀬奏だけ今後も自動更新する。
# ReGLOSSは現在のdata/videos.jsonに入っている動画を固定して引き継ぐ。
CHANNELS = [
    {
        "key": "ao",
        "name": "火威青",
        "handle": "@hiodoshiao",
        "live_only": True,
    },
    {
        "key": "kanade",
        "name": "音乃瀬奏",
        "handle": "@otonosekanade",
        "live_only": True,
    },
]


def api_get(endpoint, params):
    params["key"] = API_KEY
    url = f"{BASE_URL}/{endpoint}?" + urllib.parse.urlencode(params)

    with urllib.request.urlopen(url) as response:
        return json.loads(response.read().decode("utf-8"))


def get_channel(handle):
    data = api_get(
        "channels",
        {
            "part": "snippet,contentDetails",
            "forHandle": handle,
        },
    )

    if not data.get("items"):
        raise RuntimeError(f"チャンネルが見つかりません: {handle}")

    item = data["items"][0]

    return {
        "id": item["id"],
        "title": item["snippet"]["title"],
        "uploads": item["contentDetails"]["relatedPlaylists"]["uploads"],
    }


def get_upload_ids(playlist_id):
    video_ids = []
    page_token = None

    while True:
        params = {
            "part": "contentDetails",
            "playlistId": playlist_id,
            "maxResults": 50,
        }

        if page_token:
            params["pageToken"] = page_token

        data = api_get("playlistItems", params)

        for item in data.get("items", []):
            video_ids.append(item["contentDetails"]["videoId"])

        page_token = data.get("nextPageToken")

        if not page_token:
            break

    return video_ids


def chunks(items, size=50):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def get_video_details(video_ids):
    videos = []

    for group in chunks(video_ids):
        data = api_get(
            "videos",
            {
                "part": "snippet,contentDetails,liveStreamingDetails,status",
                "id": ",".join(group),
            },
        )
        videos.extend(data.get("items", []))

    return videos


def iso_duration_to_seconds(duration):
    duration = duration.replace("PT", "")

    hours = 0
    minutes = 0
    seconds = 0

    if "H" in duration:
        h, duration = duration.split("H", 1)
        hours = int(h)

    if "M" in duration:
        m, duration = duration.split("M", 1)
        minutes = int(m)

    if "S" in duration:
        s = duration.replace("S", "")
        seconds = int(s)

    return hours * 3600 + minutes * 60 + seconds


def is_completed_live(video):
    live = video.get("liveStreamingDetails")

    if not live:
        return False

    return bool(
        live.get("actualStartTime")
        and live.get("actualEndTime")
    )


def should_keep(video, channel):
    if video.get("status", {}).get("privacyStatus") != "public":
        return False

    if channel["live_only"]:
        return is_completed_live(video)

    return True


def normalize_video(video, channel):
    snippet = video["snippet"]
    duration = video.get("contentDetails", {}).get("duration", "PT0S")

    return {
        "videoId": video["id"],
        "title": snippet["title"],
        "channel": channel["key"],
        "channelName": channel["name"],
        "publishedAt": snippet["publishedAt"],
        "durationSeconds": iso_duration_to_seconds(duration),
        "isLiveArchive": is_completed_live(video),
        "url": f"https://www.youtube.com/watch?v={video['id']}",
    }


def load_fixed_regloss():
    if not VIDEOS_FILE.exists():
        raise RuntimeError(
            "data/videos.json がありません。"
            "ReGLOSSの固定対象を引き継げないため更新を中止します。"
        )

    old_data = json.loads(VIDEOS_FILE.read_text(encoding="utf-8"))

    regloss_videos = [
        video
        for video in old_data.get("videos", [])
        if video.get("channel") == "regloss"
    ]

    regloss_channel = old_data.get("channels", {}).get("regloss")

    if not regloss_videos or not regloss_channel:
        raise RuntimeError(
            "現在のdata/videos.jsonからReGLOSS固定データを取得できません。"
        )

    return regloss_videos, regloss_channel


def main():
    # 実行開始時点のReGLOSS対象をそのまま保存しておく。
    fixed_regloss, regloss_channel = load_fixed_regloss()

    print("=== ReGLOSS ===")
    print(f"Fixed videos: {len(fixed_regloss)}")
    print("ReGLOSSは固定対象のためYouTubeから新規動画を取得しません。")

    result = {
        "version": 1,
        "channels": {
            "regloss": {
                **regloss_channel,
                "videoCount": len(fixed_regloss),
                "autoUpdate": False,
            }
        },
        "videos": list(fixed_regloss),
    }

    for channel in CHANNELS:
        print(f"\n=== {channel['name']} ===")

        info = get_channel(channel["handle"])

        print(f"Channel: {info['title']} ({info['id']})")

        ids = get_upload_ids(info["uploads"])
        print(f"Uploads: {len(ids)}")

        details = get_video_details(ids)

        selected = [
            normalize_video(video, channel)
            for video in details
            if should_keep(video, channel)
        ]

        print(f"Selected: {len(selected)}")

        result["channels"][channel["key"]] = {
            "name": channel["name"],
            "handle": channel["handle"],
            "channelId": info["id"],
            "uploadsPlaylistId": info["uploads"],
            "videoCount": len(selected),
            "autoUpdate": True,
        }

        result["videos"].extend(selected)

    # 念のためvideoIdで重複を除く。
    unique_videos = {}
    for video in result["videos"]:
        video_id = video.get("videoId")
        if video_id:
            unique_videos[video_id] = video

    result["videos"] = list(unique_videos.values())

    result["videos"].sort(
        key=lambda x: x["publishedAt"],
        reverse=True,
    )

    VIDEOS_FILE.parent.mkdir(parents=True, exist_ok=True)

    VIDEOS_FILE.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"\n保存完了: {VIDEOS_FILE}")
    print(f"ReGLOSS固定: {len(fixed_regloss)} 本")
    print(f"合計: {len(result['videos'])} 本")


if __name__ == "__main__":
    main()

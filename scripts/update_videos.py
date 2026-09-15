import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

API_KEY = os.environ.get("YOUTUBE_API_KEY")

if not API_KEY:
    raise RuntimeError("YOUTUBE_API_KEY が設定されていません")

BASE_URL = "https://www.googleapis.com/youtube/v3"

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
    {
        "key": "regloss",
        "name": "ReGLOSS",
        "handle": "@hololivedev_is",
        "live_only": False,
    },
]


def api_get(endpoint, params):
    params["key"] = API_KEY
    url = (
        f"{BASE_URL}/{endpoint}?"
        + urllib.parse.urlencode(params)
    )

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
        raise RuntimeError(
            f"チャンネルが見つかりません: {handle}"
        )

    item = data["items"][0]

    return {
        "id": item["id"],
        "title": item["snippet"]["title"],
        "uploads": item["contentDetails"]
        ["relatedPlaylists"]["uploads"],
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
            video_ids.append(
                item["contentDetails"]["videoId"]
            )

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
                "part": (
                    "snippet,contentDetails,"
                    "liveStreamingDetails,status"
                ),
                "id": ",".join(group),
            },
        )

        videos.extend(data.get("items", []))

    return videos


def iso_duration_to_seconds(duration):
    # PT1H2M3S のような形式を秒に変換
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
    if video.get("status", {}).get(
        "privacyStatus"
    ) != "public":
        return False

    if channel["live_only"]:
        return is_completed_live(video)

                # ReGLOSS:
    # 3分（180秒）以下の動画は検索対象から除外する
    duration_seconds = parse_iso8601_duration(
        video.get("contentDetails", {}).get("duration", "PT0S")
    )

    if duration_seconds <= 180:
        return False

    return True


def normalize_video(video, channel):
    snippet = video["snippet"]
    duration = video.get(
        "contentDetails", {}
    ).get("duration", "PT0S")

    live = is_completed_live(video)

    return {
        "videoId": video["id"],
        "title": snippet["title"],
        "channel": channel["key"],
        "channelName": channel["name"],
        "publishedAt": snippet["publishedAt"],
        "durationSeconds":
            iso_duration_to_seconds(duration),
        "isLiveArchive": live,
        "url":
            f"https://www.youtube.com/watch?v={video['id']}",
    }


def main():
    result = {
        "version": 1,
        "channels": {},
        "videos": [],
    }

    for channel in CHANNELS:
        print(
            f"\n=== {channel['name']} ==="
        )

        info = get_channel(
            channel["handle"]
        )

        print(
            f"Channel: {info['title']} "
            f"({info['id']})"
        )

        ids = get_upload_ids(
            info["uploads"]
        )

        print(
            f"Uploads: {len(ids)}"
        )

        details = get_video_details(ids)

        selected = [
            normalize_video(video, channel)
            for video in details
            if should_keep(video, channel)
        ]

        print(
            f"Selected: {len(selected)}"
        )

        result["channels"][channel["key"]] = {
            "name": channel["name"],
            "handle": channel["handle"],
            "channelId": info["id"],
            "uploadsPlaylistId":
                info["uploads"],
            "videoCount": len(selected),
        }

        result["videos"].extend(selected)

    result["videos"].sort(
        key=lambda x: x["publishedAt"],
        reverse=True,
    )

    output = Path("data/videos.json")
    output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output.write_text(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(
        f"\n保存完了: {output}"
    )
    print(
        f"合計: {len(result['videos'])} 本"
    )


if __name__ == "__main__":
    main()

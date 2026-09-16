import json
import re
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

VIDEOS_FILE = Path("data/videos.json")
STATUS_FILE = Path("data/transcript_status.json")
INDEX_DIR = Path("data/search-index")
CATALOG_FILE = INDEX_DIR / "catalog.json"
MAX_VIDEOS_PER_RUN = 5
INDEX_VERSION = 2
TRANSCRIPT_URL = "https://youtube-transcript.ai/transcript/{}.txt?lang=ja"

# 最初の動作確認に使った2本を、未作成なら最優先で検索インデックス化する。
PRIORITY_VIDEO_IDS = [
    "4pw9GhX85WQ",  # 音乃瀬奏
    "T65Ct4b1Myk",  # 火威青
]


def load_json(path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))


def fetch_transcript(video_id):
    req = urllib.request.Request(
        TRANSCRIPT_URL.format(video_id),
        headers={"User-Agent": "Mozilla/5.0 Chrome/140.0 Safari/537.36"},
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace").strip()


def time_to_seconds(value):
    total = 0
    for n in map(int, value.split(":")):
        total = total * 60 + n
    return total


def parse_transcript(raw):
    marker = "## Transcript"
    body = raw[raw.index(marker) + len(marker):] if marker in raw else raw
    pattern = re.compile(
        r"\[((?:\d+:)?\d+:\d{2})\]\s*([\s\S]*?)(?=\n\s*\[((?:\d+:)?\d+:\d{2})\]|\s*$)"
    )
    rows = []
    for m in pattern.finditer(body):
        text = re.sub(r"\s+", " ", m.group(2)).strip()
        if text:
            rows.append({"t": time_to_seconds(m.group(1)), "x": text})
    return rows


def normalize(text):
    return re.sub(
        r"\s+", "", unicodedata.normalize("NFKC", text).casefold()
    )


def grams(text, n):
    s = normalize(text)
    if len(s) < n:
        return set()
    return {s[i:i+n] for i in range(len(s)-n+1)}


def build_video_index(video, rows):
    # v2 stores both bigrams and trigrams. Trigrams prevent false positives
    # such as "かな" and "なで" occurring separately in one long caption row.
    postings = {"2": {}, "3": {}}

    for row in rows:
        sec = int(row["t"])
        for n in (2, 3):
            bucket = postings[str(n)]
            for gram in grams(row["x"], n):
                bucket.setdefault(gram, []).append(sec)

    compact = {}
    for n, bucket in postings.items():
        compact[n] = {}
        for gram, times in bucket.items():
            unique = sorted(set(times))
            if not unique:
                continue
            deltas = [unique[0]]
            deltas.extend(unique[i] - unique[i-1] for i in range(1, len(unique)))
            compact[n][gram] = deltas

    return {
        "version": INDEX_VERSION,
        "videoId": video["videoId"],
        "channel": video["channel"],
        "postings": compact,
    }


def needs_rebuild(video_id):
    path = INDEX_DIR / f"{video_id}.json"
    if not path.exists():
        return True
    try:
        return load_json(path, {}).get("version", 0) < INDEX_VERSION
    except Exception:
        return True


def rebuild_catalog(videos):
    indexed = []
    for video in videos:
        p = INDEX_DIR / f'{video["videoId"]}.json'
        if p.exists():
            indexed.append({
                "videoId": video["videoId"],
                "title": video.get("title", ""),
                "channel": video.get("channel", ""),
                "channelName": video.get("channelName", ""),
                "publishedAt": video.get("publishedAt", ""),
                "url": video.get("url", ""),
            })
    save_json(CATALOG_FILE, {
        "version": INDEX_VERSION,
        "indexedVideos": len(indexed),
        "videos": indexed,
    })


def main():
    data = load_json(VIDEOS_FILE, {})
    videos = data.get("videos", [])
    status = load_json(STATUS_FILE, {}).get("videos", {})

    eligible = [
        v for v in videos
        if status.get(v.get("videoId"), {}).get("status") == "success"
        and needs_rebuild(v.get("videoId"))
    ]

    # 優先動画を先頭へ。それ以外は今までどおりの順番で処理する。
    priority_order = {
        video_id: i for i, video_id in enumerate(PRIORITY_VIDEO_IDS)
    }
    eligible.sort(
        key=lambda v: (
            0 if v.get("videoId") in priority_order else 1,
            priority_order.get(v.get("videoId"), 0),
        )
    )

    candidates = eligible[:MAX_VIDEOS_PER_RUN]

    print(f"Videos: {len(videos)}")
    print(f"Indexes to build/rebuild this run: {len(candidates)}")

    for i, video in enumerate(candidates, 1):
        vid = video["videoId"]
        print(f"[{i}/{len(candidates)}] {video.get('channelName')} / {video.get('title')}")
        try:
            raw = fetch_transcript(vid)
            rows = parse_transcript(raw)
            if not rows:
                print("  skipped: transcript could not be parsed")
                continue
            save_json(INDEX_DIR / f"{vid}.json", build_video_index(video, rows))
            print(f"  indexed v{INDEX_VERSION}: {len(rows)} transcript sections")
        except urllib.error.HTTPError as e:
            print(f"  HTTP {e.code}; will retry later")
        except Exception as e:
            print(f"  error: {str(e)[:300]}; will retry later")
        if i < len(candidates):
            time.sleep(3)

    rebuild_catalog(videos)
    print(f"Saved {CATALOG_FILE}")


if __name__ == "__main__":
    main()

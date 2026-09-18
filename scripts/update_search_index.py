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

# 最初の字幕取得テストに使った2本。
# videos.json に存在しなくても、特別追加枠として検索対象にする。
SPECIAL_VIDEOS = [
    {
        "videoId": "4pw9GhX85WQ",
        "title": "最初の字幕取得テスト動画（音乃瀬奏）",
        "channel": "kanade",
        "channelName": "音乃瀬奏",
        "publishedAt": "",
        "url": "https://www.youtube.com/watch?v=4pw9GhX85WQ",
    },
    {
        "videoId": "T65Ct4b1Myk",
        "title": "最初の字幕取得テスト動画（火威青）",
        "channel": "ao",
        "channelName": "火威青",
        "publishedAt": "",
        "url": "https://www.youtube.com/watch?v=T65Ct4b1Myk",
    },
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
    """複数のタイムスタンプ形式に対応する字幕パーサー。"""
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    marker = "## Transcript"
    body = raw[raw.index(marker) + len(marker):] if marker in raw else raw

    def to_seconds(value):
        value = value.strip().replace(",", ".")
        try:
            parts = [float(x) for x in value.split(":")]
        except ValueError:
            return None
        if len(parts) == 3:
            return int(parts[0] * 3600 + parts[1] * 60 + parts[2])
        if len(parts) == 2:
            return int(parts[0] * 60 + parts[1])
        return None

    rows = []

    # [MM:SS] text / [HH:MM:SS] text
    pat = re.compile(r"(?m)^\\s*\\[((?:\\d{1,2}:)?\\d{1,3}:\\d{2}(?:[.,]\\d+)?)\\]\\s*(.+)$")
    for m in pat.finditer(body):
        t = to_seconds(m.group(1))
        x = re.sub(r"\\s+", " ", m.group(2)).strip()
        if t is not None and x:
            rows.append({"t": t, "x": x})

    # VTT/SRT timestamp line followed by caption text
    if not rows:
        lines = body.split("\n")
        i = 0
        cue = re.compile(r"^\\s*((?:\\d{1,2}:)?\\d{1,2}:\\d{2}[.,]\\d{3})\\s*-->")
        while i < len(lines):
            m = cue.match(lines[i])
            if not m:
                i += 1
                continue
            t = to_seconds(m.group(1))
            i += 1
            parts = []
            while i < len(lines) and lines[i].strip() and not cue.match(lines[i]):
                if not lines[i].strip().isdigit():
                    parts.append(lines[i].strip())
                i += 1
            x = re.sub(r"<[^>]+>", " ", " ".join(parts))
            x = re.sub(r"\\s+", " ", x).strip()
            if t is not None and x:
                rows.append({"t": t, "x": x})

    # MM:SS text / HH:MM:SS text
    if not rows:
        pat = re.compile(r"(?m)^\\s*((?:\\d{1,2}:)?\\d{1,3}:\\d{2}(?:[.,]\\d+)?)\\s+(.+)$")
        for m in pat.finditer(body):
            t = to_seconds(m.group(1))
            x = re.sub(r"\\s+", " ", m.group(2)).strip()
            if t is not None and x and "-->" not in x:
                rows.append({"t": t, "x": x})

    cleaned = []
    seen = set()
    for row in sorted(rows, key=lambda r: r["t"]):
        key = (row["t"], row["x"])
        if key not in seen:
            seen.add(key)
            cleaned.append(row)
    return cleaned

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

    # 特別追加2本は videos.json / transcript_status.json に関係なく最優先。
    special = [
        v for v in SPECIAL_VIDEOS
        if needs_rebuild(v["videoId"])
    ]

    special_ids = {v["videoId"] for v in SPECIAL_VIDEOS}
    normal = [
        v for v in videos
        if v.get("videoId") not in special_ids
        and status.get(v.get("videoId"), {}).get("status") == "success"
        and needs_rebuild(v.get("videoId"))
    ]

    candidates = (special + normal)[:MAX_VIDEOS_PER_RUN]

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

    catalog_videos = videos + [
        v for v in SPECIAL_VIDEOS
        if v["videoId"] not in {x.get("videoId") for x in videos}
    ]
    rebuild_catalog(catalog_videos)
    print(f"Saved {CATALOG_FILE}")


if __name__ == "__main__":
    main()

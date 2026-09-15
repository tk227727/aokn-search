import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

VIDEOS_FILE = Path("data/videos.json")
STATUS_FILE = Path("data/transcript_status.json")
INDEX_DIR = Path("data/search-index")
CATALOG_FILE = INDEX_DIR / "catalog.json"

# Keep this deliberately small so the existing transcript provider is not hammered.
MAX_VIDEOS_PER_RUN = 5
TRANSCRIPT_URL = "https://youtube-transcript.ai/transcript/{}.txt?lang=ja"


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
    parts = [int(x) for x in value.split(":")]
    total = 0
    for n in parts:
        total = total * 60 + n
    return total


def clean_text(text):
    return re.sub(r"\s+", " ", text).strip()


def parse_transcript(raw):
    marker = "## Transcript"
    body = raw[raw.index(marker) + len(marker):] if marker in raw else raw
    pattern = re.compile(
        r"\[((?:\d+:)?\d+:\d{2})\]\s*([\s\S]*?)(?=\n\s*\[((?:\d+:)?\d+:\d{2})\]|\s*$)"
    )
    rows = []
    for m in pattern.finditer(body):
        text = clean_text(m.group(2))
        if text:
            rows.append({"t": time_to_seconds(m.group(1)), "x": text})
    return rows


def normalize(text):
    # Browser side also uses NFKC + lowercase. Python's casefold is suitable here.
    import unicodedata
    return unicodedata.normalize("NFKC", text).casefold().replace(" ", "")


def ngrams(text, n=2):
    s = normalize(text)
    return {s[i:i+n] for i in range(max(0, len(s)-n+1)) if s[i:i+n].strip()}


def build_video_index(video, rows):
    # Store no transcript sentences. Only 2-character grams -> timestamps.
    postings = {}
    for row in rows:
        sec = int(row["t"])
        for gram in ngrams(row["x"], 2):
            postings.setdefault(gram, []).append(sec)

    # Deduplicate timestamps and delta-encode them to keep JSON small.
    compact = {}
    for gram, times in postings.items():
        unique = sorted(set(times))
        if not unique:
            continue
        deltas = [unique[0]]
        deltas.extend(unique[i] - unique[i-1] for i in range(1, len(unique)))
        compact[gram] = deltas

    return {
        "version": 1,
        "videoId": video["videoId"],
        "channel": video["channel"],
        "postings": compact,
    }


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
        "version": 1,
        "indexedVideos": len(indexed),
        "videos": indexed,
    })


def main():
    data = load_json(VIDEOS_FILE, {})
    videos = data.get("videos", [])
    status = load_json(STATUS_FILE, {}).get("videos", {})

    # Prioritize videos whose transcript check already succeeded.
    candidates = [
        v for v in videos
        if status.get(v.get("videoId"), {}).get("status") == "success"
        and not (INDEX_DIR / f'{v.get("videoId")}.json').exists()
    ][:MAX_VIDEOS_PER_RUN]

    print(f"Videos: {len(videos)}")
    print(f"New indexes this run: {len(candidates)}")

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
            print(f"  indexed: {len(rows)} transcript sections")
        except urllib.error.HTTPError as e:
            print(f"  HTTP {e.code}; will retry on a later run")
        except Exception as e:
            print(f"  error: {str(e)[:300]}; will retry on a later run")
        if i < len(candidates):
            time.sleep(3)

    rebuild_catalog(videos)
    print(f"Saved {CATALOG_FILE}")


if __name__ == "__main__":
    main()

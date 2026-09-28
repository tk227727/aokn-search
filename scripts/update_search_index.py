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
ATTEMPTS_FILE = INDEX_DIR / "attempts.json"
RETRY_STATUS_FILE = INDEX_DIR / "retry_status.json"

GLOBAL_DIR = INDEX_DIR / "global"
GLOBAL_MANIFEST = GLOBAL_DIR / "manifest.json"

GLOBAL_SHARDS = 128

INDEX_VERSION = 2
MAX_VIDEOS_PER_RUN = 50
REQUEST_INTERVAL = 20

TRANSCRIPT_URL = (
    "https://youtube-transcript.ai/"
    "transcript/{}.txt?lang=ja"
)


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

    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with path.open(
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            separators=(",", ":")
        )


def fetch_transcript(video_id):
    req = urllib.request.Request(
        TRANSCRIPT_URL.format(video_id),
        headers={
            "User-Agent":
            "Mozilla/5.0 Chrome/140.0 Safari/537.36"
        },
    )

    with urllib.request.urlopen(
        req,
        timeout=30
    ) as response:
        return (
            response
            .read()
            .decode(
                "utf-8",
                errors="replace"
            )
            .strip()
        )


def is_rate_limit_message(raw):
    text = raw.casefold()

    phrases = [
        "high volume",
        "higher rate limits",
        "rate limit",
        "too many requests",
        "api at high volume",
    ]

    return any(
        phrase in text
        for phrase in phrases
    )


def parse_time(value):
    value = (
        value
        .strip()
        .replace(",", ".")
    )

    try:
        parts = [
            float(x)
            for x in value.split(":")
        ]
    except ValueError:
        return None

    if len(parts) == 3:
        return int(
            parts[0] * 3600
            + parts[1] * 60
            + parts[2]
        )

    if len(parts) == 2:
        return int(
            parts[0] * 60
            + parts[1]
        )

    return None


def clean_caption_text(text):
    text = re.sub(
        r"<[^>]+>",
        " ",
        text
    )

    text = (
        text
        .replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
    )

    return re.sub(
        r"\s+",
        " ",
        text
    ).strip()


def parse_transcript(raw):
    if not raw:
        return []

    raw = (
        raw
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\ufeff", "")
    )

    marker = "## Transcript"

    if marker in raw:
        body = raw[
            raw.index(marker)
            + len(marker):
        ]
    else:
        body = raw

    lines = body.splitlines()
    rows = []

    bracket = re.compile(
        r"^\s*\["
        r"(\d+(?::\d+){1,2}(?:[.,]\d+)?)"
        r"\]\s*(.*)$"
    )

    plain = re.compile(
        r"^\s*"
        r"(\d+(?::\d+){1,2}(?:[.,]\d+)?)"
        r"\s+(.+)$"
    )

    cue = re.compile(
        r"^\s*"
        r"(\d+(?::\d+){1,2}[.,]\d+)"
        r"\s*-->\s*"
        r"(\d+(?::\d+){1,2}[.,]\d+)"
    )

    i = 0

    while i < len(lines):
        line = lines[i].strip()

        if not line:
            i += 1
            continue

        m = bracket.match(line)

        if m:
            t = parse_time(m.group(1))
            text = clean_caption_text(
                m.group(2)
            )

            if (
                t is not None
                and not text
                and i + 1 < len(lines)
            ):
                next_line = (
                    lines[i + 1]
                    .strip()
                )

                if (
                    next_line
                    and not bracket.match(next_line)
                    and not cue.match(next_line)
                ):
                    text = clean_caption_text(
                        next_line
                    )
                    i += 1

            if t is not None and text:
                rows.append({
                    "t": t,
                    "x": text
                })

            i += 1
            continue

        m = cue.match(line)

        if m:
            t = parse_time(m.group(1))
            i += 1
            parts = []

            while i < len(lines):
                next_line = (
                    lines[i]
                    .strip()
                )

                if not next_line:
                    break

                if cue.match(next_line):
                    break

                if bracket.match(next_line):
                    break

                if not next_line.isdigit():
                    parts.append(next_line)

                i += 1

            text = clean_caption_text(
                " ".join(parts)
            )

            if t is not None and text:
                rows.append({
                    "t": t,
                    "x": text
                })

            continue

        m = plain.match(line)

        if (
            m
            and "-->" not in line
        ):
            t = parse_time(
                m.group(1)
            )

            text = clean_caption_text(
                m.group(2)
            )

            if t is not None and text:
                rows.append({
                    "t": t,
                    "x": text
                })

            i += 1
            continue

        inline = list(
            re.finditer(
                r"\["
                r"(\d+(?::\d+){1,2}(?:[.,]\d+)?)"
                r"\]\s*"
                r"(.*?)"
                r"(?="
                r"\[\d+(?::\d+){1,2}(?:[.,]\d+)?\]"
                r"|$)",
                line
            )
        )

        if inline:
            for item in inline:
                t = parse_time(
                    item.group(1)
                )

                text = clean_caption_text(
                    item.group(2)
                )

                if (
                    t is not None
                    and text
                ):
                    rows.append({
                        "t": t,
                        "x": text
                    })

        i += 1

    cleaned = []
    seen = set()

    for row in sorted(
        rows,
        key=lambda r: r["t"]
    ):
        key = (
            row["t"],
            row["x"]
        )

        if key in seen:
            continue

        seen.add(key)
        cleaned.append(row)

    return cleaned


def normalize(text):
    return re.sub(
        r"\s+",
        "",
        unicodedata
        .normalize(
            "NFKC",
            text
        )
        .casefold()
    )


def grams(text, n):
    s = normalize(text)

    if len(s) < n:
        return set()

    return {
        s[i:i+n]
        for i
        in range(
            len(s) - n + 1
        )
    }


def build_video_index(video, rows):
    postings = {
        "2": {},
        "3": {}
    }

    for row in rows:
        sec = int(row["t"])

        for n in (2, 3):
            bucket = postings[str(n)]

            for gram in grams(
                row["x"],
                n
            ):
                bucket.setdefault(
                    gram,
                    []
                ).append(sec)

    compact = {}

    for n, bucket in postings.items():
        compact[n] = {}

        for gram, times in bucket.items():
            unique = sorted(set(times))

            if not unique:
                continue

            deltas = [unique[0]]

            deltas.extend(
                unique[i] - unique[i - 1]
                for i in range(
                    1,
                    len(unique)
                )
            )

            compact[n][gram] = deltas

    return {
        "version": INDEX_VERSION,
        "videoId": video["videoId"],
        "channel": video["channel"],
        "postings": compact,
    }


def needs_rebuild(video_id):
    path = (
        INDEX_DIR
        / f"{video_id}.json"
    )

    if not path.exists():
        return True

    try:
        return (
            load_json(path, {})
            .get("version", 0)
            < INDEX_VERSION
        )
    except Exception:
        return True


def rebuild_catalog(videos):
    indexed = []

    for video in videos:
        p = (
            INDEX_DIR
            / f'{video["videoId"]}.json'
        )

        if p.exists():
            indexed.append({
                "videoId":
                    video["videoId"],
                "title":
                    video.get("title", ""),
                "channel":
                    video.get("channel", ""),
                "channelName":
                    video.get("channelName", ""),
                "publishedAt":
                    video.get("publishedAt", ""),
                "url":
                    video.get("url", ""),
            })

    save_json(
        CATALOG_FILE,
        {
            "version": INDEX_VERSION,
            "indexedVideos": len(indexed),
            "videos": indexed,
        }
    )


def shard_for_gram(gram):
    return (
        sum(ord(ch) for ch in gram)
        % GLOBAL_SHARDS
    )


def rebuild_global_index(videos):
    video_map = {
        v.get("videoId"): v
        for v in videos
        if v.get("videoId")
    }

    shards = [
        {}
        for _ in range(GLOBAL_SHARDS)
    ]

    indexed_count = 0

    for vid in video_map:
        path = (
            INDEX_DIR
            / f"{vid}.json"
        )

        if not path.exists():
            continue

        try:
            idx = load_json(path, {})
        except Exception:
            continue

        if (
            idx.get("version", 0)
            < INDEX_VERSION
        ):
            continue

        indexed_count += 1

        postings = idx.get(
            "postings",
            {}
        )

        for n in ("2", "3"):
            for gram, deltas in (
                postings
                .get(n, {})
                .items()
            ):
                shard = shards[
                    shard_for_gram(gram)
                ]

                key = f"{n}:{gram}"

                shard.setdefault(
                    key,
                    {}
                )[vid] = deltas

    GLOBAL_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    for old in GLOBAL_DIR.glob("*.json"):
        if old.name != "manifest.json":
            old.unlink()

    nonempty = []

    for i, data in enumerate(shards):
        if not data:
            continue

        name = f"{i:03d}.json"

        save_json(
            GLOBAL_DIR / name,
            {
                "version": 1,
                "postings": data,
            }
        )

        nonempty.append(i)

    save_json(
        GLOBAL_MANIFEST,
        {
            "version": 1,
            "shards": GLOBAL_SHARDS,
            "indexedVideos": indexed_count,
            "nonemptyShards": nonempty,
        }
    )

    print(
        "Saved global search index: "
        f"{indexed_count} videos / "
        f"{len(nonempty)} shards"
    )


def record_retry(
    retry_status,
    video_id,
    reason
):
    item = retry_status.setdefault(
        video_id,
        {
            "attempts": 0,
            "lastResult": ""
        }
    )

    item["attempts"] = (
        int(item.get("attempts", 0))
        + 1
    )

    item["lastResult"] = reason


def main():
    data = load_json(
        VIDEOS_FILE,
        {}
    )

    videos = data.get(
        "videos",
        []
    )

    status = (
        load_json(
            STATUS_FILE,
            {}
        )
        .get("videos", {})
    )

    retry_data = load_json(
        RETRY_STATUS_FILE,
        {
            "version": 1,
            "videos": {}
        }
    )

    retry_status = retry_data.get(
        "videos",
        {}
    )

    catalog_videos = (
        videos
        +
        [
            v
            for v in SPECIAL_VIDEOS
            if v["videoId"]
            not in {
                x.get("videoId")
                for x in videos
            }
        ]
    )

    # まだインデックスが完成していない
    # 動画だけを対象にする。
    eligible = []

    for video in catalog_videos:
        vid = video.get("videoId")

        if not vid:
            continue

        is_special = (
            vid in {
                x["videoId"]
                for x in SPECIAL_VIDEOS
            }
        )

        if (
            not is_special
            and status
            .get(vid, {})
            .get("status")
            != "success"
        ):
            continue

        if needs_rebuild(vid):
            eligible.append(video)

    # 試行回数が少ない動画を優先。
    # 同じ回数なら古い動画から。
    eligible.sort(
        key=lambda v: (
            retry_status
            .get(
                v["videoId"],
                {}
            )
            .get("attempts", 0),

            v.get("publishedAt")
            or "9999",

            v.get("videoId")
            or ""
        )
    )

    candidates = (
        eligible[
            :MAX_VIDEOS_PER_RUN
        ]
    )

    print(
        "Recovery mode: enabled"
    )

    print(
        "Total source videos: "
        f"{len(catalog_videos)}"
    )

    print(
        "Still missing indexes: "
        f"{len(eligible)}"
    )

    print(
        "Indexes to try this run: "
        f"{len(candidates)}"
    )

    processed = 0
    indexed_this_run = 0
    temporary_errors = 0
    parse_failures = 0
    rate_limited = False

    for i, video in enumerate(
        candidates,
        1
    ):
        vid = video["videoId"]

        previous_attempts = (
            retry_status
            .get(
                vid,
                {}
            )
            .get("attempts", 0)
        )

        print(
            f"[{i}/{len(candidates)}] "
            f"attempt {previous_attempts + 1} / "
            f"{video.get('channelName')} / "
            f"{video.get('title')}"
        )

        try:
            raw = fetch_transcript(vid)

            if is_rate_limit_message(raw):
                print(
                    "  rate limited by "
                    "transcript provider"
                )

                print(
                    "  stopping this run "
                    "to avoid further requests"
                )

                rate_limited = True
                break

            rows = parse_transcript(raw)

            if not rows:
                preview = (
                    raw[:180]
                    .replace("\n", " ")
                )

                print(
                    "  transcript could "
                    "not be parsed; "
                    "will retry in a "
                    "future run"
                )

                print(
                    "  transcript chars: "
                    f"{len(raw)}"
                )

                print(
                    "  transcript preview: "
                    f"{preview}"
                )

                record_retry(
                    retry_status,
                    vid,
                    "parse_failure"
                )

                parse_failures += 1

            else:
                save_json(
                    INDEX_DIR
                    / f"{vid}.json",
                    build_video_index(
                        video,
                        rows
                    )
                )

                # 成功したら失敗履歴から削除。
                retry_status.pop(
                    vid,
                    None
                )

                indexed_this_run += 1

                print(
                    f"  indexed "
                    f"v{INDEX_VERSION}: "
                    f"{len(rows)} "
                    "transcript sections"
                )

            processed += 1

        except urllib.error.HTTPError as e:

            if e.code == 429:
                print(
                    "  HTTP 429: "
                    "rate limited by "
                    "transcript provider"
                )

                print(
                    "  stopping this run "
                    "to avoid further requests"
                )

                rate_limited = True
                break

            print(
                f"  HTTP {e.code}; "
                "will retry in a "
                "future run"
            )

            record_retry(
                retry_status,
                vid,
                f"http_{e.code}"
            )

            temporary_errors += 1
            processed += 1

        except Exception as e:
            print(
                "  error: "
                f"{str(e)[:300]}; "
                "will retry in a "
                "future run"
            )

            record_retry(
                retry_status,
                vid,
                "temporary_error"
            )

            temporary_errors += 1
            processed += 1

        save_json(
            RETRY_STATUS_FILE,
            {
                "version": 1,
                "videos": retry_status
            }
        )

        if i < len(candidates):
            time.sleep(
                REQUEST_INTERVAL
            )

    save_json(
        RETRY_STATUS_FILE,
        {
            "version": 1,
            "videos": retry_status
        }
    )

    rebuild_catalog(
        catalog_videos
    )

    rebuild_global_index(
        catalog_videos
    )

    # この実行後に残っている本数を再計算。
    remaining = sum(
        1
        for video in catalog_videos
        if (
            video.get("videoId")
            and needs_rebuild(
                video["videoId"]
            )
            and (
                video["videoId"]
                in {
                    x["videoId"]
                    for x in SPECIAL_VIDEOS
                }
                or status
                .get(
                    video["videoId"],
                    {}
                )
                .get("status")
                == "success"
            )
        )
    )

    retry_counts = {}

    for item in retry_status.values():
        count = int(
            item.get("attempts", 0)
        )

        retry_counts[count] = (
            retry_counts.get(
                count,
                0
            )
            + 1
        )

    print("")
    print("Recovery summary:")

    print(
        f"  processed: {processed}"
    )

    print(
        "  indexed this run: "
        f"{indexed_this_run}"
    )

    print(
        "  parse failures: "
        f"{parse_failures}"
    )

    print(
        "  temporary HTTP/errors: "
        f"{temporary_errors}"
    )

    print(
        "  rate limited: "
        f"{rate_limited}"
    )

    print(
        "  indexes still missing: "
        f"{remaining}"
    )

    if retry_counts:
        print(
            "  retry history:"
        )

        for count in sorted(
            retry_counts
        ):
            print(
                f"    failed {count} "
                f"time(s): "
                f"{retry_counts[count]} "
                "video(s)"
            )

    else:
        print(
            "  retry history: none"
        )

    if remaining == 0:
        print("")
        print(
            "ALL ELIGIBLE VIDEOS "
            "ARE INDEXED."
        )

    print(
        f"Saved {CATALOG_FILE}"
    )

    print(
        f"Saved {RETRY_STATUS_FILE}"
    )


if __name__ == "__main__":
    main()

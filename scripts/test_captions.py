"""Manual five-video caption retrieval test; does not modify existing indexes."""
import json
from pathlib import Path
from youtube_transcript_api import YouTubeTranscriptApi

ROOT = Path('data')
CAPTIONS = ROOT / 'search-index' / 'captions'
LIMIT = 5
SPECIAL = ['4pw9GhX85WQ', 'T65Ct4b1Myk']

def load(path):
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding='utf-8'))

def main():
    videos = load(ROOT / 'videos.json').get('videos', [])
    status = load(ROOT / 'transcript_status.json').get('videos', {})
    known = {v.get('videoId') for v in videos}
    candidates = SPECIAL + [v['videoId'] for v in videos
                            if v.get('videoId') and v['videoId'] not in SPECIAL
                            and status.get(v['videoId'], {}).get('status') == 'success']
    selected = []
    for vid in candidates:
        if vid not in selected and not (CAPTIONS / f'{vid}.json').exists():
            selected.append(vid)
        if len(selected) == LIMIT:
            break
    api = YouTubeTranscriptApi()
    successes = 0
    for i, vid in enumerate(selected, 1):
        print(f'[{i}/{len(selected)}] {vid}', flush=True)
        try:
            transcript = api.fetch(vid, languages=['ja'])
            rows = [{'t': int(s.start), 'x': s.text.strip()}
                    for s in transcript if s.text.strip()]
            if not rows:
                print('  No caption rows', flush=True)
                continue
            CAPTIONS.mkdir(parents=True, exist_ok=True)
            (CAPTIONS / f'{vid}.json').write_text(
                json.dumps({'version': 1, 'videoId': vid, 'rows': rows},
                           ensure_ascii=False, separators=(',', ':')),
                encoding='utf-8')
            successes += 1
            print(f'  Saved {len(rows)} rows', flush=True)
        except Exception as exc:
            print(f'  {type(exc).__name__}: {str(exc)[:300]}', flush=True)
    print(f'Result: {successes}/{len(selected)} saved', flush=True)

if __name__ == '__main__':
    main()

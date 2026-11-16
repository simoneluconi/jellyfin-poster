import os
import re
import sys
import time
import random
import threading

import requests
from flask import Flask, render_template, jsonify, request
from dotenv import load_dotenv

import title_detection

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)

# Avoid crashes when emoji logs are written to a non-UTF-8 stream (e.g. Windows service / redirected output)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def env_bool(name, default):
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


# --- CONFIGURATION ---
JELLYFIN_URL = (os.getenv("JELLYFIN_URL") or "").rstrip("/")
API_KEY = os.getenv("JELLYFIN_API_KEY")
PORT = int(os.getenv("PORT", 5000))
DEBUG_MODE = env_bool("DEBUG", False)
# Title under the poster: "auto" hides it when the movie's logo is already printed on the poster,
# "true" always shows it, "false" never does
_title_env = os.getenv("SHOW_TITLE", "auto").strip().lower()
TITLE_MODE = ("show" if _title_env in ("1", "true", "yes", "on") else
              "hide" if _title_env in ("0", "false", "no", "off") else "auto")
# Use the movie's title logo (from Jellyfin) instead of the text title, when available
SHOW_LOGOS = env_bool("SHOW_LOGOS", True)
# When enabled, multiple connected screens never show the same movie at the same time
SYNC_SCREENS = env_bool("SYNC_SCREENS", False)
# Seconds each poster stays on screen in carousel mode
POSTER_DURATION = max(5, int(os.getenv("POSTER_DURATION", 30)))

# A screen that hasn't contacted the server for this long is considered disconnected
SCREEN_TIMEOUT = 90
SESSIONS_CACHE_TTL = 2
ITEM_CACHE_TTL = 600
ITEM_FIELDS = 'Overview,Taglines,MediaSources,MediaStreams,OfficialRating,ProductionYear'
PLAYABLE_TYPES = ('Movie', 'Episode')

if not JELLYFIN_URL or not API_KEY:
    print("❌ ERROR: JELLYFIN_URL or JELLYFIN_API_KEY not found in .env file.")
    sys.exit(1)


def log(msg):
    """Print logs to console"""
    print(f"[POSTER] {msg}", flush=True)


if TITLE_MODE == "auto" and not title_detection.AVAILABLE:
    log("⚠️  SHOW_TITLE=auto needs opencv-python-headless (pip install -r requirements.txt). Showing titles.")
    TITLE_MODE = "show"


def get_headers():
    # Full MediaBrowser header: newer Jellyfin servers reject X-Emby-Token / token-only variants
    return {
        'Authorization': (
            'MediaBrowser Client="Jellyfin Poster", Device="Poster Server", '
            f'DeviceId="jellyfin-poster", Version="1.0.0", Token="{API_KEY}"'
        ),
        'Content-Type': 'application/json',
    }


def jellyfin_get(path, params=None, timeout=10):
    r = requests.get(f"{JELLYFIN_URL}{path}", headers=get_headers(), params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


# --- USER ---

CACHED_USER_ID = None


def get_automatic_user_id():
    """Automatically detects the first available User ID"""
    global CACHED_USER_ID
    if CACHED_USER_ID:
        return CACHED_USER_ID
    try:
        users = jellyfin_get("/Users", timeout=5)
        if users:
            CACHED_USER_ID = users[0]['Id']
            return CACHED_USER_ID
    except Exception as e:
        log(f"Error finding user: {e}")
    return None


# --- HELPERS ---

def safe_get(data, path, default=None):
    """Safely access nested dictionary keys"""
    try:
        for key in path:
            data = data[key]
        return data if data is not None else default
    except (KeyError, IndexError, TypeError):
        return default


def ticks_to_string(ticks):
    """Convert Jellyfin ticks to H:MM:SS format"""
    if not ticks:
        return "0:00"
    seconds = int(ticks / 10_000_000)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def get_streams(item):
    return safe_get(item, ['MediaSources', 0, 'MediaStreams']) or item.get('MediaStreams') or []


def get_resolution(streams):
    video = next((s for s in streams if s.get('Type') == 'Video'), None)
    if not video:
        return ""
    width, height = video.get('Width') or 0, video.get('Height') or 0
    if width >= 3800 or height >= 2100:
        return "4K UHD"
    if width >= 1900 or height >= 1000:
        return "1080p"
    if width >= 1200 or height >= 700:
        return "720p"
    if width > 0:
        return "SD"
    return ""


AUDIO_CODECS = {
    'truehd': 'TrueHD', 'eac3': 'Dolby Digital+', 'ac3': 'Dolby Digital', 'dts': 'DTS',
    'aac': 'AAC', 'flac': 'FLAC', 'opus': 'Opus', 'mp3': 'MP3', 'vorbis': 'Vorbis',
}
CHANNELS = {1: 'Mono', 2: 'Stereo', 6: '5.1', 8: '7.1'}


def get_audio(streams):
    audio = [s for s in streams if s.get('Type') == 'Audio']
    if not audio:
        return ""
    stream = next((s for s in audio if s.get('IsDefault')), audio[0])

    codec = (stream.get('Codec') or '').lower()
    profile = stream.get('Profile') or ''
    name = AUDIO_CODECS.get(codec, codec.upper())
    if codec == 'dts' and profile.startswith('DTS-HD'):
        name = profile  # "DTS-HD MA" / "DTS-HD HRA"
    if 'atmos' in f"{profile} {stream.get('DisplayTitle', '')}".lower():
        name += ' Atmos'

    layout = re.sub(r'\(.*\)', '', stream.get('ChannelLayout') or '').strip()
    if not layout or layout.lower() == 'stereo':
        layout = CHANNELS.get(stream.get('Channels'), layout)
    if layout.lower() == 'stereo':
        layout = 'Stereo'
    return f"{name} {layout}".strip()


def format_duration(ticks):
    mins = int((ticks or 0) / 600_000_000)
    if mins <= 0:
        return ""
    return f"{mins // 60}h {mins % 60}m" if mins >= 60 else f"{mins}m"


def image_url(item_id, image_type, tag=None, **params):
    query = "&".join(f"{k}={v}" for k, v in params.items())
    if tag:
        query += f"&tag={tag}"
    return f"{JELLYFIN_URL}/Items/{item_id}/Images/{image_type}?{query}"


def detection_key(poster_id, poster_tag, logo_id, logo_tag):
    # Image tags change whenever an image is replaced in Jellyfin, so a new poster/logo is re-analysed
    return f"{poster_id}:{poster_tag}:{logo_id}:{logo_tag}"


def logo_printed_on_poster(poster_id, poster_tag, logo_id, logo_tag):
    """True/False if the logo was (not) found on the poster, None if unknown."""
    return title_detection.logo_in_poster(
        detection_key(poster_id, poster_tag, logo_id, logo_tag),
        image_url(poster_id, 'Primary', poster_tag, maxHeight=title_detection.POSTER_HEIGHT, quality=90),
        image_url(logo_id, 'Logo', logo_tag, maxWidth=1000),
        headers=get_headers(),
    )


def should_show_title(poster_id, poster_tag, logo_id, logo_tag):
    if TITLE_MODE != "auto":
        return TITLE_MODE == "show"
    if not logo_id or not poster_tag:
        return True  # nothing to compare the poster with
    return logo_printed_on_poster(poster_id, poster_tag, logo_id, logo_tag) is not True


def format_media_data(item, is_playing=False, is_paused=False, position_ticks=0):
    """Formats the raw Jellyfin data into a clean dictionary for the frontend"""
    is_episode = item.get('Type') == 'Episode'
    streams = get_streams(item)
    total_ticks = item.get('RunTimeTicks') or 0

    # Poster: for episodes use the series poster (episode "Primary" is a 16:9 screenshot)
    poster_id, poster_tag = item['Id'], safe_get(item, ['ImageTags', 'Primary'])
    if is_episode and item.get('SeriesId') and item.get('SeriesPrimaryImageTag'):
        poster_id, poster_tag = item['SeriesId'], item['SeriesPrimaryImageTag']

    # Title logo: the item's own logo, or the parent's (series logo for episodes)
    logo_id, logo_tag = None, None
    if safe_get(item, ['ImageTags', 'Logo']):
        logo_id, logo_tag = item['Id'], item['ImageTags']['Logo']
    elif item.get('ParentLogoItemId') and item.get('ParentLogoImageTag'):
        logo_id, logo_tag = item['ParentLogoItemId'], item['ParentLogoImageTag']

    show_title = should_show_title(poster_id, poster_tag, logo_id, logo_tag)
    logo_url = None
    if show_title and SHOW_LOGOS and logo_id:
        logo_url = image_url(logo_id, 'Logo', logo_tag, maxWidth=1600, quality=90)

    title = item.get('Name', 'Untitled')
    tagline = safe_get(item, ['Taglines', 0], default="")
    episode_info = ""  # shown below the title (series name first, then which episode)
    if is_episode:
        title = item.get('SeriesName') or title
        season, episode = item.get('ParentIndexNumber'), item.get('IndexNumber')
        code = f"S{season:02d}E{episode:02d}" if season is not None and episode is not None else ""
        episode_info = " · ".join(p for p in (code, item.get('Name', '')) if p)
        tagline = ""

    progress = (position_ticks / total_ticks) * 100 if total_ticks > 0 else 0

    return {
        'id': item['Id'],
        'mode': 'playing' if is_playing else 'random',
        'paused': is_paused,
        'title': title,
        'year': item.get('ProductionYear') or '',
        'tagline': tagline,
        'episode_info': episode_info,
        'overview': item.get('Overview', ''),
        'rating': item.get('OfficialRating', ''),
        'duration_badge': format_duration(total_ticks),
        'resolution': get_resolution(streams),
        'audio': get_audio(streams),
        'image_url': image_url(poster_id, 'Primary', poster_tag, maxHeight=3840, quality=90),
        'logo_url': logo_url,
        'show_title': show_title,

        # Player specific data
        'progress_percent': min(progress, 100),
        'time_current': ticks_to_string(position_ticks),
        'time_total': ticks_to_string(total_ticks),
    }


# --- SCREEN REGISTRY (used to keep multiple screens in sync) ---

_screens = {}  # screen_id -> {'item_id': str, 'title': str, 'seen': float}
_screens_lock = threading.Lock()
_SCREEN_ID_RE = re.compile(r'^[A-Za-z0-9_-]{1,64}$')


def get_screen_id():
    screen = request.args.get('screen', '')
    return screen if _SCREEN_ID_RE.match(screen) else None


def _purge_stale_screens(now):
    for sid in [sid for sid, s in _screens.items() if now - s['seen'] > SCREEN_TIMEOUT]:
        log(f"🖥️  Screen {sid[:8]} disconnected")
        del _screens[sid]


def touch_screen(screen_id):
    if not screen_id:
        return
    with _screens_lock:
        if screen_id in _screens:
            _screens[screen_id]['seen'] = time.time()


def items_on_other_screens(screen_id):
    """Item IDs currently displayed by other active screens (call with lock held)."""
    _purge_stale_screens(time.time())
    return {s['item_id'] for sid, s in _screens.items() if sid != screen_id}


def register_screen(screen_id, data):
    """Record which item a screen is showing (call with lock held)."""
    if not screen_id:
        return
    previous = _screens.get(screen_id)
    if previous is None:
        log(f"🖥️  Screen {screen_id[:8]} connected ({len(_screens) + 1} active)")
    if previous is None or previous['item_id'] != data['id']:
        prefix = "⚡ NOW PLAYING" if data['mode'] == 'playing' else "🎬"
        log(f"{prefix} [{screen_id[:8]}] {data['title']}")
    _screens[screen_id] = {'item_id': data['id'], 'title': data['title'], 'seen': time.time()}


def release_screen(screen_id):
    with _screens_lock:
        if _screens.pop(screen_id, None):
            log(f"🖥️  Screen {screen_id[:8]} disconnected")


# --- JELLYFIN DATA ---

_sessions_cache = {'ts': 0, 'data': []}
_item_cache = {}  # item_id -> (ts, item)


def get_sessions():
    """Jellyfin sessions, cached briefly so many polling screens don't hammer the server."""
    now = time.time()
    if now - _sessions_cache['ts'] > SESSIONS_CACHE_TTL:
        _sessions_cache['data'] = jellyfin_get("/Sessions", timeout=5)
        _sessions_cache['ts'] = now
    return _sessions_cache['data']


def get_item_details(item):
    """NowPlayingItem from /Sessions lacks fields like Taglines/MediaSources: fetch the full item."""
    item_id = item['Id']
    cached = _item_cache.get(item_id)
    if cached and time.time() - cached[0] < ITEM_CACHE_TTL:
        return cached[1]
    try:
        result = jellyfin_get("/Items", params={
            'userId': get_automatic_user_id(), 'ids': item_id, 'Fields': ITEM_FIELDS,
        }, timeout=5)
        full = (result.get('Items') or [item])[0]
    except Exception as e:
        log(f"Item details error: {e}")
        return item
    if len(_item_cache) > 200:
        _item_cache.clear()
    _item_cache[item_id] = (time.time(), full)
    return full


def get_now_playing_list():
    """All movies/episodes currently being played, as formatted data."""
    playing = []
    try:
        for session in get_sessions():
            item = session.get('NowPlayingItem')
            if not item or item.get('Type') not in PLAYABLE_TYPES:
                continue
            play_state = session.get('PlayState') or {}
            playing.append(format_media_data(
                get_item_details(item),
                is_playing=True,
                is_paused=bool(play_state.get('IsPaused')),
                position_ticks=play_state.get('PositionTicks') or 0,
            ))
    except Exception as e:
        log(f"Session Error: {e}")
    return playing


def pick_now_playing(screen_id):
    """Choose a playing item for this screen; with sync on, skip items shown on other screens."""
    playing = get_now_playing_list()
    if not playing:
        return None
    with _screens_lock:
        if SYNC_SCREENS and screen_id:
            taken = items_on_other_screens(screen_id)
            playing = [p for p in playing if p['id'] not in taken]
            if not playing:
                return None
        choice = playing[0]
        register_screen(screen_id, choice)
    return choice


def get_random_candidates():
    user_id = get_automatic_user_id()
    if not user_id:
        return []
    params = {
        'userId': user_id,
        'IncludeItemTypes': 'Movie',
        'Recursive': 'true',
        'Fields': ITEM_FIELDS,
        'SortBy': 'Random',
        'Limit': 20,
        'ImageTypes': 'Primary',
    }
    try:
        return jellyfin_get("/Items", params=params).get('Items', [])
    except Exception as e:
        log(f"API Error: {e}")
        return []


def pick_random(screen_id):
    candidates = get_random_candidates()
    if not candidates:
        return None
    with _screens_lock:
        own = safe_get(_screens, [screen_id, 'item_id']) if screen_id else None
        others = items_on_other_screens(screen_id) if SYNC_SCREENS and screen_id else set()
        # Preference: a different movie not shown elsewhere > same movie again > a duplicate
        available = ([c for c in candidates if c['Id'] not in others and c['Id'] != own]
                     or [c for c in candidates if c['Id'] not in others])
        if not available:
            log("⚠️  Not enough movies to keep every screen unique")
            available = candidates
        item = random.choice(available)
        register_screen(screen_id, {'id': item['Id'], 'title': item.get('Name', ''), 'mode': 'random'})
    # Formatting may analyse the poster (first time only): do it outside the lock
    return format_media_data(item)


def warm_title_detection():
    """Background: analyse every movie's poster/logo once, so 'auto' titles never delay a screen."""
    user_id = get_automatic_user_id()
    if not user_id:
        return
    try:
        items = jellyfin_get("/Items", params={
            'userId': user_id, 'IncludeItemTypes': 'Movie', 'Recursive': 'true', 'ImageTypes': 'Primary,Logo',
        }, timeout=30).get('Items', [])
    except Exception as e:
        log(f"Title detection warm-up error: {e}")
        return
    pending = [i for i in items if title_detection.cached_score(detection_key(
        i['Id'], i['ImageTags'].get('Primary'), i['Id'], i['ImageTags'].get('Logo'))) is None]
    if pending:
        log(f"🔎 Title detection: analysing {len(pending)} posters in background...")
    found = 0
    for i in items:
        tags = i['ImageTags']
        if logo_printed_on_poster(i['Id'], tags.get('Primary'), i['Id'], tags.get('Logo')):
            found += 1
    log(f"🔎 Title detection: logo found on {found}/{len(items)} posters (title hidden on those)")


# --- ROUTES ---

@app.route('/')
def index():
    return render_template('poster.html', poster_duration_ms=POSTER_DURATION * 1000)


@app.route('/api/next')
def next_poster():
    screen_id = get_screen_id()
    data = pick_now_playing(screen_id) or pick_random(screen_id)
    return jsonify(data) if data else (jsonify({'error': 'No data available'}), 503)


@app.route('/api/now-playing')
def now_playing():
    """Lightweight poll used during the carousel to switch to Now Playing immediately."""
    screen_id = get_screen_id()
    touch_screen(screen_id)
    data = pick_now_playing(screen_id)
    return jsonify(data or {'mode': 'idle'})


@app.route('/api/release', methods=['POST'])
def release():
    screen_id = get_screen_id()
    if screen_id:
        release_screen(screen_id)
    return ('', 204)


if __name__ == '__main__':
    log("--- STARTING POSTER SERVER ---")
    log(f"Target: {JELLYFIN_URL}")
    log(f"Title: {TITLE_MODE.upper()} | Title logos: {'ON' if SHOW_LOGOS else 'OFF'} | "
        f"Screen sync: {'ON' if SYNC_SCREENS else 'OFF'} | Poster duration: {POSTER_DURATION}s")
    user = get_automatic_user_id()
    if user:
        log(f"User ID detected: {user}")
    else:
        log("⚠️  Could not detect user yet (Jellyfin unreachable?). Will retry on first request.")
    # With the debug reloader this file runs twice: only the serving child should warm the cache
    serving_process = not DEBUG_MODE or os.environ.get('WERKZEUG_RUN_MAIN') == 'true'
    if TITLE_MODE == "auto" and user and serving_process:
        threading.Thread(target=warm_title_detection, daemon=True).start()
    app.run(host='0.0.0.0', port=PORT, debug=DEBUG_MODE, threaded=True)

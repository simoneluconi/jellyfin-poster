import os
import random
import requests
from flask import Flask, render_template, jsonify
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)

# --- CONFIGURATION ---
JELLYFIN_URL = os.getenv("JELLYFIN_URL")
API_KEY = os.getenv("JELLYFIN_API_KEY")
PORT = int(os.getenv("PORT", 5000))
DEBUG_MODE = os.getenv("DEBUG", "True").lower() == "true"

# Global variable to cache the User ID
CACHED_USER_ID = None

if not JELLYFIN_URL or not API_KEY:
    print("❌ ERROR: JELLYFIN_URL or JELLYFIN_API_KEY not found in .env file.")
    exit(1)

def log(msg):
    """Print logs to console"""
    print(f"[POSTER] {msg}", flush=True)

def get_headers():
    return {'X-Emby-Token': API_KEY, 'Content-Type': 'application/json'}

def get_automatic_user_id():
    """Automatically detects the first available User ID"""
    global CACHED_USER_ID
    if CACHED_USER_ID: return CACHED_USER_ID
    try:
        r = requests.get(f"{JELLYFIN_URL}/Users", headers=get_headers(), timeout=5)
        if r.status_code == 200 and len(r.json()) > 0:
            CACHED_USER_ID = r.json()[0]['Id']
            return CACHED_USER_ID
    except Exception as e:
        log(f"Error finding user: {e}")
    return None

def safe_get(data, path, default=None):
    """Safely access nested dictionary keys"""
    try:
        for key in path: data = data[key]
        return data
    except: return default

def ticks_to_string(ticks):
    """Convert Jellyfin ticks to H:MM:SS format"""
    if not ticks: return "0:00"
    seconds = int(ticks / 10000000)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"

def format_media_data(item, is_playing=False, progress_percent=0, current_ticks=0, total_ticks=0):
    """Formats the raw Jellyfin data into a clean dictionary for the frontend"""
    
    # 1. Resolution Logic
    width = safe_get(item, ['MediaSources', 0, 'MediaStreams', 0, 'Width'], default=0)
    resolution = "SD"
    if width >= 3800: resolution = "4K UHD"
    elif width >= 1900: resolution = "1080p"
    elif width >= 1200: resolution = "720p"
    elif width > 0: resolution = "HD"

    # 2. Audio Logic
    audio_display = "Stereo"
    media_streams = safe_get(item, ['MediaSources', 0, 'MediaStreams'], default=[])
    if media_streams:
        for stream in media_streams:
            if stream.get('Type') == 'Audio':
                audio_display = stream.get('DisplayTitle', 'Stereo').split(' ')[0]
                break

    # 3. Duration Logic
    mins = int(item.get('RunTimeTicks', 0) / 600000000)
    duration_badge = f"{mins // 60}h {mins % 60}m"

    return {
        'mode': 'playing' if is_playing else 'random',
        'title': item.get('Name', 'Untitled'),
        'year': item.get('ProductionYear', ''),
        'tagline': safe_get(item, ['Taglines', 0], default=""),
        'overview': item.get('Overview', ''),
        'rating': item.get('OfficialRating', 'NR'),
        'duration_badge': duration_badge,
        'resolution': resolution,
        'audio': audio_display,
        'image_url': f"{JELLYFIN_URL}/Items/{item['Id']}/Images/Primary?maxHeight=3840&quality=90",
        
        # Player specific data
        'progress_percent': progress_percent,
        'time_current': ticks_to_string(current_ticks),
        'time_total': ticks_to_string(total_ticks)
    }

def get_now_playing_item():
    """Checks if anything is currently playing"""
    url = f"{JELLYFIN_URL}/Sessions"
    try:
        r = requests.get(url, headers=get_headers(), timeout=5)
        sessions = r.json()
        for session in sessions:
            if session.get('NowPlayingItem'):
                item = session['NowPlayingItem']
                play_state = session.get('PlayState', {})
                position_ticks = play_state.get('PositionTicks', 0)
                total_ticks = item.get('RunTimeTicks', 1)
                
                percent = 0
                if total_ticks > 0: percent = (position_ticks / total_ticks) * 100
                
                log(f"⚡ NOW PLAYING: {item.get('Name')}")
                return format_media_data(
                    item, 
                    is_playing=True, 
                    progress_percent=percent,
                    current_ticks=position_ticks,
                    total_ticks=total_ticks
                )
    except Exception as e:
        log(f"Session Error: {e}")
    return None

def get_random_media():
    """Gets a random movie or checks for now playing"""
    # Priority: Now Playing
    playing = get_now_playing_item()
    if playing: return playing

    # Fallback: Random Item
    headers = get_headers()
    user_id = get_automatic_user_id()
    if not user_id: return None

    url = f"{JELLYFIN_URL}/Users/{user_id}/Items"
    params = {
        'IncludeItemTypes': 'Movie',
        'Recursive': 'true',
        'Fields': 'Overview,Taglines,MediaSources,OfficialRating,RunTimeTicks,ProductionYear',
        'SortBy': 'Random',
        'Limit': 20,
        'ImageTypes': 'Primary'
    }

    try:
        r = requests.get(url, headers=headers, params=params, timeout=10)
        items = r.json().get('Items', [])
        if not items: return None
        item = random.choice(items)
        return format_media_data(item, is_playing=False)
    except Exception as e:
        log(f"API Error: {e}")
        return None

# --- ROUTES ---

@app.route('/')
def index():
    return render_template('poster.html')

@app.route('/api/next')
def next_poster():
    data = get_random_media()
    return jsonify(data) if data else (jsonify({'error': 'No data available'}), 500)

if __name__ == '__main__':
    log("--- STARTING POSTER SERVER ---")
    log(f"Target: {JELLYFIN_URL}")
    user = get_automatic_user_id()
    if user:
        log(f"User ID detected: {user}")
        app.run(host='0.0.0.0', port=PORT, debug=DEBUG_MODE)
    else:
        log("❌ CRITICAL: Could not detect user. Check connection.")
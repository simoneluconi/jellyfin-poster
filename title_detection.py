"""Detects whether a movie's title logo is already printed on its poster.

The logo image from Jellyfin is searched inside the poster with SIFT feature matching plus a RANSAC
similarity fit (scale + rotation + translation). Five variants of the logo are tried (colors on black,
colors on white, inverted, and the alpha silhouette in both polarities), so a logo whose colors differ
from the poster's title still matches by shape.

Results are cached on disk, keyed by the image tags, so each poster/logo pair is analysed only once.
"""
import json
import os
import threading

import requests

try:
    import cv2
    import numpy as np
    AVAILABLE = True
except ImportError:  # opencv not installed: callers fall back to always showing the title
    AVAILABLE = False

# Inlier count above which the logo counts as "found". In testing, unrelated logo/poster pairs and
# textless posters peaked at 8, while posters showing the logo artwork typically score 10 to 500+.
MATCH_THRESHOLD = 10

POSTER_HEIGHT = 1200
LOGO_WIDTH = 700

CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cache', 'title_detection.json')

_lock = threading.Lock()        # OpenCV objects are not shared across threads; serialize analysis
_cache_lock = threading.Lock()
_cache = None


# --- Cache ---

def _load_cache():
    global _cache
    if _cache is None:
        try:
            with open(CACHE_FILE, encoding='utf-8') as f:
                _cache = json.load(f)
        except (OSError, ValueError):
            _cache = {}
    return _cache


def _save_cache():
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    tmp = CACHE_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(_cache, f)
    os.replace(tmp, CACHE_FILE)


def cached_score(key):
    with _cache_lock:
        return _load_cache().get(key)


# --- Image analysis ---

def _decode(data, flags):
    img = cv2.imdecode(np.frombuffer(data, np.uint8), flags)
    if img is None:
        raise ValueError("unreadable image")
    return img


def _poster_features(sift, data):
    img = _decode(data, cv2.IMREAD_GRAYSCALE)
    s = POSTER_HEIGHT / img.shape[0]
    img = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    kp, des = sift.detectAndCompute(img, None)
    return img.shape, kp, des


def _logo_variants(sift, data):
    rgba = _decode(data, cv2.IMREAD_UNCHANGED)
    if rgba.ndim == 2:
        rgba = cv2.cvtColor(rgba, cv2.COLOR_GRAY2BGRA)
    elif rgba.shape[2] == 3:
        rgba = cv2.cvtColor(rgba, cv2.COLOR_BGR2BGRA)
    s = LOGO_WIDTH / rgba.shape[1]
    rgba = cv2.resize(rgba, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    alpha = rgba[:, :, 3].astype(np.float32) / 255
    gray = cv2.cvtColor(rgba[:, :, :3], cv2.COLOR_BGR2GRAY).astype(np.float32)
    on_black = (gray * alpha).astype(np.uint8)
    on_white = (gray * alpha + 255 * (1 - alpha)).astype(np.uint8)
    shape = (alpha * 255).astype(np.uint8)
    mask = cv2.dilate((alpha > 0.05).astype(np.uint8) * 255, np.ones((7, 7), np.uint8))
    variants = []
    for img in (on_black, on_white, 255 - on_black, shape, 255 - shape):
        kp, des = sift.detectAndCompute(img, mask)
        variants.append((img.shape, kp, des))
    return variants


def _match(matcher, variant, poster):
    (lh, lw), kp_l, des_l = variant
    (ph, pw), kp_p, des_p = poster
    if des_l is None or des_p is None or len(kp_l) < 8 or len(kp_p) < 8:
        return 0
    good = [m for m, n in (p for p in matcher.knnMatch(des_l, des_p, k=2) if len(p) == 2)
            if m.distance < 0.75 * n.distance]
    if len(good) < 8:
        return 0
    src = np.float32([kp_l[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([kp_p[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    M, inliers = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=6.0,
                                             maxIters=4000, confidence=0.995)
    if M is None:
        return 0
    # Reject geometrically implausible fits: tilted too much, too small/large, or outside the poster
    scale = float(np.hypot(M[0, 0], M[1, 0]))
    angle = float(np.degrees(np.arctan2(M[1, 0], M[0, 0])))
    quad = cv2.transform(np.float32([[0, 0], [lw, 0], [lw, lh], [0, lh]]).reshape(-1, 1, 2), M).reshape(-1, 2)
    inside = (quad[:, 0].min() > -0.1 * pw and quad[:, 0].max() < 1.1 * pw and
              quad[:, 1].min() > -0.1 * ph and quad[:, 1].max() < 1.1 * ph)
    if not (inside and abs(angle) <= 40 and 0.12 <= lw * scale / pw <= 1.15):
        return 0
    return int(inliers.sum())


def analyse(poster_bytes, logo_bytes):
    """Inlier score of the logo inside the poster (0 = not found)."""
    with _lock:
        sift = cv2.SIFT_create(nfeatures=4000)
        matcher = cv2.BFMatcher(cv2.NORM_L2)
        poster = _poster_features(sift, poster_bytes)
        return max(_match(matcher, v, poster) for v in _logo_variants(sift, logo_bytes))


# --- Public API ---

def logo_in_poster(key, poster_url, logo_url, headers=None):
    """True if the logo is printed on the poster. Downloads and analyses on first call, then cached.
    Returns None if it can't be determined (detection unavailable or download/analysis error)."""
    if not AVAILABLE:
        return None
    score = cached_score(key)
    if score is None:
        try:
            poster = requests.get(poster_url, headers=headers, timeout=15)
            logo = requests.get(logo_url, headers=headers, timeout=15)
            poster.raise_for_status()
            logo.raise_for_status()
            score = analyse(poster.content, logo.content)
        except Exception:
            return None
        with _cache_lock:
            _load_cache()[key] = score
            _save_cache()
    return score >= MATCH_THRESHOLD

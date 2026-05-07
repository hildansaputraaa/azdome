# code by github.com/hildansaputraaa
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import urllib.request, urllib.error, json, os, gzip, socket, time, hashlib, glob
import subprocess, shutil, threading, tempfile, secrets, base64
from urllib.parse import urlparse, parse_qs
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# === DB CONFIG ===
DB_HOST = os.environ.get("DB_HOST", "mysql")
DB_PORT = int(os.environ.get("DB_PORT", 3306))
DB_USER = os.environ.get("DB_USER", "azdome")
DB_PASS = os.environ.get("DB_PASS", "azdome123")
DB_NAME = os.environ.get("DB_NAME", "azdome")

# === CONSTANTS ===
AZDOME_LOGIN_URL = "http://community-app.lulushun.net:8901/community-app/api/app-user/login"
API_BASE   = "http://lingdu-ap.lulushun.net:8801"
MEDIA_BASE = "http://lingdu-ap.lulushun.net:8803"
PORT       = int(os.environ.get("PORT", 8899))
DASHBOARD  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "azdome-dashboard.html")
ADMIN_PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "azdome-admin.html")
SESSION_TTL = 86400

FAVICON = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
DEFAULT_THUMBNAIL = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"

# === DATABASE ===
class Database:
    def __init__(self):
        self._lock = threading.Lock()
        self._conn = None
        self._connect()

    def _connect(self):
        try:
            import mysql.connector
            if self._conn:
                try: self._conn.close()
                except: pass
            self._conn = mysql.connector.connect(
                host=DB_HOST, port=DB_PORT, user=DB_USER,
                password=DB_PASS, database=DB_NAME,
                autocommit=True, connection_timeout=10
            )
            print(f"[DB] Connected to {DB_HOST}:{DB_PORT}/{DB_NAME}")
        except ImportError:
            print("[DB] ERROR: pip install mysql-connector-python")
            self._conn = None
        except Exception as e:
            print(f"[DB] Connection failed: {e}")
            self._conn = None

    def _ping(self):
        if not self._conn:
            self._connect(); return
        try:
            self._conn.ping(reconnect=True, attempts=3, delay=1)
        except:
            self._connect()

    def query(self, sql, params=None):
        with self._lock:
            self._ping()
            if not self._conn: raise Exception("DB tidak tersedia")
            cur = self._conn.cursor(dictionary=True)
            cur.execute(sql, params or ())
            try: return cur.fetchall()
            except: return []

    def query_one(self, sql, params=None):
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql, params=None):
        with self._lock:
            self._ping()
            if not self._conn: raise Exception("DB tidak tersedia")
            cur = self._conn.cursor()
            cur.execute(sql, params or ())
            return cur.lastrowid

    def get_config(self, key):
        row = self.query_one("SELECT config_value FROM azdome_config WHERE config_key=%s", (key,))
        return row["config_value"] if row else None

    def set_config(self, key, value):
        self.execute(
            "INSERT INTO azdome_config (config_key,config_value) VALUES (%s,%s) "
            "ON DUPLICATE KEY UPDATE config_value=%s, updated_at=NOW()",
            (key, value, value)
        )

# === PASSWORD UTILS ===
def hash_password(password):
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000).hex()
    return f"{salt}:{h}"

def verify_password(password, stored):
    try:
        salt, h = stored.split(":", 1)
        nh = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000).hex()
        return secrets.compare_digest(nh, h)
    except:
        return False

# === JWT UTILS ===
def decode_jwt_payload(token):
    try:
        payload = token.split(".")[1]
        payload += "=" * (4 - len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except:
        return {}

def get_token_expiry(token):
    return decode_jwt_payload(token).get("exp")

def token_seconds_left(token):
    exp = get_token_expiry(token)
    return (exp - time.time()) if exp else None


# === TOKEN MANAGER (auto-refresh) ===
class TokenManager:
    def __init__(self, db):
        self.db = db
        self._token = None
        self._lock = threading.Lock()
        self._load_from_db()
        threading.Thread(target=self._refresh_loop, daemon=True).start()

    def _load_from_db(self):
        t = self.db.get_config("azdome_token")
        with self._lock:
            self._token = t
        if t:
            exp = get_token_expiry(t)
            secs = token_seconds_left(t)
            if secs is not None:
                hrs = secs / 3600
                print(f"[TOKEN] Loaded from DB, expires in {hrs:.1f}h (exp={exp})")
            else:
                print("[TOKEN] Loaded from DB (no expiry info)")

    def get_token(self):
        with self._lock:
            return self._token

    def _refresh_loop(self):
        while True:
            time.sleep(300)  # cek tiap 5 menit
            try:
                self._check_and_refresh()
            except Exception as e:
                print(f"[TOKEN] refresh_loop error: {e}")

    def _check_and_refresh(self):
        with self._lock:
            token = self._token
        if not token:
            return
        secs = token_seconds_left(token)
        if secs is None:
            return
        if secs < 3600:
            print(f"[TOKEN] Expires in {secs:.0f}s — auto refreshing...")
            self._do_refresh()

    def _do_refresh(self):
        email = self.db.get_config("azdome_email")
        pwd   = self.db.get_config("azdome_password")
        if not email or not pwd:
            print("[TOKEN] No credentials stored, cannot refresh")
            return False
        _, new_token = azdome_api_login(email, pwd)
        if new_token:
            exp = get_token_expiry(new_token)
            self.db.set_config("azdome_token", new_token)
            self.db.set_config("azdome_token_expires_at", str(exp) if exp else None)
            with self._lock:
                self._token = new_token
            print(f"[TOKEN] Refreshed OK, new exp={exp}")
            return True
        print("[TOKEN] Refresh FAILED")
        return False

    def set_token_from_login(self, token):
        exp = get_token_expiry(token)
        self.db.set_config("azdome_token", token)
        self.db.set_config("azdome_token_expires_at", str(exp) if exp else None)
        with self._lock:
            self._token = token

    def get_status(self):
        with self._lock:
            token = self._token
        if not token:
            return {"has_token": False}
        exp = get_token_expiry(token)
        secs = token_seconds_left(token)
        return {
            "has_token": True,
            "token_preview": token[:30] + "...",
            "expires_at": exp,
            "seconds_left": secs,
            "is_expired": (secs is not None and secs <= 0),
        }

    def get_full_token(self):
        with self._lock:
            return self._token

# === DB INSTANCE (global, lazy init) ===
db = None
token_manager = None
db_lock = threading.Lock()

def get_db():
    global db
    if db is None:
        with db_lock:
            if db is None:
                db = Database()
    return db

def get_tm():
    global token_manager
    if token_manager is None:
        with db_lock:
            if token_manager is None:
                token_manager = TokenManager(get_db())
    return token_manager

# === ENSURE DEFAULT ADMIN ===
def ensure_default_admin(database):
    try:
        database.execute("""
            CREATE TABLE IF NOT EXISTS activity_logs (
              id            INT AUTO_INCREMENT PRIMARY KEY,
              user_id       INT,
              email         VARCHAR(128) NOT NULL,
              full_name     VARCHAR(128),
              role          VARCHAR(32),
              action        VARCHAR(128) NOT NULL,
              details       TEXT,
              ip_address    VARCHAR(64),
              user_agent    TEXT,
              created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
              FOREIGN KEY (user_id) REFERENCES internal_users(id) ON DELETE SET NULL
            )
        """)
        row = database.query_one("SELECT COUNT(*) as cnt FROM internal_users")
        if row and row["cnt"] == 0:
            ph = hash_password("Admin@1234")
            database.execute(
                "INSERT INTO internal_users (email, password_hash, role, full_name) VALUES (%s,%s,%s,%s)",
                ("admin@aldzama.com", ph, "admin", "Administrator")
            )
            print("[AUTH] Default admin created: admin@aldzama.com / Admin@1234")
    except Exception as e:
        print(f"[AUTH] ensure_default_admin error: {e}")


# === SESSION (DB-backed) ===
def session_create(database, user_id, role, full_name, email):
    # Hapus session lama (single session enforcement)
    database.execute("DELETE FROM internal_sessions WHERE email=%s", (email,))
    token = secrets.token_hex(32)
    expires = time.time() + SESSION_TTL
    import datetime
    exp_dt = datetime.datetime.fromtimestamp(expires).strftime("%Y-%m-%d %H:%M:%S")
    database.execute(
        "INSERT INTO internal_sessions (token, user_id, role, full_name, email, expires_at) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (token, user_id, role, full_name, email, exp_dt)
    )
    # Update last_login
    database.execute(
        "UPDATE internal_users SET last_login=NOW() WHERE id=%s", (user_id,)
    )
    print(f"[AUTH] Session created for {email} role={role}")
    return token

def session_get(database, token):
    row = database.query_one(
        "SELECT * FROM internal_sessions WHERE token=%s AND expires_at > NOW()", (token,)
    )
    return row

def session_delete(database, token):
    database.execute("DELETE FROM internal_sessions WHERE token=%s", (token,))

# === AZDOME API LOGIN ===
def azdome_api_login(email, password):
    try:
        password_md5 = hashlib.md5(password.encode()).hexdigest()
        payload = {
            "account": email, "password": password_md5,
            "appVersion": "3.8.0.172", "fcmToken": "azdome_dashboard",
            "lingduRegion": "ap", "osVersion": "35",
            "phoneOs": "Web", "registrationId": ""
        }
        req = urllib.request.Request(
            AZDOME_LOGIN_URL, data=json.dumps(payload).encode(), method="POST",
            headers={
                "Content-Type": "application/json; charset=UTF-8",
                "App-Key": "AZDOME", "Accept-Language": "id",
                "Accept-Encoding": "gzip", "User-Agent": "okhttp/3.14.9"
            }
        )
        resp = urllib.request.urlopen(req, timeout=10)
        raw = resp.read()
        if resp.info().get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        result = json.loads(raw)
        if result.get("code") == 200 and result.get("data", {}).get("user"):
            data = result["data"]
            tok = data.get("token")
            print(f"[AZDOME] Login sukses, token: {tok[:20]}...")
            return (data, tok)
        print(f"[AZDOME] Login failed: {result.get('message','?')}")
        return (None, None)
    except Exception as e:
        print(f"[AZDOME] Login error: {e}")
        return (None, None)


# === FFPROBE ===
def probe_stream(stream_url):
    result = {"has_video": False, "video_codec": None, "has_audio": False, "audio_codec": None, "error": None}
    try:
        url_lower = (stream_url or "").lower()
        is_rtsp = url_lower.startswith("rtsp://")
        pf = ["-analyzeduration", "5000000", "-probesize", "5000000"]
        if is_rtsp: pf += ["-rtsp_transport", "tcp", "-timeout", "10000000"]
        else: pf += ["-timeout", "10000000"]
        cmd = ["ffprobe", "-v", "error"] + pf + ["-i", stream_url, "-show_streams", "-select_streams", "v:0", "-of", "json"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if r.stdout:
            try:
                for s in json.loads(r.stdout).get("streams", []):
                    if s.get("codec_type") == "video":
                        result["has_video"] = True; result["video_codec"] = s.get("codec_name", "unknown")
            except: pass
        cmd2 = ["ffprobe", "-v", "error"] + pf + ["-i", stream_url, "-show_streams", "-select_streams", "a:0", "-of", "json"]
        r2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=15)
        if r2.stdout:
            try:
                for s in json.loads(r2.stdout).get("streams", []):
                    if s.get("codec_type") == "audio":
                        result["has_audio"] = True; result["audio_codec"] = s.get("codec_name", "unknown")
            except: pass
        if r.stderr: result["error"] = r.stderr[:500]
    except subprocess.TimeoutExpired: result["error"] = "ffprobe timeout"
    except Exception as e: result["error"] = str(e)
    return result

def _get_input_flags(stream_url):
    u = (stream_url or "").lower()
    if u.startswith("rtsp://"):
        return ["-fflags", "+genpts+igndts", "-err_detect", "ignore_err", "-analyzeduration", "5000000", "-probesize", "5000000", "-rtsp_transport", "tcp", "-timeout", "15000000"]
    elif u.startswith("rtmp://") or u.startswith("rtmps://"):
        return ["-fflags", "+genpts+igndts", "-err_detect", "ignore_err", "-analyzeduration", "5000000", "-probesize", "5000000"]
    return ["-fflags", "+genpts+igndts", "-err_detect", "ignore_err", "-analyzeduration", "5000000", "-probesize", "5000000", "-f", "flv"]

def build_ffmpeg_cmd(stream_url, hls_dir, m3u8, probe_info):
    has_audio = probe_info.get("has_audio", True)
    audio_codec = (probe_info.get("audio_codec") or "").lower()
    BAD = {"pcm_mulaw","pcm_alaw","g726","g711","g722","g723","g729","adpcm_g722","adpcm_g726","adpcm_ima_wav","adpcm_ms","opus","vorbis","mp3","mp2","amr_nb","amr_wb","unknown",""}
    cmd = ["ffmpeg", "-loglevel", "warning"] + _get_input_flags(stream_url) + ["-i", stream_url, "-map", "0:v:0", "-c:v", "copy", "-muxdelay", "0", "-muxpreload", "0"]
    if not has_audio:
        cmd += ["-an"]
    else:
        cmd += ["-map", "0:a:0?", "-c:a", "aac", "-b:a", "64k", "-ar", "44100", "-ac", "2", "-af", "aresample=async=1:min_hard_comp=0.100000:first_pts=0"]
    cmd += ["-max_muxing_queue_size", "2048", "-f", "hls", "-hls_time", "2", "-hls_list_size", "6", "-hls_flags", "delete_segments+append_list", "-hls_segment_type", "mpegts", "-hls_allow_cache", "0", "-start_number", "0", "-hls_segment_filename", os.path.join(hls_dir, "seg%03d.ts"), m3u8]
    return cmd

# === HLS SESSION ===
hls_sessions = {}
hls_lock = threading.Lock()

def hls_cleanup():
    while True:
        time.sleep(10)
        with hls_lock:
            dead = [k for k,v in hls_sessions.items() if time.time()-v["ts"]>60]
            for k in dead:
                s = hls_sessions.pop(k)
                try: s["proc"].kill(); shutil.rmtree(s["dir"], ignore_errors=True)
                except: pass

threading.Thread(target=hls_cleanup, daemon=True).start()

def start_hls(stream_url, session_id, probe_info=None):
    with hls_lock:
        if session_id in hls_sessions:
            old = hls_sessions[session_id]
            try: old["proc"].kill(); shutil.rmtree(old["dir"], ignore_errors=True)
            except: pass
        hls_dir = tempfile.mkdtemp(prefix="azdome_hls_")
        m3u8 = os.path.join(hls_dir, "live.m3u8")
        if probe_info is None: probe_info = {}
        cmd = build_ffmpeg_cmd(stream_url, hls_dir, m3u8, probe_info)
        log_path = os.path.join(hls_dir, "ffmpeg.log")
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=open(log_path, "wb"))
        hls_sessions[session_id] = {"proc": proc, "dir": hls_dir, "url": stream_url, "m3u8": m3u8, "log": log_path, "ts": time.time(), "ready": False, "probe": probe_info}
        def mark_ready():
            for i in range(50):
                if _check_m3u8_ready(hls_dir, m3u8, session_id): return
                with hls_lock:
                    s = hls_sessions.get(session_id)
                    if s and s["proc"].poll() is not None: _print_ffmpeg_log(log_path, session_id, s["proc"].returncode); return
                time.sleep(0.1)
            for i in range(100):
                if _check_m3u8_ready(hls_dir, m3u8, session_id): return
                with hls_lock:
                    s = hls_sessions.get(session_id)
                    if s and s["proc"].poll() is not None: _print_ffmpeg_log(log_path, session_id, s["proc"].returncode); return
                time.sleep(0.25)
        threading.Thread(target=mark_ready, daemon=True).start()
        return hls_dir, m3u8

def _check_m3u8_ready(hls_dir, m3u8, session_id):
    if not os.path.exists(m3u8) or os.path.getsize(m3u8) == 0: return False
    ts_files = [f for f in os.listdir(hls_dir) if f.endswith(".ts")]
    if not ts_files: return False
    try:
        with open(m3u8, "r") as f: content = f.read()
        if not ("#EXTM3U" in content and "#EXTINF" in content and ".ts" in content): return False
        if not any(os.path.getsize(os.path.join(hls_dir, f)) > 1024 for f in ts_files): return False
        with hls_lock:
            if session_id in hls_sessions: hls_sessions[session_id]["ready"] = True
        return True
    except: return False

def _print_ffmpeg_log(log_path, session_id, returncode):
    print(f"[HLS] FFmpeg died for {session_id} (code={returncode})")
    try:
        with open(log_path, "r", errors="replace") as lf: print(f"[FFmpeg]:\n{lf.read()[-2000:]}")
    except: pass

def _try_fallback_no_audio(stream_url, session_id, hls_dir, m3u8, log_path, probe_info=None):
    try:
        if os.path.exists(m3u8): os.remove(m3u8)
        for seg in glob.glob(os.path.join(hls_dir, "seg*.ts")):
            try: os.remove(seg)
            except: pass
    except: pass
    fb = dict(probe_info) if probe_info else {}
    fb["has_audio"] = False
    if not fb.get("video_codec"): fb["video_codec"] = "h264"
    cmd = build_ffmpeg_cmd(stream_url, hls_dir, m3u8, fb)
    lf = open(log_path, "ab"); lf.write(b"\n--- FALLBACK NO-AUDIO ---\n")
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=lf)
    with hls_lock:
        if session_id in hls_sessions: hls_sessions[session_id]["proc"] = proc; hls_sessions[session_id]["ready"] = False
    for i in range(100):
        if _check_m3u8_ready(hls_dir, m3u8, session_id): return True
        with hls_lock:
            s = hls_sessions.get(session_id)
            if s and s["proc"].poll() is not None: return False
        time.sleep(0.25)
    return False

def _try_fallback_force_decode(stream_url, session_id, hls_dir, m3u8, log_path, probe_info=None):
    try:
        if os.path.exists(m3u8): os.remove(m3u8)
        for seg in glob.glob(os.path.join(hls_dir, "seg*.ts")):
            try: os.remove(seg)
            except: pass
    except: pass
    bf = _get_input_flags(stream_url)
    cmd = ["ffmpeg", "-loglevel", "warning"] + bf + ["-skip_frame", "noref", "-i", stream_url, "-an", "-map", "0:v:0", "-c:v", "libx264", "-profile:v", "baseline", "-level:v", "3.1", "-pix_fmt", "yuv420p", "-b:v", "1500k", "-maxrate:v", "2000k", "-bufsize:v", "4000k", "-preset", "veryfast", "-tune", "zerolatency", "-g", "30", "-keyint_min", "30", "-sc_threshold", "0", "-vsync", "cfr", "-r", "15", "-muxdelay", "0", "-muxpreload", "0", "-max_muxing_queue_size", "4096", "-f", "hls", "-hls_time", "2", "-hls_list_size", "6", "-hls_flags", "delete_segments+append_list", "-hls_segment_type", "mpegts", "-hls_allow_cache", "0", "-start_number", "0", "-hls_segment_filename", os.path.join(hls_dir, "seg%03d.ts"), m3u8]
    lf = open(log_path, "ab"); lf.write(b"\n--- FALLBACK FORCE-DECODE ---\n")
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=lf)
    with hls_lock:
        if session_id in hls_sessions: hls_sessions[session_id]["proc"] = proc; hls_sessions[session_id]["ready"] = False
    for i in range(100):
        if _check_m3u8_ready(hls_dir, m3u8, session_id): return True
        with hls_lock:
            s = hls_sessions.get(session_id)
            if s and s["proc"].poll() is not None: return False
        time.sleep(0.25)
    return False


# === API CALL (uses token manager) ===
def api_call(path, method="GET", body=None, timeout=15, retries=2):
    url = API_BASE + path
    token = get_tm().get_token()
    if not token:
        print(f"[API] WARNING: No token available for {path}")
        return None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Authorization", token)
        req.add_header("App-Key", "AZDOME")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept-Encoding", "gzip")
        req.add_header("User-Agent", "okhttp/3.14.9")
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
            raw = resp.read()
            if resp.info().get("Content-Encoding") == "gzip": raw = gzip.decompress(raw)
            return json.loads(raw)
        except urllib.error.HTTPError as e:
            if e.code == 503 and attempt < retries:
                time.sleep((attempt + 1) * 2); continue
            print(f"[API] HTTP {e.code} for {path}")
            return None
        except Exception as e:
            print(f"[API] Error {path}: {e}")
            return None
    return None

def wait_camera_ready(device_sn, send_event, max_wait=120):
    send_event("progress", "Menunggu kamera menyala...")
    for i in range(max_wait // 3):
        elapsed = (i + 1) * 3
        if i % 5 == 0 and i > 0:
            try: api_call(f"/lingdu-app/api/user-device/device-wakeup?imei={device_sn}")
            except: pass
        try:
            r = api_call(f"/lingdu-app/api/monitoring/scan-channel?deviceSn={device_sn}")
            if r is not None:
                data = r.get("data")
                if data is not None:
                    send_event("progress", f"Kamera merespons! ({elapsed}s)")
                    return True
                else:
                    send_event("progress", f"Menunggu kamera siap... ({elapsed}s)")
                    send_event("ping", "keep-alive")
            else:
                send_event("progress", f"Kamera belum merespons... ({elapsed}s)")
                send_event("ping", "keep-alive")
        except Exception as e:
            print(f"[STREAM] scan-channel error: {e}")
        time.sleep(3)
    return False

def verify_stream_has_data(url, send_event, timeout=8):
    try:
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "Mozilla/5.0")
        resp = urllib.request.urlopen(req, timeout=timeout)
        chunk = resp.read(4096)
        if len(chunk) > 100:
            send_event("progress", f"Stream data OK ({len(chunk)} bytes)")
            return True
        return False
    except Exception as e:
        send_event("progress", f"Stream tidak bisa diakses: {e}")
        return False

def get_stream_url_sse(device_sn, channel, send_event, retries=3):
    send_event("progress", "Membangunkan kamera...")
    try: api_call(f"/lingdu-app/api/user-device/device-wakeup?imei={device_sn}")
    except: pass
    time.sleep(1)
    if not wait_camera_ready(device_sn, send_event):
        send_event("progress", "Kamera tidak merespons setelah 120 detik")
        return None
    for attempt in range(retries):
        send_event("progress", f"[{attempt+1}/{retries}] Memulai siaran live...")
        body = json.dumps({"channelNo": int(channel), "deviceSn": device_sn, "mediaType": 0, "streamType": 1, "zlmEnable": 1}).encode()
        try: api_call("/lingdu-app/api/monitoring/start-live", "POST", body)
        except: pass
        time.sleep(2)
        try:
            r = api_call(f"/lingdu-app/api/monitoring/stream-url?deviceSn={device_sn}&channelNo={channel}&zlmEnable=1")
            if not r: time.sleep(3); continue
            data = r.get("data")
            if not data: time.sleep(3); continue
            send_event("progress", "URL ditemukan, memverifikasi stream...")
            if verify_stream_has_data(data, send_event):
                send_event("progress", "Stream siap! Memulai HLS encoder...")
                return data
            time.sleep(3)
        except Exception as e:
            send_event("progress", f"Error: {e}"); time.sleep(3)
    return None


# === LOGIN PAGE HTML ===
LOGIN_HTML = b"""<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Aldzama - Login</title>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<link rel="icon" type="image/svg+xml" href="/media/favicon.svg">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{min-height:100vh;background:linear-gradient(135deg,#0f172a 0%,#1e3a5f 50%,#0f172a 100%);display:flex;align-items:center;justify-content:center;font-family:'Plus Jakarta Sans',sans-serif;}
.card{background:white;border-radius:20px;padding:40px;width:100%;max-width:400px;box-shadow:0 25px 60px rgba(0,0,0,.4);}
.logo{display:flex;align-items:center;justify-content:center;flex-direction:column;gap:8px;margin-bottom:32px;}
.logo-text p{font-size:12px;color:#6b7280;font-weight:500;}
.form-group{margin-bottom:18px;}
label{display:block;font-size:12px;font-weight:700;color:#374151;margin-bottom:6px;text-transform:uppercase;letter-spacing:.5px;}
input{width:100%;padding:11px 14px;border:1.5px solid #e4e7ec;border-radius:10px;font-size:14px;font-family:inherit;color:#111827;transition:border-color .15s;outline:none;}
input:focus{border-color:#1d6fe8;box-shadow:0 0 0 3px rgba(29,111,232,.1);}
.btn{width:100%;padding:13px;background:linear-gradient(135deg,#1d6fe8,#0ea5e9);color:white;border:none;border-radius:10px;font-size:15px;font-weight:700;cursor:pointer;font-family:inherit;transition:all .15s;margin-top:6px;}
.btn:hover{transform:translateY(-1px);box-shadow:0 6px 20px rgba(29,111,232,.4);}
.error{background:#fee2e2;color:#dc2626;padding:10px 14px;border-radius:8px;font-size:13px;font-weight:600;margin-bottom:16px;display:none;}
.error.show{display:block;}
.note{font-size:11px;color:#6b7280;margin-top:16px;padding:10px;background:#f3f4f6;border-radius:8px;line-height:1.5;}
.footer{text-align:center;margin-top:24px;font-size:11px;color:#9ca3af;}
</style></head>
<body>
<div class="card">
  <div class="logo">
    <img src="/media/logo_aldzama_transparan.png" alt="Aldzama" style="height:70px;width:auto;max-width:100%;display:block;margin:0 auto;">
    <div class="logo-text"><p>Fleet Monitor System</p></div>
  </div>
  <div class="error" id="err"></div>
  <div class="form-group"><label>Email Perusahaan</label><input type="email" id="email" placeholder="email@aldzama.com" autocomplete="email"></div>
  <div class="form-group"><label>Password</label><input type="password" id="pw" placeholder="Password" autocomplete="current-password"></div>
  <button class="btn" id="btn" onclick="doLogin()">Masuk</button>
  <div class="note">&#128274; Login menggunakan akun internal perusahaan yang dibuat oleh admin.</div>
  <div class="footer">Aldzama Fleet Monitor &copy; 2025</div>
</div>
<script>
document.addEventListener("keydown",e=>{if(e.key==="Enter")doLogin();});
async function doLogin(){
  const email=document.getElementById("email").value.trim();
  const p=document.getElementById("pw").value;
  const err=document.getElementById("err"),btn=document.getElementById("btn");
  if(!email||!p){err.textContent="Email dan password wajib diisi";err.classList.add("show");return;}
  btn.textContent="Memverifikasi...";btn.disabled=true;err.classList.remove("show");
  try{
    const r=await fetch("/auth/login",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({email,password:p})});
    const d=await r.json();
    if(d.ok){window.location.href="/";}
    else{err.textContent=d.msg||"Login gagal";err.classList.add("show");btn.textContent="Masuk";btn.disabled=false;}
  }catch{err.textContent="Server tidak dapat dihubungi";err.classList.add("show");btn.textContent="Masuk";btn.disabled=false;}
}
</script></body></html>"""

FORBIDDEN_HTML = b"""<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Aldzama - Akses Ditolak</title>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<link rel="icon" type="image/svg+xml" href="/media/favicon.svg">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{min-height:100vh;background:linear-gradient(135deg,#0f172a 0%,#1e3a5f 50%,#0f172a 100%);display:flex;align-items:center;justify-content:center;font-family:'Plus Jakarta Sans',sans-serif;}
.card{background:white;border-radius:20px;padding:40px;width:100%;max-width:400px;box-shadow:0 25px 60px rgba(0,0,0,.4);text-align:center;}
.icon{font-size:48px;margin-bottom:20px;}
.title{font-size:24px;font-weight:700;color:#111827;margin-bottom:12px;}
.desc{font-size:14px;color:#6b7280;margin-bottom:24px;line-height:1.5;}
.btn{display:inline-block;width:100%;padding:13px;background:linear-gradient(135deg,#1d6fe8,#0ea5e9);color:white;text-decoration:none;border:none;border-radius:10px;font-size:15px;font-weight:700;cursor:pointer;font-family:inherit;transition:all .15s;}
.btn:hover{transform:translateY(-1px);box-shadow:0 6px 20px rgba(29,111,232,.4);}
</style></head>
<body>
<div class="card">
  <div class="icon">&#128274;</div>
  <div class="title">Akses Ditolak</div>
  <div class="desc">Anda tidak memiliki izin (admin) untuk mengakses halaman ini.</div>
  <a href="/" class="btn">Kembali ke Dashboard</a>
</div>
</body></html>"""

# === HTTP HANDLER ===

class Handler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(200); self._cors(); self.end_headers()

    def _get_token(self):
        for part in self.headers.get("Cookie","").split(";"):
            part = part.strip()
            if part.startswith("azdome_session="):
                return part[len("azdome_session="):]
        return None

    def _require_auth(self, roles=None):
        token = self._get_token()
        if not token: self._redirect("/login"); return None
        try:
            s = session_get(get_db(), token)
        except Exception as e:
            print(f"[AUTH] session_get error: {e}")
            self._redirect("/login"); return None
        if not s: self._redirect("/login"); return None
        if roles and s.get("role") not in roles:
            self._json(403, {"ok": False, "msg": "Akses ditolak"}); return None
        return s

    def do_GET(self):
        p = urlparse(self.path)
        path = p.path
        qs = parse_qs(p.query)

        if path == "/login":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(LOGIN_HTML)))
            self.end_headers(); self.wfile.write(LOGIN_HTML); return

        if path == "/auth/logout":
            token = self._get_token()
            if token:
                try: session_delete(get_db(), token)
                except: pass
            self.send_response(302)
            self.send_header("Location", "/login")
            self.send_header("Set-Cookie", "azdome_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict")
            self.end_headers(); return

        if path in ("/", "/dashboard"):
            if not self._require_auth(): return
            self._serve_html(DASHBOARD); return

        if path == "/admin":
            user = self._require_auth()
            if not user: return
            if user.get("role") != "admin":
                self.send_response(403)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(FORBIDDEN_HTML)))
                self.end_headers()
                self.wfile.write(FORBIDDEN_HTML)
                return
            self._serve_html(ADMIN_PAGE); return

        if path == "/favicon.ico":
            self.send_response(302); self.send_header("Location", "/media/favicon.svg"); self.end_headers(); return

        if path.startswith("/media/"):
            local_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), path.lstrip("/"))
            if os.path.exists(local_path) and os.path.isfile(local_path):
                self._serve_file(local_path, os.path.basename(local_path)); return
            self._proxy_media(path); return

        user = self._require_auth()
        if not user: return

        if path == "/auth/me":
            self._json(200, {"ok": True, "user": {
                "name": user.get("full_name",""),
                "email": user.get("email",""),
                "role": user.get("role","")
            }}); return

        if path.startswith("/hls/"):
            parts = path.split("/")
            if len(parts) >= 4:
                sid = parts[2]; fname = parts[3]
                with hls_lock:
                    s = hls_sessions.get(sid)
                if s:
                    s["ts"] = time.time()
                    self._serve_file(os.path.join(s["dir"], fname), fname); return
            self.send_response(404); self._cors(); self.end_headers(); return

        if path == "/debug/hls-log":
            sn = qs.get("sn",[""])[0]; ch = qs.get("ch",["1"])[0]; sid = f"{sn}_{ch}"
            with hls_lock: s = hls_sessions.get(sid)
            if not s: self._json(404, {"ok": False, "msg": f"Session {sid} tidak ditemukan"}); return
            try:
                with open(s["log"], "r", errors="replace") as f: log_content = f.read()
                self._json(200, {"ok": True, "session_id": sid, "ffmpeg_log": log_content[-3000:], "proc_alive": s["proc"].poll() is None, "ready": s.get("ready",False)})
            except Exception as e: self._json(500, {"ok": False, "msg": str(e)})
            return

        if path == "/debug/probe":
            sn = qs.get("sn",[""])[0]; ch = qs.get("ch",["1"])[0]
            if not sn: self._json(400, {"ok": False, "msg": "sn required"}); return
            r = api_call(f"/lingdu-app/api/monitoring/stream-url?deviceSn={sn}&channelNo={ch}&zlmEnable=1")
            if not r or not r.get("data"): self._json(500, {"ok": False, "msg": "Gagal ambil URL stream"}); return
            self._json(200, {"ok": True, "stream_url": r["data"][:80]+"...", "probe": probe_stream(r["data"])}); return

        # === ADMIN API ===
        if path == "/admin/token-status":
            if not self._require_auth(roles=["admin"]): return
            status = get_tm().get_status()
            self._json(200, {"ok": True, "status": status, "azdome_email": get_db().get_config("azdome_email") or ""}); return

        if path == "/admin/token-full":
            u2 = self._require_auth(roles=["admin"])
            if not u2: return
            self._json(200, {"ok": True, "token": get_tm().get_full_token() or ""}); return

        if path == "/admin/users":
            if not self._require_auth(roles=["admin"]): return
            rows = get_db().query("SELECT id,email,role,full_name,is_active,last_login,created_at FROM internal_users ORDER BY created_at")
            for r in rows:
                if r.get("last_login"): r["last_login"] = str(r["last_login"])
                if r.get("created_at"): r["created_at"] = str(r["created_at"])
            self._json(200, {"ok": True, "users": rows}); return

        if path == "/admin/logs":
            if not self._require_auth(roles=["admin"]): return
            rows = get_db().query("SELECT id,email,full_name,role,action,details,ip_address,user_agent,created_at FROM activity_logs ORDER BY created_at DESC LIMIT 100")
            for r in rows:
                if r.get("created_at"): r["created_at"] = str(r["created_at"])
            self._json(200, {"ok": True, "logs": rows}); return

        if path == "/get-stream":
            sn = qs.get("sn",[""])[0]; ch = qs.get("ch",["1"])[0]
            if not sn: self._json(400,{"ok":False,"msg":"sn required"}); return
            self.send_response(200); self._cors()
            self.send_header("Content-Type","text/event-stream")
            self.send_header("Cache-Control","no-cache, no-transform")
            self.send_header("Connection","keep-alive")
            self.send_header("X-Accel-Buffering","no")
            self.end_headers()
            self.wfile.write(b": connection established\n\n"); self.wfile.flush()

            def send_event(etype, data):
                try:
                    msg = data if isinstance(data, str) else json.dumps(data)
                    self.wfile.write(f"event: {etype}\ndata: {msg}\n\n".encode())
                    self.wfile.flush()
                except Exception as e: print(f"[SSE] error: {e}")

            try:
                print(f"[STREAM] START: sn={sn}, ch={ch}")
                stream_url = get_stream_url_sse(sn, ch, send_event)
                if stream_url:
                    sid_key = f"{sn}_{ch}"
                    send_event("progress","Mendeteksi codec kamera...")
                    probe_info = probe_stream(stream_url)
                    send_event("progress",f"Codec: video={probe_info.get('video_codec','?')}, audio={probe_info.get('audio_codec','?')}")
                    start_hls(stream_url, sid_key, probe_info=probe_info)
                    send_event("progress","Menunggu segmen HLS pertama...")
                    ready = False
                    for i in range(350):
                        with hls_lock: ready = hls_sessions.get(sid_key,{}).get("ready",False)
                        if ready: break
                        if i > 0 and i % 30 == 0: send_event("progress",f"Encoder berjalan... ({i*0.1:.0f}s)")
                        with hls_lock: s_info = hls_sessions.get(sid_key,{})
                        if s_info.get("proc") and s_info["proc"].poll() is not None:
                            rc = s_info["proc"].returncode
                            _print_ffmpeg_log(s_info.get("log",""), sid_key, rc)
                            send_event("progress",f"Encoder gagal (exit={rc}). Mencoba fallback tanpa audio...")
                            with hls_lock: s_fb = hls_sessions.get(sid_key,{})
                            if _try_fallback_no_audio(stream_url, sid_key, s_fb.get("dir",""), s_fb.get("m3u8",""), s_fb.get("log",""), probe_info=probe_info):
                                send_event("progress","Fallback berhasil! (tanpa audio)"); ready = True; break
                            send_event("progress","Mencoba force-decode video...")
                            with hls_lock: s_fb2 = hls_sessions.get(sid_key,{})
                            if _try_fallback_force_decode(stream_url, sid_key, s_fb2.get("dir",""), s_fb2.get("m3u8",""), s_fb2.get("log",""), probe_info=probe_info):
                                send_event("progress","Force-decode berhasil!"); ready = True; break
                            send_event("done",{"ok":False,"msg":f"Encoder gagal. Cek /debug/hls-log?sn={sn}&ch={ch}"}); return
                        time.sleep(0.1)
                    with hls_lock: s_info = hls_sessions.get(sid_key,{})
                    m3u8_path = s_info.get("m3u8","")
                    try:
                        with open(m3u8_path,"r") as f: content = f.read()
                        if "#EXTM3U" in content and "#EXTINF" in content:
                            send_event("done",{"ok":True,"hls":f"/hls/{sid_key}/live.m3u8"})
                        else:
                            send_event("done",{"ok":False,"msg":"Stream tidak valid"})
                    except: send_event("done",{"ok":False,"msg":"Stream timeout"})
                else:
                    send_event("done",{"ok":False,"msg":"Kamera offline atau tidak merespons"})
            except Exception as e:
                print(f"[STREAM] ERROR: {e}"); send_event("done",{"ok":False,"msg":f"Error: {e}"})
            return

        if path == "/stop-stream":
            sn = qs.get("sn",[""])[0]; ch = qs.get("ch",["1"])[0]; sid = f"{sn}_{ch}"
            with hls_lock:
                if sid in hls_sessions:
                    s = hls_sessions.pop(sid)
                    try:
                        if s.get("proc"): s["proc"].kill()
                        if s.get("dir"): shutil.rmtree(s["dir"], ignore_errors=True)
                    except: pass
            self._json(200,{"ok":True}); return

        self._proxy_api("GET")

    def do_POST(self):
        p = urlparse(self.path); path = p.path
        n = int(self.headers.get("Content-Length",0))
        body = json.loads(self.rfile.read(n)) if n else {}

        if path == "/auth/login":
            email = body.get("email","").strip()
            password = body.get("password","")
            if not email or not password:
                self._json(400,{"ok":False,"msg":"Email dan password wajib diisi"}); return
            try:
                u = get_db().query_one("SELECT * FROM internal_users WHERE email=%s AND is_active=1", (email,))
            except Exception as e:
                self._json(500,{"ok":False,"msg":"Database error. Pastikan DB sudah tersambung."}); return
            if not u or not verify_password(password, u.get("password_hash","")):
                self._json(401,{"ok":False,"msg":"Email atau password salah"}); return
            token = session_create(get_db(), u["id"], u["role"], u.get("full_name",""), email)
            
            ip_addr = self.client_address[0]
            user_agent = self.headers.get("User-Agent", "")
            try:
                get_db().execute(
                    "INSERT INTO activity_logs (user_id, email, full_name, role, action, details, ip_address, user_agent) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (u["id"], email, u.get("full_name",""), u["role"], "Login", "User login berhasil", ip_addr, user_agent)
                )
            except Exception as e:
                print(f"[AUTH] Failed to write activity log: {e}")
                
            print(f"[AUTH] Login: {email} role={u['role']}")
            self.send_response(200); self._cors()
            self.send_header("Content-Type","application/json")
            self.send_header("Set-Cookie", f"azdome_session={token}; Path=/; Max-Age={SESSION_TTL}; HttpOnly; SameSite=Strict")
            self.end_headers()
            self.wfile.write(json.dumps({"ok":True,"name":u.get("full_name",""),"role":u["role"]}).encode())
            return

        user = self._require_auth()
        if not user: return

        if path == "/admin/azdome/credentials":
            if user.get("role") != "admin":
                self._json(403,{"ok":False,"msg":"Akses ditolak"}); return
            az_email = body.get("email","").strip()
            az_pwd   = body.get("password","")
            if not az_email or not az_pwd:
                self._json(400,{"ok":False,"msg":"Email dan password Azdome wajib diisi"}); return
            data, token = azdome_api_login(az_email, az_pwd)
            if not token:
                self._json(401,{"ok":False,"msg":"Login Azdome gagal. Cek email/password."}); return
            get_db().set_config("azdome_email", az_email)
            get_db().set_config("azdome_password", az_pwd)
            get_tm().set_token_from_login(token)
            exp = get_token_expiry(token)
            self._json(200,{"ok":True,"msg":"Token Azdome berhasil disimpan","expires_at":exp}); return

        if path == "/admin/azdome/refresh":
            if user.get("role") != "admin":
                self._json(403,{"ok":False,"msg":"Akses ditolak"}); return
            ok = get_tm()._do_refresh()
            if ok:
                self._json(200,{"ok":True,"msg":"Token berhasil di-refresh","status":get_tm().get_status()})
            else:
                self._json(500,{"ok":False,"msg":"Refresh gagal. Pastikan credentials Azdome sudah disimpan."})
            return

        if path == "/admin/users":
            if user.get("role") != "admin":
                self._json(403,{"ok":False,"msg":"Akses ditolak"}); return
            u_email = body.get("email","").strip()
            u_pwd   = body.get("password","")
            u_role  = body.get("role","viewer")
            u_name  = body.get("full_name","").strip()
            if not u_email or not u_pwd:
                self._json(400,{"ok":False,"msg":"Email dan password wajib diisi"}); return
            if u_role not in ["admin","viewer"]:
                self._json(400,{"ok":False,"msg":"Role tidak valid"}); return
            try:
                ph = hash_password(u_pwd)
                uid = get_db().execute(
                    "INSERT INTO internal_users (email,password_hash,role,full_name) VALUES (%s,%s,%s,%s)",
                    (u_email, ph, u_role, u_name)
                )
                try:
                    get_db().execute(
                        "INSERT INTO activity_logs (user_id, email, full_name, role, action, details, ip_address, user_agent) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                        (user.get("id"), user.get("email"), user.get("full_name"), user.get("role"), "Tambah User", f"Menambahkan user {u_email} ({u_role})", self.client_address[0], self.headers.get("User-Agent",""))
                    )
                except: pass
                self._json(200,{"ok":True,"msg":f"User {u_email} berhasil dibuat","id":uid})
            except Exception as e:
                if "Duplicate" in str(e):
                    self._json(409,{"ok":False,"msg":"Email sudah terdaftar"})
                else:
                    self._json(500,{"ok":False,"msg":str(e)})
            return

        self._proxy_api("POST")

    def do_PUT(self):
        p = urlparse(self.path); path = p.path
        user = self._require_auth(roles=["admin"])
        if not user: return
        n = int(self.headers.get("Content-Length",0))
        body = json.loads(self.rfile.read(n)) if n else {}

        if path.startswith("/admin/users/"):
            uid = path.split("/")[-1]
            user = self._require_auth(roles=["admin"])
            if not user: return
            updates = []; params = []
            if "full_name" in body: updates.append("full_name=%s"); params.append(body["full_name"])
            if "role" in body:
                if body["role"] not in ["admin","viewer"]:
                    self._json(400,{"ok":False,"msg":"Role tidak valid"}); return
                updates.append("role=%s"); params.append(body["role"])
            if "is_active" in body: updates.append("is_active=%s"); params.append(1 if body["is_active"] else 0)
            if "password" in body and body["password"]:
                updates.append("password_hash=%s"); params.append(hash_password(body["password"]))
            if not updates: self._json(400,{"ok":False,"msg":"Tidak ada data untuk diupdate"}); return
            params.append(uid)
            get_db().execute(f"UPDATE internal_users SET {','.join(updates)},updated_at=NOW() WHERE id=%s", params)
            try:
                get_db().execute(
                    "INSERT INTO activity_logs (user_id, email, full_name, role, action, details, ip_address, user_agent) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (user.get("id"), user.get("email"), user.get("full_name"), user.get("role"), "Edit User", f"Mengubah data user ID {uid}: " + ", ".join([u.split("=")[0] for u in updates]), self.client_address[0], self.headers.get("User-Agent",""))
                )
            except: pass
            self._json(200,{"ok":True,"msg":"User berhasil diupdate"}); return

        self._json(404,{"ok":False,"msg":"Not found"})

    def do_DELETE(self):
        p = urlparse(self.path); path = p.path
        user = self._require_auth(roles=["admin"])
        if not user: return

        if path.startswith("/admin/users/"):
            uid = path.split("/")[-1]
            # Prevent self-delete
            cur = get_db().query_one("SELECT email FROM internal_users WHERE id=%s", (uid,))
            if cur and cur.get("email") == user.get("email"):
                self._json(400,{"ok":False,"msg":"Tidak bisa menghapus akun sendiri"}); return
            get_db().execute("DELETE FROM internal_users WHERE id=%s", (uid,))
            try:
                get_db().execute(
                    "INSERT INTO activity_logs (user_id, email, full_name, role, action, details, ip_address, user_agent) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (user.get("id"), user.get("email"), user.get("full_name"), user.get("role"), "Hapus User", f"Menghapus user ID {uid}", self.client_address[0], self.headers.get("User-Agent",""))
                )
            except: pass
            self._json(200,{"ok":True,"msg":"User dihapus"}); return

        self._proxy_api("DELETE")

    def _redirect(self, loc):
        self.send_response(302); self.send_header("Location", loc); self.end_headers()

    def _serve_file(self, filepath, filename):
        ext = filename.rsplit(".",1)[-1]
        ct = {"m3u8":"application/vnd.apple.mpegurl","ts":"video/mp2t","svg":"image/svg+xml","png":"image/png","css":"text/css"}.get(ext,"application/octet-stream")
        for _ in range(20):
            if os.path.exists(filepath): break
            time.sleep(0.1)
        try:
            with open(filepath,"rb") as f: data = f.read()
            self.send_response(200); self._cors()
            self.send_header("Content-Type",ct)
            self.send_header("Content-Length",str(len(data)))
            self.send_header("Cache-Control","no-cache, no-store, must-revalidate")
            self.send_header("Pragma","no-cache"); self.send_header("Expires","0")
            self.end_headers(); self.wfile.write(data)
        except: self.send_response(404); self._cors(); self.end_headers()

    def _proxy_media(self, path):
        local_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), path.strip("/"))
        if os.path.exists(local_path): self._serve_file(local_path, os.path.basename(local_path)); return
        url = MEDIA_BASE + path
        req = urllib.request.Request(url); req.add_header("User-Agent","Mozilla/5.0")
        try:
            resp = urllib.request.urlopen(req, timeout=10); raw = resp.read()
            ct = resp.getheader("Content-Type","image/png")
            self.send_response(200); self._cors()
            self.send_header("Content-Type",ct); self.send_header("Content-Length",str(len(raw)))
            self.send_header("Cache-Control","max-age=86400")
            self.end_headers(); self.wfile.write(raw)
        except:
            self.send_response(200); self._cors()
            self.send_header("Content-Type","image/png"); self.send_header("Content-Length",str(len(DEFAULT_THUMBNAIL)))
            self.end_headers(); self.wfile.write(DEFAULT_THUMBNAIL)

    def _serve_html(self, filepath):
        try:
            with open(filepath,"rb") as f: data = f.read()
            self.send_response(200)
            self.send_header("Content-Type","text/html; charset=utf-8")
            self.send_header("Content-Length",str(len(data)))
            self.end_headers(); self.wfile.write(data)
        except FileNotFoundError:
            self.send_response(404); self.end_headers()
            self.wfile.write(b"File not found")

    def _proxy_api(self, method):
        url = API_BASE + self.path; body = None
        if method in ("POST","PUT"):
            n = int(self.headers.get("Content-Length",0))
            body = self.rfile.read(n) if n else b"{}"
        token = get_tm().get_token()
        if not token: self._json(503,{"code":503,"message":"Token Azdome tidak tersedia"}); return
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Authorization",token); req.add_header("App-Key","AZDOME")
        req.add_header("Content-Type","application/json"); req.add_header("Accept-Encoding","gzip")
        req.add_header("User-Agent","okhttp/3.14.9")
        try:
            resp = urllib.request.urlopen(req, timeout=15); raw = resp.read()
            if resp.info().get("Content-Encoding") == "gzip": raw = gzip.decompress(raw)
            self.send_response(200); self._cors()
            self.send_header("Content-Type","application/json")
            self.end_headers(); self.wfile.write(raw)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                if e.info().get("Content-Encoding") == "gzip": raw = gzip.decompress(raw)
            except: pass
            self.send_response(e.code); self._cors()
            self.send_header("Content-Type","application/json")
            self.end_headers(); self.wfile.write(raw)
        except Exception as e:
            self.send_response(500); self._cors(); self.end_headers()
            self.wfile.write(json.dumps({"code":500,"message":str(e)}).encode())

    def _json(self, code, data):
        b = json.dumps(data).encode()
        self.send_response(code); self._cors()
        self.send_header("Content-Type","application/json")
        self.end_headers(); self.wfile.write(b)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin","*")
        self.send_header("Access-Control-Allow-Headers","*")
        self.send_header("Access-Control-Allow-Methods","GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Expose-Headers","Content-Type, Content-Length, Cache-Control")

    def log_message(self, fmt, *args):
        path = urlparse(self.path).path
        if path not in ["/favicon.ico"]:
            print(f"[{time.strftime('%H:%M:%S')}] {path} {args[1] if len(args)>1 else ''}")


if __name__ == "__main__":
    database = get_db()
    ensure_default_admin(database)
    get_tm()  # Init token manager
    has_ffmpeg  = bool(shutil.which("ffmpeg"))
    has_ffprobe = bool(shutil.which("ffprobe"))
    print(f"""
â•”â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•—
â•‘   AZDOME SERVER v12.0 (Internal Auth)   â•‘
â• â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•£
â•‘  URL      : http://0.0.0.0:{PORT:<5}     â•‘
â•‘  ffmpeg   : {'OK' if has_ffmpeg  else 'TIDAK ADA!'}                              â•‘
â•‘  ffprobe  : {'OK' if has_ffprobe else 'TIDAK ADA!'}                              â•‘
â•‘  Auth     : Internal (DB-backed)         â•‘
â•‘  DB       : {DB_HOST}:{DB_PORT}/{DB_NAME:<12}  â•‘
â• â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•£
â•‘  DEFAULT ADMIN:                          â•‘
â•‘  Email  : admin@aldzama.com              â•‘
â•‘  Pass   : Admin@1234  (GANTI SEGERA!)   â•‘
â• â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•£
â•‘  ROUTES:                                 â•‘
â•‘  GET  /admin         â†’ Admin Panel       â•‘
â•‘  GET  /admin/users   â†’ List users        â•‘
â•‘  POST /admin/users   â†’ Buat user         â•‘
â•‘  PUT  /admin/users/id â†’ Edit user        â•‘
â•‘  POST /admin/azdome/credentials â†’ Token â•‘
â•‘  GET  /admin/token-status â†’ Cek token   â•‘
â•‘  POST /admin/azdome/refresh â†’ Refresh   â•‘
â•šâ•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
""")
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    srv.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[AZDOME] Stopped.")
        with hls_lock:
            for s in hls_sessions.values():
                try: s["proc"].kill(); shutil.rmtree(s["dir"], ignore_errors=True)
                except: pass

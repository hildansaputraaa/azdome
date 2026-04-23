from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import urllib.request, urllib.error, json, os, gzip, socket, time, hashlib, glob
import subprocess, shutil, threading, tempfile, secrets
from urllib.parse import urlparse, parse_qs

# ═══ SESSION (in-memory, no DB needed) ═══════════════════════
# Format: { session_token: { 'azdome_token': str, 'name': str, 'expires': float } }
azdome_sessions = {}
azdome_sessions_lock = threading.Lock()

TOKEN_AZDOME = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJuYmYiOjE3ODEwNzQ0NjMsImFwcElkIjoyLCJleHAiOjE3ODEwNzQ0NjMsInVzZXJJZCI6MzA0MzksImlhdCI6MTc3MzI5ODQ2MywianRpIjoiYTUxYmM2OTEtMTE1NS00NDY3LTgzYjktZGEwZTgxNjE4Zjg5In0.I43AhD_iPkZGCerxI4dRHKJry_RwQF_CzxMXQyQCtNWN61c7punYD593RwrZsq4Z6q_2AHDmALVOXHsU8NLTQw"
AZDOME_LOGIN_URL = "http://community-app.lulushun.net:8901/community-app/api/app-user/login"
API_BASE   = "http://lingdu-ap.lulushun.net:8801"
MEDIA_BASE = "http://lingdu-ap.lulushun.net:8803"
PORT       = int(os.environ.get("PORT", 8899))
DASHBOARD  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "azdome-dashboard.html")
SESSION_TTL = 86400  # 24 jam

FAVICON = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
DEFAULT_THUMBNAIL = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"


# ═══ SESSION HELPERS ════════════════════════════

def session_create(azdome_token, name, email):
    """Buat session baru, simpan di memory. Logout session lama dengan email yang sama."""
    token = secrets.token_hex(32)
    expires = time.time() + SESSION_TTL
    with azdome_sessions_lock:
        # Cari dan hapus session lama dengan email yang sama (Single Session Enforcement)
        old_sessions = [k for k, v in azdome_sessions.items() if v.get('email') == email]
        for k in old_sessions:
            azdome_sessions.pop(k, None)
            print(f"[AUTH] Single session enforcement: logged out old session for {email}")

        azdome_sessions[token] = {
            'azdome_token': azdome_token,
            'name': name,
            'email': email,
            'expires': expires,
        }
    _session_cleanup()
    return token

def session_get(token):
    """Ambil session jika masih valid, None jika expired/tidak ada."""
    with azdome_sessions_lock:
        s = azdome_sessions.get(token)
    if not s:
        return None
    if time.time() > s['expires']:
        with azdome_sessions_lock:
            azdome_sessions.pop(token, None)
        return None
    return s

def session_delete(token):
    with azdome_sessions_lock:
        azdome_sessions.pop(token, None)

def _session_cleanup():
    """Hapus session expired (jalankan sesekali)."""
    now = time.time()
    with azdome_sessions_lock:
        expired = [k for k, v in azdome_sessions.items() if now > v['expires']]
        for k in expired:
            azdome_sessions.pop(k, None)



# ═══ AZDOME LOGIN ═══════════════════════════════

def azdome_login(email, password):
    try:
        password_md5 = hashlib.md5(password.encode()).hexdigest()
        payload = {
            "account": email,
            "password": password_md5,
            "appVersion": "3.8.0.172",
            "fcmToken": "azdome_dashboard",
            "lingduRegion": "ap",
            "osVersion": "35",
            "phoneOs": "Web",
            "registrationId": ""
        }
        req = urllib.request.Request(
            AZDOME_LOGIN_URL,
            data=json.dumps(payload).encode(),
            method="POST",
            headers={
                "Content-Type": "application/json; charset=UTF-8",
                "App-Key": "AZDOME",
                "Accept-Language": "id",
                "Accept-Encoding": "gzip",
                "User-Agent": "okhttp/3.14.9"
            }
        )
        resp = urllib.request.urlopen(req, timeout=10)
        raw = resp.read()
        if resp.info().get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        result = json.loads(raw)
        if result.get("code") == 200 and result.get("data", {}).get("user"):
            data = result.get("data")
            azdome_token = data.get("token")
            print(f"[AZDOME] Login sukses, token: {azdome_token[:20]}...")
            return (data, azdome_token)
        else:
            print(f"[AZDOME] Login failed: {result.get('message', 'Unknown error')}")
            return (None, None)
    except Exception as e:
        print(f"[AZDOME] Login error: {e}")
        return (None, None)

# ═══ FFPROBE: Deteksi codec kamera sebelum encode ═══════════════════════

def probe_stream(stream_url):
    """
    Jalankan ffprobe untuk deteksi codec video/audio dari stream.
    Return dict: {
        'has_video': bool, 'video_codec': str,
        'has_audio': bool, 'audio_codec': str,
        'error': str or None
    }
    """
    result = {
        'has_video': False, 'video_codec': None,
        'has_audio': False, 'audio_codec': None,
        'error': None
    }
    try:
        url_lower = (stream_url or '').lower()
        is_rtsp = url_lower.startswith('rtsp://')

        probe_input_flags = [
            "-analyzeduration", "5000000",
            "-probesize", "5000000",
        ]
        if is_rtsp:
            probe_input_flags += ["-rtsp_transport", "tcp", "-timeout", "10000000"]
        else:
            probe_input_flags += ["-timeout", "10000000"]

        cmd = [
            "ffprobe",
            "-v", "error",
        ] + probe_input_flags + [
            "-i", stream_url,
            "-show_streams",
            "-select_streams", "v:0",
            "-of", "json"
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if r.stdout:
            try:
                data = json.loads(r.stdout)
                streams = data.get("streams", [])
                for s in streams:
                    if s.get("codec_type") == "video":
                        result['has_video'] = True
                        result['video_codec'] = s.get("codec_name", "unknown")
                    elif s.get("codec_type") == "audio":
                        result['has_audio'] = True
                        result['audio_codec'] = s.get("codec_name", "unknown")
            except json.JSONDecodeError:
                pass

        # Probe audio juga
        cmd2 = [
            "ffprobe",
            "-v", "error",
        ] + probe_input_flags + [
            "-i", stream_url,
            "-show_streams",
            "-select_streams", "a:0",
            "-of", "json"
        ]
        r2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=15)
        if r2.stdout:
            try:
                data2 = json.loads(r2.stdout)
                streams2 = data2.get("streams", [])
                for s in streams2:
                    if s.get("codec_type") == "audio":
                        result['has_audio'] = True
                        result['audio_codec'] = s.get("codec_name", "unknown")
            except json.JSONDecodeError:
                pass

        if r.stderr:
            result['error'] = r.stderr[:500]

        print(f"[PROBE] video={result['video_codec']}, audio={result['audio_codec']}")
    except subprocess.TimeoutExpired:
        result['error'] = "ffprobe timeout"
        print("[PROBE] Timeout")
    except Exception as e:
        result['error'] = str(e)
        print(f"[PROBE] Error: {e}")
    return result


def _get_input_flags(stream_url):
    """
    Tentukan FFmpeg input flags berdasarkan protokol stream URL.

    ZLMediaKit (zlmEnable=1) biasanya serve HTTP-FLV:
      http://host:port/live/stream.flv
    FFmpeg WAJIB dikasih -f flv untuk HTTP-FLV agar tidak AVERROR_INVALIDDATA.
    Jangan pakai -reconnect untuk live stream (itu untuk VOD).
    """
    url_lower = (stream_url or '').lower()

    if url_lower.startswith('rtsp://'):
        return [
            "-fflags", "+genpts+igndts",
            "-err_detect", "ignore_err",
            "-analyzeduration", "5000000",
            "-probesize", "5000000",
            "-rtsp_transport", "tcp",
            "-timeout", "15000000",
        ]
    elif url_lower.startswith('rtmp://') or url_lower.startswith('rtmps://'):
        return [
            "-fflags", "+genpts+igndts",
            "-err_detect", "ignore_err",
            "-analyzeduration", "5000000",
            "-probesize", "5000000",
        ]
    else:
        # HTTP / HTTPS — ZLMediaKit biasanya HTTP-FLV
        # -f flv WAJIB: tanpa ini FFmpeg exit=8 meski codec h264/aac valid
        # Jangan pakai -reconnect: itu untuk VOD, bukan live stream
        flags = [
            "-fflags", "+genpts+igndts",
            "-err_detect", "ignore_err",
            "-analyzeduration", "5000000",
            "-probesize", "5000000",
        ]
        # Deteksi format: .flv → -f flv, .m3u8 → biarkan auto-detect
        if '.flv' in url_lower or ('.flv' not in url_lower and '.m3u8' not in url_lower):
            # Default ke FLV karena ZLMediaKit default output adalah HTTP-FLV
            flags += ["-f", "flv"]
        return flags



def build_ffmpeg_cmd(stream_url, hls_dir, m3u8, probe_info):
    """
    Build FFmpeg command yang adaptif berdasarkan codec dan protokol stream.

    Protokol yang didukung:
    - RTSP  : gunakan -rtsp_transport tcp
    - RTMP  : tanpa -rtsp_transport
    - HTTP  : gunakan -reconnect (HTTP-FLV dari ZLMediaKit/zlmEnable=1)

    FIX EXIT CODE 8 (AVERROR_INVALIDDATA):
    - Penyebab: -rtsp_transport tcp dipakai untuk HTTP stream → protocol error
    - Fix: deteksi protokol via _get_input_flags()
    - Jika audio codec tidak dikenal: paksa transcode ke AAC
    - Jika tidak ada audio: skip audio (-an)
    """

    has_audio = probe_info.get('has_audio', True)  # assume ada audio kalau probe gagal
    audio_codec = (probe_info.get('audio_codec') or '').lower()
    video_codec = (probe_info.get('video_codec') or '').lower()

    # Codec audio yang butuh transcode (tidak bisa direct mux ke mpegts dengan benar)
    PROBLEMATIC_AUDIO = {
        'pcm_mulaw', 'pcm_alaw', 'g726', 'g711', 'g722', 'g723',
        'g729', 'adpcm_g722', 'adpcm_g726', 'adpcm_ima_wav',
        'adpcm_ms', 'opus', 'vorbis', 'mp3', 'mp2', 'amr_nb', 'amr_wb',
        'unknown', ''
    }

    need_audio_transcode = audio_codec in PROBLEMATIC_AUDIO
    no_audio = not has_audio

    # Deteksi protokol
    url_lower = (stream_url or '').lower()
    proto = 'rtsp' if url_lower.startswith('rtsp://') else \
            'rtmp' if (url_lower.startswith('rtmp://') or url_lower.startswith('rtmps://')) else \
            'http'

    print(f"[CMD] proto={proto}, video={video_codec}, audio={audio_codec}, "
          f"no_audio={no_audio}, need_transcode={need_audio_transcode}")

    # Input flags sesuai protokol
    input_flags = _get_input_flags(stream_url)

    cmd = [
        "ffmpeg",
        "-loglevel", "warning",
    ] + input_flags + [
        "-i", stream_url,
    ]

    # ── VIDEO ────────────────────────────────────────────────────────────────
    cmd += [
        # Map video stream
        "-map", "0:v:0",

        # Gunakan 'copy' untuk mempertahankan resolusi & kualitas asli dari kamera
        "-c:v", "copy",
        
        # Clean mux timestamps untuk HLS
        "-muxdelay", "0",
        "-muxpreload", "0",
    ]

    # ── AUDIO ────────────────────────────────────────────────────────────────
    if no_audio:
        # Tidak ada audio track sama sekali
        cmd += ["-an"]
        print("[CMD] Audio: NONE (kamera tanpa audio)")
    else:
        cmd += ["-map", "0:a:0?"]  # Tanda tanya = opsional, tidak error kalau tidak ada

        if need_audio_transcode or audio_codec == '':
            # Transcode ke AAC (paling aman untuk mpegts HLS)
            cmd += [
                "-c:a", "aac",
                "-b:a", "64k",
                "-ar", "44100",
                "-ac", "2",
                # Fix sync audio yang jelek dari kamera
                "-af", "aresample=async=1:min_hard_comp=0.100000:first_pts=0",
            ]
            print(f"[CMD] Audio: TRANSCODE ke AAC (dari {audio_codec})")
        else:
            # Audio sudah AAC atau format yang kompatibel
            cmd += [
                "-c:a", "aac",
                "-b:a", "64k",
                "-ar", "44100",
                "-ac", "2",
                "-af", "aresample=async=1:min_hard_comp=0.100000:first_pts=0",
            ]
            print(f"[CMD] Audio: TRANSCODE (from {audio_codec} -> AAC)")

    # ── HLS OUTPUT ───────────────────────────────────────────────────────────
    cmd += [
        "-max_muxing_queue_size", "2048",
        "-f", "hls",
        "-hls_time", "2",
        "-hls_list_size", "6",
        "-hls_flags", "delete_segments+append_list",
        "-hls_segment_type", "mpegts",
        "-hls_allow_cache", "0",
        "-start_number", "0",
        "-hls_segment_filename", os.path.join(hls_dir, "seg%03d.ts"),
        m3u8
    ]

    return cmd




# ═══ HLS SESSION ════════════════════════════════

hls_sessions = {}
hls_lock = threading.Lock()

def hls_cleanup():
    while True:
        time.sleep(10)
        with hls_lock:
            dead = [k for k,v in hls_sessions.items() if time.time()-v["ts"]>60]
            for k in dead:
                s = hls_sessions.pop(k)
                try:
                    s["proc"].kill()
                    shutil.rmtree(s["dir"], ignore_errors=True)
                    print(f"[HLS] Cleaned {k}")
                except: pass

threading.Thread(target=hls_cleanup, daemon=True).start()


def start_hls(stream_url, session_id, probe_info=None):
    with hls_lock:
        if session_id in hls_sessions:
            old = hls_sessions[session_id]
            try: old["proc"].kill(); shutil.rmtree(old["dir"], ignore_errors=True)
            except: pass

        hls_dir = tempfile.mkdtemp(prefix="azdome_hls_")
        m3u8    = os.path.join(hls_dir, "live.m3u8")

        if probe_info is None:
            probe_info = {}

        cmd = build_ffmpeg_cmd(stream_url, hls_dir, m3u8, probe_info)

        log_path = os.path.join(hls_dir, "ffmpeg.log")
        log_file = open(log_path, "wb")

        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=log_file)

        hls_sessions[session_id] = {
            "proc": proc,
            "dir": hls_dir,
            "url": stream_url,
            "m3u8": m3u8,
            "log": log_path,
            "ts": time.time(),
            "ready": False,
            "probe": probe_info,
        }
        print(f"[HLS] Started session={session_id}, dir={hls_dir}")
        print(f"[HLS] CMD: {' '.join(cmd)}")

        def mark_ready():
            # Phase 1: Fast polling (100ms) untuk 5s pertama
            for i in range(50):
                if _check_m3u8_ready(hls_dir, m3u8, session_id):
                    print(f"[HLS] Ready (fast): {session_id} at {i*100}ms")
                    return
                with hls_lock:
                    s = hls_sessions.get(session_id)
                    if s and s["proc"].poll() is not None:
                        _print_ffmpeg_log(log_path, session_id, s["proc"].returncode)
                        return
                time.sleep(0.1)

            # Phase 2: Slow polling (250ms) sampai 30s total
            for i in range(100):
                if _check_m3u8_ready(hls_dir, m3u8, session_id):
                    print(f"[HLS] Ready (slow): {session_id} at {5000+i*250}ms")
                    return
                with hls_lock:
                    s = hls_sessions.get(session_id)
                    if s and s["proc"].poll() is not None:
                        _print_ffmpeg_log(log_path, session_id, s["proc"].returncode)
                        return
                time.sleep(0.25)

            print(f"[HLS] Timeout: {session_id}")

        threading.Thread(target=mark_ready, daemon=True).start()
        return hls_dir, m3u8


def _check_m3u8_ready(hls_dir, m3u8, session_id):
    """Cek apakah HLS m3u8 valid dan minimal satu .ts segment punya data nyata."""
    if not os.path.exists(m3u8) or os.path.getsize(m3u8) == 0:
        return False
    ts_files = [f for f in os.listdir(hls_dir) if f.endswith('.ts')]
    if not ts_files:
        return False
    try:
        with open(m3u8, 'r') as f:
            content = f.read()
        if not ("#EXTM3U" in content and "#EXTINF" in content and ".ts" in content):
            return False
        ts_ok = any(os.path.getsize(os.path.join(hls_dir, f)) > 1024 for f in ts_files)
        if not ts_ok:
            return False
        with hls_lock:
            if session_id in hls_sessions:
                hls_sessions[session_id]["ready"] = True
        return True
    except:
        return False


def _print_ffmpeg_log(log_path, session_id, returncode):
    print(f"[HLS] FFmpeg died for {session_id} (code={returncode})")
    try:
        with open(log_path, 'r', errors='replace') as lf:
            tail = lf.read()[-2000:]
        print(f"[FFmpeg log tail]:\n{tail}")
    except: pass


def _try_fallback_no_audio(stream_url, session_id, hls_dir, m3u8, log_path, probe_info=None):
    """
    Fallback: coba ulang tanpa audio sama sekali.
    Dipanggil kalau FFmpeg exit code != 0 dan audio diduga penyebabnya.
    Menggunakan probe_info asli agar video_codec tidak salah.
    Bersihkan file m3u8 lama sebelum mulai.
    """
    print(f"[HLS] Trying fallback: no-audio for {session_id}")
    
    # Bersihkan m3u8 dan segment lama agar tidak corrupt
    try:
        if os.path.exists(m3u8):
            os.remove(m3u8)
            print(f"[HLS] Cleaned old m3u8: {m3u8}")
        for seg in glob.glob(os.path.join(hls_dir, "seg*.ts")):
            try:
                os.remove(seg)
            except:
                pass
    except:
        pass
    
    # Gunakan probe_info asli, tapi paksa has_audio=False
    fb_probe = dict(probe_info) if probe_info else {}
    fb_probe['has_audio'] = False
    # Jika probe tidak berhasil deteksi video_codec, asumsikan h264
    if not fb_probe.get('has_video'):
        fb_probe['has_video'] = True
    if not fb_probe.get('video_codec'):
        fb_probe['video_codec'] = 'h264'
    cmd = build_ffmpeg_cmd(stream_url, hls_dir, m3u8, fb_probe)

    log_file = open(log_path, "ab")  # append ke log yang sama
    log_file.write(b"\n\n--- FALLBACK NO-AUDIO ---\n\n")
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=log_file)

    with hls_lock:
        if session_id in hls_sessions:
            hls_sessions[session_id]["proc"] = proc
            hls_sessions[session_id]["ready"] = False

    print(f"[HLS] Fallback no-audio CMD: {' '.join(cmd)}")

    # Tunggu siap
    for i in range(100):
        if _check_m3u8_ready(hls_dir, m3u8, session_id):
            print(f"[HLS] Fallback no-audio ready at {i*250}ms")
            return True
        with hls_lock:
            s = hls_sessions.get(session_id)
            if s and s["proc"].poll() is not None:
                _print_ffmpeg_log(log_path, session_id, s["proc"].returncode)
                return False
        time.sleep(0.25)
    return False


def _try_fallback_force_decode(stream_url, session_id, hls_dir, m3u8, log_path, probe_info=None):
    """
    Fallback level 2: paksa software decode dengan flag toleransi maksimal.
    Berguna untuk codec proprietary atau stream corrupt.
    Gunakan _get_input_flags() agar HTTP/RTSP/RTMP diperlakukan benar.
    Bersihkan file m3u8 lama sebelum mulai.
    """
    print(f"[HLS] Trying fallback: force-decode (level 2) for {session_id}")
    
    # Bersihkan m3u8 dan segment lama
    try:
        if os.path.exists(m3u8):
            os.remove(m3u8)
            print(f"[HLS] Cleaned old m3u8: {m3u8}")
        for seg in glob.glob(os.path.join(hls_dir, "seg*.ts")):
            try:
                os.remove(seg)
            except:
                pass
    except:
        pass
    
    video_codec = (probe_info or {}).get('video_codec', 'unknown')

    # Ambil input flags sesuai protokol, lalu tambahkan toleransi ekstra
    base_flags = _get_input_flags(stream_url)
    # Ganti analyzeduration/probesize dengan nilai lebih besar
    # dan tambahkan skip_frame + nobuffer
    extra_flags = []
    for flag in base_flags:
        if flag in ("5000000",):
            extra_flags.append("10000000")  # double probe/analyze duration
        elif flag == "15000000":
            extra_flags.append("20000000")  # extend timeout
        else:
            extra_flags.append(flag)
    # Inject fflags nobuffer jika belum ada
    if "+genpts+igndts+discardcorrupt" in " ".join(extra_flags):
        idx = extra_flags.index("+genpts+igndts+discardcorrupt")
        extra_flags[idx] = "+genpts+igndts+discardcorrupt+nobuffer"

    cmd = [
        "ffmpeg",
        "-loglevel", "warning",
    ] + extra_flags + [
        "-skip_frame", "noref",
        "-i", stream_url,
        # Hanya video, paksa re-encode
        "-an",
        "-map", "0:v:0",
        "-c:v", "libx264",
        "-profile:v", "baseline",
        "-level:v", "3.1",
        "-pix_fmt", "yuv420p",
        "-b:v", "1500k",
        "-maxrate:v", "2000k",
        "-bufsize:v", "4000k",
        "-preset", "veryfast",
        "-tune", "zerolatency",
        # GOP fixed
        "-g", "30",
        "-keyint_min", "30",
        "-sc_threshold", "0",
        "-vsync", "cfr",
        "-r", "15",
        "-muxdelay", "0",
        "-muxpreload", "0",
        "-max_muxing_queue_size", "4096",

        "-f", "hls",
        "-hls_time", "2",
        "-hls_list_size", "6",
        "-hls_flags", "delete_segments+append_list",
        "-hls_segment_type", "mpegts",
        "-hls_allow_cache", "0",
        "-start_number", "0",
        "-hls_segment_filename", os.path.join(hls_dir, "seg%03d.ts"),
        m3u8
    ]
    print(f"[HLS] Force-decode CMD (video_codec was: {video_codec}): {' '.join(cmd)}")

    log_file = open(log_path, "ab")
    log_file.write(b"\n\n--- FALLBACK FORCE-DECODE (level 2) ---\n\n")
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=log_file)

    with hls_lock:
        if session_id in hls_sessions:
            hls_sessions[session_id]["proc"] = proc
            hls_sessions[session_id]["ready"] = False

    for i in range(100):
        if _check_m3u8_ready(hls_dir, m3u8, session_id):
            print(f"[HLS] Force-decode ready at {i*250}ms")
            return True
        with hls_lock:
            s = hls_sessions.get(session_id)
            if s and s["proc"].poll() is not None:
                _print_ffmpeg_log(log_path, session_id, s["proc"].returncode)
                return False
        time.sleep(0.25)
    return False


def api_call(path, method="GET", body=None, timeout=15, azdome_token=None, retries=2):
    url = API_BASE + path
    token = azdome_token if azdome_token else TOKEN_AZDOME
    
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Authorization", token)
        req.add_header("App-Key", "AZDOME")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept-Encoding", "gzip")
        req.add_header("User-Agent", "okhttp/3.14.9")
        
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
            raw  = resp.read()
            if resp.info().get("Content-Encoding") == "gzip": 
                raw = gzip.decompress(raw)
            return json.loads(raw)
        except urllib.error.HTTPError as e:
            if e.code == 503 and attempt < retries:
                wait_time = (attempt + 1) * 2
                print(f"[API] 503 detected for {path}, retrying in {wait_time}s... ({attempt+1}/{retries})")
                time.sleep(wait_time)
                continue
            print(f"[API] HTTP Error {e.code} for {path}: {e.reason}")
            return None
        except Exception as e:
            print(f"[API] Error for {path}: {e}")
            return None
    return None

def wait_camera_ready(device_sn, send_event, max_wait=120, azdome_token=None):
    send_event("progress", "Menunggu kamera menyala (scan-channel polling)...")
    print(f"[STREAM] wait_camera_ready started, max_wait={max_wait}s")
    wakeup_interval = 5
    for i in range(max_wait // 3):
        elapsed = (i + 1) * 3
        if i % wakeup_interval == 0 and i > 0:
            try:
                api_call(f"/lingdu-app/api/user-device/device-wakeup?imei={device_sn}", azdome_token=azdome_token)
            except Exception as e:
                print(f"[STREAM] Wakeup error at {elapsed}s: {e}")
        try:
            r = api_call(f"/lingdu-app/api/monitoring/scan-channel?deviceSn={device_sn}", azdome_token=azdome_token)
            if r is not None:
                data = r.get("data")
                if data is not None:
                    print(f"[STREAM] Camera ready at {elapsed}s")
                    send_event("progress", f"Kamera merespons! Channel siap ({elapsed}s)")
                    return True
                else:
                    msg = f"Menunggu kamera siap... ({elapsed}s)"
                    send_event("progress", msg)
                    # Send an extra event to keep Cloudflare happy
                    send_event("ping", "keep-alive")
                    if i % 5 == 0: print(f"[STREAM] {msg}")
            else:
                if i % 5 == 0: print(f"[STREAM] Kamera belum merespons ({elapsed}s)")
                send_event("progress", f"Kamera belum merespons... ({elapsed}s)")
                send_event("ping", "keep-alive")
        except Exception as e:
            print(f"[STREAM] scan-channel error at {elapsed}s: {e}")
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
        send_event("progress", f"Stream kosong ({len(chunk)} bytes)")
        return False
    except Exception as e:
        send_event("progress", f"Stream tidak bisa diakses: {e}")
        return False

def get_stream_url_sse(device_sn, channel, send_event, retries=3, azdome_token=None):
    send_event("progress", "Membangunkan kamera...")
    try:
        api_call(f"/lingdu-app/api/user-device/device-wakeup?imei={device_sn}", azdome_token=azdome_token)
    except Exception as e:
        print(f"[STREAM] Wakeup error: {e}")
    time.sleep(1)

    ready = wait_camera_ready(device_sn, send_event, max_wait=120, azdome_token=azdome_token)
    if not ready:
        send_event("progress", "Kamera tidak merespons setelah 120 detik")
        return None

    for attempt in range(retries):
        send_event("progress", f"[{attempt+1}/{retries}] Memulai siaran live...")
        start_live_body = json.dumps({
            "channelNo": int(channel),
            "deviceSn": device_sn,
            "mediaType": 0,
            "streamType": 1,  # 0 = Main Stream (HD), 1 = Sub Stream
            "zlmEnable": 1
        }).encode()
        try:
            r = api_call("/lingdu-app/api/monitoring/start-live", "POST", start_live_body, azdome_token=azdome_token)
            if r: print(f"[STREAM] start-live response: {r.get('code', '?')}")
        except Exception as e:
            print(f"[STREAM] start-live error: {e}")
        time.sleep(2)

        send_event("progress", f"[{attempt+1}/{retries}] Mendapatkan URL stream...")
        try:
            r = api_call(f"/lingdu-app/api/monitoring/stream-url?deviceSn={device_sn}&channelNo={channel}&zlmEnable=1", azdome_token=azdome_token)
            if not r:
                send_event("progress", "URL null, mencoba lagi..."); time.sleep(3); continue
            data = r.get("data")
            if not data:
                send_event("progress", f"URL tidak ada (code={r.get('code')}), mencoba lagi..."); time.sleep(3); continue
            send_event("progress", "URL ditemukan, memverifikasi stream...")
            print(f"[STREAM] URL: {data[:80]}...")
            if verify_stream_has_data(data, send_event):
                send_event("progress", "Stream siap! Memulai HLS encoder...")
                return data
            send_event("progress", "Stream belum siap, tunggu 3 detik...")
            time.sleep(3)
        except Exception as e:
            print(f"[STREAM] stream-url exception: {e}")
            send_event("progress", f"Error: {e}"); time.sleep(3)

    return None

# ═══ LOGIN PAGE ══════════════════════════════════

LOGIN_HTML = """<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Aldzama - Login</title>
<link    href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap"
    rel="stylesheet">
  <link rel="icon" type="image/png" href="/media/logo_aldzama_transparan.png">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{min-height:100vh;background:linear-gradient(135deg,#0f172a 0%,#1e3a5f 50%,#0f172a 100%);display:flex;align-items:center;justify-content:center;font-family:'Plus Jakarta Sans',sans-serif;}
.card{background:white;border-radius:20px;padding:40px;width:100%;max-width:400px;box-shadow:0 25px 60px rgba(0,0,0,.4);}
.logo{display:flex;align-items:center;justify-content:center;gap:12px;margin-bottom:32px;}
.logo-icon{width:52px;height:52px;border-radius:14px;background:linear-gradient(135deg,#1d6fe8,#0ea5e9);display:flex;align-items:center;justify-content:center;font-size:24px;box-shadow:0 4px 16px rgba(29,111,232,.4);}
.logo-text h1{font-size:22px;font-weight:800;color:#111827;}
.logo-text p{font-size:12px;color:#6b7280;font-weight:500;}
.form-group{margin-bottom:18px;}
label{display:block;font-size:12px;font-weight:700;color:#374151;margin-bottom:6px;text-transform:uppercase;letter-spacing:.5px;}
input{width:100%;padding:11px 14px;border:1.5px solid #e4e7ec;border-radius:10px;font-size:14px;font-family:inherit;color:#111827;transition:border-color .15s;outline:none;}
input:focus{border-color:#1d6fe8;box-shadow:0 0 0 3px rgba(29,111,232,.1);}
.btn{width:100%;padding:13px;background:linear-gradient(135deg,#1d6fe8,#0ea5e9);color:white;border:none;border-radius:10px;font-size:15px;font-weight:700;cursor:pointer;font-family:inherit;transition:all .15s;margin-top:6px;}
.btn:hover{transform:translateY(-1px);box-shadow:0 6px 20px rgba(29,111,232,.4);}
.error{background:#fee2e2;color:#dc2626;padding:10px 14px;border-radius:8px;font-size:13px;font-weight:600;margin-bottom:16px;display:none;}
.error.show{display:block;}
.success{background:#dcfce7;color:#16a34a;padding:10px 14px;border-radius:8px;font-size:13px;font-weight:600;margin-bottom:16px;display:none;}
.success.show{display:block;}
.footer{text-align:center;margin-top:24px;font-size:11px;color:#9ca3af;}
.note{font-size:11px;color:#6b7280;margin-top:16px;padding:10px;background:#f3f4f6;border-radius:8px;line-height:1.5;}
</style></head>
<body>
<div class="card">
  <div class="logo" style="flex-direction: column; gap: 8px;">
    <img src="/media/logo_aldzama_transparan.png" alt="Aldzama" style="height: 60px; width: auto; object-fit: contain;">
    <div class="logo-text" style="text-align: center;"><p>Fleet Monitor System</p></div>
  </div>
  <div class="error" id="err"></div>
  <div class="success" id="suc"></div>
  <div class="form-group"><label>Email AZDOME</label><input type="email" id="email" placeholder="Email akun AZDOME" autocomplete="email"></div>
  <div class="form-group"><label>Password</label><input type="password" id="pw" placeholder="Password AZDOME" autocomplete="current-password"></div>
  <button class="btn" id="btn" onclick="doLogin()">Masuk</button>
  <div class="note">&#128161; Login menggunakan akun AZDOME Anda.</div>
  <div class="footer">Aldzama Fleet Monitor &copy; 2024</div>
</div>
<script>
document.addEventListener("keydown",e=>{if(e.key==="Enter")doLogin();});
async function doLogin(){
  const email=document.getElementById("email").value.trim();
  const p=document.getElementById("pw").value;
  const err=document.getElementById("err"),suc=document.getElementById("suc"),btn=document.getElementById("btn");
  if(!email||!p){err.textContent="Email dan password wajib diisi";err.classList.add("show");suc.classList.remove("show");return;}
  btn.textContent="Memverifikasi...";btn.disabled=true;err.classList.remove("show");suc.classList.remove("show");
  try{
    const r=await fetch("/auth/login",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({email:email,password:p})});
    const d=await r.json();
    if(d.ok){suc.textContent="Login berhasil!";suc.classList.add("show");setTimeout(()=>{window.location.href="/";},1200);}
    else{err.textContent=d.msg||"Login gagal";err.classList.add("show");btn.textContent="Masuk";btn.disabled=false;}
  }catch{err.textContent="Server tidak dapat dihubungi";err.classList.add("show");btn.textContent="Masuk";btn.disabled=false;}
}
</script></body></html>""".encode('utf-8')

# ═══ HTTP HANDLER ════════════════════════════════

class Handler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(200); self._cors(); self.end_headers()

    def _get_azdome_token(self):
        session_token = self._get_token()
        if not session_token: return None
        s = session_get(session_token)
        return s['azdome_token'] if s else None

    def _get_token(self):
        for part in self.headers.get("Cookie","").split(";"):
            part = part.strip()
            if part.startswith("azdome_session="):
                return part[len("azdome_session="):]
        return None

    def _require_auth(self):
        token = self._get_token()
        if not token:
            self._redirect("/login"); return None
        s = session_get(token)
        if not s:
            self._redirect("/login"); return None
        return s

    def do_GET(self):
        p    = urlparse(self.path)
        path = p.path
        qs   = parse_qs(p.query)

        if path == "/login":
            self.send_response(200)
            self.send_header("Content-Type","text/html; charset=utf-8")
            self.send_header("Content-Length",str(len(LOGIN_HTML)))
            self.end_headers(); self.wfile.write(LOGIN_HTML); return

        if path == "/auth/logout":
            token = self._get_token()
            if token: session_delete(token)
            self.send_response(302)
            self.send_header("Location","/login")
            self.send_header("Set-Cookie","azdome_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict")
            self.end_headers(); return

        if path in ("/","/dashboard"):
            if not self._require_auth(): return
            self._serve_html(); return

        if path == "/favicon.ico":
            favicon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "media", "logo_aldzama_transparan.png")
            if os.path.exists(favicon_path):
                self._serve_file(favicon_path, "logo_aldzama_transparan.png")
            else:
                self.send_response(200); self.send_header("Content-Type","image/png")
                self.send_header("Content-Length",str(len(FAVICON))); self.end_headers()
                self.wfile.write(FAVICON)
            return

        if path.startswith("/media/"):
            self._proxy_media(path); return

        user = self._require_auth()
        if not user: return

        if path.startswith("/hls/"):
            parts = path.split("/")
            if len(parts) >= 4:
                sid = parts[2]; fname = parts[3]
                with hls_lock:
                    s = hls_sessions.get(sid)
                if s:
                    s["ts"] = time.time()
                    self._serve_file(os.path.join(s["dir"],fname), fname); return
            self.send_response(404); self._cors(); self.end_headers(); return

        # ── DEBUG: Baca log FFmpeg ──────────────────────────────────────────
        if path == "/debug/hls-log":
            sn = qs.get("sn",[""])[0]; ch = qs.get("ch",["1"])[0]
            sid = f"{sn}_{ch}"
            with hls_lock:
                s = hls_sessions.get(sid)
            if not s:
                self._json(404, {"ok": False, "msg": f"Session {sid} tidak ditemukan. Coba segera setelah error."}); return
            try:
                with open(s["log"], "r", errors="replace") as f:
                    log_content = f.read()
                self._json(200, {
                    "ok": True,
                    "session_id": sid,
                    "stream_url": s.get("url", "")[:80] + "...",
                    "probe": s.get("probe", {}),
                    "ffmpeg_log": log_content[-3000:],
                    "proc_alive": s["proc"].poll() is None,
                    "proc_returncode": s["proc"].poll(),
                    "ready": s.get("ready", False),
                })
            except Exception as e:
                self._json(500, {"ok": False, "msg": str(e)})
            return

        # ── DEBUG: Lihat M3U8 content ─────────────────────────────────────────
        if path == "/debug/m3u8-content":
            sn = qs.get("sn",[""])[0]; ch = qs.get("ch",["1"])[0]
            sid = f"{sn}_{ch}"
            with hls_lock:
                s = hls_sessions.get(sid)
            if not s:
                self._json(404, {"ok": False, "msg": f"Session {sid} tidak ditemukan."}); return
            try:
                m3u8_path = s.get("m3u8", "")
                if not os.path.exists(m3u8_path):
                    self._json(404, {"ok": False, "msg": "M3U8 belum dibuat."}); return
                with open(m3u8_path, "r") as f:
                    m3u8_content = f.read()
                # List semua file di directory
                hls_dir = s.get("dir", "")
                files = os.listdir(hls_dir) if os.path.exists(hls_dir) else []
                ts_files = [f for f in files if f.endswith('.ts')]
                self._json(200, {
                    "ok": True,
                    "session_id": sid,
                    "m3u8_path": m3u8_path,
                    "m3u8_content": m3u8_content,
                    "ts_files": ts_files,
                    "ts_count": len(ts_files),
                })
            except Exception as e:
                self._json(500, {"ok": False, "msg": str(e)})
            return

        # ── DEBUG: Probe stream saja ─────────────────────────────────────────
        if path == "/debug/probe":
            sn = qs.get("sn",[""])[0]; ch = qs.get("ch",["1"])[0]
            if not sn:
                self._json(400, {"ok": False, "msg": "sn required"}); return
            azdome_token = self._get_azdome_token()
            # Ambil URL stream
            r = api_call(f"/lingdu-app/api/monitoring/stream-url?deviceSn={sn}&channelNo={ch}&zlmEnable=1", azdome_token=azdome_token)
            if not r or not r.get("data"):
                self._json(500, {"ok": False, "msg": "Gagal ambil URL stream", "api_resp": r}); return
            stream_url = r["data"]
            probe_info = probe_stream(stream_url)
            self._json(200, {
                "ok": True,
                "stream_url": stream_url[:80] + "...",
                "probe": probe_info
            })
            return

        if path == "/get-stream":
            sn = qs.get("sn",[""])[0]; ch = qs.get("ch",["1"])[0]
            if not sn: self._json(400,{"ok":False,"msg":"sn required"}); return

            self.send_response(200); self._cors()
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            
            # Initial ping/comment to force Cloudflare to open the stream immediately
            self.wfile.write(b": connection established\n\n")
            self.wfile.flush()

            def send_event(etype, data):
                try:
                    msg = data if isinstance(data, str) else json.dumps(data)
                    self.wfile.write(f"event: {etype}\ndata: {msg}\n\n".encode())
                    self.wfile.flush()
                except Exception as e:
                    print(f"[SSE] send_event error: {e}")

            try:
                azdome_token = self._get_azdome_token()
                if not azdome_token:
                    print("[STREAM] WARNING: azdome_token tidak tersedia, fallback ke TOKEN_AZDOME")
                    send_event("progress", "WARNING: Token tidak ada, menggunakan cadangan...")

                print(f"[STREAM] START: sn={sn}, ch={ch}")
                stream_url = get_stream_url_sse(sn, ch, send_event, azdome_token=azdome_token)

                if stream_url:
                    sid_key = f"{sn}_{ch}"

                    # ── PROBE: Deteksi codec sebelum mulai encode ──────────
                    send_event("progress", "Mendeteksi codec kamera...")
                    probe_info = probe_stream(stream_url)
                    if probe_info.get('error') and not probe_info.get('has_video'):
                        send_event("progress", f"Probe gagal: {probe_info['error']} — mencoba default...")
                    else:
                        codec_msg = f"Codec: video={probe_info.get('video_codec','?')}, audio={probe_info.get('audio_codec','?')}"
                        send_event("progress", codec_msg)

                    start_hls(stream_url, sid_key, probe_info=probe_info)
                    send_event("progress", "Menunggu segmen HLS pertama...")

                    ready = False
                    for i in range(350):
                        with hls_lock:
                            ready = hls_sessions.get(sid_key, {}).get("ready", False)
                        if ready:
                            print(f"[HLS] Ready after {i*100}ms")
                            break
                        if i > 0 and i % 30 == 0:
                            send_event("progress", f"Encoder berjalan... ({i*0.1:.0f}s)")
                        with hls_lock:
                            s_info = hls_sessions.get(sid_key, {})
                        if s_info.get("proc") and s_info["proc"].poll() is not None:
                            rc = s_info["proc"].returncode
                            _print_ffmpeg_log(s_info.get("log",""), sid_key, rc)

                            # ── FALLBACK BERTINGKAT: exit code apapun ──────
                            video_codec_info = probe_info.get('video_codec', 'unknown') if probe_info else 'unknown'
                            audio_codec_info = probe_info.get('audio_codec', 'unknown') if probe_info else 'unknown'

                            # Level 1: Coba tanpa audio
                            send_event("progress", f"Encoder gagal (exit={rc}). Mencoba fallback tanpa audio...")
                            print(f"[HLS] Exit={rc} detected, trying no-audio fallback for {sid_key}")
                            with hls_lock:
                                s_fb = hls_sessions.get(sid_key, {})
                            fb_ok = _try_fallback_no_audio(
                                stream_url, sid_key,
                                s_fb.get("dir",""), s_fb.get("m3u8",""), s_fb.get("log",""),
                                probe_info=probe_info
                            )
                            if fb_ok:
                                send_event("progress", "Fallback berhasil! (tanpa audio)")
                                ready = True
                                break

                            # Level 2: Force software decode dengan toleransi maksimal
                            send_event("progress", f"Fallback audio gagal. Mencoba force-decode video ({video_codec_info})...")
                            print(f"[HLS] No-audio fallback failed, trying force-decode for {sid_key}")
                            with hls_lock:
                                s_fb2 = hls_sessions.get(sid_key, {})
                            fb2_ok = _try_fallback_force_decode(
                                stream_url, sid_key,
                                s_fb2.get("dir",""), s_fb2.get("m3u8",""), s_fb2.get("log",""),
                                probe_info=probe_info
                            )
                            if fb2_ok:
                                send_event("progress", "Force-decode berhasil! (mode kompatibilitas)")
                                ready = True
                                break

                            # Semua fallback gagal
                            err_msg = (
                                f"Encoder gagal (exit={rc}). "
                                f"Codec kamera tidak didukung: video={video_codec_info}, audio={audio_codec_info}. "
                                f"Cek /debug/hls-log?sn={sn}&ch={ch}"
                            )
                            send_event("done", {"ok": False, "msg": err_msg})
                            return
                        time.sleep(0.1)

                    if not ready:
                        with hls_lock:
                            s_info = hls_sessions.get(sid_key, {})
                        m3u8_path = s_info.get("m3u8","")
                        if not (m3u8_path and os.path.exists(m3u8_path) and os.path.getsize(m3u8_path) > 0):
                            send_event("done", {"ok": False, "msg": "Encoder timeout 35s. Format kamera tidak kompatibel."})
                            return
                        send_event("progress", "Stream mungkin siap, mencoba...")

                    with hls_lock:
                        s_info = hls_sessions.get(sid_key, {})
                    m3u8_path = s_info.get("m3u8","")
                    m3u8_valid = False
                    try:
                        if m3u8_path and os.path.exists(m3u8_path):
                            with open(m3u8_path,'r') as f:
                                content = f.read()
                            m3u8_valid = "#EXTM3U" in content and "#EXTINF" in content and ".ts" in content
                    except: pass

                    if m3u8_valid:
                        send_event("done", {"ok": True, "hls": f"/hls/{sid_key}/live.m3u8"})
                        print(f"[STREAM] SUCCESS: /hls/{sid_key}/live.m3u8")
                    else:
                        send_event("done", {"ok": False, "msg": "Stream tidak valid. Format kamera tidak didukung."})
                else:
                    send_event("done", {"ok": False, "msg": "Kamera offline atau tidak merespons"})
            except Exception as e:
                print(f"[STREAM] ERROR: {e}")
                send_event("done", {"ok": False, "msg": f"Error: {e}"})
            return

        if path == "/stop-stream":
            sn = qs.get("sn",[""])[0]; ch = qs.get("ch",["1"])[0]
            sid = f"{sn}_{ch}"
            with hls_lock:
                if sid in hls_sessions:
                    s = hls_sessions.pop(sid)
                    try:
                        if s.get("proc"): s["proc"].kill()
                        if s.get("dir"):  shutil.rmtree(s["dir"],ignore_errors=True)
                    except: pass
            self._json(200,{"ok":True}); return

        if path == "/auth/me":
            s = self._require_auth()
            if not s: return
            self._json(200,{"ok":True,"user":{"name":s.get("name","")}}); return

        self._proxy_api("GET")

    def do_POST(self):
        p    = urlparse(self.path)
        path = p.path

        if path == "/auth/login":
            n    = int(self.headers.get("Content-Length",0))
            body = json.loads(self.rfile.read(n)) if n else {}
            email    = body.get("email","").strip()
            password = body.get("password","")
            if not email or not password:
                self._json(400,{"ok":False,"msg":"Email dan password wajib diisi"}); return
            azdome_data, azdome_token = azdome_login(email, password)
            if not azdome_data or not azdome_token:
                self._json(401,{"ok":False,"msg":"Email atau password AZDOME salah"}); return
            user_info = azdome_data.get("user", {})
            name = user_info.get("nickname") or user_info.get("name") or email
            token = session_create(azdome_token, name, email)
            print(f"[AUTH] Login: {email} → session created, name={name}")
            self.send_response(200); self._cors()
            self.send_header("Content-Type","application/json")
            self.send_header("Set-Cookie",
                f"azdome_session={token}; Path=/; Max-Age={SESSION_TTL}; HttpOnly; SameSite=Strict")
            self.end_headers()
            self.wfile.write(json.dumps({"ok":True,"name":name}).encode())
            return

        user = self._require_auth()
        if not user: return
        self._proxy_api("POST")

    def do_DELETE(self):
        p = urlparse(self.path)
        user = self._require_auth()
        if not user: return
        self._proxy_api("DELETE")

    def _redirect(self, loc):
        self.send_response(302); self.send_header("Location",loc); self.end_headers()

    def _serve_file(self, filepath, filename):
        ext = filename.rsplit(".",1)[-1]
        ct  = {"m3u8":"application/vnd.apple.mpegurl","ts":"video/mp2t"}.get(ext,"application/octet-stream")
        for _ in range(20):
            if os.path.exists(filepath): break
            time.sleep(0.1)
        try:
            with open(filepath,"rb") as f: data = f.read()
            self.send_response(200); self._cors()
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(data)))
            # Safari native HLS: WAJIB no-cache agar tidak stuck di playlist lama
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.send_header("Connection", "keep-alive")
            self.end_headers(); self.wfile.write(data)
        except:
            self.send_response(404); self._cors(); self.end_headers()

    def _proxy_media(self, path):
        local_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), path.strip("/"))
        if os.path.exists(local_path):
            self._serve_file(local_path, os.path.basename(local_path))
            return
            
        url = MEDIA_BASE + path
        req = urllib.request.Request(url)
        req.add_header("User-Agent","Mozilla/5.0")
        try:
            resp = urllib.request.urlopen(req, timeout=10)
            raw  = resp.read()
            ct   = resp.getheader("Content-Type","image/png")
            self.send_response(200); self._cors()
            self.send_header("Content-Type",ct)
            self.send_header("Content-Length",str(len(raw)))
            self.send_header("Cache-Control","max-age=86400")
            self.end_headers(); self.wfile.write(raw)
        except:
            self.send_response(200); self._cors()
            self.send_header("Content-Type","image/png")
            self.send_header("Content-Length",str(len(DEFAULT_THUMBNAIL)))
            self.end_headers(); self.wfile.write(DEFAULT_THUMBNAIL)

    def _serve_html(self):
        try:
            with open(DASHBOARD,"rb") as f: data = f.read()
            self.send_response(200)
            self.send_header("Content-Type","text/html; charset=utf-8")
            self.send_header("Content-Length",str(len(data)))
            self.end_headers(); self.wfile.write(data)
        except FileNotFoundError:
            self.send_response(404); self.end_headers()
            self.wfile.write(b"azdome-dashboard.html not found")

    def _proxy_api(self, method):
        url  = API_BASE + self.path
        body = None
        if method == "POST":
            n    = int(self.headers.get("Content-Length",0))
            body = self.rfile.read(n) if n else b"{}"
        azdome_token = self._get_azdome_token() or TOKEN_AZDOME
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Authorization", azdome_token)
        req.add_header("App-Key","AZDOME")
        req.add_header("Content-Type","application/json")
        req.add_header("Accept-Encoding","gzip")
        req.add_header("User-Agent","okhttp/3.14.9")
        try:
            resp = urllib.request.urlopen(req, timeout=15)
            raw  = resp.read()
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
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        # Safari native HLS memerlukan ini agar bisa baca header dari M3U8 response
        self.send_header("Access-Control-Expose-Headers", "Content-Type, Content-Length, Cache-Control")

    def log_message(self, fmt, *args):
        path = urlparse(self.path).path
        if path not in ["/favicon.ico"]:
            print(f"[{time.strftime('%H:%M:%S')}] {path} {args[1] if len(args)>1 else ''}")


if __name__ == "__main__":
    has_ffmpeg  = bool(shutil.which("ffmpeg"))
    has_ffprobe = bool(shutil.which("ffprobe"))
    print(f"""
╔══════════════════════════════════════╗
║   AZDOME SERVER v11.0 (NO-DB)       ║
╠══════════════════════════════════════╣
║  URL     : http://0.0.0.0:{PORT:<5}   ║
║  ffmpeg  : {'OK' if has_ffmpeg  else 'TIDAK ADA!'}                            ║
║  ffprobe : {'OK' if has_ffprobe else 'TIDAK ADA!'}                            ║
║  Auth    : AZDOME login only          ║
║  Session : in-memory (no DB)          ║
╠══════════════════════════════════════╣
║  DEBUG:                               ║
║  GET /debug/hls-log?sn=SN&ch=1       ║
║  GET /debug/probe?sn=SN&ch=1         ║
╚══════════════════════════════════════╝
""")
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    srv.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[AZDOME] Stopped.")
        with hls_lock:
            for s in hls_sessions.values():
                try: s["proc"].kill(); shutil.rmtree(s["dir"],ignore_errors=True)
                except: pass
# AZDOME Fleet Dashboard (Aldzama)

Dashboard web sederhana untuk memantau perangkat/kendaraan AZDOME (GPS, status online, event, dan live stream) lewat API Lingdu/AZDOME, dengan server proxy + HLS transcoding berbasis `ffmpeg`.

## Fitur

- Login menggunakan akun AZDOME (cookie session `HttpOnly`, in-memory)
- Daftar kendaraan + indikator status (hijau/kuning/abu-abu)
- Lokasi terakhir (GPS) + peta
- Event/rekaman
- Live view: ambil URL stream → transcode ke HLS (`.m3u8` + `.ts`)
- Proxy endpoint `GET/POST/DELETE` ke `API_BASE` (`/lingdu-app/api/...`)
- Endpoint debug untuk melihat log/probe HLS

## Menjalankan (Docker) — direkomendasikan

Prasyarat: Docker + Docker Compose.

```bash
docker compose up --build
```

Akses:
- `http://localhost:8899`

Konfigurasi port:
- `docker-compose.yml` memetakan `8899:8899`
- Env `PORT` dibaca dari `azdome-server.py` (default `8899`)

## Menjalankan (tanpa Docker)

Prasyarat:
- Python 3.11+
- `ffmpeg` dan `ffprobe` tersedia di PATH

Jalankan:

```bash
set PORT=8899
python azdome-server.py
```

Lalu buka `http://localhost:8899`.

## Alur Live Stream (HLS)

Dashboard memanggil endpoint SSE:

- `GET /get-stream?sn=<DEVICE_SN>&ch=<CHANNEL>`

Server akan:
1) wake up device (opsional),
2) ambil URL stream dari API,
3) `ffprobe` untuk deteksi codec,
4) jalankan `ffmpeg` untuk HLS,
5) kirim event `done` berisi path HLS: `/hls/<sn>_<ch>/live.m3u8`.

Stop stream:
- `GET /stop-stream?sn=<DEVICE_SN>&ch=<CHANNEL>`

## Debugging

- `GET /debug/hls-log?sn=<DEVICE_SN>&ch=1` — tail log ffmpeg + info session
- `GET /debug/m3u8-content?sn=<DEVICE_SN>&ch=1` — isi m3u8 + list segmen `.ts`
- `GET /debug/probe?sn=<DEVICE_SN>&ch=1` — probe codec stream saja

## Status Device → Warna Indikator

Indikator status (dot di list kendaraan) mengikuti `onlineState`:

- `onlineState = 1` → **hijau** (online)
- `onlineState = 2` → **kuning** (sleep)
- selain itu → **abu-abu** (offline/unknown)

Catatan implementasi:
- `onlineState` yang stabil diambil dari endpoint `GET /lingdu-app/api/location/last-location?deviceSn=...`.
- Jika `onlineState` belum tersedia, UI fallback sementara ke `mqttState` (anggap online jika `mqttState === 1`).

## Struktur Repo

- `azdome-server.py` — HTTP server + proxy API + HLS transcoding
- `azdome-dashboard.html` — single-file dashboard UI
- `media/` — aset (logo, dsb) + output HLS/log saat runtime
- `Dockerfile`, `docker-compose.yml` — packaging + runtime container
- `init.sql` — legacy/opsional (tidak dipakai oleh mode `NO-DB` di server saat ini)

## Catatan Keamanan / Produksi

Project ini cocok untuk internal tooling.

- Session disimpan **in-memory** (restart server = logout semua)
- Ada fallback token hardcoded (`TOKEN_AZDOME`) di `azdome-server.py` untuk kasus tertentu; untuk produksi sebaiknya dihapus dan wajib login.
- Tidak ada TLS/HTTPS bawaan (pakai reverse proxy jika dibutuhkan).


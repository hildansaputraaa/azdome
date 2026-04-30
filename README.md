# AZDOME Fleet Dashboard (Aldzama)

Dashboard web untuk memantau perangkat/kendaraan AZDOME (GPS, status online, event, dan live stream) melalui API Lingdu/AZDOME, dengan server proxy + HLS transcoding berbasis `ffmpeg`.

**Code by [github.com/hildansaputraaa](https://github.com/hildansaputraaa)**

## Fitur Unggulan

- **Single Session Enforcement**: Mencegah satu akun digunakan secara bersamaan. Login baru otomatis mengeluarkan (logout) sesi lama.
- **Auto-Sorting Kendaraan**: Daftar kendaraan otomatis diurutkan berdasarkan status: **Online (Hijau)** di paling kiri, diikuti **Sleep (Kuning)**, dan **Offline (Abu-abu)**.
- **Live Stream HLS**: Mengambil stream RTSP/RTMP dari kamera dan melakukan transcoding ke HLS secara real-time.
- **Cloudflare Tunnel Ready**: Optimasi header dan SSE pings untuk menjaga koneksi tetap stabil saat menggunakan Cloudflare Tunnel.
- **Peta Lokasi Real-time**: Integrasi Leaflet.js dengan tracking kecepatan dan status mesin (ACC).
- **Info Kartu SIM**: Memantau paket data, masa berlaku, dan sisa kuota SIM card pada perangkat.

## Cara Menjalankan (Docker) — Direkomendasikan

Prasyarat: Docker + Docker Compose.

**Konfigurasi Environment (Opsional):**
Salin file `.env.example` menjadi `.env` lalu sesuaikan konfigurasi port dan kredensial database jika diperlukan.
```bash
cp .env.example .env
```

Jalankan container:
```bash
docker compose up --build -d
```

Akses:
- `http://localhost:8899` (atau domain tunnel Anda)

-- AKUN DEFAULT (dibuat otomatis oleh server saat startup)
--   Email    : admin@aldzama.com
--   Password : Admin@1234
--   Role     : superuser
-- SEGERA GANTI PASSWORD SETELAH LOGIN PERTAMA!


## Cara Menjalankan (Tanpa  Docker)

Prasyarat:
- Python 3.11+
- `ffmpeg` dan `ffprobe` tersedia di PATH sistem.

Jalankan:
```bash
# Windows
set PORT=8899
python azdome-server.py

# Linux/Mac
PORT=8899 python3 azdome-server.py
```

## Struktur Proyek

- `azdome-server.py`: Backend server (Python) yang menangani proxy API, manajemen sesi, dan transcoding HLS.
- `azdome-dashboard.html`: Antarmuka pengguna (Frontend) berbasis HTML5/JS.
- `media/`: Folder untuk menyimpan aset (logo, favicon) dan output HLS sementara.
- `Dockerfile` & `docker-compose.yml`: Konfigurasi containerization.

## Catatan Teknis

- **Manajemen Sesi**: Sesi disimpan di memory (in-memory). Jika server direstart, semua pengguna harus login ulang.
- **Keamanan**: Menggunakan cookie `HttpOnly` dan `SameSite=Strict` untuk keamanan sesi.
- **Transcoding**: `ffmpeg` digunakan untuk mengubah stream mentah menjadi segmen `.ts` agar bisa diputar di browser (Chrome/Edge/Safari).

---
*Dikembangkan untuk PT Aldzama oleh hildansaputraaa.*


# AZDOME Fleet Dashboard (Aldzama)

Dashboard web untuk memantau perangkat/kendaraan AZDOME (GPS, status online, event, dan live stream) melalui API Lingdu/AZDOME, dengan server proxy + HLS transcoding berbasis `ffmpeg`. Terintegrasi dengan sistem Autentikasi Internal (DB-backed) dan Role-Based Access Control (RBAC).

**Code by [github.com/hildansaputraaa](https://github.com/hildansaputraaa)**

## Fitur Unggulan

- **Sistem Autentikasi Internal**: Tidak lagi bergantung sepenuhnya pada login Azdome di sisi user. Menggunakan sistem login internal berbasis database MySQL (aman dan terpusat).
- **Role-Based Access Control (RBAC)**: Terdapat role `superuser` (akses penuh ke seluruh sistem) dan `viewer` (hanya bisa memantau dashboard).
- **Admin Panel**: Dashboard khusus `superuser` untuk mengelola data user, memantau riwayat login, menyimpan *credentials* Azdome pusat, dan mengatur token.
- **Log Aktivitas Login**: Pencatatan riwayat masuk (audit log) setiap pengguna secara mendetail, mencakup waktu, alamat IP, dan perangkat (User-Agent).
- **Single Session Enforcement**: Mencegah satu akun digunakan secara bersamaan. Login baru akan otomatis memutus (logout) sesi lama.
- **Auto-Sorting Kendaraan**: Daftar kendaraan otomatis diurutkan berdasarkan status: **Online (Hijau)** di paling kiri, diikuti **Sleep (Kuning)**, dan **Offline (Abu-abu)**.
- **Live Stream HLS**: Mengambil stream RTSP/RTMP dari kamera dan melakukan transcoding ke format HLS secara real-time.
- **Cloudflare Tunnel Ready**: Optimasi header dan SSE pings untuk menjaga koneksi tetap stabil dan tidak terputus saat menggunakan Cloudflare Tunnel.

## Cara Menjalankan (Docker) — Direkomendasikan

Prasyarat: Docker + Docker Compose.

```bash
docker compose up --build -d
```

Akses:
- `http://localhost:8899` (atau domain tunnel Anda)

> **⚠️ PERHATIAN: AKUN DEFAULT SUPERUSER**  
> Akun di bawah ini akan dibuat otomatis oleh database saat pertama kali dijalankan:
> - **Email**: `admin@aldzama.com`
> - **Password**: `Admin@1234`
> - **Role**: `superuser`
> 
> *SEGERA GANTI PASSWORD SETELAH LOGIN PERTAMA!*

## Cara Menjalankan (Tanpa Docker)

Prasyarat:
- Python 3.11+
- `ffmpeg` dan `ffprobe` tersedia di PATH sistem.
- MySQL Server (sesuaikan variabel env `DB_HOST`, `DB_USER`, `DB_PASS`, `DB_NAME` jika diperlukan).

Jalankan:
```bash
# Windows
set PORT=8899
python azdome-server.py

# Linux/Mac
PORT=8899 python3 azdome-server.py
```

## Struktur Proyek

- `azdome-server.py`: Backend server (Python) yang menangani routing API, autentikasi DB-backed, dan HLS encoder.
- `azdome-dashboard.html`: Antarmuka utama pemantauan kendaraan (Frontend Viewer).
- `azdome-admin.html`: Antarmuka panel kontrol untuk Superuser (Frontend Admin).
- `init.sql`: Skema *database* MySQL untuk tabel pengguna, sesi, konfigurasi Azdome, dan log aktivitas.
- `media/`: Folder untuk penyimpanan statis (logo perusahaan, favicon) dan output buffer HLS.
- `Dockerfile` & `docker-compose.yml`: Konfigurasi *containerization* dan orkestrasi *service* (Web & DB).

## Catatan Teknis

- **Manajemen Sesi**: Sesi tidak lagi in-memory. Seluruh data kredensial dan sesi kini persisten disimpan di database (`internal_sessions`), tetap aman meskipun server direstart.
- **Keamanan Sandi**: Menggunakan *hashing password* PBKDF2 (SHA-256) untuk mengenkripsi kata sandi pengguna internal.
- **Proteksi Akses**: Menggunakan proteksi 403 Forbidden kustom jika role `viewer` mencoba mengakses halaman manajemen Admin.
- **Transcoding**: Mengandalkan modul `subprocess` pada Python untuk memerintah `ffmpeg` dalam mengonversi stream mentah menjadi chunk video (`.ts`) yang *web-friendly*.

---
*Dikembangkan untuk PT Aldzama oleh hildansaputraaa.*

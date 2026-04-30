-- ═══════════════════════════════════════════════════════
-- AZDOME Fleet Monitor - Schema Internal Auth
-- code by github.com/hildansaputraaa
-- ═══════════════════════════════════════════════════════

-- Tabel user internal perusahaan
-- Role: superuser > viewer
CREATE TABLE IF NOT EXISTS internal_users (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  email         VARCHAR(128) NOT NULL UNIQUE,
  password_hash VARCHAR(512) NOT NULL,   -- format: salt:pbkdf2_sha256_hex
  role          ENUM('superuser','viewer') NOT NULL DEFAULT 'viewer',
  full_name     VARCHAR(128),
  is_active     TINYINT(1) NOT NULL DEFAULT 1,
  last_login    DATETIME NULL,
  created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- Sesi login internal (DB-backed)
CREATE TABLE IF NOT EXISTS internal_sessions (
  token       VARCHAR(128) NOT NULL PRIMARY KEY,
  user_id     INT NOT NULL,
  role        VARCHAR(32) NOT NULL,
  full_name   VARCHAR(128),
  email       VARCHAR(128),
  expires_at  DATETIME NOT NULL,
  created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id) REFERENCES internal_users(id) ON DELETE CASCADE
);

-- Konfigurasi Azdome: credentials + token terpusat
CREATE TABLE IF NOT EXISTS azdome_config (
  config_key   VARCHAR(64) NOT NULL PRIMARY KEY,
  config_value TEXT,
  updated_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

INSERT IGNORE INTO azdome_config (config_key, config_value) VALUES
  ('azdome_email',            NULL),
  ('azdome_password',         NULL),
  ('azdome_token',            NULL),
  ('azdome_token_expires_at', NULL);

-- Log Aktivitas Login
CREATE TABLE IF NOT EXISTS login_logs (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  user_id       INT NOT NULL,
  email         VARCHAR(128) NOT NULL,
  full_name     VARCHAR(128),
  role          VARCHAR(32),
  ip_address    VARCHAR(64),
  user_agent    TEXT,
  login_time    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id) REFERENCES internal_users(id) ON DELETE CASCADE
);

-- ═══════════════════════════════════════════════════════
-- AKUN DEFAULT (dibuat otomatis oleh server saat startup)
--   Email    : admin@aldzama.com
--   Password : Admin@1234
--   Role     : superuser
-- SEGERA GANTI PASSWORD SETELAH LOGIN PERTAMA!
-- ═══════════════════════════════════════════════════════

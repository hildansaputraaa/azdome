CREATE TABLE IF NOT EXISTS users (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  username    VARCHAR(64) NOT NULL UNIQUE,
  password    VARCHAR(256) NOT NULL,
  role        ENUM('admin','operator','viewer') NOT NULL DEFAULT 'viewer',
  full_name   VARCHAR(128),
  is_active   TINYINT(1) NOT NULL DEFAULT 1,
  last_login  DATETIME,
  created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sessions (
  token           VARCHAR(128) NOT NULL PRIMARY KEY,
  user_id         INT NOT NULL,
  azdome_token    TEXT,
  azdome_user_id  INT,
  expires_at      DATETIME NOT NULL,
  created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- Default: admin / Admin@1234  (SEGERA GANTI SETELAH LOGIN PERTAMA)
INSERT IGNORE INTO users (username, password, role, full_name) VALUES (
  'admin',
  '$2b$12$vTjvDIDfF1ak/DNcKt5O2.ichCz3UmZPQ8tJbv7SfyRglk/SelGXK',
  'admin',
  'Administrator'
);

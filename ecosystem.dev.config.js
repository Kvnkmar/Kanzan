/**
 * Kanzen — Development PM2 Configuration
 *
 * Uses Django runserver (auto-reload on Python changes) and
 * Celery worker with watchdog-based autoreload.
 *
 * Start:  pm2 start ecosystem.dev.config.js
 * Stop:   pm2 delete ecosystem.dev.config.js
 * Or use: make dev / make dev-stop
 */

const PROJECT_ROOT = "/home/kavin/Kanzen";
const VENV_BIN = `${PROJECT_ROOT}/env/bin`;

const COMMON_CONFIG = {
  cwd: PROJECT_ROOT,
  instances: 1,
  exec_mode: "fork",
  autorestart: true,
  max_restarts: 50,
  min_uptime: "3s",
  log_date_format: "YYYY-MM-DD HH:mm:ss Z",
  merge_logs: true,
};

const BASE_ENV = {
  DJANGO_SETTINGS_MODULE: "main.settings",
  PYTHONUNBUFFERED: "1",
  VIRTUAL_ENV: `${PROJECT_ROOT}/env`,
  PATH: `${VENV_BIN}:/usr/local/bin:/usr/bin:/bin`,
};

module.exports = {
  apps: [
    {
      // Django runserver with auto-reload (restarts on .py changes automatically)
      name: "kanzan-django",
      script: `${VENV_BIN}/python`,
      args: "manage.py runserver 0.0.0.0:8001",
      interpreter: "none",
      ...COMMON_CONFIG,
      max_memory_restart: "2G",
      error_file: `${PROJECT_ROOT}/logs/django-error.log`,
      out_file: `${PROJECT_ROOT}/logs/django-out.log`,
      env: BASE_ENV,
    },

    {
      // Celery worker — PM2 watches .py files and restarts on change
      name: "kanzan-celery-worker",
      script: `${VENV_BIN}/celery`,
      args: "-A main worker -Q kanzan_default,kanzan_email,kanzan_webhooks -c 2 -l info --pool prefork -n kanzan-dev-worker@%h --max-tasks-per-child=50",
      interpreter: "none",
      ...COMMON_CONFIG,
      max_memory_restart: "1G",
      kill_timeout: 10000,
      // Watch Python files for auto-restart
      watch: [
        "apps/*/tasks.py",
        "apps/*/services.py",
        "main/celery.py",
      ],
      watch_delay: 2000,
      ignore_watch: ["__pycache__", "*.pyc", "logs", "env", "static", "media"],
      error_file: `${PROJECT_ROOT}/logs/celery-worker-error.log`,
      out_file: `${PROJECT_ROOT}/logs/celery-worker-out.log`,
      env: { ...BASE_ENV, C_FORCE_ROOT: "true" },
    },

    {
      // Celery beat — lightweight, rarely needs restart
      name: "kanzan-celery-beat",
      script: `${VENV_BIN}/celery`,
      args: "-A main beat -l info",
      interpreter: "none",
      ...COMMON_CONFIG,
      max_memory_restart: "512M",
      error_file: `${PROJECT_ROOT}/logs/celery-beat-error.log`,
      out_file: `${PROJECT_ROOT}/logs/celery-beat-out.log`,
      env: { ...BASE_ENV, C_FORCE_ROOT: "true" },
    },

    {
      // Flower — optional in dev, useful for debugging tasks
      name: "kanzan-flower",
      script: `${VENV_BIN}/celery`,
      args: `-A main flower --port=5556 --url_prefix=flower --basic_auth=${process.env.KANZAN_FLOWER_AUTH || "admin:changeme"}`,
      interpreter: "none",
      ...COMMON_CONFIG,
      max_memory_restart: "512M",
      error_file: `${PROJECT_ROOT}/logs/flower-error.log`,
      out_file: `${PROJECT_ROOT}/logs/flower-out.log`,
      env: { ...BASE_ENV, C_FORCE_ROOT: "true" },
    },
  ],
};

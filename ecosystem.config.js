/**
 * Kanzen - PM2 Process Manager Configuration
 *
 * Port allocation (avoids conflicts with Tempest on port 8000):
 *   ASGI server: Unix socket /tmp/kanzan.sock (Nginx proxies 8001 here)
 *   Redis: db3 (cache), db4 (Celery broker), db5 (Channels layer)
 *   Celery queues: kanzan_default, kanzan_email, kanzan_webhooks
 *   Flower: port 5556 (Tempest uses 5555)
 */

const PROJECT_ROOT = "/home/kavin/Kanzen";
const VENV_BIN = `${PROJECT_ROOT}/.venv/bin`;

const COMMON_CONFIG = {
  cwd: PROJECT_ROOT,
  instances: 1,
  exec_mode: "fork",
  watch: false,
  autorestart: true,
  max_restarts: 10,
  min_uptime: "10s",
  log_date_format: "YYYY-MM-DD HH:mm:ss Z",
  merge_logs: true,
};

const BASE_ENV = {
  DJANGO_SETTINGS_MODULE: "main.settings",
  PYTHONUNBUFFERED: "1",
  VIRTUAL_ENV: `${PROJECT_ROOT}/.venv`,
  PATH: `${VENV_BIN}:/usr/local/bin:/usr/bin:/bin`,
};

module.exports = {
  apps: [
    {
      name: "kanzan-django",
      script: `${VENV_BIN}/gunicorn`,
      args: "main.asgi:application -w 2 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8001 --timeout 120 --graceful-timeout 30",
      interpreter: "none",
      ...COMMON_CONFIG,
      max_memory_restart: "2G",
      kill_timeout: 8000,
      error_file: `${PROJECT_ROOT}/logs/django-error.log`,
      out_file: `${PROJECT_ROOT}/logs/django-out.log`,
      env: BASE_ENV,
    },

    {
      name: "kanzan-celery-worker",
      script: `${VENV_BIN}/celery`,
      args: "-A main worker -Q kanzan_default,kanzan_email,kanzan_webhooks -c 4 -l info --pool prefork -n kanzan-worker@%h --max-tasks-per-child=200",
      interpreter: "none",
      ...COMMON_CONFIG,
      max_memory_restart: "2G",
      kill_timeout: 15000,
      error_file: `${PROJECT_ROOT}/logs/celery-worker-error.log`,
      out_file: `${PROJECT_ROOT}/logs/celery-worker-out.log`,
      env: { ...BASE_ENV, C_FORCE_ROOT: "true" },
    },

    {
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

    {
      name: "kanzan-smtp",
      script: `${VENV_BIN}/python`,
      args: "manage.py run_smtp_server",
      interpreter: "none",
      ...COMMON_CONFIG,
      max_memory_restart: "512M",
      kill_timeout: 8000,
      error_file: `${PROJECT_ROOT}/logs/smtp-error.log`,
      out_file: `${PROJECT_ROOT}/logs/smtp-out.log`,
      env: BASE_ENV,
    },
  ],
};

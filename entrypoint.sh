#!/usr/bin/env bash
set -euo pipefail

DB_HOST="${DB_HOST:-db}"
DB_PORT="${DB_PORT:-5432}"

echo "Waiting for ${DB_HOST}:${DB_PORT} (nc)..."
until nc -z "${DB_HOST}" "${DB_PORT}" >/dev/null 2>&1; do
  sleep 1
done
echo "Database is up!"

python manage.py makemigrations --noinput
python manage.py migrate --noinput
exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 2 --timeout 300
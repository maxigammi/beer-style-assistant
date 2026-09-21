#!/usr/bin/env bash
# Проверка готовности сервера. Секретов не печатает: только «задано / не задано».
# Запуск из каталога приложения:  deploy/preflight.sh
# Код возврата: 0 — всё в порядке, 1 — есть блокирующие проблемы.
set -u
cd "$(dirname "$0")/.."

fail=0
ok()   { printf '  [ok]   %s\n' "$1"; }
bad()  { printf '  [FAIL] %s\n' "$1"; fail=1; }
warn() { printf '  [warn] %s\n' "$1"; }

echo "Python"
if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY=python3; fi
if $PY -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
  ok "$($PY --version)"
else
  bad "нужен Python 3.11+, найден: $($PY --version 2>&1)"
fi

echo "Настройки (.env)"
if [ -f .env ]; then
  mode=$(stat -c '%a' .env)
  [ "$mode" = "600" ] && ok ".env права 600" || bad ".env права $mode, нужны 600 (chmod 600 .env)"
  for key in TELEGRAM_TOKEN ADMIN_IDS OPENAI_API_KEY GIGACHAT_AUTH_KEY; do
    if grep -qE "^${key}=.+" .env; then ok "$key задан"; else bad "$key не задан"; fi
  done
else
  bad ".env не найден (cp .env.example .env и заполнить)"
fi

echo "Сеть (HTTP-код; 000 = нет соединения или не прошла проверка сертификата)"
check() {  # имя, url, ожидаемые коды через пробел
  code=$(curl -s -o /dev/null -m 15 -w '%{http_code}' "$2")
  case " $3 " in *" $code "*) ok "$1: $code";; *) bad "$1: $code (ожидалось: $3)";; esac
}
check "Telegram API"         https://api.telegram.org/                          "200 302 404"
check "OpenAI API"           https://api.openai.com/v1/models                   "401"
check "GigaChat OAuth (сертификат Минцифры)" https://ngw.devices.sberbank.ru:9443/api/v2/oauth "405 401"
check "GigaChat API (сертификат Минцифры)"   https://api.giga.chat/v1/models    "401"

echo "Данные"
[ -f data/index/index.faiss ] && ok "индекс есть" || warn "индекса нет: выполнить scripts/ingest.py"
[ -d data/styles ] && [ "$(ls data/styles/*.md 2>/dev/null | wc -l)" -ge 30 ] \
  && ok "база знаний: $(ls data/styles/*.md | wc -l) файлов" || bad "нет data/styles/*.md"

echo
if [ $fail -eq 0 ]; then echo "Итог: готово к запуску"; else echo "Итог: есть проблемы, запускать нельзя"; fi
exit $fail

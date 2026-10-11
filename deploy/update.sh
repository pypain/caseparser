#!/usr/bin/env bash
# Update only the existing caseparser service. Preserve database, TLS and password.
set -euo pipefail
umask 077
cd "$(dirname "$0")/.."
[[ $EUID -eq 0 ]] || { echo 'Запусти от root'; exit 1; }
[[ -d /opt/caseparser/app && -f /etc/caseparser/environment ]] || { echo 'Сначала требуется установка caseparser'; exit 1; }
[[ $(systemctl show caseparser -p FragmentPath --value) == /etc/systemd/system/caseparser.service ]] || { echo 'Неожиданный сервис, обновление остановлено'; exit 1; }
systemctl cat caseparser | grep -q 'ExecStart=.* /opt/caseparser/app/app.py' || { echo 'Неожиданный путь приложения'; exit 1; }
python3 -m unittest discover -s tests -q
TASK_STAMP=$(date -u +%Y%m%dT%H%M%SZ)
TASK_STAGE="/opt/caseparser/staging-$TASK_STAMP"
TASK_BACKUP="/opt/caseparser/backup-$TASK_STAMP"
[[ ! -e $TASK_STAGE && ! -e $TASK_BACKUP ]] || { echo 'Каталог обновления уже существует'; exit 1; }
install -d -m 755 "$TASK_STAGE"
tar -cf - app.py auth.py collect.py sources.py static README.md | tar -xf - -C "$TASK_STAGE"
find "$TASK_STAGE" -type d -exec chmod 755 {} +
find "$TASK_STAGE" -type f -exec chmod 644 {} +
TASK_PORT=$(sed -n 's/^PORT=//p' /etc/caseparser/environment)
TASK_CERT=$(sed -n 's/^CASEPARSER_TLS_CERT=//p' /etc/caseparser/environment)
TASK_IP=$(openssl x509 -in "$TASK_CERT" -noout -ext subjectAltName | sed -n 's/.*IP Address:\([0-9.]*\).*/\1/p')
[[ $TASK_PORT =~ ^[0-9]+$ && -n $TASK_IP ]] || { echo 'Не удалось определить HTTPS-адрес сервиса'; exit 1; }
TASK_ACTIVE=0
systemctl is-active --quiet caseparser && TASK_ACTIVE=1
TASK_SWITCHED=0
TASK_STOPPED=0
TASK_OK=0
rollback() {
  if [[ $TASK_SWITCHED == 1 && $TASK_OK == 0 ]]; then
    echo 'Проверка обновления не прошла, возвращаю предыдущий код.'
    systemctl stop caseparser || true
    if [[ -d /opt/caseparser/app ]]; then mv /opt/caseparser/app "$TASK_STAGE-failed"; fi
    mv "$TASK_BACKUP" /opt/caseparser/app
    if [[ $TASK_ACTIVE == 1 ]]; then systemctl start caseparser || true; fi
    echo 'База, пароль, сертификат и другие сайты не менялись.'
  elif [[ $TASK_STOPPED == 1 && $TASK_OK == 0 && $TASK_ACTIVE == 1 ]]; then
    systemctl start caseparser || true
  fi
}
trap rollback EXIT
systemctl stop caseparser
TASK_STOPPED=1
if [[ -f /var/lib/caseparser/cases.sqlite3 ]]; then
  python3 - "/opt/caseparser/database-$TASK_STAMP.sqlite3" <<'PY'
import sqlite3,sys
with sqlite3.connect('/var/lib/caseparser/cases.sqlite3') as source:
    with sqlite3.connect(sys.argv[1]) as backup: source.backup(backup)
PY
fi
mv /opt/caseparser/app "$TASK_BACKUP"
TASK_SWITCHED=1
mv "$TASK_STAGE" /opt/caseparser/app
systemctl start caseparser
for task_attempt in {1..20}; do
  TASK_STATUS=$(curl --noproxy "$TASK_IP" --silent --cacert "$TASK_CERT" \
    --resolve "$TASK_IP:$TASK_PORT:127.0.0.1" -o /dev/null -w '%{http_code}' "https://$TASK_IP:$TASK_PORT/api/state" || true)
  if [[ $TASK_STATUS == 401 ]]; then TASK_OK=1; break; fi
  sleep 1
done
[[ $TASK_OK == 1 ]] || { echo 'HTTPS-проверка входа не прошла'; exit 1; }
echo "Обновлено. Предыдущий код: $TASK_BACKUP"
echo 'Обновление цен появится после запросов к источникам. Данные и пароль сохранены.'

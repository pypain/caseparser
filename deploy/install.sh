#!/usr/bin/env bash
# First installation only. Never changes nginx, existing websites or firewall rules.
set -euo pipefail
umask 077
cd "$(dirname "$0")/.."
TASK_SOURCE=$(pwd)
TASK_PORT=${CASEPARSER_DEPLOY_PORT:-8443}
TASK_IP=${CASEPARSER_DEPLOY_IP:-107.161.175.52}
[[ $EUID -eq 0 ]] || { echo 'Запусти установку от root'; exit 1; }
[[ $TASK_PORT =~ ^[0-9]+$ ]] && (( TASK_PORT >= 1024 && TASK_PORT <= 65535 )) || { echo 'Нужен порт от 1024 до 65535'; exit 1; }
[[ $TASK_IP =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo 'Укажи IPv4'; exit 1; }
for task_command in python3 openssl systemctl ss useradd install tar curl; do
  command -v "$task_command" >/dev/null || { echo "Не найдена команда: $task_command. Установка не начата."; exit 1; }
done
python3 -c 'import sys; assert sys.version_info >= (3,12), "Нужен Python 3.12+"'
[[ $(systemctl is-system-running 2>/dev/null || true) != offline ]] || { echo 'Нужен работающий systemd'; exit 1; }
for task_path in /opt/caseparser /etc/caseparser /var/lib/caseparser /etc/systemd/system/caseparser.service; do
  [[ ! -e $task_path ]] || { echo "Уже существует $task_path. Остановлено без перезаписи."; exit 1; }
done
! id caseparser >/dev/null 2>&1 || { echo 'Пользователь caseparser уже существует. Остановлено.'; exit 1; }
[[ -z $(ss -H -ltn "sport = :$TASK_PORT") ]] || { echo "Порт $TASK_PORT занят. Установка не начата."; exit 1; }
! systemctl cat caseparser.service >/dev/null 2>&1 || { echo 'Сервис caseparser уже существует. Остановлено.'; exit 1; }
echo 'Проверка приложения перед установкой…'
python3 -m unittest discover -s tests -q
echo 'Введи пароль для входа в приложение. Он не будет сохранен открытым текстом.'
TASK_HASH=$(python3 auth.py)
[[ $TASK_HASH == 'pbkdf2_sha256$'* ]] || { echo 'Не удалось создать хеш'; exit 1; }
TASK_COMMITTED=0
rollback() {
  if [[ $TASK_COMMITTED != 1 ]]; then
    echo 'Установка не завершена. Отключаю только созданный сервис caseparser.'
    systemctl disable --now caseparser.service 2>/dev/null || true
    echo 'Существующие сайты, nginx и firewall не изменялись. Созданные каталоги сохранены для диагностики.'
  fi
}
trap rollback EXIT
useradd --system --user-group --home-dir /var/lib/caseparser --shell /usr/sbin/nologin caseparser
install -d -m 755 /opt/caseparser /opt/caseparser/app
install -d -m 750 -o root -g caseparser /etc/caseparser
install -d -m 700 -o caseparser -g caseparser /var/lib/caseparser
tar -cf - app.py auth.py collect.py static README.md | tar -xf - -C /opt/caseparser/app
chown -R root:root /opt/caseparser
find /opt/caseparser -type d -exec chmod 755 {} +
find /opt/caseparser -type f -exec chmod 644 {} +
ss -ltn > /opt/caseparser/listeners-before.txt
openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 365 \
  -keyout /etc/caseparser/key.pem -out /etc/caseparser/cert.pem \
  -subj "/CN=$TASK_IP" -addext "subjectAltName=IP:$TASK_IP" >/dev/null 2>&1
chown root:caseparser /etc/caseparser/{key,cert}.pem
chmod 640 /etc/caseparser/{key,cert}.pem
cat > /etc/caseparser/environment <<EOF
CASEPARSER_PASSWORD_HASH=$TASK_HASH
CASEPARSER_COOKIE_SECURE=1
CASEPARSER_HOST=0.0.0.0
PORT=$TASK_PORT
CASEPARSER_DB=/var/lib/caseparser/cases.sqlite3
CASEPARSER_TLS_CERT=/etc/caseparser/cert.pem
CASEPARSER_TLS_KEY=/etc/caseparser/key.pem
EOF
unset TASK_HASH
chmod 600 /etc/caseparser/environment
cat > /etc/systemd/system/caseparser.service <<'EOF'
[Unit]
Description=Caseparser private case dashboard
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=caseparser
Group=caseparser
WorkingDirectory=/opt/caseparser/app
EnvironmentFile=/etc/caseparser/environment
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/usr/bin/python3 /opt/caseparser/app/app.py
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/caseparser
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX

[Install]
WantedBy=multi-user.target
EOF
# Use the Python interpreter already tested, without altering the system Python.
TASK_PYTHON=$(command -v python3)
sed -i "s|ExecStart=/usr/bin/python3 |ExecStart=$TASK_PYTHON |" /etc/systemd/system/caseparser.service
systemctl daemon-reload
systemctl enable --now caseparser.service
TASK_READY=0
for task_attempt in {1..20}; do
  if curl --silent --fail --cacert /etc/caseparser/cert.pem \
    --resolve "$TASK_IP:$TASK_PORT:127.0.0.1" "https://$TASK_IP:$TASK_PORT/login" >/dev/null; then
    TASK_READY=1; break
  fi
  sleep 1
done
[[ $TASK_READY == 1 ]] || { echo 'HTTPS-проверка не прошла; смотри journalctl -u caseparser'; exit 1; }
TASK_STATUS=$(curl --silent --cacert /etc/caseparser/cert.pem \
  --resolve "$TASK_IP:$TASK_PORT:127.0.0.1" -o /dev/null -w '%{http_code}' "https://$TASK_IP:$TASK_PORT/api/state")
[[ $TASK_STATUS == 401 ]] || { echo 'Проверка защиты входа не прошла'; exit 1; }
TASK_COMMITTED=1
echo "Готово: https://$TASK_IP:$TASK_PORT"
echo 'Сертификат самоподписанный: браузер потребует доверия. Fingerprint:'
openssl x509 -in /etc/caseparser/cert.pem -noout -fingerprint -sha256
echo 'Порт должен быть доступен в firewall хостинга. Правила firewall не изменялись.'

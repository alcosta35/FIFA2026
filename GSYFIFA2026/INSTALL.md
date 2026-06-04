# GSYFIFA2026 — Instalação no Serverlab
### Abordagem conservadora: reutiliza tudo que já existe no wc2026

---

## Mapa do que já existe × o que é novo

| Componente | wc2026 (existente) | gsyfifa2026 (novo) | Ação |
|---|---|---|---|
| Python 3 | `/usr/bin/python3` | mesmo | ✅ nada a instalar |
| Flask / flask-cors | venv em `/opt/wc2026/venv` | novo venv em `/opt/gsyfifa2026/venv` | copiar padrão do wc2026 |
| Nginx | rodando | adicionar novo site | não toca no wc2026 |
| cloudflared | tunnel `e4f748ab-...` ativo | adicionar hostname | editar config.yml |
| Usuário de serviço | `www-data` | `www-data` | ✅ mesmo usuário |
| Porta API | 5050 | **5051** | ✅ sem conflito |
| Web root | `/var/www/wc2026` | `/var/www/gsyfifa2026` | novo diretório |
| Dados API | `/opt/wc2026` | `/opt/gsyfifa2026` | novo diretório |

### Por que venv e não pip no sistema?
Ubuntu 22.04+ implementa o PEP 668 ("externally managed environment"): o pip recusa instalar
pacotes no Python do sistema para não entrar em conflito com o apt. Um venv próprio por
aplicação é a solução padrão — é o mesmo motivo pelo qual o wc2026 usa venv.

---

## Passo 0 — Conectar ao servidor

```bash
ssh serverlab@192.168.2.126
```

---

## Passo 1 — Verificar o que já está instalado (não instalar nada ainda)

```bash
# Python 3 (deve já existir)
python3 --version

# Confirmar que o venv do wc2026 existe e tem Flask
/opt/wc2026/venv/bin/pip list | grep -i flask

# Nginx rodando
sudo systemctl status nginx

# cloudflared rodando
sudo systemctl status cloudflared

# wc2026 intocado
sudo systemctl status wc2026
curl http://127.0.0.1:5050/api/health
```

---

## Passo 2 — Criar diretórios

```bash
# Diretório de dados da API (bets.json, results.json)
sudo mkdir -p /opt/gsyfifa2026
sudo chown www-data:www-data /opt/gsyfifa2026
sudo chmod 750 /opt/gsyfifa2026

# Web root (Nginx serve o index.html daqui)
sudo mkdir -p /var/www/gsyfifa2026
```

---

## Passo 3 — Criar o venv e instalar Flask

```bash
# Criar venv — mesmo padrão do wc2026
sudo python3 -m venv /opt/gsyfifa2026/venv

# Instalar Flask e flask-cors no venv
sudo /opt/gsyfifa2026/venv/bin/pip install flask flask-cors

# Verificar
/opt/gsyfifa2026/venv/bin/python -c "import flask, flask_cors; print('OK')"

# Ajustar proprietário
sudo chown -R www-data:www-data /opt/gsyfifa2026/venv
```

---

## Passo 4 — Copiar os arquivos da aplicação

A partir da sua máquina Windows (PowerShell):

```powershell
$src = "C:\Users\ccost\OneDrive\Documents\Courses and training\IA Training\Novos Desafios\FIFA 2026 Simples\FIFA2026\GSYFIFA2026"

scp "$src\api.py"     serverlab@192.168.2.126:/opt/gsyfifa2026/
scp "$src\index.html" serverlab@192.168.2.126:/var/www/gsyfifa2026/
```

De volta ao servidor, ajustar permissões:

```bash
sudo chown www-data:www-data /opt/gsyfifa2026/api.py
sudo chown www-data:www-data /var/www/gsyfifa2026/index.html
sudo chmod 644 /var/www/gsyfifa2026/index.html
```

---

## Passo 5 — Instalar o serviço systemd

```bash
# Copiar o arquivo de serviço (ou criar manualmente com o conteúdo abaixo)
sudo nano /etc/systemd/system/gsyfifa2026.service
```

Conteúdo — **substitua `MEUPIN` pelo PIN desejado**:

```ini
[Unit]
Description=GSYFIFA2026 Bolão – API Server
After=network.target

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory=/opt/gsyfifa2026

ExecStart=/opt/gsyfifa2026/venv/bin/python /opt/gsyfifa2026/api.py

Environment=GSY_DATA_DIR=/opt/gsyfifa2026
Environment=GSY_HOST=127.0.0.1
Environment=GSY_PORT=5051
Environment=GSY_ADMIN_PIN=MEUPIN

Restart=on-failure
RestartSec=5

StandardOutput=journal
StandardError=journal
SyslogIdentifier=gsyfifa2026

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable gsyfifa2026
sudo systemctl start gsyfifa2026
sudo systemctl status gsyfifa2026

# Confirmar API
curl http://127.0.0.1:5051/api/health
# Esperado: {"status": "ok", "bets": 0, "phase_state": {}, "champion": ""}
```

---

## Passo 6 — Criar o site Nginx

```bash
sudo nano /etc/nginx/sites-available/gsyfifa2026
```

Conteúdo (idêntico ao `nginx-wc2026.conf`, apenas hostname e porta diferentes):

```nginx
# /etc/nginx/sites-available/gsyfifa2026
server {
    listen 80;
    server_name gsyfifa2026.alcosta-cse.net;

    root /var/www/gsyfifa2026;
    index index.html;

    location / {
        try_files $uri /index.html;
    }

    location /api/ {
        proxy_pass         http://127.0.0.1:5051;
        proxy_http_version 1.1;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 10s;
    }

    location /api/health {
        proxy_pass http://127.0.0.1:5051;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/gsyfifa2026 /etc/nginx/sites-enabled/
sudo nginx -t                   # deve mostrar "syntax is ok"
sudo systemctl reload nginx     # reload, NÃO restart — wc2026 não cai

# Confirmar os dois sites ativos
ls /etc/nginx/sites-enabled/
```

---

## Passo 7 — Adicionar hostname ao cloudflared existente

### 7.1 Editar /etc/cloudflared/config.yml

O arquivo atual está em `/etc/cloudflared/config.yml`.
Adicione **apenas as duas linhas marcadas** — não altere nada mais:

```yaml
tunnel: e4f748ab-3b6c-4cf1-b05d-c10d0a228c4a
credentials-file: /home/serverlab/.cloudflared/e4f748ab-3b6c-4cf1-b05d-c10d0a228c4a.json

ingress:
  - hostname: sshserver.alcosta-cse.net
    service: ssh://localhost:22
  - hostname: nextcloud.alcosta-cse.net
    service: http://localhost:80
  - hostname: n8n.alcosta-cse.net
    service: http://localhost:5678
  - hostname: wc2026.alcosta-cse.net
    service: http://localhost:80
  - hostname: gsyfifa2026.alcosta-cse.net   # ← ADICIONAR
    service: http://localhost:80             # ← ADICIONAR
  - service: http_status:404

loglevel: info
logfile: /var/log/cloudflared.log
```

```bash
sudo nano /etc/cloudflared/config.yml
# (adicionar as duas linhas acima, salvar)
```

### 7.2 Adicionar registro DNS no Cloudflare

**Opção A — via CLI (mais rápido):**
```bash
cloudflared tunnel route dns e4f748ab-3b6c-4cf1-b05d-c10d0a228c4a gsyfifa2026.alcosta-cse.net
```

**Opção B — via painel Cloudflare (dash.cloudflare.com):**
1. Acesse `alcosta-cse.net` → **DNS → Records → Add record**
2. Preencha:
   - Type: `CNAME`
   - Name: `gsyfifa2026`
   - Target: `e4f748ab-3b6c-4cf1-b05d-c10d0a228c4a.cfargotunnel.com`
   - Proxy: ✅ Proxied (nuvem laranja)
3. Save

### 7.3 Reiniciar cloudflared

```bash
sudo systemctl restart cloudflared
sudo systemctl status cloudflared

# Confirmar que o wc2026 continua vivo após o restart
curl https://wc2026.alcosta-cse.net/api/health
```

---

## Passo 8 — Verificação final

```bash
# Serviços
sudo systemctl status wc2026 gsyfifa2026 nginx cloudflared

# APIs locais
curl http://127.0.0.1:5050/api/health    # wc2026 — não deve ter mudado
curl http://127.0.0.1:5051/api/health    # gsyfifa2026 — novo

# Via URL pública
curl https://wc2026.alcosta-cse.net/api/health
curl https://gsyfifa2026.alcosta-cse.net/api/health
```

---

## Referência rápida

| | wc2026 | gsyfifa2026 |
|---|---|---|
| URL | `wc2026.alcosta-cse.net` | `gsyfifa2026.alcosta-cse.net` |
| Porta API | `5050` | `5051` |
| Python | `/opt/wc2026/venv/bin/python` | `/opt/gsyfifa2026/venv/bin/python` |
| Web root | `/var/www/wc2026` | `/var/www/gsyfifa2026` |
| Dados | `/opt/wc2026` | `/opt/gsyfifa2026` |
| Tunnel CF | `e4f748ab-...` | `e4f748ab-...` (mesmo) |

---

## Comandos de manutenção

```bash
# Logs em tempo real
sudo journalctl -u gsyfifa2026 -f

# Atualizar HTML ou api.py
sudo cp api.py     /opt/gsyfifa2026/
sudo cp index.html /var/www/gsyfifa2026/
sudo chown www-data:www-data /opt/gsyfifa2026/api.py /var/www/gsyfifa2026/index.html
sudo systemctl restart gsyfifa2026    # só o serviço Flask — nginx e cloudflared ficam

# Backup dos dados
sudo cp /opt/gsyfifa2026/bets.json    ~/gsy_bets_$(date +%F).json
sudo cp /opt/gsyfifa2026/results.json ~/gsy_results_$(date +%F).json
```

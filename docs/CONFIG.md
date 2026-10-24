# Einstellungen

## `config.env` (nur Installation)

Vorlage: [`config.example.env`](../config.example.env). `install-ct.sh` und `deploy.sh` lesen die Datei (Proxmox).
`scripts/setup.sh` nimmt die App-Werte als Umgebungsvariablen und schreibt sie **einmal** nach `/etc/isis/isis.env`.
Spätere Änderungen also dort machen. `config.env` ist in `.gitignore`.

| Variable | Standard | |
|---|---|---|
| `CT_ID`, `CT_HOSTNAME` | 108, isis | Proxmox-Container |
| `CT_IP`, `CT_GW`, `CT_DNS`, `CT_BRIDGE` | – | `CT_IP=dhcp` oder `192.168.1.50/24` |
| `CT_STORAGE`, `CT_DISK_GB`, `CT_CORES`, `CT_MEMORY_MB` | local-lvm, 20, 4, 4096 | ohne jeff reichen 2 Kerne / 1–2 GB |
| `CT_TEMPLATE` | Debian 13.6 | `pveam available --section system` |
| `DATA_HOST_DIR` | leer | Host-Ordner → `/mnt/isis` im CT |
| `ISIS_UID` | 999 | feste UID des Dienstbenutzers (für die Host-ACL) |
| `WITH_JEFF` | 1 | llama.cpp + jeff bauen |

## `/etc/isis/isis.env` (Laufzeit, root:isis 0640)

Gelesen von allen Diensten und vom Befehl `isis`. Nach einer Änderung: `systemctl restart isis-api` (Timer-Dienste
lesen die Datei bei jedem Lauf neu).

| Variable | Standard | |
|---|---|---|
| `ISIS_URL` | `https://isis.tu-berlin.de` | Moodle-Adresse |
| `ISIS_STATE` | `/var/lib/isis` | Token, DB, Suchindex. In den Units fest als `ReadWritePaths` |
| `ISIS_DATA` | `/mnt/isis` | Dateien, `isis.ics`, `wissen/`. Braucht den Marker `.isis-data` |
| `ISIS_MAX_FILE_MB` | 200 | größere Dateien werden gelistet, aber nicht geladen |
| `ISIS_SKIP_COURSES` | – | Kurs-IDs (Komma/Leerzeichen), die nie abgeglichen werden |
| `ISIS_OLLAMA` | `http://127.0.0.1:11434` | Ollama |
| `ISIS_MODEL` | `qwen3.5:9b` | Ollama-Modell für Fakten und Chat |
| `ISIS_WISSEN_SKIP` | – | Dateinamen-Muster (Komma), die nicht ins Wissensnetz kommen, z. B. `*Ordnung*,*Modulhandbuch*` |
| `ISIS_JEFF` | `http://127.0.0.1:8766` | jeff (llama-server). `off` = ohne Prüfmodell |
| `ISIS_JEFF_MODELS` | `/opt/jeff/models` | `*.jeff.json` der Adapter |
| `ISIS_API_TOKEN` | zufällig (setup.sh) | Bearer-Token der API. Ohne Token startet `isis-api` nicht |
| `ISIS_API_ALLOW` | `127.0.0.1` | erlaubte Client-IPs der API (Komma-Liste) |
| `ISIS_API_PORT` | 8091 | |

Unterordner: `/var/lib/isis/{token,icloud.json}` (0600), `isis.db`, `search.db`. `/mnt/isis/files/<kursid>_<kurz>/<abschnitt>/<datei>`,
`/mnt/isis/isis.ics`, `/mnt/isis/wissen/<Kurs>/<Titel>.md`.

## Zeitpläne

| Unit | wann | ändern |
|---|---|---|
| `isis-sync.timer` | alle 2 h zu :15 ± 10 min | `systemctl edit isis-sync.timer` → `OnCalendar=` |
| `isis-wissen.timer` | 03:30, **standardmäßig aus** | `systemctl enable --now isis-wissen.timer` |

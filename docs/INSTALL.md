# Installation

Zwei Wege mit demselben Ergebnis:

- **Proxmox VE:** `install-ct.sh` legt einen unprivilegierten Debian-13-Container an und richtet ihn ein, `deploy.sh`
  spielt den Code ein.
- **Debian 13 (VM, Rechner):** `scripts/setup.sh` und `scripts/install-app.sh` direkt als root.

Getestet mit Proxmox VE 9, Debian 13.6 und Ollama auf einem zweiten Rechner mit GTX 1070.

## 1. Ollama (für Wissensnetz und Chat)

Der Abgleich braucht keine KI. Für Wissensnetz und Chat muss irgendwo im LAN Ollama laufen:

```sh
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen3.5:9b
# Kontext auf 8k: systemctl edit ollama  →
#   [Service]
#   Environment=OLLAMA_CONTEXT_LENGTH=8192
#   Environment=OLLAMA_HOST=0.0.0.0:11434
systemctl restart ollama
```

- Mehr als 8k Kontext ist nicht nötig. Die Pipeline ist so gebaut, dass ein Aufruf höchstens ≈ 60 % davon braucht.
  Mehr Kontext würde auf kleinen Karten auf die CPU auslagern.
- **Firewall:** Ollama hat keine Anmeldung. Gib Port 11434 nur für die IP des isis-Servers frei, z. B. mit nftables:
  `tcp dport 11434 ip saddr <isis-IP> accept`.
- Läuft Ollama auf demselben Rechner, bleibt `ISIS_OLLAMA=http://127.0.0.1:11434`.

## 2a. Proxmox

```sh
git clone https://github.com/mikadahl-dev/isis-assistant.git /root/isis && cd /root/isis
cp config.example.env config.env
$EDITOR config.env
```

Wichtig in `config.env` (alle Werte in [CONFIG.md](CONFIG.md)):

| Wert | Bedeutung |
|---|---|
| `CT_ID`, `CT_IP`, `CT_GW`, `CT_DNS`, `CT_BRIDGE` | Container-Nummer und Netz (`CT_IP=dhcp` geht auch) |
| `CT_TEMPLATE` | Debian-13-Vorlage, vorher laden: `pveam update && pveam download local debian-13-standard_13.6-1_amd64.tar.zst` |
| `DATA_HOST_DIR` | Host-Ordner für Dateien, `isis.ics` und Vault (z. B. auf einer HDD), wird nach `/mnt/isis` eingebunden. Leer = im Container |
| `ISIS_OLLAMA` | Adresse von Ollama aus Schritt 1 |
| `ISIS_API_ALLOW` | IP deines Dashboards, falls du die API nutzt |
| `WITH_JEFF` | `0` spart den llama.cpp-Build (~1 GB, ein paar Minuten). Dann gibt es keine ⚠-Markierungen |

```sh
./install-ct.sh    # einmalig
./deploy.sh        # danach bei jeder Änderung
```

`install-ct.sh` gibt bei `DATA_HOST_DIR` per ACL nur dem Dienstbenutzer Zugriff (Host-UID `100000 + ISIS_UID`). Root
anderer unprivilegierter Container kommt nicht hinein. Auf NTFS (ntfs3) gehen ACLs beim Neu-Einhängen verloren, dann
die `setfacl`-Zeile aus `install-ct.sh` nach dem Mount erneut ausführen.

> Proxmox startet einen Container mit Bind-Mount nicht, solange der Host-Ordner fehlt (z. B. bei abgesteckter HDD).

## 2b. Debian ohne Proxmox

```sh
git clone https://github.com/mikadahl-dev/isis-assistant.git /opt/isis-src && cd /opt/isis-src
sudo env ISIS_OLLAMA=http://192.168.1.20:11434 ISIS_API_ALLOW=127.0.0.1 WITH_JEFF=1 scripts/setup.sh
sudo scripts/install-app.sh
```

Daten landen in `/mnt/isis`. Willst du sie auf einer anderen Platte, häng sie dort ein (vor `setup.sh` oder danach,
dann `chown isis: /mnt/isis && runuser -u isis -- touch /mnt/isis/.isis-data`).

## 3. Login (einmalig, wieder wenn das Token abläuft)

ISIS nutzt Shibboleth mit Pflicht-2FA. `isis` geht deshalb denselben Weg wie die Moodle-App:

```sh
isis login
```

1. Den angezeigten `launch.php`-Link im Browser öffnen und normal mit TUB-Konto + 2FA anmelden.
2. Am Ende will der Browser `moodlemobile://token=…` öffnen. Das schlägt fehl, das ist normal. Die Seite kann leer
   bleiben.
3. Diese Adresse kopieren:
   - **Firefox:** Adresszeile oder Konsole (F12)
   - **Chrome/Edge:** F12 → Netzwerk *vor* dem Login öffnen, die letzte Weiterleitung (303) von `launch.php`,
     Kopfzeile `Location`
4. `isis login 'moodlemobile://token=…'` → „Token gespeichert. Angemeldet als …“

Der Link gilt nur für den letzten `isis login` (Signaturprüfung). Abgelaufenes Token: Der Abgleich endet mit Code 75,
`isis status` sagt „`isis login` ausführen“.

## 4. Prüfen

```sh
isis whoami                    # Konto, Moodle-Version, Funktionen
isis sync && isis status       # erster Abgleich (lädt alle Dateien)
isis deadlines
systemctl list-timers 'isis*'  # isis-sync.timer alle 2 h
isis wissen build --course <id>   # Pilot mit einem Kurs (ID aus `isis courses`), ~10 min je Foliensatz
isis wissen status
```

Danach `/mnt/isis/wissen` in Obsidian öffnen. Gefällt dir das Ergebnis: `systemctl enable --now isis-wissen.timer`
(täglich 03:30, nur neue oder geänderte Dateien kosten Rechenzeit).

**API:**

```sh
. /etc/isis/isis.env
curl -H "Authorization: Bearer $ISIS_API_TOKEN" http://127.0.0.1:8091/status
```

**Tests** (offline): `cd /opt/isis && venv/bin/python -m unittest discover -s /path/to/checkout/tests`

## Update

`git pull && ./deploy.sh` (Proxmox) bzw. `git pull && sudo scripts/install-app.sh`. Die Datenbank wird beim Start
automatisch erweitert (`MIGRATIONS` in `app/isis/db.py`). Abhängigkeiten sind in `requirements.txt` fest gepinnt.

## Rückbau

- **Proxmox:** `pct stop <CT_ID> && pct destroy <CT_ID>`. `DATA_HOST_DIR` bleibt und muss von Hand gelöscht werden.
- **Debian:** `systemctl disable --now isis-sync.timer isis-wissen.timer isis-api jeff`, dann
  `rm -rf /opt/isis /opt/jeff /etc/isis /var/lib/isis /usr/local/bin/isis /etc/systemd/system/{isis,jeff}*`,
  `userdel isis; userdel jeff`.
- **Token in ISIS widerrufen:** Profil → Einstellungen → Sicherheitsschlüssel → „Moodle mobile web service“ zurücksetzen.

## Probleme

| Symptom | Ursache |
|---|---|
| `pct exec <id> -- isis` findet den Befehl nicht | `pct exec` hat kein `/usr/local/bin` im PATH → `/usr/local/bin/isis` |
| Abgleich: „Datenablage … fehlt (Festplatte nicht eingehängt?)“ | Marker `/mnt/isis/.isis-data` fehlt, meist ist die Platte nicht eingehängt |
| `isis wissen build`: Verbindung abgelehnt | Ollama-Firewall oder `ISIS_OLLAMA` falsch: `curl $ISIS_OLLAMA/api/tags` |
| jeff stürzt ab (OOM) | llama-server ohne `--cache-ram 0` (steht in `systemd/jeff.service`) |
| Leere Seite beim Login | erwartet, siehe Schritt 3 |

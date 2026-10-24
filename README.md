# isis – lokaler Lernassistent für ISIS (TU Berlin Moodle)

*Self-hosted assistant for Moodle (built for TU Berlin's ISIS): mirrors your courses, files and deadlines, and turns
the slides into a linked Obsidian vault with a local LLM. German UI and docs.*

`isis` läuft auf einem eigenen kleinen Server (Proxmox-Container, VM oder Debian-Rechner) und

- **gleicht alle 2 Stunden ab:** laufende Kurse, alle Dateien (PDFs, Ordner, Textseiten, Aufgabenanhänge), Links und
  Kalendertermine inklusive „Abgabe noch offen“ → SQLite + Ordner, dazu eine **`isis.ics`** mit allen Terminen;
- **baut ein Wissensnetz:** ein lokales 9B-Modell (Ollama, `qwen3.5:9b`) zieht aus den Folien Begriffe, Formeln,
  Verfahren und Termine heraus, **Code prüft jedes Zitat gegen die Folie**, ein kleines Prüfmodell
  ([jeff](https://github.com/firelex/jeff)) markiert Zweifelhaftes, und daraus entsteht ein **Obsidian-Vault** mit
  Verweisen und Quellenangabe (Datei + Seite);
- **bietet eine lesende API** für ein Dashboard: Termine, Dateien, Notizen, Volltextsuche und einen **Chat**, der nur
  aus deinen Notizen, dem Folientext und den Terminen antwortet und die Quellen nennt.

Alles bleibt lokal. Dein TU-Passwort und dein 2FA-Geheimnis sieht der Dienst nie (siehe [Sicherheit](docs/SECURITY.md)).

> Inoffizielles Projekt, nicht von der TU Berlin. Es nutzt dieselbe Schnittstelle wie die offizielle Moodle-App, mit
> deinem eigenen Konto und gedrosselt (1 Anfrage/s). Nur für den persönlichen Gebrauch, bitte die Nutzungsbedingungen
> von ISIS beachten. Andere Moodle-Seiten mit freigeschalteter Mobile-App sollten über `ISIS_URL` ebenso gehen
> (ungetestet).

## Überblick

```
             ISIS (Moodle REST, Token)                       Ollama (GPU, qwen3.5:9b)
                     │                                                ▲
  isis-sync.timer ───┤ alle 2 h                     isis-wissen.timer │ nachts (optional)
                     ▼                                                │
   /var/lib/isis/isis.db  ◀── Kurse, Dateien, Links, Termine ──▶  Häppchen ≤ 60 % Kontext
   /mnt/isis/files/…  /mnt/isis/isis.ics                             │ Zitat-Prüfung (Code)
                     │                                               │ jeff-Prüfung (CPU, optional)
                     │                                               ▼
                     │                                  Notizen + Verweise → /mnt/isis/wissen (Obsidian)
                     ▼
            isis-api (:8091, nur lesend, Token) ──▶ Dashboard: Übersicht · Dateien · Wissen · Chat
```

Details: [Architektur](docs/ARCHITECTURE.md).

## Voraussetzungen

| | |
|---|---|
| Server | Debian 13 (Proxmox-LXC empfohlen; Skripte für beides). 2 Kerne/2 GB reichen ohne jeff, mit jeff 4 Kerne/4 GB |
| Speicher | ~20 GB System, Platz für die Kursdateien (bei 6 Kursen ≈ 20 MB) |
| KI | [Ollama](https://ollama.com) im LAN mit `qwen3.5:9b` (≈ 6,3 GB VRAM, z. B. GTX 1070 8 GB) und `OLLAMA_CONTEXT_LENGTH=8192`. Nur für Wissensnetz und Chat nötig, der Abgleich läuft ohne |
| Konto | ISIS-Zugang (TUB-Konto + 2FA), einmalig ein Browser für den Login |

## Schnellstart (Proxmox)

```sh
git clone https://github.com/mikadahl-dev/isis-assistant.git && cd isis-assistant
cp config.example.env config.env && $EDITOR config.env   # CT-Nummer, IP, Ollama-Adresse, Datenordner
./install-ct.sh        # einmalig: Container, Benutzer, venv, jeff (dauert ein paar Minuten)
./deploy.sh            # Code + Dienste; nach jeder Änderung erneut
pct enter <CT_ID>
isis login             # Link im Browser öffnen, mit TUB-Konto + 2FA anmelden, Weiterleitung einfügen
isis sync && isis deadlines
```

Ohne Proxmox (Debian 13, als root): `scripts/setup.sh` und danach `scripts/install-app.sh`, Einstellungen als
Umgebungsvariablen. Ausführlich mit Ollama-Einrichtung, Login, Prüfen und Rückbau: **[Installation](docs/INSTALL.md)**.

## Benutzung

```sh
isis login                    # Login-Link erzeugen; danach: isis login 'moodlemobile://token=…'
isis whoami                   # Konto + freigeschaltete Funktionen
isis courses [--all]          # laufende (oder alle) Kurse
isis deadlines                # kommende Termine, offene Abgaben zuerst
isis files | download | sync  # Dateiliste, nur laden, kompletter Abgleich (macht sonst der Timer)
isis status | new             # letzte Läufe, Neues seit dem letzten Abgleich
isis wissen build [--course ID] [--file KEY] [--recheck]   # Wissensnetz bauen (KI)
isis wissen status            # Häppchen, Notizen, Verweise, Fehler
isis set-icloud | icloud-test # iCloud-Zugang hinterlegen/prüfen (verdeckte Eingabe, schreibt noch nichts)
```

- **Kalender:** `/mnt/isis/isis.ics` in eine Kalender-App importieren oder per Webserver abonnieren.
- **Vault:** `/mnt/isis/wissen` als Obsidian-Vault öffnen (z. B. per Syncthing/Nextcloud auf deine Geräte). Eigene
  Notizen dort bleiben erhalten; `isis` ändert nur Dateien mit `generiert: isis`.
- **Nächtlicher Lauf:** `systemctl enable --now isis-wissen.timer`, sobald dir das Ergebnis gefällt.
- **Dashboard:** API in [docs/API.md](docs/API.md), Beispiel-Oberfläche in [`contrib/control-center`](contrib/control-center).

## Dokumentation

| | |
|---|---|
| [docs/INSTALL.md](docs/INSTALL.md) | Installation (Proxmox und Debian), Ollama, Login, Prüfen, Update, Rückbau |
| [docs/CONFIG.md](docs/CONFIG.md) | alle Einstellungen |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Abgleich, Datenbank, Wissensnetz-Pipeline, jeff, Chat |
| [docs/SECURITY.md](docs/SECURITY.md) | Token, was gespeichert wird, Härtung, Token widerrufen |
| [docs/API.md](docs/API.md) | lesende HTTP-API für Dashboards |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Stand, Erfahrungen aus dem Pilot, nächste Schritte |

## Tests

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests     # offline: Login-Signatur, Zitat-Prüfung, Häppchen, ICS
```

## Lizenz

MIT, siehe [LICENSE](LICENSE). jeff, llama.cpp und die Modelle werden bei der Installation von ihren Projekten
geladen und stehen unter deren Lizenzen.

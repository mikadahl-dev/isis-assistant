# Sicherheit und Datenschutz

## Was gespeichert wird und was nicht

| | wo | |
|---|---|---|
| TUB-Passwort, 2FA/TOTP-Geheimnis | **nirgends** | Login läuft im eigenen Browser |
| Moodle-Web-Service-Token | `/var/lib/isis/token` (0600, Benutzer `isis`) | gibt Lesezugriff auf ISIS wie die Moodle-App |
| privates Token aus der Weiterleitung | **verworfen** | könnte eine Browser-Sitzung erzeugen |
| iCloud-App-Passwort (optional) | `/var/lib/isis/icloud.json` (0600) | nur über `isis set-icloud` mit verdeckter Eingabe, wird vorher lesend geprüft |
| API-Token | `/etc/isis/isis.env` (root:isis 0640) | zufällig bei der Installation |
| Kursdateien, Termine, Notizen | `isis.db`, `/mnt/isis` | deine Kursinhalte, nicht veröffentlichen |

Ins Repository gehört nichts davon. `config.env`, `*.env`, `*.db` und `token` stehen in `.gitignore`.

## Login-Ablauf

`isis login` erzeugt einen zufälligen Passport und speichert ihn. Die Weiterleitung `moodlemobile://token=…` wird nur
angenommen, wenn ihre Signatur `md5(ISIS_URL + passport)` zu diesem Passport passt. Ein untergeschobener Link aus einem
fremden Login wird also abgelehnt. Danach wird der Passport gelöscht. Getestet in `tests/test_offline.py`.

**Widerrufen:** ISIS → Profil → Einstellungen → Sicherheitsschlüssel → Schlüssel für „Moodle mobile web service“
zurücksetzen. Dann `rm /var/lib/isis/token`.

## Härtung

- Alle Dienste laufen als `isis` bzw. `jeff`, ohne Capabilities, mit `NoNewPrivileges`, `ProtectSystem=strict`,
  `ProtectHome`, `PrivateTmp`, `PrivateDevices`, nur IPv4/IPv6/Unix-Sockets und Speichergrenzen. Schreibbar sind nur
  `/var/lib/isis` und `/mnt/isis`. Die API darf `/mnt/isis` nur lesen.
- Code root-eigen, nicht schreibbar für den Dienst. Abhängigkeiten in `requirements.txt` gepinnt. jeff-Modelle per
  SHA256 geprüft, llama.cpp auf einen festen Commit gepinnt.
- `download()` holt nur URLs unter `ISIS_URL` (Token geht nie an fremde Hosts). Anfragen an ISIS gedrosselt (1/s).
- Proxmox: unprivilegierter Container. Der Datenordner auf dem Host ist per ACL nur für den Dienstbenutzer offen.
- Unattended-upgrades ist an.

## API

- Nur Client-IPs aus `ISIS_API_ALLOW` **und** `Authorization: Bearer <ISIS_API_TOKEN>`. Kein TLS: Für den Weg über das
  LAN einen Reverse-Proxy mit TLS davorsetzen, oder die API wie vorgesehen nur von einem Dashboard-Backend aus
  aufrufen, das den Token nicht an Browser gibt.
- Bis auf `POST /chat` nur lesend. `/chat` beantwortet Fragen und ändert nichts.
- `/file` liefert nur Dateien aus der Datenbank, deren Pfad unter `ISIS_DATA/files` liegt (kein Pfad aus der Anfrage).
  Ein Frontend sollte Dateien mit `Content-Security-Policy: sandbox` ausliefern, weil ISIS-Seiten HTML enthalten können
  (siehe `contrib/control-center`).
- **Achtung:** Wer das Dashboard ohne Anmeldung im LAN betreibt, macht deine Termine, Kursdateien und Notizen für alle
  im Netz lesbar.

## Ollama und jeff

Ollama hat keine Anmeldung. Gib den Port per Firewall nur für den isis-Server frei. jeff lauscht nur auf `127.0.0.1`.
Folientext geht an Ollama, sonst nirgends hin.

## Sicherheitslücken melden

Bitte ein GitHub-Issue ohne Details eröffnen oder die Security-Advisory-Funktion des Repositories nutzen.

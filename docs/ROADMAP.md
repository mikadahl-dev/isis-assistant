# Stand und Roadmap

| Schritt | Stand |
|---|---|
| 1 Verbindung: Einmal-Token, REST, Dateien | ✅ |
| 2 Abgleich nach SQLite + Dateien (Timer 2 h) | ✅ |
| 3 Kalender: `isis.ics` lokal ✅ · iCloud-Zugang (`isis set-icloud`, nur lesend geprüft) ✅ · Termine per CalDAV eintragen | teils |
| 4 Wissensnetz: Folien → 9B → Zitat-Prüfung → jeff → Obsidian-Vault | Pilot ✅ |
| 5 Lesende API + Dashboard (Übersicht, Dateien, Wissen, Chat mit Quellen) | ✅ |
| 6 Erinnerungen an Abgaben (z. B. Telegram, nur senden) | offen |
| 7 Anki-Karten (`.apkg` je Kurs mit `genanki`) aus Notizen vom Typ begriff/formel mit Wichtigkeit ≥ 2, GUID = Knoten-ID | offen |

## Warum ein Wissensnetz statt direkt „Folie → Anki“?

Ein 9B-Modell auf einer 8-GB-Karte ist für eine große, freie Aufgabe zu schwach. Es schafft aber zuverlässig eine
kleine Aufgabe mit festem Schema: „Welche Begriffe, Formeln, Verfahren und Termine stehen in diesem Text? Zitiere
wörtlich.“ Alles andere macht Code: Zitate prüfen, Doppelte zusammenführen, verlinken, Dateien schreiben. Karten,
Erinnerungen und Chat werden später aus dem geprüften Netz abgeleitet statt direkt aus den Folien.

## Budget je Aufruf (8k Kontext)

Anleitung ≈ 700 Token, Folientext ≤ 5.500 Zeichen (≈ 2,5k Token, bei Formelfolien bis ≈ 2,6k), Antwort ≤ 1.500 Token →
höchstens ≈ 60 %. Mehr Kontext würde auf einer 8-GB-Karte teilweise auf die CPU auslagern und alles sehr langsam machen.

## Ergebnisse aus dem Pilot (ein Kurs: 2 Foliensätze + Übungsblatt + Kursseite)

- 14 Modellaufrufe in 20 min (GTX 1070), maximal 58 % Kontext.
- 102 Einträge vom Modell. 9 ohne echtes Zitat verworfen, 93 übernommen, davon 25 von jeff angezweifelt.
- Ergebnis: 72 Notizen mit 129 Verweisen. Definitionen meist sachlich richtig und mit wörtlichem Beleg.
- **jeff markiert nur.** Der Adapter `ground` ist auf Englisch trainiert und hat richtige deutsche Aussagen als
  „contradicted“ eingestuft, auch wenn sie wörtlich auf der Folie standen. Hart filtert nur die
  Zitatprüfung.
- **llama-server-Speicherleck:** Der RAM-Prompt-Cache (`--cache-ram`, Standard 8 GiB) wuchs um ≈ 42 MB je Anfrage bis
  zum OOM. Mit `--cache-ram 0 --ctx-checkpoints 0` bleibt es stabil bei ≈ 470 MB.
- Chat: Antwort in ≈ 8–10 s (≈ 2k Token Kontext, ≈ 26 Token/s). Die Fakten stimmen meist. Die Quellennummer zeigt manchmal
  auf die falsche Fundstelle.

## Offen / Ideen

- Doppelte mit anderem Wortlaut zusammenführen (z. B. „…dichte“ vs. „…konzentration“), z. B. per
  Embedding-Ähnlichkeit vor der jeff-Frage.
- Rauschen wie Anschauungsvergleiche oder Kursorganisation herausfiltern.
- jeff-Adapter `ground` mit deutschen Beispielen nachtrainieren oder durch eine NLI-Prüfung ersetzen.
- Mathe-lastige Kurse: Formeln nur als Zitat übernehmen.
- Termine aus dem Folientext (`termin`) als „unbestätigt“ neben die ISIS-Termine stellen.
- iCloud-Kalender per CalDAV beschreiben (eigener Kalender, UID je ISIS-Termin).
- Eigenständige kleine Weboberfläche direkt aus `isis-api` (heute: siehe `contrib/control-center`).

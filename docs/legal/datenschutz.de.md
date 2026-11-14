> **ENTWURF / WIP**: Platzhalter `[…]` ausfüllen und vor Veröffentlichung mit dem tatsächlichen Server-Setup abgleichen. Keine Rechtsberatung.
> Wird später unter `https://homebound.luthopian.de/datenschutz` ausgeliefert. Eine englische Kurzfassung steht am Ende.

# Datenschutzerklärung

- **Audience:** visitors of the sync service and the owner; the German text is binding.
- **Scope:** the draft of the Datenschutzerklärung for the sync service. The English translation is [`datenschutz.en.md`](datenschutz.en.md).
- **Kept current:** draft, to be checked by a lawyer before the sync service goes public (2026-11-14).

## 1. Verantwortlicher

Tobias Luther, E-Mail: luther-tobias@gmx.de (siehe Impressum).
Ein Datenschutzbeauftragter ist nicht bestellt und nicht erforderlich.

## 2. Grundsatz

Homebound verarbeitet so wenige Daten wie möglich:
- Es gibt keine Registrierung mit E-Mail-Adresse oder Passwort.
- Es gibt keine Werbung, kein Tracking und keine Analyse-Tools.
- Es werden keine Cookies Dritter gesetzt.

Die Desktop-App funktioniert auch vollständig ohne den Sync-Dienst. In diesem Fall verlassen keine Daten den eigenen Rechner (außer den direkten Abfragen der App bei zKillboard).

## 3. Welche Daten verarbeitet werden

| Daten | Herkunft | Zweck | Rechtsgrundlage | Speicherdauer |
|---|---|---|---|---|
| EVE-Charakter-ID und -Name | EVE-SSO-Login (Fenris Creations) | Anmeldung, Zuordnung zu Karten und Zahlungen | Art. 6 Abs. 1 lit. b DSGVO (Vertrag) | solange das Konto besteht |
| Corporation-ID des Charakters | öffentliche ESI-Schnittstelle von Fenris Creations | Zuordnung verpasster Zahlungen (siehe Nutzungsbedingungen) | Art. 6 Abs. 1 lit. b und f DSGVO | solange das Konto besteht, Verwarnungen 12 Monate |
| Sitzungs-Token | vom Server erzeugt | angemeldet bleiben | Art. 6 Abs. 1 lit. b DSGVO | bis Abmeldung oder Ablauf |
| Karteninhalte (Systeme, Verbindungen, Signaturen, Notizen, Einstellungen) | von Nutzern eingetragen | Synchronisierung zwischen Mitgliedern | Art. 6 Abs. 1 lit. b DSGVO | Verbindungen automatisch nach 2 Tagen, Systeme nach 4 Tagen (außer Heimatsystem und angeheftete Systeme); die ganze Karte 2 Monate nach Ablauf der Zahlung |
| Live-Standort von Charakteren | Desktop-App, nur wenn pro Charakter aktiviert (Opt-in) | Anzeige für Kartenmitglieder | Art. 6 Abs. 1 lit. a DSGVO (Einwilligung, jederzeit abschaltbar) | **wird nicht gespeichert**, nur im Arbeitsspeicher, solange die Verbindung besteht |
| Zahlungsdaten (Journal-ID, zahlender Charakter, ISK-Betrag, Datum, Verwendungszweck) | Wallet-Journal des Empfangscharakters (ESI) | Abrechnung des Abonnements | Art. 6 Abs. 1 lit. b DSGVO | solange das Konto besteht, danach 12 Monate |
| Nutzung der kostenlosen Testphase (Charakter-ID, Konto-ID, Zeitpunkt) | beim Start der Testphase | Missbrauchsschutz: die Testphase gibt es nur einmal pro Charakter, auch nach Löschung oder Weiterverkauf | Art. 6 Abs. 1 lit. f DSGVO | unbegrenzt |
| IP-Adresse, Zeitpunkt, Anfrage | technisch bei jedem Aufruf | Betrieb, Missbrauchsschutz (Rate-Limits), Fehlersuche | Art. 6 Abs. 1 lit. f DSGVO | Rate-Limits nur im Arbeitsspeicher; Server-Logs 14 Tage |

Es werden keine besonderen Kategorien personenbezogener Daten verarbeitet.

## 4. Cookies und lokale Speicherung

Die Web-Ansicht setzt nur ein **technisch notwendiges Sitzungs-Cookie** für die Anmeldung (§ 25 Abs. 2 Nr. 2 TDDDG). Dafür ist keine Einwilligung nötig. Andere Cookies gibt es nicht. Die Desktop-App speichert ihr Sitzungs-Token lokal auf dem eigenen Rechner.

## 5. Anmeldung über EVE Online (Fenris Creations)

Die Anmeldung erfolgt über EVE Single Sign-On von Fenris Creations (vormals CCP Games), Reykjavík, Island. Beim Login wird man zu Fenris Creations weitergeleitet. Homebound fordert dabei **keine Berechtigungen** (Scopes) an und erhält nur Charakter-ID und Charakternamen. Für die Verarbeitung bei Fenris Creations gilt deren Datenschutzerklärung: https://www.ccpgames.com/privacy-policy. Island ist als EWR-Staat datenschutzrechtlich gleichgestellt.

## 6. Empfänger und Auftragsverarbeiter

- **Hetzner Online GmbH**, Industriestr. 25, 91710 Gunzenhausen, Deutschland: Server-Hosting in Deutschland. Mit Hetzner besteht ein Vertrag zur Auftragsverarbeitung nach Art. 28 DSGVO.
- **Hetzner Online GmbH** (wie oben): Datenbank-Backups im Hetzner Object Storage an einem anderen Standort als der Server (Helsinki1). Der Vertrag zur Auftragsverarbeitung mit Hetzner gilt auch dafür.
- **healthchecks.io** (SIA Monkey See Monkey Do, Lettland): erhält nur technische Statussignale („Server läuft") und **keine** personenbezogenen Daten von Nutzern.
- **Andere Mitglieder derselben Karte** sehen Karteninhalte, die Charakternamen der Mitglieder und, falls aktiviert, Live-Standorte.

Eine Weitergabe an sonstige Dritte findet nicht statt, und Daten werden nicht verkauft.

## 7. Löschung

- Verbindungen und Systeme werden wie in der Tabelle beschrieben automatisch entfernt.
- Läuft ein Abonnement aus, wird die Karte auf dem Server **2 Monate nach Ablauf** gelöscht. Lokale Kopien in den Desktop-Apps der Mitglieder bleiben davon unberührt und liegen in deren eigener Verantwortung.
- Ein Konto kann jederzeit per E-Mail an luther-tobias@gmx.de oder in der App unter Cloud → Account → „Delete account …“ gelöscht werden. Verknüpfte Charaktere und Sitzungen werden sofort gelöscht. Zahlungsdaten und Zahlungsbestätigungen bleiben noch 12 Monate gespeichert und werden dann zusammen mit dem restlichen Konto endgültig gelöscht; nur der Eintrag zur kostenlosen Testphase bleibt (siehe Tabelle). Lokale Karten bleiben auf deinem Rechner.
- Backups werden nach spätestens 30 Tagen überschrieben.

## 8. Deine Rechte

Du hast das Recht auf:
- Auskunft (Art. 15 DSGVO)
- Berichtigung (Art. 16)
- Löschung (Art. 17)
- Einschränkung der Verarbeitung (Art. 18)
- Datenübertragbarkeit (Art. 20)
- Widerspruch gegen Verarbeitungen auf Grundlage berechtigter Interessen (Art. 21)
- Widerruf einer Einwilligung, z. B. für Live-Standorte, jederzeit mit Wirkung für die Zukunft (Art. 7 Abs. 3)

Für alle Anfragen genügt eine E-Mail an luther-tobias@gmx.de. Bitte gib dabei deine Charakter-ID oder deinen Charakternamen an.

## 9. Sicherheit

- Alle Verbindungen sind per TLS verschlüsselt.
- Der Server ist nur per SSH-Schlüssel erreichbar.
- Backups werden vor dem Hochladen verschlüsselt; der Speicheranbieter sieht nur verschlüsselte Daten.
- Das Wallet-Token des Empfangscharakters ist zusätzlich verschlüsselt gespeichert.

## 10. Änderungen

Diese Erklärung wird angepasst, wenn sich der Dienst ändert. Stand: 08.11.2026.

---

### English summary

Homebound stores only what the sync service needs:
- your EVE character ID and name (via EVE SSO, no scopes requested), plus your corporation ID
- the map data members enter
- ISK payment records from the receiving character's wallet journal
- server logs with IP addresses, kept for 14 days

Live character locations are opt-in and never stored. There is no tracking and no third-party cookies, only a session cookie.

Hosting is at Hetzner in Germany. Backups go to Hetzner's object storage in another EU location.

Connections are auto-deleted after 2 days and systems after 4 days. A map is deleted 2 months after its subscription lapses.

You have the usual GDPR rights (access, deletion and so on). Contact: luther-tobias@gmx.de.

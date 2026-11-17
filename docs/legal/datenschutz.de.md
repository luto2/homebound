> **ENTWURF / WIP**: Platzhalter `[…]` ausfüllen und vor Veröffentlichung mit dem tatsächlichen Server-Setup abgleichen. Keine Rechtsberatung.
> Wird später unter `https://homebound.luthopian.de/datenschutz` ausgeliefert. Eine englische Kurzfassung steht am Ende.

# Datenschutzerklärung

- **Audience:** visitors of the sync service and the owner; the German text is binding.
- **Scope:** the draft of the Datenschutzerklärung for the sync service and the network requests of the desktop app. The English translation is [`datenschutz.en.md`](datenschutz.en.md).
- **Kept current:** draft, to be checked by a lawyer before the sync service goes public; checked against `9c545d7` (2026-11-17). Update it when the app contacts a new service or the server stores new data.

## 1. Verantwortlicher

Tobias Luther, E-Mail: luther-tobias@gmx.de (siehe Impressum).
Ein Datenschutzbeauftragter ist nicht bestellt und nicht erforderlich.

## 2. Grundsatz

Homebound verarbeitet so wenige Daten wie möglich:
- Es gibt keine Registrierung mit E-Mail-Adresse oder Passwort.
- Es gibt keine Werbung, kein Tracking und keine Analyse-Tools.
- Es werden keine Cookies Dritter gesetzt.

Die Desktop-App funktioniert auch vollständig ohne den Sync-Dienst. In diesem Fall verlassen keine Daten den eigenen Rechner außer den Abfragen bei öffentlichen Diensten, die Abschnitt 3 aufzählt.

## 3. Verbindungen der Desktop-App zu anderen Diensten

Auch ohne den Sync-Dienst ruft die Desktop-App öffentliche Daten aus dem Internet ab. Bei jeder Abfrage erhält der jeweilige Dienst technisch bedingt die IP-Adresse des Rechners, den Zeitpunkt und eine Programmkennung (User-Agent, z. B. `homebound/1.0 (personal offline wormhole mapper)`). Weitere Daten werden nur übermittelt, wo es unten steht. Die Abfragen gehen direkt vom eigenen Rechner an den jeweiligen Dienst. Der Anbieter von Homebound erhält sie nicht und speichert sie nicht, außer den Abfragen beim Homebound-Server; für diese ist Rechtsgrundlage Art. 6 Abs. 1 lit. f DSGVO (berechtigtes Interesse, der App die Kartendaten bereitzustellen). Für die Verarbeitung bei den übrigen Diensten sind diese selbst verantwortlich; es gelten ihre eigenen Datenschutzerklärungen.

**Beim ersten Start** und wenn die Kartendaten neu erstellt werden (`--build-static`):

| Dienst | Wofür | Was zusätzlich übermittelt wird |
|---|---|---|
| Homebound-Server (homebound.luthopian.de, siehe Abschnitt 1, Hosting bei Hetzner) | Wurmloch-Daten (Statics und Wurmloch-Typen) | nichts; Server-Logs wie in Abschnitt 4 (14 Tage) |
| Fenris Creations, Island (developers.eveonline.com) | statische Spieldaten von EVE Online (SDE: Systeme, Klassen, Gates) | nichts |

Nur wenn der Homebound-Server keine brauchbaren Wurmloch-Daten liefert, holt die App sie direkt aus den Quellen:

| Dienst | Wofür | Was zusätzlich übermittelt wird |
|---|---|---|
| GitHub, Inc., USA (raw.githubusercontent.com) | Wurmloch-Daten des Projekts Wanderer | nichts |
| anoik.is (Betreiber und Sitz nicht angegeben) | Statics der Wurmloch-Systeme | nichts |
| whtype.info (privat betrieben, Sitz nicht angegeben) | wohin Wurmloch-Typen führen können | nichts |
| zKillboard (zkillboard.com, privat betrieben, Sitz nicht angegeben) | Statics einzelner Systeme, bei denen sich die Quellen widersprechen | die IDs dieser Sonnensysteme |

**Im laufenden Betrieb:**

| Dienst | Wofür | Was zusätzlich übermittelt wird | Abschalten |
|---|---|---|---|
| zKillboard (zkillboard.com) | Kill-Aktivität der Systeme in den angezeigten Ketten | die IDs dieser Sonnensysteme; daraus lässt sich ablesen, welche Systeme man sich ansieht | Settings → zKill |
| EVE-Scout (api.eve-scout.com, Betreiber und Sitz nicht angegeben) | öffentliche Thera- und Turnur-Verbindungen für Routen, alle 5 Minuten | nichts | Settings → Routes |
| Fenris Creations, Island (esi.evetech.net) | nur wenn zwei Charakter-IDs in den Chat-Logs denselben Namen haben (Kopie eines Charakters auf einem Testserver) | diese Charakter-IDs | nicht nötig: ohne Namensgleichheit keine Abfrage |

Es gibt keine Anmeldung bei diesen Diensten, und es werden keine Karteninhalte, Charakternamen oder Standorte übertragen. Einige dieser Dienste sitzen außerhalb der EU und des EWR oder geben ihren Sitz nicht an: GitHub, Inc. sitzt in den USA, und bei anoik.is, whtype.info, zKillboard und EVE-Scout ist der Sitz nicht bekannt. Dort kann das Datenschutzniveau niedriger sein als in der EU. Wer das vermeiden will, schaltet zKillboard und EVE-Scout in den Settings ab; die Abfragen beim ersten Start entfallen, solange der Homebound-Server erreichbar ist. Fenris Creations sitzt in Island (EWR).

[Für die anwaltliche Prüfung: Der Entwurf geht davon aus, dass der Anbieter von Homebound für die Abfragen an fremde Dienste nicht Verantwortlicher ist (er erhält die Daten nicht, ErwG 78 DSGVO) und deshalb keine Übermittlung nach Kapitel V DSGVO vorliegt (EDSA-Leitlinien 05/2021, Kriterium 2). Falls gemeinsame Verantwortlichkeit für das Auslösen der Abfragen angenommen wird (EuGH C-40/17 Fashion ID), wären Art. 26 und für GitHub Art. 45 DSGVO mit dem EU-US Data Privacy Framework zu prüfen (Zertifizierung von GitHub, Inc. auf dataprivacyframework.gov bestätigen; Rechtsmittel C-703/25 P anhängig).]

## 4. Welche Daten verarbeitet werden

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

## 5. Cookies und lokale Speicherung

Die Web-Ansicht setzt nur ein **technisch notwendiges Sitzungs-Cookie** für die Anmeldung (§ 25 Abs. 2 Nr. 2 TDDDG). Dafür ist keine Einwilligung nötig. Andere Cookies gibt es nicht. Die Desktop-App speichert ihr Sitzungs-Token lokal auf dem eigenen Rechner.

## 6. Anmeldung über EVE Online (Fenris Creations)

Die Anmeldung erfolgt über EVE Single Sign-On von Fenris Creations (vormals CCP Games), Reykjavík, Island. Beim Login wird man zu Fenris Creations weitergeleitet. Homebound fordert dabei **keine Berechtigungen** (Scopes) an und erhält nur Charakter-ID und Charakternamen. Für die Verarbeitung bei Fenris Creations gilt deren Datenschutzerklärung: https://www.ccpgames.com/privacy-policy. Island ist als EWR-Staat datenschutzrechtlich gleichgestellt.

## 7. Empfänger und Auftragsverarbeiter

- **Hetzner Online GmbH**, Industriestr. 25, 91710 Gunzenhausen, Deutschland: Server-Hosting in Deutschland. Mit Hetzner besteht ein Vertrag zur Auftragsverarbeitung nach Art. 28 DSGVO.
- **Hetzner Online GmbH** (wie oben): Datenbank-Backups im Hetzner Object Storage an einem anderen Standort als der Server (Helsinki1). Der Vertrag zur Auftragsverarbeitung mit Hetzner gilt auch dafür.
- **healthchecks.io** (SIA Monkey See Monkey Do, Lettland): erhält nur technische Statussignale („Server läuft") und **keine** personenbezogenen Daten von Nutzern.
- **Andere Mitglieder derselben Karte** sehen Karteninhalte, die Charakternamen der Mitglieder und, falls aktiviert, Live-Standorte.

Eine Weitergabe an sonstige Dritte findet nicht statt, und Daten werden nicht verkauft.

## 8. Löschung

- Verbindungen und Systeme werden wie in der Tabelle beschrieben automatisch entfernt.
- Läuft ein Abonnement aus, wird die Karte auf dem Server **2 Monate nach Ablauf** gelöscht. Lokale Kopien in den Desktop-Apps der Mitglieder bleiben davon unberührt und liegen in deren eigener Verantwortung.
- Ein Konto kann jederzeit per E-Mail an luther-tobias@gmx.de oder in der App unter Cloud → Account → „Delete account …“ gelöscht werden. Verknüpfte Charaktere und Sitzungen werden sofort gelöscht. Zahlungsdaten und Zahlungsbestätigungen bleiben noch 12 Monate gespeichert und werden dann zusammen mit dem restlichen Konto endgültig gelöscht; nur der Eintrag zur kostenlosen Testphase bleibt (siehe Tabelle). Lokale Karten bleiben auf deinem Rechner.
- Backups werden nach spätestens 30 Tagen überschrieben.

## 9. Deine Rechte

Du hast das Recht auf:
- Auskunft (Art. 15 DSGVO)
- Berichtigung (Art. 16)
- Löschung (Art. 17)
- Einschränkung der Verarbeitung (Art. 18)
- Datenübertragbarkeit (Art. 20)
- Widerspruch gegen Verarbeitungen auf Grundlage berechtigter Interessen (Art. 21)
- Widerruf einer Einwilligung, z. B. für Live-Standorte, jederzeit mit Wirkung für die Zukunft (Art. 7 Abs. 3)

Für alle Anfragen genügt eine E-Mail an luther-tobias@gmx.de. Bitte gib dabei deine Charakter-ID oder deinen Charakternamen an.

## 10. Sicherheit

- Alle Verbindungen sind per TLS verschlüsselt.
- Der Server ist nur per SSH-Schlüssel erreichbar.
- Backups werden vor dem Hochladen verschlüsselt; der Speicheranbieter sieht nur verschlüsselte Daten.
- Das Wallet-Token des Empfangscharakters ist zusätzlich verschlüsselt gespeichert.

## 11. Änderungen

Diese Erklärung wird angepasst, wenn sich der Dienst ändert. Stand: 17.11.2026.

---

### English summary

Homebound stores only what the sync service needs:
- your EVE character ID and name (via EVE SSO, no scopes requested), plus your corporation ID
- the map data members enter
- ISK payment records from the receiving character's wallet journal
- server logs with IP addresses, kept for 14 days

The desktop app also contacts public services (the Homebound server and CCP's static data on first start, zKillboard, EVE-Scout); each sees your IP address. Section 3 lists them.

Live character locations are opt-in and never stored. There is no tracking and no third-party cookies, only a session cookie.

Hosting is at Hetzner in Germany. Backups go to Hetzner's object storage in another EU location.

Connections are auto-deleted after 2 days and systems after 4 days. A map is deleted 2 months after its subscription lapses.

You have the usual GDPR rights (access, deletion and so on). Contact: luther-tobias@gmx.de.

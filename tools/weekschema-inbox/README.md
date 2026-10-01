# Weekschema-inbox: het au-pair-weekschema dynamisch maken

Stand: 1 oktober 2026. Onderzoek + bouwstenen, nog niet geïnstalleerd in Home Assistant
(zie "Wat er eerst moet gebeuren").

## Hoe het nu werkt

- Elske maakt per week een Word-bestand (`~/Dropbox/.../Aupair/Week schedules/2026/*.docx`).
- De Mac Mini parseert de drie nieuwste bestanden (`~/scripts/weekschema/sync_weekschema.py`),
  schrijft `weekschema.json` + `weekschema_today.html` naar `/config/www/` van HA en de iPad
  toont dat als iframe in kolom 3 van `ipad-wand` (`.views[0].sections[3].cards[1]`).
- Een wijziging betekent dus: Word openen, aanpassen, opslaan, wachten op Dropbox en op de
  volgende sync. Dat is de reden dat een losse afspraak ("dinsdag Thijs tandarts 15:00")
  vaak niet op het paneel komt.

## Ontwerp: één trechter, meerdere ingangen

```
mail  ─┐
WhatsApp-groep (notificatie op Android) ─┤
Assist (typen in HA-app / Atom Echo)     ─┼─► script.weekschema_item_toevoegen ─► Google-agenda "Weekschema"
invoerveld op de iPad                    ─┤                                             │
agenda-app op elke telefoon ─────────────┘  (rechtstreeks, zonder script)               ▼
                                                 sensor.weekschema_extra ─► kaart onder het iframe op ipad-wand
```

Kernkeuze: de dynamische items gaan **niet** in het Word-bestand en **niet** in een los
HA-bestand, maar in een **gedeelde Google-agenda "Weekschema"**. Redenen:

1. HA heeft de Google Calendar-integratie al (account derkvankampen@gmail.com) en beide
   bestaande agenda's zijn schrijfbaar vanuit HA (`supported_features: 3`), dus
   `calendar.create_event` werkt zonder extra integratie.
2. Iedereen met de agenda-app (Elske, Derk, au pair) kan items **ook direct** toevoegen,
   verplaatsen of verwijderen. Mail/WhatsApp/Assist zijn dan "snelle ingangen", geen enige weg.
3. Verwijderen hoeft niet via HA: gewoon in de agenda-app of in het HA-agendapaneel.
4. Het patroon (agenda → trigger-template-sensor → markdown-kaart) staat al in huis voor de
   Zermelo-roosters, inclusief de `this`-fallback-les van 19-09.

Het Word-schema blijft de basis (vaste ritmes, ophalen/brengen, eten). De agenda is de laag
"wat is er deze dagen anders of extra".

Alternatief voor een nieuwe agenda: de bestaande, lege Google-gezinsagenda **"Gezin"**
(`family17920918689768585313@group.calendar.google.com`). Nadeel: daar staat straks ook
ander gezinsspul in dat dan op het paneel verschijnt. Advies: aparte agenda "Weekschema".
De agenda van medischadvies.vankampen@gmail.com is zakelijk (Medisch Advies) en blijft
buiten dit plan; HA leest die ook nu niet.

## Ingangen, eerlijk beoordeeld

| Ingang | Hoe | Oordeel |
|---|---|---|
| **Mail**, onderwerp bevat "weekschema" | Ingebouwde **IMAP**-integratie vuurt `imap_content`; `automation_mail_inbox.yaml` knipt handtekening/quotes af en stuurt de regels naar het script. Onderwerp "weekschema: di 15:00 Thijs tandarts" zonder tekst werkt ook. | ✅ Betrouwbaar, geen HACS. Eenmalig: 2-staps-verificatie + app-wachtwoord. Vertraging = IMAP-poll (standaard elke 30 s, IDLE als Gmail dat toestaat). Let op: medischadvies.vankampen@gmail.com is de zakelijke mailbox; HA mag alleen een apart label "Weekschema" lezen (Gmail-filter, inbox overslaan), of er komt een eigen gratis Gmail-adres voor. |
| **HA-app / Assist** | `automation_assist.yaml`: zin "zet … in het weekschema" of "weekschema …" in de Assist-chat van de HA-app (Pixel, Samsung, iPad). Werkt met de ingebouwde Assist, geen LLM. | ✅ Direct beschikbaar. Spraak via de Atom Echo kan ook, maar Nederlandse STT op namen (Sieb, Jochem) is wisselvallig; typen is de betrouwbare vorm. |
| **iPad bij de trapkast** | `input_text.weekschema_invoer` + knop (`dashboard_cards.yaml`, `script_weekschema_invoer_dashboard.yaml`). | ✅ Voor de au pair zonder telefoon in de hand. |
| **Agenda-app** | Items direct in de gedeelde agenda zetten. | ✅ Nul techniek, altijd beschikbaar. Voor de au pair misschien zelfs de natuurlijkste weg. |
| **WhatsApp, route A: notificatie-sensor** | HA Companion (Android) leest de melding van WhatsApp-groep "Weekschema" via de "Last notification"-sensor; `automation_whatsapp_notificatie.yaml` haalt de tekst eruit. | ⚠️ Werkt in de praktijk, maar is een hack: telefoon moet aan en online zijn, groep mag niet gedempt zijn, gebundelde meldingen geven alleen het laatste bericht, en de sensor ziet alle meldingen van de toegestane apps (allow-list op alleen WhatsApp zetten). Geen antwoord terug in WhatsApp; bevestiging komt als HA-push op de Pixel. |
| **WhatsApp, route B: officiële API** | WhatsApp Business Cloud API (Meta) of Twilio-WhatsApp met een webhook naar HA (Nabu Casa cloudhook maakt die van buiten bereikbaar). | ⚙️💰 Netjes en tweerichting (HA kan terugappen), maar: Meta Business-verificatie, een tweede telefoonnummer dat nog niet op WhatsApp zit, tokens die verlopen, en bij Twilio kosten per bericht. Pas doen als route A tegenvalt én WhatsApp echt het hoofdkanaal moet zijn. |
| **WhatsApp, route C: onofficiële bridges** | HACS/Docker-koppelingen op basis van WhatsApp Web. | ❌ Niet doen: tegen de WhatsApp-voorwaarden, kans dat het nummer geblokkeerd wordt, breekt bij elke WhatsApp-update. |
| **Telegram** (ter info) | Ingebouwde `telegram_bot`-integratie, polling, geen webhook nodig; HA kan ook antwoorden. | ✅ Technisch de makkelijkste chat-ingang, maar het gezin zit op WhatsApp. Alleen zinvol als de au pair er geen bezwaar tegen heeft. |

Aanbevolen volgorde: **agenda + mail + Assist + iPad-invoer** eerst (allemaal ingebouwd),
daarna de WhatsApp-notificatieroute als proef op de Pixel. Dat volgt het
asymmetrische-kostenprincipe: de agenda en het script zijn de dure, gedeelde basis; elke
ingang erbij is daarna een automation van tien regels.

## Berichtformaat (alle ingangen hetzelfde)

Eén item per regel: `[dag] [tijd] tekst`

- dag: `vandaag`, `morgen`, `overmorgen`, `ma`…`zo`, `maandag`…`zondag`, `7-10`, `7/10`, `7-10-2026`
  (weekdag = eerstvolgende, inclusief vandaag; `3/1` in oktober = 3 januari volgend jaar)
- tijd: `15:00`, `15.00`, `15u`, `15u30`, `15:00-16:00`
- geen dag = vandaag; geen tijd = hele dag; geen eindtijd = 1 uur
- een regel die niet te parsen is wordt **niet weggegooid** maar als hele-dag-item vandaag gezet

Getest in HA op 01-10-2026 (`ha_eval_template`) met acht varianten, allemaal correct;
de kaart-template en het mail-afknipwerk zijn ook live getest.

## Bestanden

| Bestand | Wat | Installeren via |
|---|---|---|
| `script_weekschema_toevoegen.yaml` | De parser + `calendar.create_event` per regel + event `weekschema_ververst` | `ha_config_set_script` of Scripts → YAML |
| `script_weekschema_invoer_dashboard.yaml` | Knop op de iPad: invoerveld → script → veld leeg | idem |
| `automation_mail_inbox.yaml` | IMAP-mail → script → push naar Elske | `ha_config_set_automation` |
| `automation_assist.yaml` | Assist-zin → script → gesproken/getypte bevestiging | idem |
| `automation_whatsapp_notificatie.yaml` | WhatsApp-groepsmelding op de Pixel → script | idem (na test van de sensor-attributen) |
| `template_sensor_weekschema_extra.yaml` | Trigger-template-sensor met de items van vandaag/morgen/overmorgen | `configuration.yaml` via Samba-mount, dan Template entities herladen |
| `dashboard_cards.yaml` | Markdown-kaart + invoerkaart voor kolom 3 van `ipad-wand` | `ha_config_set_dashboard(python_transform=...)` |

## Wat er eerst moet gebeuren (Derk/Elske)

1. **Google-agenda "Weekschema" aanmaken** in het account derkvankampen@gmail.com
   (calendar.google.com → Andere agenda's → + → Nieuwe agenda), delen met Elske en de au pair
   met "Wijzigingen aanbrengen". Daarna in HA de Google-integratie herladen; de entiteit
   wordt `calendar.weekschema` (anders de naam in de YAML-bestanden aanpassen).
2. **Voor de mail-ingang**: kiezen tussen (a) het bestaande huis-adres met een Gmail-filter
   (onderwerp bevat "weekschema" → label "Weekschema", inbox overslaan; HA leest alleen die
   map, de zakelijke Medisch Advies-post blijft erbuiten) of (b) een eigen gratis Gmail-adres
   alleen hiervoor. Op dat account 2-staps-verificatie aanzetten en een app-wachtwoord maken
   (Google-account → Beveiliging → App-wachtwoorden). Dat wachtwoord in HA invullen bij
   Add integration → IMAP, folder "Weekschema" bij (a) of INBOX bij (b). Claude kan dit niet
   doen, het is een geheim. Het mailadres van de au pair in de afzenderlijst van
   `automation_mail_inbox.yaml` zetten.
3. **Voor de WhatsApp-proef**: op de Pixel in de HA-app de sensor "Last notification"
   aanzetten met allow-list = WhatsApp; WhatsApp-groep "Weekschema" maken; één testbericht
   sturen en de attributen van `sensor.pixel_10_pro_last_notification` bekijken.

Daarna kan Claude op afstand installeren: helper `input_text.weekschema_invoer`, beide
scripts, de automations, de dashboardkaarten (MCP), en de sensor-YAML klaarzetten voor de
Mac (`configuration.yaml` is niet via MCP te bewerken).

## Fasering (voorstel)

1. **Sessie 1 (30 min)**: agenda + script + Assist + iPad-invoer + kaart. Direct bruikbaar,
   te testen vanaf de HA-app.
2. **Sessie 2 (20 min)**: IMAP-integratie + mail-automation, test met één mail.
3. **Sessie 3 (optioneel)**: WhatsApp-notificatieroute op de Pixel; na een week beoordelen
   of het betrouwbaar genoeg is of dat route B (officiële API) de moeite waard is.
4. **Later**: het Mac-script `sync_weekschema.py` de agenda laten meelezen
   (`POST /api/services/calendar/get_events?return_response` met het bestaande `.ha_token`)
   zodat de extra items ook in de iframe-HTML en de PDF terechtkomen. Niet nodig voor het
   paneel, wel mooi voor het geprinte weekschema.

## Bekende beperkingen

- Een meerdaags hele-dag-item dat vóór vandaag begint, komt niet op de kaart (de kaart matcht
  op startdatum). Voor "hele week geen hockey" dus per dag een regel sturen, of in de
  agenda-app invoeren.
- Dubbele items worden niet ontdubbeld: twee keer mailen = twee keer in de agenda. Weghalen
  gaat via de agenda-app.
- De mail-automation accepteert alleen bekende afzenders; een mail vanaf een ander adres
  wordt stil genegeerd (bewust, anders kan iedereen op het paneel schrijven).
- `calendar.create_event` naar Google heeft wel eens een paar seconden vertraging; het
  script wacht 3 s en vuurt dan `weekschema_ververst` zodat de kaart ververst. De
  15-minuten-timer vangt de rest op.

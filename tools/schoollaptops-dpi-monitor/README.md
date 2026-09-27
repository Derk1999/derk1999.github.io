# Schoollaptops DPI-monitor

Meet per schoollaptop (Thijs .88, Jochem .85, Sieb .91) hoeveel YouTube- en
VPN/proxy-verkeer er per dag door de UCG-Ultra gaat, en zet dat in Home Assistant.
Zo is te zien of de UniFi-blokkades werken, zonder handmatig in de UniFi-app te kijken.
Alleen de clients in `config.json` worden gemeten. Niets anders in huis.

## Onderdelen

Op de Mac Mini (dit script, via launchd elke 15 minuten):

- `schoollaptops_dpi_sync.py` haalt per laptop de DPI-tellers per app op bij de UCG
  (`/proxy/network/api/s/default/stat/stadpi`, type `by_app`), berekent het dagverschil
  en stuurt dat naar HA. MAC-adressen worden bij de UCG opgezocht op basis van het vaste IP.
- `config.json` (niet in git): API-sleutel, HA-token, de drie laptops.
- `state.json`: laatste tellerstanden en dagtotalen. `last_response_<prefix>.json`: ruwe
  antwoorden van de UCG, handig als het antwoordformaat anders blijkt dan verwacht.
- `sync.log`: logboek.

In Home Assistant (al aangemaakt):

| Per kind (prefix `thijs_laptop`, `jochem_laptop`, `sieb_laptop`) | Doel |
|---|---|
| `input_number.<prefix>_youtube_mb_vandaag` | YouTube-verkeer vandaag |
| `input_number.<prefix>_vpn_proxy_mb_vandaag` | verkeer in DPI-categorie 11 (VPN/proxy/Tor) |
| `input_number.<prefix>_totaal_mb_vandaag` | alle verkeer vandaag |
| `input_number.<prefix>_youtube_drempel_mb` | meldingsdrempel (standaard 100 MB) |
| `input_text.<prefix>_top_apps_vandaag` | top 4 apps vandaag |
| `input_datetime.<prefix>_dpi_laatste_check` | tijdstip laatste meting |
| `automation.<prefix>_youtube_verkeer_gedetecteerd` | pushmelding boven de drempel |
| `automation.<prefix>_vpn_proxy_verkeer_gedetecteerd` | pushmelding boven 20 MB |
| `automation.<prefix>_dpi_meting_stilgevallen` | melding als 2 uur geen meting (script zet hem aan) |

Gedeeld:

- `automation.schoolapparaten_unifi_regels_bewaken` zet de vijf UniFi-regels voor de
  schoolapparaten weer aan als iemand ze uitschakelt, en meldt dat.
- `input_boolean.schoolapparaten_regels_bewaken`: uit = bewuste uitzondering toestaan.
- Dashboard `iPad Wand`, kolom 2, blok "Schoollaptops": een regel per kind.

## Installeren op de Mac Mini

1. UniFi-app op `https://192.168.68.1`: Settings, Control Plane, Integrations, Create API Key.
2. HA: profiel (linksonder), tabblad Security, Long-lived access tokens, Create token.
3. Terminal op de Mac:

```
mkdir -p ~/scripts/src && cd ~/scripts/src
git clone --branch claude/thijs-laptop-limits-check-1u0lyv https://github.com/Derk1999/derk1999.github.io.git schoollaptops-dpi 2>/dev/null || (cd schoollaptops-dpi && git pull)
cd schoollaptops-dpi/tools/schoollaptops-dpi-monitor
bash install.sh
```

De installer vraagt om de sleutel en het token, schrijft `config.json`, laadt de
launchd-job en doet een testrun. De eerste run is een nulmeting (0 MB); vanaf de
tweede run (15 minuten later) komen de echte dagtotalen.

## Controleren

```
tail -20 ~/scripts/schoollaptops-dpi/sync.log
launchctl list | grep schoollaptopsdpi
```

Op het iPad-dashboard wordt de tegel groen met "Blokkades werken" zodra de eerste
meting binnen is. Grijs = nog geen of verouderde meting. Rood = YouTube boven de
drempel of VPN/proxy boven 20 MB, dan is er ook een pushmelding gegaan.

## Interpretatie

- Werkt het blok, dan ziet DPI toch nog het begin van elke poging (TLS-handshake).
  Een paar MB YouTube per dag is dus normaal. Tientallen MB is video.
- Category 11 verkeer boven 20 MB betekent dat een VPN of proxy echt data doorlaat.
- App-id's zonder naam staan als `app <cat>:<app>`; alleen YouTube (`4:112`) heeft een
  naam. De naam van andere id's staat in de UniFi-app onder de client, tabblad Traffic.
  Voeg ze toe aan `APP_NAMES` in het script als je ze vaker wilt zien.

## Problemen

- HTTP 401/403 van de UCG: API-sleutel ongeldig. Vul eventueel `username`/`password` van
  het lokale admin-account in `config.json` als fallback.
- "geen MAC bekend voor 192.168.68.x": de UCG kent geen client met dat vaste IP. Check in
  de UniFi-app welke client het IP heeft.
- "Onverwacht antwoord van UCG": kijk in `last_response_<prefix>.json` en pas
  `extract_counters` aan.
- Handmatig draaien: `python3 ~/scripts/schoollaptops-dpi/schoollaptops_dpi_sync.py --verbose`.

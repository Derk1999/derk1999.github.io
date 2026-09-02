# Toezicht: websites per apparaat

Pipeline die elk uur uit AdGuard Home een overzicht bouwt van de top 20
websites per apparaat (IP), drukste apparaat bovenaan, en dit als HTML
naar de Home Assistant `www`-map schrijft. Het Toezicht-dashboard toont
dit bovenaan de Schermtijd-pagina als iframe (`/local/toezicht_websites.html`).

Zelfde patroon als de weekschema-pipeline: script + launchd op de Mac Mini,
output via de Samba-mount `~/mnt/ha-config/www/`.

## Voorbereiding in Home Assistant

Al gedaan in deze setup: de add-on heeft poort **80/tcp** vrijgegeven als
**8053** en `leave_front_door_open` staat aan, dus de API is bereikbaar op
`http://homeassistant.local:8053` zonder login. Geeft de API ooit een 401,
vul dan `username`/`password` in config.json in.

## Installatie op de Mac Mini

```
mkdir -p ~/scripts/toezicht && curl -fsSL https://raw.githubusercontent.com/derk1999/derk1999.github.io/claude/chrome-boek-school-website-femtjq/toezicht-websites/install.sh | bash
```

Daarna testen:

```
python3 ~/scripts/toezicht/sync_toezicht_websites.py
```

Moet eindigen met `OK: toezicht_websites.html geschreven`.

## Wat het meet (en niet)

- **Actieve minuten** per site = minuten waarin dat domein minstens één
  DNS-verzoek deed. Goede benadering van bestede tijd, geen exacte schermtijd.
- Alleen verkeer op het **thuisnetwerk**; school/4G blijft onzichtbaar.
- Infrastructuur-ruis (telemetrie, tijdservers, advertentienetwerken) wordt
  weggefilterd; uitbreiden kan via `extra_noise_domains` in config.json.
- Geblokkeerde pogingen staan apart vermeld per apparaat.

## Configuratie

`~/scripts/toezicht/config.json`:

- `client_names`: IP → leesbare naam (b.v. `"192.168.68.88": "Chromebook Tieners"`)
- `exclude_ips`: apparaten die je niet wilt zien (b.v. eigen telefoons)
- `hours` (24), `top_n` (20), `min_queries` (20): venster en drempels
- Log: `~/scripts/toezicht/sync.log`

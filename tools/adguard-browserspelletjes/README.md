# Browserspelletjes blokkeren voor "Chromebook Tieners" (besluit Elske 02-10-2026)

Voegt 31 domeinregels plus één regex-regel toe aan de AdGuard Home user rules,
alleen voor de client `Chromebook Tieners` (.85/.88/.91/.94). Raakt `PC speelkamer`
(.84) en `Telefoons Tieners` niet.

Draaien **op de Mac Mini** (of Derks pc) in het thuisnetwerk; AdGuard op
192.168.68.68 is vanuit een cloud-sessie niet bereikbaar.

```bash
cd ~/scripts/toezicht
curl -fsSLO https://raw.githubusercontent.com/Derk1999/derk1999.github.io/ccr-ea518e6b-imib8r/tools/adguard-browserspelletjes/adguard_browserspelletjes_2026-10-02.py
python3 adguard_browserspelletjes_2026-10-02.py --dry-run   # eerst kijken
python3 adguard_browserspelletjes_2026-10-02.py             # uitvoeren
```

Wat het doet:
1. `GET /control/filtering/status`, backup naar
   `~/scripts/toezicht/adguard_user_rules_backup_2026-10-02.txt`. Bestaat die al en
   wijkt hij af van de live regels, dan stopt het script (exit 2) zonder iets te wijzigen.
2. Commentaarregel `! Browserspelletjes Chromebook Tieners (02-10-2026)` + de regels
   toevoegen (geen duplicaten, bestaande regels ongemoeid), `POST /control/filtering/set_rules`.
3. 5 s wachten, dan `GET /control/filtering/check_host` voor .91 (poki.com,
   gamedistribution.com, crazygames.com → FilteredBlackList; google.com,
   classroom.google.com, somtoday.nl, zermelo.nl → NotFiltered of FilteredSafeSearch)
   en .84 (poki.com → niet FilteredBlackList). Blocklist-checks worden tot 3x herhaald
   omdat AdGuard direct na `set_rules` soms nog oud antwoordt.
4. Samenvatting: regels voor/na, checks, backup-pad. Geen wachtwoorden in de output.

Credentials komen uit `~/unifi-migratie/.env` (`ADGUARD_USER`, `ADGUARD_PASSWORD`).
Terugdraaien: de inhoud van het backup-bestand terugzetten via `set_rules`
(of in de AdGuard-UI onder Filters → Custom filtering rules).

Getest op 02-10-2026 tegen een nagebouwde AdGuard-API: dry-run, eerste run
(3 → 36 regels, 8/8 checks), afwijkende backup (stopt), herhaalde run (0 duplicaten),
fout wachtwoord (HTTP 401, stopt).

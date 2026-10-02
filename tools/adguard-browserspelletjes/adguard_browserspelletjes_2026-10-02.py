#!/usr/bin/env python3
"""Blokkeer browserspelletjes voor de AdGuard-client 'Chromebook Tieners'.

Besluit Elske 02-10-2026. Alleen standaardbibliotheek (urllib), geen extra packages.
Draaien op de Mac Mini (of Derks pc) in het thuisnetwerk:

    python3 adguard_browserspelletjes_2026-10-02.py            # uitvoeren
    python3 adguard_browserspelletjes_2026-10-02.py --dry-run  # alleen tonen wat er zou gebeuren

Stappen:
 1. GET /control/filtering/status -> backup van user_rules naar
    ~/scripts/toezicht/adguard_user_rules_backup_2026-10-02.txt
    (stopt als dat bestand al bestaat en afwijkt van de live regels)
 2. Nieuwe regels toevoegen (bestaande ongemoeid, geen duplicaten) en wegschrijven
    met POST /control/filtering/set_rules
 3. 5 s wachten, verifieren met check_host voor .91 (geblokkeerd / niet geblokkeerd)
    en .84 (poki.com NIET geblokkeerd)
 4. Korte samenvatting. Geen wachtwoorden in de output.
"""
import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("ADGUARD_BASE", "http://192.168.68.68:8053")
ENV_FILE = os.path.expanduser(os.environ.get("ADGUARD_ENV_FILE", "~/unifi-migratie/.env"))
BACKUP_PATH = os.path.expanduser(
    os.environ.get("ADGUARD_BACKUP_PATH",
                   "~/scripts/toezicht/adguard_user_rules_backup_2026-10-02.txt"))

CLIENT = "Chromebook Tieners"
COMMENT = "! Browserspelletjes Chromebook Tieners (02-10-2026)"
DOMAINS = [
    "poki.com", "poki.nl", "poki-gdn.com", "crazygames.com", "crazygames.nl",
    "gamedistribution.com", "gamemonetize.com", "gamepix.com", "html5games.com",
    "y8.com", "friv.com", "kizi.com", "agame.com", "miniclip.com", "lagged.com",
    "coolmathgames.com", "spele.nl", "1001spelletjes.nl", "funnygames.nl",
    "twoplayergames.org", "iogames.space", "armorgames.com", "kongregate.com",
    "newgrounds.com", "itch.io", "slither.io", "agar.io", "krunker.io", "1v1.lol",
    "shellshock.io", "retrobowl.me",
]
REGEX_RULE = "/unblocked[-_]?games?/$client='%s'" % CLIENT
NEW_RULES = ["||%s^$client='%s'" % (d, CLIENT) for d in DOMAINS] + [REGEX_RULE]

KID_IP = "192.168.68.91"      # Chromebook Tieners (Sieb)
OTHER_IP = "192.168.68.84"    # PC speelkamer, mag NIET geraakt worden
EXPECT_BLOCKED = ["poki.com", "gamedistribution.com", "crazygames.com"]
EXPECT_OPEN = ["google.com", "classroom.google.com", "somtoday.nl", "zermelo.nl"]
OPEN_OK = ("NotFilteredNotFound", "NotFilteredWhiteList", "FilteredSafeSearch")


def load_env(path):
    creds = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                if line.startswith("export "):
                    line = line[7:]
                key, _, val = line.partition("=")
                creds[key.strip()] = val.strip().strip("'\"")
    except FileNotFoundError:
        sys.exit("FOUT: %s niet gevonden. Draai dit script op de Mac Mini." % path)
    if not creds.get("ADGUARD_USER") or not creds.get("ADGUARD_PASSWORD"):
        sys.exit("FOUT: ADGUARD_USER/ADGUARD_PASSWORD ontbreken in %s" % path)
    return creds["ADGUARD_USER"], creds["ADGUARD_PASSWORD"]


class AdGuard:
    def __init__(self, base, user, password):
        self.base = base.rstrip("/")
        token = base64.b64encode(("%s:%s" % (user, password)).encode()).decode()
        self.headers = {"Authorization": "Basic " + token,
                        "Content-Type": "application/json"}

    def _call(self, method, path, body=None, timeout=20):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data,
                                     headers=self.headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            sys.exit("FOUT: %s %s -> HTTP %s %s" % (method, path, exc.code, detail))
        except (urllib.error.URLError, OSError) as exc:
            sys.exit("FOUT: %s %s onbereikbaar (%s). Zit je in het thuisnetwerk?"
                     % (method, path, exc))
        return json.loads(raw) if raw.strip() else {}

    def status(self):
        return self._call("GET", "/control/filtering/status")

    def set_rules(self, rules):
        return self._call("POST", "/control/filtering/set_rules", {"rules": rules})

    def check_host(self, name, client):
        qs = urllib.parse.urlencode({"name": name, "client": client})
        return self._call("GET", "/control/filtering/check_host?" + qs).get("reason", "?")


def rules_text(rules):
    return "\n".join(rules) + ("\n" if rules else "")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true",
                    help="niets schrijven, alleen tonen wat er zou gebeuren")
    args = ap.parse_args()

    user, password = load_env(ENV_FILE)
    ag = AdGuard(BASE, user, password)

    # Stap 1: huidige regels ophalen + backup
    live_rules = list(ag.status().get("user_rules") or [])
    n_before = len(live_rules)
    live_text = rules_text(live_rules)

    if os.path.exists(BACKUP_PATH):
        with open(BACKUP_PATH, encoding="utf-8") as fh:
            existing = fh.read()
        if existing != live_text:
            print("STOP: backup %s bestaat al en is ONGELIJK aan de live regels."
                  % BACKUP_PATH)
            print("      backup: %d regels, live: %d regels. Niets gewijzigd."
                  % (len(existing.splitlines()), n_before))
            print("      Vergelijk eerst handmatig (bijv. met diff) voordat je verder gaat.")
            sys.exit(2)
        print("Backup bestaat al en is gelijk aan de live regels: %s" % BACKUP_PATH)
    elif args.dry_run:
        print("[dry-run] Zou backup schrijven naar %s (%d regels)" % (BACKUP_PATH, n_before))
    else:
        os.makedirs(os.path.dirname(BACKUP_PATH), exist_ok=True)
        with open(BACKUP_PATH, "w", encoding="utf-8") as fh:
            fh.write(live_text)
        os.chmod(BACKUP_PATH, 0o600)
        print("Backup geschreven: %s (%d regels)" % (BACKUP_PATH, n_before))

    # Stap 2: nieuwe regels bepalen (geen duplicaten, bestaande onaangetast)
    existing_set = set(r.strip() for r in live_rules)
    to_add = [r for r in NEW_RULES if r not in existing_set]
    already = len(NEW_RULES) - len(to_add)
    block = []
    if to_add:
        if COMMENT not in existing_set:
            block.append(COMMENT)
        block.extend(to_add)
    new_rules = live_rules + block
    n_after = len(new_rules)

    print("Regels: %d voor -> %d na (+%d nieuw, %d stonden er al)"
          % (n_before, n_after, len(to_add), already))
    if not to_add:
        print("Niets toe te voegen; set_rules wordt overgeslagen.")
    elif args.dry_run:
        print("[dry-run] Zou toevoegen:")
        for r in block:
            print("   " + r)
        return
    else:
        ag.set_rules(new_rules)
        print("set_rules uitgevoerd.")

    # Stap 3: verifieren
    print("5 s wachten voor verificatie...")
    time.sleep(5)
    results = []  # (label, ok)

    for name in EXPECT_BLOCKED:
        reason = None
        for attempt in range(3):          # direct na set_rules kan AdGuard nog oud antwoorden
            reason = ag.check_host(name, KID_IP)
            if reason == "FilteredBlackList":
                break
            if attempt < 2:
                time.sleep(5)
        ok = reason == "FilteredBlackList"
        results.append(("%s @ %s geblokkeerd (%s)" % (name, KID_IP, reason), ok))

    for name in EXPECT_OPEN:
        reason = ag.check_host(name, KID_IP)
        ok = reason in OPEN_OK
        results.append(("%s @ %s open (%s)" % (name, KID_IP, reason), ok))

    reason = ag.check_host("poki.com", OTHER_IP)
    ok = reason != "FilteredBlackList"
    results.append(("poki.com @ %s NIET geblokkeerd (%s)" % (OTHER_IP, reason), ok))

    # Stap 4: samenvatting
    print("\nSamenvatting")
    print("  Client        : %s" % CLIENT)
    print("  Regels        : %d voor, %d na" % (n_before, n_after))
    print("  Backup        : %s" % BACKUP_PATH)
    print("  Verificatie   :")
    failed = 0
    for label, ok in results:
        print("    [%s] %s" % ("OK " if ok else "FOUT", label))
        failed += 0 if ok else 1
    if failed:
        print("  %d check(s) mislukt. Regels staan WEL in AdGuard; controleer handmatig "
              "of herstel met de backup." % failed)
        sys.exit(1)
    print("  Alle %d checks geslaagd." % len(results))


if __name__ == "__main__":
    main()

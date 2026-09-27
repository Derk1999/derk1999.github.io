#!/usr/bin/env python3
"""
schoollaptops_dpi_sync.py

Haalt bij de UniFi-gateway (UCG-Ultra) de DPI-statistieken per app op voor de
schoollaptops (Thijs, Jochem, Sieb) en zet dagtotalen in Home Assistant-helpers.
Alleen de clients die in config.json onder "clients" staan worden gemeten; niets
anders in huis. Een client wordt aangeduid met zijn vaste IP (zoals de UniFi-regels
dat doen); het MAC-adres wordt bij de gateway opgezocht en gecachet.
Per client (prefix bijv. thijs_laptop):

  input_number.thijs_laptop_youtube_mb_vandaag    YouTube-verkeer vandaag (MB)
  input_number.thijs_laptop_vpn_proxy_mb_vandaag  VPN/proxy-verkeer vandaag (MB, DPI-categorie 11)
  input_number.thijs_laptop_totaal_mb_vandaag     al het verkeer vandaag (MB)
  input_text.thijs_laptop_top_apps_vandaag        top 4 apps vandaag
  input_datetime.thijs_laptop_dpi_laatste_check   tijdstip laatste geslaagde meting

De DPI-tellers op de gateway zijn cumulatief. Dit script bewaart de vorige
tellerstand in state.json en telt alleen het verschil op bij het dagtotaal.
Bij een teller die terugvalt (herstart gateway, reset DPI) wordt de nieuwe
stand als verschil genomen.

Alleen standaardbibliotheek, geen pip nodig. Draait via launchd elke 15 minuten.

Gebruik:
  python3 schoollaptops_dpi_sync.py            normale run (stil, logt naar sync.log)
  python3 schoollaptops_dpi_sync.py --verbose  ook naar de terminal loggen
  python3 schoollaptops_dpi_sync.py --dry-run  niets naar HA sturen, wel state bijwerken
  python3 schoollaptops_dpi_sync.py --parse-file bestand.json
                                       verwerk een opgeslagen UCG-antwoord (debug)
  python3 schoollaptops_dpi_sync.py --probe
                                       probeer verschillende DPI-endpoints op de UCG en toon
                                       per variant hoeveel data terugkomt (debug, niets naar HA)
"""

import datetime
import http.cookiejar
import json
import logging
import os
import ssl
import sys
import urllib.error
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(BASE, "config.json")
STATE_PATH = os.path.join(BASE, "state.json")
RAW_PATH = os.path.join(BASE, "last_response.json")
LOG_PATH = os.path.join(BASE, "sync.log")

# UniFi DPI: compound app-id = (categorie << 16) + app.  262256 = 4 << 16 + 112 = YouTube.
YOUTUBE_KEYS = {"4:112"}
VPN_CATS = {11}  # "Bypass proxies & tunnels"
APP_NAMES = {"4:112": "YouTube"}

# Per client een set HA-helpers. Prefix "thijs_laptop" hoort bij de helpers die
# in HA zijn aangemaakt. Voor een extra laptop: helpers met andere prefix aanmaken
# en een extra item in config.json "clients" zetten.
def entities_for(prefix):
    return {
        "youtube": f"input_number.{prefix}_youtube_mb_vandaag",
        "vpn": f"input_number.{prefix}_vpn_proxy_mb_vandaag",
        "total": f"input_number.{prefix}_totaal_mb_vandaag",
        "top": f"input_text.{prefix}_top_apps_vandaag",
        "lastcheck": f"input_datetime.{prefix}_dpi_laatste_check",
        "watchdog": f"automation.{prefix}_dpi_meting_stilgevallen",
    }

TIMEOUT = 20


def setup_logging(verbose):
    try:
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 1_000_000:
            os.replace(LOG_PATH, LOG_PATH + ".1")
    except OSError:
        pass
    handlers = [logging.FileHandler(LOG_PATH)]
    if verbose:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)


def insecure_ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # UCG heeft een self-signed certificaat
    return ctx


class UnifiClient:
    def __init__(self, cfg):
        self.base = cfg["ucg_url"].rstrip("/")
        self.site = cfg.get("site", "default")
        self.api_key = cfg.get("api_key") or ""
        self.username = cfg.get("username") or ""
        self.password = cfg.get("password") or ""
        self.jar = http.cookiejar.CookieJar()
        self.csrf = ""
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar),
            urllib.request.HTTPSHandler(context=insecure_ctx()),
        )

    def _request(self, path, body=None, extra_headers=None):
        url = self.base + path
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.api_key:
            headers["X-API-KEY"] = self.api_key
        if self.csrf:
            headers["x-csrf-token"] = self.csrf
        if extra_headers:
            headers.update(extra_headers)
        req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
        with self.opener.open(req, timeout=TIMEOUT) as resp:
            token = resp.headers.get("x-csrf-token")
            if token:
                self.csrf = token
            raw = resp.read().decode("utf-8", "replace")
            return json.loads(raw) if raw.strip() else {}

    def login(self):
        if not (self.username and self.password):
            raise RuntimeError("Geen API-sleutel geaccepteerd en geen username/password in config.json")
        logging.info("Inloggen met gebruikersnaam/wachtwoord (fallback)")
        self._request("/api/auth/login", {"username": self.username, "password": self.password, "rememberMe": False})

    def _get(self, path):
        self.ensure_session()
        try:
            return self._request(path)
        except urllib.error.HTTPError as err:
            if err.code in (401, 403):
                self.login()
                return self._request(path)
            raise

    def resolve_mac(self, ip):
        """Zoek het MAC-adres bij een IP: eerst vaste reservering (rest/user), dan actieve clients (stat/sta)."""
        users = self._get(f"/proxy/network/api/s/{self.site}/rest/user").get("data") or []
        for u in users:
            if isinstance(u, dict) and u.get("use_fixedip") and u.get("fixed_ip") == ip and u.get("mac"):
                return u["mac"].lower(), u.get("name") or u.get("hostname") or ""
        active = self._get(f"/proxy/network/api/s/{self.site}/stat/sta").get("data") or []
        for s in active:
            if isinstance(s, dict) and s.get("ip") == ip and s.get("mac"):
                return s["mac"].lower(), s.get("name") or s.get("hostname") or ""
        return None, ""

    def ensure_session(self):
        if not self.api_key and self.username and not self.csrf:
            self.login()

    def dpi_by_app(self, mac):
        self.ensure_session()
        path = f"/proxy/network/api/s/{self.site}/stat/stadpi"
        body = {"type": "by_app", "macs": [mac.lower()]}
        try:
            return self._request(path, body)
        except urllib.error.HTTPError as err:
            if err.code in (401, 403):
                logging.warning("UCG gaf %s op API-sleutel, probeer login-fallback", err.code)
                self.login()
                return self._request(path, body)
            raise


def extract_counters(response, mac):
    """Geef dict 'cat:app' -> bytes (rx+tx) uit het stadpi-antwoord."""
    data = response.get("data") if isinstance(response, dict) else None
    if not isinstance(data, list):
        raise ValueError("Onverwacht antwoord van UCG (geen data-lijst)")
    entry = None
    for item in data:
        if isinstance(item, dict) and str(item.get("mac", "")).lower() == mac.lower():
            entry = item
            break
    if entry is None and len(data) == 1 and isinstance(data[0], dict):
        entry = data[0]
    if entry is None:
        return {}
    apps = entry.get("by_app")
    if apps is None:
        apps = [entry] if "app" in entry else []
    counters = {}
    for app in apps:
        if not isinstance(app, dict):
            continue
        raw_app = int(app.get("app", -1))
        cat = app.get("cat")
        if raw_app > 0xFFFF:  # compound id
            cat = raw_app >> 16
            raw_app = raw_app & 0xFFFF
        if cat is None:
            cat = -1
        key = f"{int(cat)}:{raw_app}"
        total = int(app.get("rx_bytes", 0) or 0) + int(app.get("tx_bytes", 0) or 0)
        counters[key] = counters.get(key, 0) + total
    return counters


def update_state(state, counters, today):
    """Werk dagtotalen bij op basis van tellerverschillen. Geeft het bijgewerkte state-dict terug."""
    if state.get("date") != today:
        state["date"] = today
        state["today"] = {}
    prev_all = state.get("counters") or {}
    first_run = not state.get("baselined", False)
    today_bytes = state.get("today") or {}
    for key, cur in counters.items():
        prev = prev_all.get(key)
        if first_run:
            delta = 0
        elif prev is None:
            delta = cur
        elif cur >= prev:
            delta = cur - prev
        else:
            delta = cur  # teller gereset
        if delta:
            today_bytes[key] = today_bytes.get(key, 0) + delta
    merged = dict(prev_all)
    merged.update(counters)
    state["counters"] = merged
    state["today"] = today_bytes
    state["baselined"] = True
    return state


def summarize(today_bytes):
    yt = sum(v for k, v in today_bytes.items() if k in YOUTUBE_KEYS)
    vpn = sum(v for k, v in today_bytes.items() if int(k.split(":")[0]) in VPN_CATS)
    total = sum(today_bytes.values())
    top = sorted(today_bytes.items(), key=lambda kv: kv[1], reverse=True)[:4]
    parts = []
    for key, val in top:
        if val <= 0:
            continue
        cat = int(key.split(":")[0])
        name = APP_NAMES.get(key) or ("VPN/proxy " + key if cat in VPN_CATS else "app " + key)
        parts.append(f"{name} {val / 1e6:.0f} MB")
    top_text = " · ".join(parts) if parts else "geen verkeer vandaag"
    return yt / 1e6, vpn / 1e6, total / 1e6, top_text[:255]


def ha_call(cfg, domain, service, payload):
    url = cfg["ha_url"].rstrip("/") + f"/api/services/{domain}/{service}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": "Bearer " + cfg["ha_token"], "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        resp.read()


def push_to_ha(cfg, ent, yt_mb, vpn_mb, total_mb, top_text, now_local):
    ha_call(cfg, "input_number", "set_value", {"entity_id": ent["youtube"], "value": round(yt_mb, 1)})
    ha_call(cfg, "input_number", "set_value", {"entity_id": ent["vpn"], "value": round(vpn_mb, 1)})
    ha_call(cfg, "input_number", "set_value", {"entity_id": ent["total"], "value": round(total_mb, 1)})
    ha_call(cfg, "input_text", "set_value", {"entity_id": ent["top"], "value": top_text})
    ha_call(cfg, "input_datetime", "set_datetime",
            {"entity_id": ent["lastcheck"], "datetime": now_local.strftime("%Y-%m-%d %H:%M:%S")})
    # Watchdog-automation pas actief zodra er echt metingen binnenkomen.
    try:
        ha_call(cfg, "automation", "turn_on", {"entity_id": ent["watchdog"]})
    except urllib.error.HTTPError:
        pass  # geen watchdog voor deze client, niet erg


def clients_from_config(cfg):
    clients = cfg.get("clients")
    if not clients and cfg.get("client_mac"):
        clients = [{"name": "Thijs", "mac": cfg["client_mac"], "prefix": "thijs_laptop"}]
    result = []
    for c in clients or []:
        if not (c.get("mac") or c.get("ip")):
            continue
        name = c.get("name") or c.get("ip") or c.get("mac")
        result.append({
            "name": name,
            "ip": c.get("ip") or "",
            "mac": (c.get("mac") or "").lower(),
            "prefix": c.get("prefix") or (name.lower() + "_laptop"),
        })
    return result


def _preview(obj, n=700):
    txt = json.dumps(obj, ensure_ascii=False)
    return txt if len(txt) <= n else txt[:n] + " ..."


def probe(cfg, clients):
    """Probeer meerdere DPI-varianten en print per variant een samenvatting. Slaat alles op in probe_result.json."""
    unifi = UnifiClient(cfg)
    site = unifi.site
    mac = ""
    if clients:
        c = clients[0]
        mac = c["mac"]
        if not mac and c["ip"]:
            try:
                mac, _ = unifi.resolve_mac(c["ip"])
            except Exception as err:  # noqa: BLE001
                print("MAC opzoeken mislukt:", err)
        print(f"Probe voor {c['name']} ({c['ip'] or '-'} / {mac or 'geen MAC'})")
    variants = [
        ("POST stat/stadpi by_app+macs", f"/proxy/network/api/s/{site}/stat/stadpi", {"type": "by_app", "macs": [mac]}),
        ("POST stat/stadpi by_cat+macs", f"/proxy/network/api/s/{site}/stat/stadpi", {"type": "by_cat", "macs": [mac]}),
        ("POST stat/stadpi by_app (alle clients)", f"/proxy/network/api/s/{site}/stat/stadpi", {"type": "by_app"}),
        ("GET  stat/stadpi", f"/proxy/network/api/s/{site}/stat/stadpi", None),
        ("POST stat/sitedpi by_app", f"/proxy/network/api/s/{site}/stat/sitedpi", {"type": "by_app"}),
        ("POST stat/sitedpi by_cat", f"/proxy/network/api/s/{site}/stat/sitedpi", {"type": "by_cat"}),
        ("GET  stat/dpi", f"/proxy/network/api/s/{site}/stat/dpi", None),
        ("GET  rest/setting (dpi aan?)", f"/proxy/network/api/s/{site}/rest/setting", None),
    ]
    results = {}
    for label, path, body in variants:
        try:
            unifi.ensure_session()
            resp = unifi._request(path, body)
        except urllib.error.HTTPError as err:
            print(f"{label:42s} HTTP {err.code}")
            results[label] = {"error": err.code}
            continue
        except Exception as err:  # noqa: BLE001
            print(f"{label:42s} fout: {err}")
            results[label] = {"error": str(err)}
            continue
        data = resp.get("data") if isinstance(resp, dict) else resp
        if label.startswith("GET  rest/setting"):
            dpi_settings = [d for d in (data or []) if isinstance(d, dict) and d.get("key") in ("dpi", "traffic_identification", "ips")]
            print(f"{label:42s} {_preview(dpi_settings, 400)}")
            results[label] = dpi_settings
            continue
        n = len(data) if isinstance(data, list) else "?"
        sub = ""
        if isinstance(data, list) and data and isinstance(data[0], dict):
            first = data[0]
            keys = sorted(first.keys())
            sub = f" keys={keys[:12]}"
            for k in ("by_app", "by_cat"):
                if isinstance(first.get(k), list):
                    sub += f" {k}={len(first[k])}"
            macs = [d.get("mac") for d in data if isinstance(d, dict) and d.get("mac")]
            if macs:
                sub += f" macs={len(macs)} onze_mac={'ja' if mac in macs else 'nee'}"
        print(f"{label:42s} data={n}{sub}")
        print("    " + _preview(data, 500))
        results[label] = data
    save_json(os.path.join(BASE, "probe_result.json"), results)
    print("Volledige antwoorden opgeslagen in", os.path.join(BASE, "probe_result.json"))
    return 0


def main(argv):
    verbose = "--verbose" in argv or "-v" in argv
    dry_run = "--dry-run" in argv
    parse_file = None
    if "--parse-file" in argv:
        idx = argv.index("--parse-file")
        parse_file = argv[idx + 1] if idx + 1 < len(argv) else None
        if not parse_file:
            print("Gebruik: --parse-file <bestand.json>")
            return 2
    setup_logging(verbose)

    cfg = load_json(CFG_PATH, None)
    if not cfg:
        logging.error("config.json ontbreekt of is ongeldig: %s", CFG_PATH)
        return 2
    for key in ("ucg_url", "ha_url", "ha_token"):
        if not cfg.get(key):
            logging.error("config.json mist veld '%s'", key)
            return 2
    clients = clients_from_config(cfg)
    if not clients:
        logging.error("config.json bevat geen clients (of client_mac)")
        return 2

    if "--probe" in argv:
        return probe(cfg, clients)

    unifi = UnifiClient(cfg)
    all_state = load_json(STATE_PATH, {})
    if not isinstance(all_state, dict) or ("counters" in all_state and "clients" not in all_state):
        all_state = {}  # oud formaat (enkele client) opnieuw baselinen
    per_client = all_state.setdefault("clients", {})
    now_local = datetime.datetime.now()
    today = now_local.date().isoformat()
    rc = 0

    mac_cache = all_state.setdefault("mac_by_ip", {})
    for client in clients:
        ent = entities_for(client["prefix"])
        mac = client["mac"]
        if not mac and client["ip"]:
            mac = mac_cache.get(client["ip"], "")
            if not mac or not parse_file:
                try:
                    found, ucg_name = unifi.resolve_mac(client["ip"])
                except (urllib.error.URLError, OSError, ValueError, RuntimeError) as err:
                    logging.error("[%s] MAC opzoeken voor %s mislukt: %s", client["name"], client["ip"], err)
                    found, ucg_name = None, ""
                if found:
                    if found != mac:
                        logging.info("[%s] %s hoort bij MAC %s (UniFi-naam: %s)",
                                     client["name"], client["ip"], found, ucg_name or "-")
                    mac = found
                    mac_cache[client["ip"]] = mac
        if not mac:
            logging.error("[%s] geen MAC bekend voor %s (client nooit gezien op de UCG?)", client["name"], client["ip"])
            rc = 1
            continue
        try:
            if parse_file:
                response = load_json(parse_file, None)
                if response is None:
                    logging.error("Kan %s niet lezen", parse_file)
                    return 2
            else:
                response = unifi.dpi_by_app(mac)
                save_json(RAW_PATH.replace(".json", f"_{client['prefix']}.json"), response)
        except urllib.error.HTTPError as err:
            logging.error("[%s] UCG HTTP-fout %s: %s", client["name"], err.code, err.reason)
            rc = 1
            continue
        except (urllib.error.URLError, OSError, ValueError, RuntimeError) as err:
            logging.error("[%s] UCG niet bereikbaar of ongeldig antwoord: %s", client["name"], err)
            rc = 1
            continue

        try:
            counters = extract_counters(response, mac)
        except ValueError as err:
            logging.error("[%s] %s. Antwoord opgeslagen naast het script", client["name"], err)
            rc = 1
            continue

        state = per_client.get(client["prefix"])
        if not isinstance(state, dict) or state.get("mac") != mac:
            state = {"mac": mac}  # nieuw of ander apparaat: opnieuw baselinen
        state = update_state(state, counters, today)
        per_client[client["prefix"]] = state
        save_json(STATE_PATH, all_state)

        yt_mb, vpn_mb, total_mb, top_text = summarize(state["today"])
        logging.info("[%s] apps=%d youtube=%.1fMB vpn=%.1fMB totaal=%.1fMB top=%s",
                     client["name"], len(counters), yt_mb, vpn_mb, total_mb, top_text)

        if dry_run:
            logging.info("[%s] dry-run: niets naar HA gestuurd", client["name"])
            continue
        try:
            push_to_ha(cfg, ent, yt_mb, vpn_mb, total_mb, top_text, now_local)
        except urllib.error.HTTPError as err:
            logging.error("[%s] HA HTTP-fout %s: %s (token geldig? helpers aanwezig?)",
                          client["name"], err.code, err.reason)
            rc = 1
        except (urllib.error.URLError, OSError) as err:
            logging.error("[%s] HA niet bereikbaar: %s", client["name"], err)
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

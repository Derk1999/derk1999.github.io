#!/usr/bin/env python3
"""Toezicht: top websites per apparaat, uit AdGuard Home.

Haalt per client-IP de AdGuard-querylog op (afgelopen 24 uur), aggregeert
naar hoofddomeinen en schrijft een donker HTML-overzicht naar de HA
www-map (Samba mount). Drukste apparaat bovenaan, top 20 domeinen per
apparaat, gesorteerd op "actieve minuten" (minuten waarin het domein
minstens 1 DNS-verzoek deed) als benadering van bestede tijd.

Alleen Python-stdlib. Draait via launchd elk uur op de Mac Mini.
"""
import html
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "config.json"
LOG_PATH = SCRIPT_DIR / "sync.log"

DEFAULTS = {
    "base_url": "http://homeassistant.local:8053",
    "username": "",
    "password": "",
    "hours": 24,
    "top_n": 20,
    "top_blocked_n": 5,
    "max_clients": 12,
    "max_entries_per_client": 4000,
    "min_queries": 20,
    "client_names": {},
    "exclude_ips": [],
    "output_dir": "~/mnt/ha-config/www",
    "output_name": "toezicht_websites.html",
    "extra_noise_domains": [],
}

# Infrastructuur/telemetrie-domeinen die niets zeggen over waar iemand
# echt op zit. Suffix-match: "gvt1.com" filtert ook "edgedl.me.gvt1.com".
NOISE_DOMAINS = [
    "in-addr.arpa", "ip6.arpa", "home.arpa", "local", "lan",
    "ntp.org", "time.google.com", "time.apple.com", "time.windows.com",
    "time.cloudflare.com",
    "connectivitycheck.gstatic.com", "msftconnecttest.com", "msftncsi.com",
    "captive.apple.com", "apple-dns.net", "push.apple.com", "ocsp.apple.com",
    "mtalk.google.com", "clients.l.google.com", "gvt1.com", "gvt2.com",
    "gvt3.com", "update.googleapis.com", "safebrowsing.googleapis.com",
    "optimizationguide-pa.googleapis.com", "content-autofill.googleapis.com",
    "firebaselogging-pa.googleapis.com", "app-measurement.com",
    "crashlytics.com", "doubleclick.net", "googlesyndication.com",
    "googleadservices.com", "googletagmanager.com", "google-analytics.com",
    "adservice.google.com",
    "digicert.com", "pki.goog", "amazontrust.com", "lencr.org",
]

TWO_PART_TLDS = {
    "co.uk", "org.uk", "ac.uk", "com.au", "net.au", "org.au", "co.nz",
    "com.br", "co.jp", "com.mx", "com.tr", "com.cn", "co.za",
}

BLOCKED_REASONS = {
    "FilteredBlackList", "FilteredParental", "FilteredSafeBrowsing",
    "FilteredBlockedService", "FilteredInvalid",
}

TIME_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:?\d{2})?"
)


def log(msg):
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {msg}"
    print(line)
    try:
        with open(LOG_PATH, "a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def load_config():
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as fh:
            cfg.update(json.load(fh))
    return cfg


def parse_time(raw):
    """AdGuard geeft RFC3339 met nanoseconden; Python 3.9 kan dat niet
    native aan, dus zelf parsen."""
    m = TIME_RE.match(raw or "")
    if not m:
        return None
    base = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S")
    frac = m.group(2) or ""
    micro = int((frac + "000000")[:6]) if frac else 0
    tz_raw = m.group(3)
    if not tz_raw or tz_raw == "Z":
        tzinfo = timezone.utc
    else:
        tz_c = tz_raw.replace(":", "")
        sign = 1 if tz_c[0] == "+" else -1
        tzinfo = timezone(
            sign * timedelta(hours=int(tz_c[1:3]), minutes=int(tz_c[3:5]))
        )
    return base.replace(microsecond=micro, tzinfo=tzinfo)


def api_get(cfg, path, params=None):
    url = cfg["base_url"].rstrip("/") + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url)
    if cfg["username"]:
        import base64
        cred = base64.b64encode(
            f"{cfg['username']}:{cfg['password']}".encode()
        ).decode()
        req.add_header("Authorization", f"Basic {cred}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def registrable_domain(name):
    labels = (name or "").lower().rstrip(".").split(".")
    if len(labels) <= 2:
        return ".".join(labels)
    last2 = ".".join(labels[-2:])
    if last2 in TWO_PART_TLDS and len(labels) >= 3:
        return ".".join(labels[-3:])
    return last2


def is_noise(domain, noise_list):
    for n in noise_list:
        if domain == n or domain.endswith("." + n):
            return True
    return False


def top_client_ips(cfg):
    stats = api_get(cfg, "/control/stats")
    ordered = []
    for item in stats.get("top_clients") or []:
        for ip, count in item.items():
            if ip in cfg["exclude_ips"]:
                continue
            ordered.append((ip, count))
    ordered.sort(key=lambda x: -x[1])
    return [ip for ip, _ in ordered[: cfg["max_clients"]]]


def fetch_client_entries(cfg, ip, cutoff):
    """Pagineert de querylog voor 1 client-IP terug tot de cutoff."""
    entries = []
    older_than = None
    for _page in range(40):
        params = {"search": ip, "limit": 500}
        if older_than:
            params["older_than"] = older_than
        try:
            resp = api_get(cfg, "/control/querylog", params)
        except (urllib.error.URLError, OSError) as exc:
            log(f"  querylog-fout voor {ip}: {exc}")
            break
        items = resp.get("data") or []
        if not items:
            break
        reached_cutoff = False
        for it in items:
            raw_t = it.get("time")
            dt = parse_time(raw_t)
            if dt is None:
                continue
            if dt < cutoff:
                reached_cutoff = True
                break
            client_val = it.get("client")
            if isinstance(client_val, dict):
                client_ip = client_val.get("ip") or client_val.get("name")
            else:
                client_ip = client_val
            if client_ip != ip:
                continue
            qname = (it.get("question") or {}).get("name") or ""
            if not qname:
                continue
            reason = it.get("reason") or ""
            cname = (it.get("client_info") or {}).get("name") or ""
            entries.append((dt, qname, reason in BLOCKED_REASONS, cname))
        older_than = items[-1].get("time")
        if reached_cutoff or len(entries) >= cfg["max_entries_per_client"]:
            break
    return entries


def aggregate(cfg, ip, entries, noise_list):
    dom_minutes = defaultdict(set)
    dom_count = defaultdict(int)
    blocked_count = defaultdict(int)
    client_minutes = set()
    client_name = ""
    total = 0
    for dt, qname, blocked, cname in entries:
        if cname and not client_name:
            client_name = cname
        full = (qname or "").lower().rstrip(".")
        if is_noise(full, noise_list):
            continue
        dom = registrable_domain(full)
        if is_noise(dom, noise_list):
            continue
        minute = dt.astimezone().strftime("%Y%m%d%H%M")
        if blocked:
            blocked_count[dom] += 1
            continue
        total += 1
        dom_minutes[dom].add(minute)
        dom_count[dom] += 1
        client_minutes.add(minute)
    top = sorted(
        dom_minutes,
        key=lambda d: (-len(dom_minutes[d]), -dom_count[d]),
    )[: cfg["top_n"]]
    name = cfg["client_names"].get(ip) or client_name or ip
    return {
        "ip": ip,
        "name": name,
        "active_minutes": len(client_minutes),
        "queries": total,
        "blocked_total": sum(blocked_count.values()),
        "top": [
            {
                "domain": d,
                "minutes": len(dom_minutes[d]),
                "queries": dom_count[d],
            }
            for d in top
        ],
        "top_blocked": sorted(
            blocked_count.items(), key=lambda x: -x[1]
        )[: cfg["top_blocked_n"]],
    }


def fmt_minutes(m):
    if m >= 60:
        return f"{m // 60}u {m % 60:02d}m"
    return f"{m}m"


def render_html(cfg, clients, generated):
    parts = []
    parts.append(
        """<!doctype html><html lang="nl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="1800">
<title>Websites per apparaat</title>
<style>
:root{color-scheme:dark}
body{margin:0;padding:10px 12px 20px;background:#111113;color:#e8e8ea;
 font:15px/1.45 -apple-system,'SF Pro Text',Roboto,sans-serif}
h1{font-size:19px;margin:2px 0 2px}
.sub{color:#9a9aa2;font-size:12.5px;margin:0 0 12px}
.client{background:#1c1c1f;border-radius:14px;padding:12px 14px;margin:0 0 12px}
.chead{display:flex;flex-wrap:wrap;align-items:baseline;gap:8px;margin-bottom:8px}
.cname{font-size:16px;font-weight:650}
.cip{color:#8a8a92;font-size:12px}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:2px 0 10px}
.chip{background:#2a2a2f;border-radius:999px;padding:2px 10px;font-size:12px;
 color:#cfcfd6}
.chip.b{background:#3a2226;color:#ff9b9b}
.row{display:grid;grid-template-columns:22px 1fr 92px;gap:8px;align-items:center;
 padding:3px 0;font-size:13.5px}
.rank{color:#7a7a84;font-size:12px;text-align:right}
.dom{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bar{position:relative;height:17px;background:#26262b;border-radius:5px;
 overflow:hidden;grid-column:2/4;margin:0 0 2px}
.fill{position:absolute;inset:0 auto 0 0;background:#3f7cff;border-radius:5px;
 opacity:.85}
.val{color:#9a9aa2;font-size:12px;text-align:right;white-space:nowrap}
.blk{color:#ff9b9b;font-size:12px;margin-top:8px}
.leeg{color:#9a9aa2;font-size:13px}
</style></head><body>
"""
    )
    parts.append("<h1>&#127760; Websites per apparaat</h1>")
    parts.append(
        f'<p class="sub">Afgelopen {cfg["hours"]} uur &middot; bijgewerkt '
        f"{generated} &middot; sortering: actieve minuten (DNS-verzoeken "
        f"via AdGuard, geen exacte schermtijd)</p>"
    )
    if not clients:
        parts.append(
            '<p class="leeg">Geen apparaten met genoeg verkeer gevonden in '
            "dit venster.</p>"
        )
    for c in clients:
        parts.append('<div class="client">')
        parts.append(
            f'<div class="chead"><span class="cname">{html.escape(c["name"])}'
            f'</span><span class="cip">{html.escape(c["ip"])}</span></div>'
        )
        chips = (
            f'<span class="chip">&#8776; {fmt_minutes(c["active_minutes"])} '
            f'actief</span><span class="chip">{c["queries"]} verzoeken</span>'
        )
        if c["blocked_total"]:
            chips += (
                f'<span class="chip b">&#128683; {c["blocked_total"]} '
                f"geblokkeerd</span>"
            )
        parts.append(f'<div class="chips">{chips}</div>')
        max_min = max((d["minutes"] for d in c["top"]), default=1)
        for i, d in enumerate(c["top"], 1):
            pct = max(3, round(100 * d["minutes"] / max_min))
            parts.append(
                f'<div class="row"><span class="rank">{i}</span>'
                f'<span class="dom">{html.escape(d["domain"])}</span>'
                f'<span class="val">{fmt_minutes(d["minutes"])} &middot; '
                f'{d["queries"]}x</span></div>'
                f'<div class="row" style="padding:0"><span></span>'
                f'<div class="bar"><div class="fill" style="width:{pct}%">'
                f"</div></div></div>"
            )
        if c["top_blocked"]:
            blocked_str = ", ".join(
                f"{html.escape(d)} ({n}x)" for d, n in c["top_blocked"]
            )
            parts.append(
                f'<div class="blk">&#128683; Vaakst geblokkeerd: '
                f"{blocked_str}</div>"
            )
        parts.append("</div>")
    parts.append("</body></html>")
    return "".join(parts)


def main():
    cfg = load_config()
    noise_list = NOISE_DOMAINS + list(cfg.get("extra_noise_domains") or [])
    try:
        ips = top_client_ips(cfg)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            log("FOUT: AdGuard geeft 401. Vul username/password in "
                "config.json in (AdGuard-gebruiker bij leave_front_door_open "
                "aan, anders je HA-login).")
        else:
            log(f"FOUT: AdGuard-API gaf HTTP {exc.code} op {cfg['base_url']}.")
        return 1
    except (urllib.error.URLError, OSError) as exc:
        log(f"FOUT: AdGuard-API onbereikbaar op {cfg['base_url']}: {exc}. "
            "Is de poort vrijgegeven in de add-on (Netwerk, 80/tcp)?")
        return 1
    log(f"Top clients uit AdGuard-stats: {len(ips)}")
    cutoff = datetime.now(timezone.utc) - timedelta(hours=cfg["hours"])
    clients = []
    for ip in ips:
        entries = fetch_client_entries(cfg, ip, cutoff)
        agg = aggregate(cfg, ip, entries, noise_list)
        if agg["queries"] + agg["blocked_total"] >= cfg["min_queries"]:
            clients.append(agg)
        log(f"  {ip}: {len(entries)} logregels, "
            f"{agg['queries']} bruikbaar, "
            f"{fmt_minutes(agg['active_minutes'])} actief")
    clients.sort(key=lambda c: (-c["active_minutes"], -c["queries"]))
    generated = datetime.now().strftime("%d-%m %H:%M")
    page = render_html(cfg, clients, generated)
    out_dir = Path(cfg["output_dir"]).expanduser()
    if not out_dir.is_dir():
        log(f"FOUT: {out_dir} bestaat niet. Is de Samba-mount actief? "
            "Mount met: mount_smbfs //USER:PASS@homeassistant.local/config "
            "~/mnt/ha-config")
        return 1
    out_path = out_dir / cfg["output_name"]
    out_path.write_text(page, encoding="utf-8")
    json_path = out_dir / (Path(cfg["output_name"]).stem + ".json")
    json_path.write_text(
        json.dumps({"generated": generated, "clients": clients},
                   ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    log(f"OK: {out_path.name} geschreven ({len(clients)} apparaten)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

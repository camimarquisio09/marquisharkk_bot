"""
Discogs Alert Checker
Corre cada 10 minutos (via Railway scheduler).
Lee las alertas desde alerts.json y manda mensajes por Telegram.
"""

import os, json, time, requests
from datetime import datetime

# ── Configuración (variables de entorno) ─────────────────
DISCOGS_TOKEN = os.environ["DISCOGS_TOKEN"]
TG_BOT_TOKEN  = os.environ["TG_BOT_TOKEN"]
TG_CHAT_ID    = os.environ["TG_CHAT_ID"]
ALERTS_FILE   = os.environ.get("ALERTS_FILE", "alerts.json")
CURRENCY      = os.environ.get("CURRENCY", "USD")

DISCOGS_HEADERS = {
    "Authorization": f"Discogs token={DISCOGS_TOKEN}",
    "User-Agent": "DiscogsAlertBot/1.0",
}

# ── Telegram ─────────────────────────────────────────────
def send_telegram(msg: str, chat_id: str = None):
    target = chat_id or TG_CHAT_ID
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    requests.post(url, json={
        "chat_id": target,
        "text": msg,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    })

def notify_all(msg: str, alert: dict):
    """Manda el mensaje a todos los chat IDs de la alerta."""
    chat_ids = alert.get("chatIds") or [alert.get("chatId", TG_CHAT_ID)]
    if isinstance(chat_ids, str):
        chat_ids = [chat_ids]
    for cid in chat_ids:
        send_telegram(msg, cid)

# ── Discogs API ───────────────────────────────────────────
def get_release_info(release_id: int) -> dict | None:
    url = f"https://api.discogs.com/releases/{release_id}"
    r = requests.get(url, headers=DISCOGS_HEADERS)
    if r.status_code != 200:
        return None
    return r.json()

def get_release_lowest_price(release_id: int) -> tuple:
    url = f"https://api.discogs.com/releases/{release_id}"
    r = requests.get(url, headers=DISCOGS_HEADERS)
    if r.status_code != 200:
        return 0, None
    data = r.json()
    return data.get("num_for_sale", 0), data.get("lowest_price")

# Jerarquía de condiciones (de mejor a peor)
CONDITION_RANK = {'M': 0, 'NM': 1, 'VG+': 2, 'VG': 3, 'G+': 4, 'G': 5, 'F': 6, 'P': 7}

def get_listings_by_condition(release_id: int, min_condition: str, max_price: float) -> list:
    url = f"https://api.discogs.com/marketplace/listings?release_id={release_id}&status=For+Sale&sort=price&sort_order=asc&per_page=50"
    r = requests.get(url, headers=DISCOGS_HEADERS)
    if r.status_code != 200:
        return []
    listings = r.json().get("listings", [])
    min_rank = CONDITION_RANK.get(min_condition, 99)
    result = []
    for l in listings:
        cond  = l.get("condition", "")
        rank  = CONDITION_RANK.get(cond, 99)
        price = l.get("price", {}).get("value", 9999)
        if rank <= min_rank and price <= max_price:
            result.append(l)
    return result

# ── Procesamiento de alertas ──────────────────────────────
def check_alerts(alerts: list) -> list:
    updated = []

    for alert in alerts:
        release_id = alert["releaseId"]
        title      = alert["title"]
        year       = alert.get("year", "")
        label_year = f" ({year})" if year else ""

        print(f"[{datetime.now():%H:%M}] Chequeando: {title}{label_year}")
        time.sleep(1.5)

        # ── Tipo 1: avisa cuando aparece alguna copia ───────
        if alert["type"] == "new_copy":
            info = get_release_info(release_id)
            if info is None:
                updated.append(alert)
                continue

            copies = info.get("num_for_sale", 0)
            if copies > 0:
                lowest = info.get("lowest_price")
                price_txt = f"\n💰 Precio mínimo: {CURRENCY} {lowest:.2f}" if lowest else ""
                url = f"https://www.discogs.com/sell/release/{release_id}"
                msg = (
                    f"🟢 <b>¡Apareció una copia!</b>\n"
                    f"🎵 <b>{title}</b>{label_year}\n"
                    f"📦 {copies} copia{'s' if copies > 1 else ''} disponible{'s' if copies > 1 else ''}"
                    f"{price_txt}\n"
                    f"🔗 <a href='{url}'>Ver en Discogs</a>"
                )
                notify_all(msg, alert)
                print(f"  ✅ Notificación enviada ({copies} copias)")
                continue
            else:
                print(f"  ⏳ Sin copias todavía")
                updated.append(alert)

        # ── Tipo 2: avisa si el precio baja del límite ──────
        elif alert["type"] == "price_drop":
            max_price = alert.get("maxPrice", 9999)
            currency  = alert.get("currency", CURRENCY)
            num_for_sale, lowest_price = get_release_lowest_price(release_id)

            if lowest_price is not None and lowest_price <= max_price:
                url = f"https://www.discogs.com/sell/release/{release_id}"
                msg = (
                    f"💸 <b>¡Precio bajo encontrado!</b>\n"
                    f"🎵 <b>{title}</b>{label_year}\n"
                    f"💰 Precio mínimo: {currency} {lowest_price:.2f} (tu límite: {currency} {max_price})\n"
                    f"📦 {num_for_sale} copia{'s' if num_for_sale != 1 else ''} disponible{'s' if num_for_sale != 1 else ''}\n"
                    f"🔗 <a href='{url}'>Ver en Discogs</a>"
                )
                notify_all(msg, alert)
                print(f"  ✅ Precio bajo notificado: {currency} {lowest_price:.2f}")
            else:
                print(f"  ⏳ Precio mínimo actual: {currency} {lowest_price} — sobre el límite de {currency} {max_price}")
            updated.append(alert)

        # ── Tipo 3: combinada (precio + condición) ──────────
        elif alert["type"] == "combined":
            max_price = alert.get("maxPrice", 9999)
            min_cond  = alert.get("minCondition", "VG+")
            currency  = alert.get("currency", CURRENCY)
            listings  = get_listings_by_condition(release_id, min_cond, max_price)

            if listings:
                best   = listings[0]
                price  = best["price"]["value"]
                cond   = best.get("condition", "")
                seller = best["seller"]["username"]
                url    = f"https://www.discogs.com/sell/item/{best['id']}"
                msg = (
                    f"🎯 <b>¡Oferta encontrada!</b>\n"
                    f"🎵 <b>{title}</b>{label_year}\n"
                    f"💰 {currency} {price:.2f} (tu límite: {currency} {max_price})\n"
                    f"📀 Condición: {cond} (tu mínimo: {min_cond})\n"
                    f"👤 Vendedor: {seller}\n"
                    f"🔗 <a href='{url}'>Ver oferta</a>"
                )
                notify_all(msg, alert)
                print(f"  ✅ Oferta combinada notificada: {currency} {price:.2f} — {cond}")
            else:
                print(f"  ⏳ Sin copias {min_cond}+ bajo {currency} {max_price}")
            updated.append(alert)

    return updated

# ── Main ──────────────────────────────────────────────────
def main():
    print(f"=== Discogs Alert Checker — {datetime.now():%Y-%m-%d %H:%M} ===")

    if not os.path.exists(ALERTS_FILE):
        print("No se encontró alerts.json. Nada que chequear.")
        return

    with open(ALERTS_FILE) as f:
        alerts = json.load(f)

    if not alerts:
        print("Lista de alertas vacía.")
        return

    print(f"Chequeando {len(alerts)} alerta(s)...\n")
    updated = check_alerts(alerts)

    with open(ALERTS_FILE, "w") as f:
        json.dump(updated, f, indent=2, ensure_ascii=False)

    removed = len(alerts) - len(updated)
    print(f"\n✓ Listo. {removed} alerta(s) cumplida(s) y eliminada(s).")

if __name__ == "__main__":
    main()

"""
Discogs Alert Checker
Corre cada 1 hora (via cron o Railway scheduler).
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
def send_telegram(msg: str):
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    requests.post(url, json={
        "chat_id": TG_CHAT_ID,
        "text": msg,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    })

# ── Discogs API ───────────────────────────────────────────
def get_release_info(release_id: int) -> dict | None:
    """Obtiene info básica + cantidad de copias en venta."""
    url = f"https://api.discogs.com/releases/{release_id}"
    r = requests.get(url, headers=DISCOGS_HEADERS)
    if r.status_code != 200:
        return None
    return r.json()

def get_release_lowest_price(release_id: int) -> tuple:
    """Devuelve (num_for_sale, lowest_price) usando el endpoint de releases."""
    url = f"https://api.discogs.com/releases/{release_id}"
    r = requests.get(url, headers=DISCOGS_HEADERS)
    if r.status_code != 200:
        return 0, None
    data = r.json()
    return data.get("num_for_sale", 0), data.get("lowest_price")

# ── Procesamiento de alertas ──────────────────────────────
def check_alerts(alerts: list) -> list:
    """
    Recorre las alertas, consulta Discogs y dispara notificaciones.
    Devuelve la lista actualizada (sin alertas ya cumplidas de tipo new_copy).
    """
    updated = []

    for alert in alerts:
        release_id = alert["releaseId"]
        title      = alert["title"]
        year       = alert.get("year", "")
        label_year = f" ({year})" if year else ""

        print(f"[{datetime.now():%H:%M}] Chequeando: {title}{label_year}")
        time.sleep(1.5)  # respetar rate limit de Discogs

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
                send_telegram(msg)
                print(f"  ✅ Notificación enviada ({copies} copias)")
                # Eliminar la alerta: ya se cumplió
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
                send_telegram(msg)
                print(f"  ✅ Precio bajo notificado: {currency} {lowest_price:.2f}")
            else:
                print(f"  ⏳ Precio mínimo actual: {currency} {lowest_price} — sobre el límite de {currency} {max_price}")
            updated.append(alert)  # precio: la alerta se mantiene activa

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

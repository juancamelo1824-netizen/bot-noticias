"""Bot de noticias: lee fuentes RSS y envía las novedades a Telegram."""

import html
import json
import os
import sys
import time
from pathlib import Path

import feedparser
import requests

# Fuentes de noticias (nombre -> URL del RSS). Agrega o quita las que quieras.
FUENTES = {
    "BBC Mundo": "https://feeds.bbci.co.uk/mundo/rss.xml",
    "El País": "https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/portada",
    "DW Español": "https://rss.dw.com/xml/rss-sp-all",
    "France 24": "https://www.france24.com/es/rss",
    "Infobae": "https://www.infobae.com/arc/outboundfeeds/rss/",
    "Europa Press": "https://www.europapress.es/rss/rss.aspx",
}

MAX_POR_FUENTE = 3  # noticias nuevas máximas por fuente en cada envío
ARCHIVO_VISTOS = Path(__file__).parent / "vistos.json"
LIMITE_VISTOS = 2000  # cuántos enlaces recordar para no repetir

TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
# WhatsApp vía CallMeBot (opcional: si faltan, solo se envía a Telegram)
WA_PHONE = os.environ.get("WHATSAPP_PHONE")
WA_APIKEY = os.environ.get("WHATSAPP_APIKEY")
PRUEBA = "--prueba" in sys.argv  # imprime en pantalla en vez de enviar


def cargar_vistos():
    if ARCHIVO_VISTOS.exists():
        return json.loads(ARCHIVO_VISTOS.read_text(encoding="utf-8"))
    return []


def guardar_vistos(vistos):
    ARCHIVO_VISTOS.write_text(
        json.dumps(vistos[-LIMITE_VISTOS:], ensure_ascii=False, indent=0),
        encoding="utf-8",
    )


def obtener_nuevas(vistos):
    """Devuelve {fuente: [(titulo, enlace), ...]} con lo que no se ha enviado."""
    vistos_set = set(vistos)
    nuevas = {}
    for nombre, url in FUENTES.items():
        try:
            feed = feedparser.parse(url, agent="Mozilla/5.0 (bot-noticias)")
        except Exception as e:
            print(f"Error leyendo {nombre}: {e}")
            continue
        if not feed.entries:
            print(f"Sin noticias en {nombre} (¿URL caída?)")
        items = []
        for entrada in feed.entries:
            enlace = entrada.get("link")
            titulo = entrada.get("title", "").strip()
            if not enlace or not titulo or enlace in vistos_set:
                continue
            vistos.append(enlace)
            vistos_set.add(enlace)
            if len(items) < MAX_POR_FUENTE:
                items.append((titulo, enlace))
        if items:
            nuevas[nombre] = items
    return nuevas


def armar_mensajes(nuevas):
    """Arma mensajes HTML para Telegram, respetando el límite de 4096 caracteres."""
    mensajes, actual = [], "📰 <b>Noticias</b>\n"
    for fuente, items in nuevas.items():
        bloque = f"\n<b>{html.escape(fuente)}</b>\n"
        for titulo, enlace in items:
            bloque += f'• <a href="{html.escape(enlace)}">{html.escape(titulo)}</a>\n'
        if len(actual) + len(bloque) > 4000:
            mensajes.append(actual)
            actual = ""
        actual += bloque
    mensajes.append(actual)
    return mensajes


def armar_mensajes_whatsapp(nuevas):
    """Arma mensajes en formato WhatsApp (*negrita*), cortos para no fallar."""
    mensajes, actual = [], "📰 *Noticias*\n"
    for fuente, items in nuevas.items():
        bloque = f"\n*{fuente}*\n"
        for titulo, enlace in items:
            bloque += f"• {titulo}\n{enlace}\n"
        if len(actual) + len(bloque) > 1500:
            mensajes.append(actual)
            actual = ""
        actual += bloque
    mensajes.append(actual)
    return mensajes


def enviar_whatsapp(texto):
    r = requests.get(
        "https://api.callmebot.com/whatsapp.php",
        params={"phone": WA_PHONE, "text": texto, "apikey": WA_APIKEY},
        timeout=60,
    )
    r.raise_for_status()


def enviar(texto):
    r = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json={
            "chat_id": CHAT_ID,
            "text": texto,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=30,
    )
    r.raise_for_status()


def main():
    if not PRUEBA and not (TOKEN and CHAT_ID):
        sys.exit("Faltan TELEGRAM_TOKEN y TELEGRAM_CHAT_ID")

    vistos = cargar_vistos()
    nuevas = obtener_nuevas(vistos)

    if not nuevas:
        print("No hay noticias nuevas.")
    else:
        for mensaje in armar_mensajes(nuevas):
            if PRUEBA:
                print(mensaje)
            else:
                enviar(mensaje)
                time.sleep(1)
        if WA_PHONE and WA_APIKEY:
            # Si WhatsApp falla, no se pierde el envío de Telegram
            try:
                for mensaje in armar_mensajes_whatsapp(nuevas):
                    if PRUEBA:
                        print(mensaje)
                    else:
                        enviar_whatsapp(mensaje)
                        time.sleep(5)
            except Exception as e:
                print(f"Error enviando a WhatsApp: {e}")
        print(f"Enviadas noticias de {len(nuevas)} fuentes.")

    if not PRUEBA:
        guardar_vistos(vistos)


if __name__ == "__main__":
    main()

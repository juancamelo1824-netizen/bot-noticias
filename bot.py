"""Bot de noticias: lee fuentes RSS y envía las novedades a Telegram y WhatsApp."""

import html
import json
import os
import re
import sys
import time
from pathlib import Path

import feedparser
import requests

# Secciones -> fuentes. Cada fuente: (nombre, URL del RSS, máximo por envío).
# Google News ordena por relevancia (cuántos medios cubren la noticia),
# así que sus primeras noticias son las más importantes del momento.
# Para Colombia se usa la edición colombiana; para lo internacional, la de
# España, porque la colombiana mezcla noticias locales en "Mundo".
GN_CO = "hl=es-419&gl=CO&ceid=CO:es-419"
GN_MUNDO = "hl=es&gl=ES&ceid=ES:es"
SECCIONES = {
    "🇨🇴 Colombia": [
        ("Google News", f"https://news.google.com/rss/headlines/section/topic/NATION?{GN_CO}", 5),
        ("El Tiempo", "https://www.eltiempo.com/rss/colombia.xml", 2),
    ],
    "🌎 Internacional": [
        ("Google News", f"https://news.google.com/rss/headlines/section/topic/WORLD?{GN_MUNDO}", 5),
        ("BBC Mundo", "https://feeds.bbci.co.uk/mundo/rss.xml", 2),
        ("France 24", "https://www.france24.com/es/am%C3%A9rica-latina/rss", 1),
    ],
}

# La edición de España a veces cuela noticias locales de allá en "Mundo"
EXCLUIR = {"🌎 Internacional": re.compile(r"\b(España|Madrid|Sánchez|PSOE|Feijóo)\b")}

SIMILITUD_DUPLICADO = 0.5  # títulos que comparten este % de palabras = misma noticia
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


def palabras(titulo):
    """Palabras significativas de un título, para detectar noticias repetidas."""
    titulo = titulo.rsplit(" - ", 1)[0]  # quita el " - Medio" de Google News
    return {p for p in re.findall(r"\w+", titulo.lower()) if len(p) > 3}


def es_repetida(titulo, elegidas):
    p = palabras(titulo)
    for otra in elegidas:
        q = palabras(otra)
        if p and q and len(p & q) / min(len(p), len(q)) >= SIMILITUD_DUPLICADO:
            return True
    return False


def obtener_nuevas(vistos):
    """Devuelve {seccion: [(titulo, enlace), ...]} con lo que no se ha enviado."""
    vistos_set = set(vistos)
    nuevas = {}
    elegidas = []  # títulos de esta ronda, para no repetir la misma noticia
    for seccion, fuentes in SECCIONES.items():
        items = []
        for nombre, url, maximo in fuentes:
            try:
                feed = feedparser.parse(url, agent="Mozilla/5.0 (bot-noticias)")
            except Exception as e:
                print(f"Error leyendo {nombre}: {e}")
                continue
            if not feed.entries:
                print(f"Sin noticias en {nombre} (¿URL caída?)")
            cuenta = 0
            for entrada in feed.entries:
                enlace = entrada.get("link")
                titulo = entrada.get("title", "").strip()
                if not enlace or not titulo or enlace in vistos_set:
                    continue
                vistos.append(enlace)
                vistos_set.add(enlace)
                if cuenta >= maximo or es_repetida(titulo, elegidas):
                    continue
                if seccion in EXCLUIR and EXCLUIR[seccion].search(titulo):
                    continue
                if nombre != "Google News":
                    titulo = f"{titulo} - {nombre}"
                items.append((titulo, enlace))
                elegidas.append(titulo)
                cuenta += 1
        if items:
            nuevas[seccion] = items
    return nuevas


def armar_mensajes(nuevas):
    """Arma mensajes HTML para Telegram, respetando el límite de 4096 caracteres."""
    mensajes, actual = [], "📰 <b>Noticias</b>\n"
    for seccion, items in nuevas.items():
        bloque = f"\n<b>{html.escape(seccion)}</b>\n"
        for titulo, enlace in items:
            bloque += f'• <a href="{html.escape(enlace)}">{html.escape(titulo)}</a>\n'
        if len(actual) + len(bloque) > 4000:
            mensajes.append(actual)
            actual = ""
        actual += bloque
    mensajes.append(actual)
    return mensajes


def acortar(enlace):
    """Acorta enlaces largos con TinyURL; si falla, deja el original."""
    if len(enlace) <= 60:
        return enlace
    try:
        r = requests.get(
            "https://tinyurl.com/api-create.php", params={"url": enlace}, timeout=20
        )
        corto = r.text.strip()
        if r.ok and corto.startswith("https://"):
            return corto
    except Exception as e:
        print(f"No se pudo acortar {enlace[:60]}...: {e}")
    return enlace


def armar_mensajes_whatsapp(nuevas):
    """Arma mensajes en formato WhatsApp (*negrita*), cortos para no fallar."""
    mensajes, actual = [], "📰 *Noticias*\n"
    for seccion, items in nuevas.items():
        bloque = f"\n*{seccion}*\n"
        for titulo, enlace in items:
            bloque += f"• {titulo}\n{acortar(enlace)}\n"
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
        print(f"Enviadas noticias de {len(nuevas)} secciones.")

    if not PRUEBA:
        guardar_vistos(vistos)


if __name__ == "__main__":
    main()

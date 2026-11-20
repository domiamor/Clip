import os
import re
import shutil
import subprocess
import threading
import time
from urllib.parse import urlparse

import requests
from flask import Flask, Response, jsonify, render_template, request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CLIPS_DIR = os.path.join(BASE_DIR, "static", "clips")
RAW_VIDEO = os.path.join(BASE_DIR, "temp_live.mp4")
os.makedirs(CLIPS_DIR, exist_ok=True)

# Credenciales: se guardan en el servidor (variables de entorno), no en el navegador.
TIKTOK_TOKEN = os.environ.get("TIKTOK_TOKEN", "")
TIKTOK_PRIVACY = os.environ.get("TIKTOK_PRIVACY", "SELF_ONLY")  # PUBLIC_TO_EVERYONE solo con app aprobada
IG_TOKEN = os.environ.get("IG_TOKEN", "")
IG_USER_ID = os.environ.get("IG_USER_ID", "")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
COOKIES_FILE = os.environ.get("COOKIES_FILE", "")

app = Flask(__name__)
lock = threading.Lock()
state = {"activo": False, "estado": "Inactivo", "progreso": 0, "clips": []}

YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}


# ---------- Seguridad ----------
@app.before_request
def proteger():
    if not APP_PASSWORD:
        return None
    # Instagram necesita descargar el clip sin contraseña.
    if request.path.startswith("/static/clips/"):
        return None
    auth = request.authorization
    if auth and auth.password == APP_PASSWORD:
        return None
    return Response("Acceso restringido", 401, {"WWW-Authenticate": 'Basic realm="AutoClipper"'})


def url_valida(url):
    try:
        p = urlparse(url)
    except ValueError:
        return False
    return p.scheme in ("http", "https") and (p.hostname or "").lower() in YT_HOSTS


def set_estado(texto=None, progreso=None):
    with lock:
        if texto is not None:
            state["estado"] = texto
        if progreso is not None:
            state["progreso"] = progreso


# ---------- Publicación ----------
def publicar_tiktok(ruta, titulo):
    try:
        size = os.path.getsize(ruta)
        r = requests.post(
            "https://open.tiktokapis.com/v2/post/publish/video/init/",
            headers={
                "Authorization": f"Bearer {TIKTOK_TOKEN}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            json={
                "post_info": {"title": titulo[:150], "privacy_level": TIKTOK_PRIVACY},
                "source_info": {
                    "source": "FILE_UPLOAD",
                    "video_size": size,
                    "chunk_size": size,
                    "total_chunk_count": 1,
                },
            },
            timeout=30,
        ).json()
        upload_url = (r.get("data") or {}).get("upload_url")
        if not upload_url:
            msg = (r.get("error") or {}).get("message", "Token inválido o sin permisos")
            return f"TikTok: error. {msg}"
        with open(ruta, "rb") as f:
            put = requests.put(
                upload_url,
                data=f,
                headers={
                    "Content-Type": "video/mp4",
                    "Content-Length": str(size),
                    "Content-Range": f"bytes 0-{size - 1}/{size}",
                },
                timeout=300,
            )
        if put.status_code in (200, 201, 206):
            return f"TikTok: enviado ({TIKTOK_PRIVACY})"
        return f"TikTok: la subida falló (HTTP {put.status_code})"
    except Exception as e:
        return f"TikTok: falló. {e}"


def publicar_instagram(nombre_archivo, titulo):
    if not PUBLIC_URL:
        return "Instagram: falta la variable PUBLIC_URL"
    try:
        base = f"https://graph.facebook.com/v19.0/{IG_USER_ID}"
        r = requests.post(
            f"{base}/media",
            data={
                "media_type": "REELS",
                "video_url": f"{PUBLIC_URL}/static/clips/{nombre_archivo}",
                "caption": titulo,
                "access_token": IG_TOKEN,
            },
            timeout=30,
        ).json()
        cid = r.get("id")
        if not cid:
            return f"Instagram: error. {(r.get('error') or {}).get('message', 'Error de API')}"

        # Esperar a que Instagram termine de procesar el video.
        for _ in range(30):
            s = requests.get(
                f"https://graph.facebook.com/v19.0/{cid}",
                params={"fields": "status_code", "access_token": IG_TOKEN},
                timeout=30,
            ).json()
            code = s.get("status_code")
            if code == "FINISHED":
                break
            if code in ("ERROR", "EXPIRED"):
                return f"Instagram: el procesamiento falló ({code})"
            time.sleep(5)
        else:
            return "Instagram: tiempo de espera agotado"

        p = requests.post(
            f"{base}/media_publish",
            data={"creation_id": cid, "access_token": IG_TOKEN},
            timeout=30,
        ).json()
        if p.get("id"):
            return "Instagram: publicado"
        return f"Instagram: error. {(p.get('error') or {}).get('message', 'No se pudo publicar')}"
    except Exception as e:
        return f"Instagram: falló. {e}"


# ---------- Proceso principal ----------
def procesar(url, cantidad, duracion, titulo, usar_tiktok, usar_ig):
    try:
        set_estado("Capturando el directo…", 10)
        for f in os.listdir(CLIPS_DIR):
            os.remove(os.path.join(CLIPS_DIR, f))
        if os.path.exists(RAW_VIDEO):
            os.remove(RAW_VIDEO)

        total = cantidad * duracion + 15
        cmd = [
            "yt-dlp", "--no-playlist", "--force-overwrites",
            "-f", "best[height<=1080]/best",
            "--remux-video", "mp4",
            "--downloader", "ffmpeg",
            "--downloader-args", f"ffmpeg_i:-t {total}",
            "-o", RAW_VIDEO,
        ]
        if COOKIES_FILE and os.path.exists(COOKIES_FILE):
            cmd += ["--cookies", COOKIES_FILE]
        cmd += ["--", url]
        subprocess.run(cmd, check=True, capture_output=True, text=True)

        for i in range(cantidad):
            n = i + 1
            nombre = f"clip_{n}.mp4"
            salida = os.path.join(CLIPS_DIR, nombre)
            set_estado(f"Recortando clip {n} de {cantidad} en 9:16…", 30 + int(i / cantidad * 60))
            subprocess.run([
                "ffmpeg", "-y", "-ss", str(i * duracion), "-i", RAW_VIDEO,
                "-t", str(duracion),
                "-vf", "crop=ih*9/16:ih,scale=1080:1920",
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
                salida,
            ], check=True, capture_output=True, text=True)

            clip = {"archivo": f"/static/clips/{nombre}", "redes": []}
            with lock:
                state["clips"].append(clip)

            texto = f"{titulo} #Parte{n} #fyp"
            if usar_tiktok:
                set_estado(f"Clip {n}: publicando en TikTok…")
                clip["redes"].append(publicar_tiktok(salida, texto))
            if usar_ig:
                set_estado(f"Clip {n}: publicando en Instagram…")
                clip["redes"].append(publicar_instagram(nombre, texto))
            set_estado(progreso=30 + int(n / cantidad * 65))

        set_estado("Listo. Ya puedes descargar tus clips.", 100)
    except subprocess.CalledProcessError as e:
        detalle = (e.stderr or "").strip().splitlines()
        set_estado(f"Error: {detalle[-1] if detalle else e}", 100)
    except Exception as e:
        set_estado(f"Error: {e}", 100)
    finally:
        if os.path.exists(RAW_VIDEO):
            os.remove(RAW_VIDEO)
        with lock:
            state["activo"] = False


# ---------- Rutas ----------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/config")
def config():
    return jsonify({
        "tiktok": bool(TIKTOK_TOKEN),
        "instagram": bool(IG_TOKEN and IG_USER_ID and PUBLIC_URL),
    })


@app.route("/iniciar", methods=["POST"])
def iniciar():
    d = request.get_json(silent=True) or {}
    url = (d.get("youtube_url") or "").strip()
    if not url_valida(url):
        return jsonify({"ok": False, "error": "Pega un enlace válido de YouTube."}), 400
    try:
        cantidad = max(1, min(10, int(d.get("cantidad", 3))))
        duracion = max(10, min(90, int(d.get("duracion", 30))))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Cantidad o duración no válidas."}), 400
    titulo = re.sub(r"\s+", " ", (d.get("titulo") or "Momento épico del directo")).strip()[:120]
    usar_tiktok = bool(d.get("tiktok")) and bool(TIKTOK_TOKEN)
    usar_ig = bool(d.get("instagram")) and bool(IG_TOKEN and IG_USER_ID)

    with lock:
        if state["activo"]:
            return jsonify({"ok": False, "error": "Ya hay un proceso en marcha."}), 409
        state.update(activo=True, estado="Iniciando…", progreso=5, clips=[])

    threading.Thread(
        target=procesar,
        args=(url, cantidad, duracion, titulo, usar_tiktok, usar_ig),
        daemon=True,
    ).start()
    return jsonify({"ok": True})


@app.route("/estado")
def estado():
    with lock:
        return jsonify({**state, "clips": [dict(c, redes=list(c["redes"])) for c in state["clips"]]})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

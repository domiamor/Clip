# AutoClipper Live

Convierte un directo de YouTube en clips verticales 9:16 y, opcionalmente, los publica en TikTok e Instagram Reels.

## Desplegar en Render
1. Crea un repositorio en GitHub y sube el contenido de esta carpeta.
2. En render.com: New > Web Service > conecta el repositorio. Render detecta el Dockerfile.
3. En Environment agrega estas variables:
   - `APP_PASSWORD`: contraseña para entrar a la página (usuario: cualquiera).
   - `PUBLIC_URL`: la URL de tu servicio, por ejemplo `https://autoclipper.onrender.com` (sin barra final). Pon un valor provisional y corrígelo cuando Render te dé la URL real.
   - Opcional: `TIKTOK_TOKEN`, `IG_TOKEN`, `IG_USER_ID`, `TIKTOK_PRIVACY`.
   - Opcional: `COOKIES_FILE` con la ruta de un `cookies.txt` si YouTube bloquea la descarga.
4. Despliega, abre la URL, escribe la contraseña y pega el enlace del directo.

## Probar en tu computadora
```
pip install -r requirements.txt     # y tener ffmpeg instalado
python app.py                       # abre http://localhost:5000
```

## Notas
- Los clips son los primeros N segundos del directo desde que lo capturas, recortados al centro.
- TikTok solo permite `SELF_ONLY` hasta que aprueben tu app.
- Usa solo contenido propio o con permiso.

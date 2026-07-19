#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Архиватор ссылок → Markdown. ЗАЩИЩЁННАЯ ВЕРСИЯ ДЛЯ ТЕЛЕФОНА
============================================================
Запускается на компьютере, управляется с телефона через браузер.

Защита (работает из коробки, у каждой установки — своя):
- код доступа генерируется автоматически при первом запуске и хранится
  ТОЛЬКО на вашем компьютере (файл web_secret.txt, в git не попадает);
- без кода не работает ни одна функция: ни очередь, ни журнал, ни файлы;
- защита от перебора кода: после 5 неверных попыток — пауза, каждая
  неверная попытка искусственно замедляется;
- сервер отдаёт файлы только из папки «Архив», ничего больше;
- безопасные заголовки браузера (nosniff, no-frame, no-store).

ВАЖНО про доступ не из дома: НИКОГДА не пробрасывайте порт 8080 в
интернет через роутер. Для удалённого доступа используйте Tailscale
(бесплатно, шифрование WireGuard) — см. ИНСТРУКЦИЯ_ТЕЛЕФОН.md.

Запуск:  python archiver_web.py
Файл archiver.py должен лежать в той же папке.
"""

import hmac
import os
import secrets
import socket
import threading
import time

from flask import Flask, request, jsonify, send_file, Response

import archiver as core

APP_PORT = 8080
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_ROOT = os.path.join(BASE_DIR, "Архив")
SECRET_FILE = os.path.join(BASE_DIR, "web_secret.txt")


def get_token() -> str:
    """Код доступа этой установки. Создаётся один раз, локально."""
    if os.path.isfile(SECRET_FILE):
        tok = open(SECRET_FILE, encoding="utf-8").read().strip()
        if tok:
            return tok
    tok = secrets.token_urlsafe(12)
    with open(SECRET_FILE, "w", encoding="utf-8") as f:
        f.write(tok)
    return tok


TOKEN = get_token()
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024  # запросы не больше 1 МБ

_state = {"lines": [], "busy": False, "result": None}
_lock = threading.Lock()
_bruteforce = {"fails": 0, "until": 0.0}


def log(msg: str):
    with _lock:
        _state["lines"].append(str(msg))


def worker(url: str, model: str, insta: str, savevid: bool, lang: str):
    try:
        os.makedirs(OUT_ROOT, exist_ok=True)
        path = core.process_url(url, OUT_ROOT, log, model, insta,
                                savevid, lang)
        with _lock:
            _state["result"] = path
        log("ГОТОВО ✅ Файл сохранён на компьютере:")
        log(path)
    except Exception as e:  # noqa: BLE001
        log(f"ОШИБКА ❌ {e}")
    finally:
        with _lock:
            _state["busy"] = False


# ---------------------- защита ----------------------

def is_authed() -> bool:
    # код принимается ТОЛЬКО из защищённой куки (не из адресной строки)
    supplied = request.cookies.get("auth") or ""
    return hmac.compare_digest(supplied, TOKEN)


@app.before_request
def guard():
    if request.path == "/login" and request.method == "POST":
        return None  # обрабатывается в login(), с собственной защитой
    if is_authed():
        return None  # вошедших блокировка перебора не касается
    if time.time() < _bruteforce["until"]:
        return Response("Слишком много неверных попыток. "
                        "Подождите минуту.", status=429)
    if request.cookies.get("auth"):
        # неверная кука: замедляем и считаем как попытку подбора
        time.sleep(1.0)
        _bruteforce["fails"] += 1
        if _bruteforce["fails"] >= 5:
            _bruteforce["fails"] = 0
            _bruteforce["until"] = time.time() + 60
    if request.path == "/":
        return Response(LOGIN_PAGE, mimetype="text/html")
    return Response("Нет доступа", status=401)


@app.after_request
def harden(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    if request.path in ("/log", "/file", "/start"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


LOGIN_PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Архиватор — вход</title>
<style>
 body{font-family:-apple-system,Roboto,sans-serif;background:#f4f4f7;
      display:flex;min-height:100vh;align-items:center;justify-content:center}
 @media (prefers-color-scheme: dark){body{background:#111;color:#eee}
   input{background:#1c1c1e;color:#eee;border-color:#333}}
 .c{max-width:340px;padding:24px;text-align:center}
 input{width:100%;box-sizing:border-box;font-size:1.1em;padding:12px;
       border:1px solid #ccc;border-radius:10px;text-align:center}
 button{width:100%;padding:13px;font-size:1.05em;border:0;margin-top:12px;
       border-radius:12px;background:#d97757;color:#fff;font-weight:600}
 p{opacity:.7;font-size:.9em}
</style></head><body><div class="c">
<h2>🔒 Архиватор ссылок</h2>
<p>Введите код доступа. Он показан в чёрном окне на компьютере
и лежит в файле web_secret.txt рядом с программой.</p>
<form method="post" action="/login">
<input name="key" autocomplete="off" autofocus placeholder="код доступа">
<button>Войти</button></form>
</div></body></html>"""


PAGE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Архиватор ссылок</title>
<style>
  :root { color-scheme: light dark;
    --bg:#f4f4f7; --fg:#1c1c1e; --card:#fff; --border:#ccc; }
  :root[data-theme="dark"] {
    --bg:#111; --fg:#eee; --card:#1c1c1e; --border:#333; }
  @media (prefers-color-scheme: dark) {
    :root[data-theme="auto"] {
      --bg:#111; --fg:#eee; --card:#1c1c1e; --border:#333; } }
  body { font-family: -apple-system, Roboto, "Segoe UI", sans-serif;
         margin: 0; padding: 16px; max-width: 560px;
         margin-inline: auto; background: var(--bg); color: var(--fg); }
  input, select, .log { background: var(--card); color: var(--fg);
                        border-color: var(--border) !important; }
  .card { background: var(--card); }
  .theme { display:flex; gap:6px; justify-content:flex-end;
           margin:-6px 0 10px; }
  .theme button { width:auto; margin:0; padding:6px 12px; font-size:.8em;
           background:var(--card); color:var(--fg);
           border:1px solid var(--border); border-radius:8px;
           font-weight:400; }
  .theme button.on { background:#d97757; color:#fff; border-color:#d97757; }
  h1 { font-size: 1.3em; margin: 4px 0 14px; }
  .card { background: #fff; border-radius: 14px; padding: 14px;
          box-shadow: 0 1px 4px rgba(0,0,0,.08); margin-bottom: 14px; }
  label { display: block; font-size: .85em; opacity: .7; margin: 10px 0 4px; }
  input, select { width: 100%; box-sizing: border-box; font-size: 1em;
          padding: 12px; border: 1px solid #ccc; border-radius: 10px; }
  .row { display: flex; gap: 10px; align-items: center; margin-top: 10px; }
  button { width: 100%; padding: 14px; font-size: 1.05em; border: 0;
          border-radius: 12px; background: #d97757; color: #fff;
          font-weight: 600; margin-top: 14px; }
  button:disabled { opacity: .5; }
  .log { white-space: pre-wrap; font-family: ui-monospace, monospace;
         font-size: .82em; background: #fff; border: 1px solid #ddd;
         border-radius: 12px; padding: 10px; min-height: 120px;
         max-height: 45vh; overflow-y: auto; }
  #dl { text-align: center; text-decoration: none; display: none;
        background: #2e7d32; padding: 14px; border-radius: 12px;
        color: #fff; font-weight: 600; margin-top: 10px; }
</style>
</head>
<body>
<h1>📥 Архиватор ссылок <span style="opacity:.5;font-size:.6em">🔒 защищено</span></h1>
<div class="theme">
  <button id="t-auto" onclick="setTheme('auto')">Авто</button>
  <button id="t-light" onclick="setTheme('light')">Светлая</button>
  <button id="t-dark" onclick="setTheme('dark')">Тёмная</button>
</div>

<div class="card">
  <label>Ссылка (видео, Telegram, Instagram, статья)</label>
  <input id="url" type="url" placeholder="https://..." inputmode="url">

  <label>Качество расшифровки видео</label>
  <select id="model">
    <option value="tiny">tiny — быстро, грубо</option>
    <option value="base">base</option>
    <option value="small" selected>small — баланс (рекомендуется)</option>
    <option value="medium">medium — медленно, точно</option>
  </select>

  <label>Язык заметки</label>
  <input id="lang" type="text" placeholder="Как в оригинале">

  <label>Логин Instagram (необязательно)</label>
  <input id="insta" type="text" autocapitalize="none">

  <div class="row">
    <input id="savevid" type="checkbox" checked style="width:auto">
    <label style="margin:0">Сохранять сжатую копию видео</label>
  </div>

  <button id="go" onclick="start()">Сохранить</button>
  <a id="dl" href="/file">⬇ Скачать .md на телефон</a>
</div>

<div class="card">
  <label style="margin-top:0">Журнал</label>
  <div class="log" id="log">Готов к работе. Файлы сохраняются на компьютере
в папку «Архив».</div>
</div>

<script>
function applyTheme() {
  const t = localStorage.getItem('theme') || 'auto';
  document.documentElement.setAttribute('data-theme', t);
  for (const id of ['auto','light','dark']) {
    const b = document.getElementById('t-' + id);
    if (b) b.classList.toggle('on', id === t);
  }
}
function setTheme(t) { localStorage.setItem('theme', t); applyTheme(); }
applyTheme();

async function start() {
  const url = document.getElementById('url').value.trim();
  if (!url) { alert('Вставьте ссылку'); return; }
  document.getElementById('go').disabled = true;
  document.getElementById('dl').style.display = 'none';
  const r = await fetch('/start', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      url: url,
      model: document.getElementById('model').value,
      insta: document.getElementById('insta').value.trim(),
      lang: document.getElementById('lang').value.trim(),
      savevid: document.getElementById('savevid').checked
    })
  });
  const j = await r.json();
  if (!j.ok) { alert(j.msg || 'Ошибка'); document.getElementById('go').disabled = false; }
}
async function poll() {
  try {
    const r = await fetch('/log');
    if (!r.ok) return;
    const j = await r.json();
    const box = document.getElementById('log');
    box.textContent = j.lines.join('\\n') || '...';
    box.scrollTop = box.scrollHeight;
    document.getElementById('go').disabled = j.busy;
    document.getElementById('dl').style.display = j.done ? 'block' : 'none';
  } catch (e) {}
}
setInterval(poll, 2000);
</script>
</body>
</html>
"""


@app.route("/login", methods=["POST"])
def login():
    if time.time() < _bruteforce["until"]:
        return Response("Слишком много неверных попыток. "
                        "Подождите минуту.", status=429)
    key = (request.form.get("key") or "").strip()
    if hmac.compare_digest(key, TOKEN):
        _bruteforce["fails"] = 0
        resp = Response(status=302, headers={"Location": "/"})
        resp.set_cookie("auth", TOKEN, max_age=30 * 24 * 3600,
                        httponly=True, samesite="Strict")
        return resp
    time.sleep(1.0)
    _bruteforce["fails"] += 1
    if _bruteforce["fails"] >= 5:
        _bruteforce["fails"] = 0
        _bruteforce["until"] = time.time() + 60
    page = LOGIN_PAGE.replace(
        "<h2>🔒 Архиватор ссылок</h2>",
        "<h2>🔒 Архиватор ссылок</h2>"
        "<p style='color:#e05555'>Неверный код, попробуйте ещё раз.</p>")
    return Response(page, mimetype="text/html")


@app.route("/")
def index():
    return Response(PAGE, mimetype="text/html")


@app.route("/start", methods=["POST"])
def start():
    data = request.get_json(force=True, silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"ok": False, "msg": "Ссылка пустая"})
    with _lock:
        if _state["busy"]:
            return jsonify({"ok": False,
                            "msg": "Предыдущая ссылка ещё обрабатывается"})
        _state["busy"] = True
        _state["result"] = None
        _state["lines"] = []
    threading.Thread(
        target=worker,
        args=(url, data.get("model") or "small",
              (data.get("insta") or "").strip(),
              bool(data.get("savevid", True)),
              (data.get("lang") or "").strip()),
        daemon=True).start()
    return jsonify({"ok": True})


@app.route("/log")
def get_log():
    with _lock:
        return jsonify({"lines": list(_state["lines"]),
                        "busy": _state["busy"],
                        "done": bool(_state["result"])})


@app.route("/file")
def get_file():
    with _lock:
        p = _state["result"]
    root = os.path.abspath(OUT_ROOT)
    if not p or not os.path.isfile(p) or \
            not os.path.abspath(p).startswith(root + os.sep):
        return "Файл не найден", 404
    return send_file(p, as_attachment=True)


def tailscale_ip() -> str:
    """IP компьютера в сети Tailscale (100.x.x.x) или '' если его нет."""
    import subprocess
    try:
        out = subprocess.run(["tailscale", "ip", "-4"],
                             capture_output=True, text=True, timeout=5)
        if out.returncode == 0:
            ip = (out.stdout.strip().splitlines() or [""])[0].strip()
            if ip.startswith("100."):
                return ip
    except Exception:  # noqa: BLE001
        pass
    try:  # запасной способ: ищем адрес диапазона 100.64-127.x.x
        for info in socket.getaddrinfo(socket.gethostname(), None,
                                       socket.AF_INET):
            ip = info[4][0]
            parts = ip.split(".")
            if parts[0] == "100" and 64 <= int(parts[1]) <= 127:
                return ip
    except Exception:  # noqa: BLE001
        pass
    return ""


def all_ips() -> list:
    """Все IPv4-адреса компьютера; домашние (192.168/10.x) — первыми."""
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None,
                                       socket.AF_INET):
            ips.add(info[4][0])
    except Exception:  # noqa: BLE001
        pass
    ips.add(local_ip())
    ips.discard("127.0.0.1")

    def rank(ip):
        p = ip.split(".")
        home = (ip.startswith(("192.168.", "10."))
                or (p[0] == "172" and p[1].isdigit()
                    and 16 <= int(p[1]) <= 31))
        return 0 if home else 1
    return sorted(ips, key=rank)


def local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:  # noqa: BLE001
        return "127.0.0.1"
    finally:
        s.close()


if __name__ == "__main__":
    ts = tailscale_ip()
    print("=" * 62)
    if ts:
        print("  РЕЖИМ МАКСИМАЛЬНОЙ ЗАЩИТЫ (обнаружен Tailscale)")
        print("  Сервер слушает ТОЛЬКО зашифрованную сеть Tailscale:")
        print(f"  Адрес для телефона:  http://{ts}:{APP_PORT}")
        print("  (на телефоне должен быть установлен Tailscale и включён)")
        print("  В домашней Wi-Fi и в интернете сервер не виден никому.")
        host = ts
    else:
        print("  Сервер архиватора запущен (режим локальной сети)")
        addrs = all_ips() or [local_ip()]
        print("  Адрес для телефона (если несколько — пробуйте по порядку):")
        for a in addrs:
            print(f"     http://{a}:{APP_PORT}")
        print("  Телефон должен быть в той же Wi-Fi сети.")
        print("  Если на компьютере работает VPN: включите в его настройках")
        print("  «Разрешить локальную сеть» (LAN traffic / split tunneling).")
        print("  Совет: установите Tailscale (tailscale.com) — сервер")
        print("  перейдёт в режим максимальной защиты автоматически.")
        host = "0.0.0.0"
    print(f"  КОД ДОСТУПА:  {TOKEN}")
    print("  Введите код на телефоне один раз — дальше он запомнится.")
    print("  Никогда не пробрасывайте этот порт в интернет!")
    print("=" * 62)
    app.run(host=host, port=APP_PORT, debug=False)

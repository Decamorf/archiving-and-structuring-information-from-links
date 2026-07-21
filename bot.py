#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram-бот как удалённый пульт архиватора.
Работает ВНУТРИ приложения (отдельный поток), включается галочкой.

Принцип: вы шлёте боту ссылку -> компьютер обрабатывает -> логи
дублируются в чат -> готовый результат приходит одним .zip
(заметка .md + все фото/кадры рядом с ней).

Безопасность:
- отвечает ТОЛЬКО вашему Telegram ID (chat_id), любой другой игнорируется;
- токен бота и разрешённый ID хранятся локально в bot_config.json,
  который не попадает в git;
- никакого веб-порта, входящих соединений нет — бот сам ходит к Telegram.

Зависит только от requests (уже установлен) — без сторонних библиотек,
общается с Telegram Bot API напрямую (long polling).
"""

import os
import io
import json
import time
import zipfile
import threading
import traceback

API = "https://api.telegram.org/bot{token}/{method}"


class TelegramBot:
    def __init__(self, token, allowed_id, process_fn, data_dir, settings_fn):
        """
        token        — токен бота от @BotFather
        allowed_id   — ваш Telegram ID (int); только он получает ответы
        process_fn   — функция обработки: (url, out_root, log,
                       model, insta, savevid, lang) -> путь к .md
        data_dir     — куда складывать архив
        settings_fn  — функция без аргументов -> dict текущих настроек GUI
                       (model, insta, savevid, lang)
        """
        self.token = token.strip()
        self.allowed_id = int(allowed_id)
        self.process_fn = process_fn
        self.data_dir = data_dir
        self.settings_fn = settings_fn
        self._stop = threading.Event()
        self._offset = 0
        self.on_log = None          # колбэк для журнала приложения
        self._busy = False

    # ---------- низкоуровневое общение с Telegram ----------
    def _call(self, method, **params):
        import requests
        url = API.format(token=self.token, method=method)
        r = requests.get(url, params=params, timeout=70)
        r.raise_for_status()
        return r.json()

    def _send(self, text):
        try:
            self._call("sendMessage", chat_id=self.allowed_id,
                       text=text[:4000])
        except Exception:  # noqa: BLE001
            pass

    def _send_document(self, path, caption=""):
        import requests
        url = API.format(token=self.token, method="sendDocument")
        with open(path, "rb") as f:
            requests.post(url, data={"chat_id": self.allowed_id,
                                     "caption": caption[:1000]},
                          files={"document": f}, timeout=300)

    def applog(self, msg):
        if self.on_log:
            self.on_log(f"[бот] {msg}")

    # ---------- проверка токена ----------
    def check(self):
        j = self._call("getMe")
        if not j.get("ok"):
            raise RuntimeError("Токен отклонён Telegram")
        return j["result"].get("username", "")

    # ---------- сбор результата в zip ----------
    def _make_zip(self, md_path):
        folder = os.path.dirname(md_path)
        base = os.path.splitext(os.path.basename(md_path))[0]
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(md_path, os.path.basename(md_path))
            # прикладываем файлы, относящиеся к этой заметке (общий префикс)
            for fn in os.listdir(folder):
                if fn == os.path.basename(md_path):
                    continue
                if fn.startswith(base):
                    z.write(os.path.join(folder, fn), fn)
        buf.seek(0)
        return buf, base

    # ---------- обработка одной ссылки ----------
    def _handle_url(self, url):
        if self._busy:
            self._send("Занят предыдущей ссылкой, подождите…")
            return
        self._busy = True
        try:
            self._send(f"Принял: {url}\nОбрабатываю…")

            def log(msg):
                self.applog(msg)
                # ключевые этапы дублируем в чат (не спамим каждой строкой)
                low = str(msg).lower()
                if any(k in low for k in ("тема", "конспект", "скачив",
                                          "расшифров", "ошибк", "готово",
                                          "перевож")):
                    self._send(str(msg))

            s = self.settings_fn() or {}
            out_root = os.path.join(self.data_dir, "Архив")
            os.makedirs(out_root, exist_ok=True)
            md_path = self.process_fn(
                url, out_root, log,
                s.get("model", "small"), s.get("insta", ""),
                s.get("savevid", True), s.get("lang", ""))

            self._send("Собираю архив…")
            buf, base = self._make_zip(md_path)
            # сохраняем zip во временный файл для отправки
            zpath = os.path.join(self.data_dir, f"_send_{base}.zip")
            with open(zpath, "wb") as f:
                f.write(buf.getvalue())
            try:
                self._send_document(zpath, caption=f"Готово: {base}")
            finally:
                try:
                    os.remove(zpath)
                except OSError:
                    pass
            self._send("✅ Готово")
        except Exception as e:  # noqa: BLE001
            self._send(f"❌ Ошибка: {e}")
            self.applog(f"ошибка: {traceback.format_exc()}")
        finally:
            self._busy = False

    # ---------- основной цикл ----------
    def run(self):
        try:
            uname = self.check()
            self.applog(f"запущен как @{uname}, отвечаю только ID "
                        f"{self.allowed_id}")
            self._send("🟢 Архиватор на связи. Пришлите ссылку.")
        except Exception as e:  # noqa: BLE001
            self.applog(f"не удалось запустить: {e}")
            return

        while not self._stop.is_set():
            try:
                j = self._call("getUpdates", offset=self._offset,
                               timeout=50)
            except Exception:  # noqa: BLE001
                time.sleep(3)
                continue
            for upd in j.get("result", []):
                self._offset = upd["update_id"] + 1
                msg = upd.get("message") or upd.get("channel_post")
                if not msg:
                    continue
                chat_id = (msg.get("chat") or {}).get("id")
                text = (msg.get("text") or "").strip()
                # ЖЁСТКАЯ проверка: только ваш ID
                if chat_id != self.allowed_id:
                    continue
                if not text:
                    continue
                if text.lower() in ("/start", "/help"):
                    self._send("Пришлите ссылку (видео, статья, пост "
                               "Telegram/Instagram/X) — верну архивом .zip.")
                    continue
                if text.startswith("http"):
                    threading.Thread(target=self._handle_url,
                                     args=(text,), daemon=True).start()
                else:
                    self._send("Это не похоже на ссылку. Пришлите URL.")

    def stop(self):
        self._stop.set()

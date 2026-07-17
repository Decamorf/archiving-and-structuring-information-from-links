#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Архиватор ссылок → Markdown
============================
Вставьте ссылку (YouTube / другое видео, пост Telegram, статья) —
приложение сохранит содержимое в .md файл, разложенный по папкам:

    Архив/
    ├── Видео/<Название канала>/<Название видео>.md
    ├── Telegram/<Имя канала>/<Заголовок поста>.md  (+ фото рядом)
    └── Статьи/<Сайт>/<Заголовок статьи>.md

В каждом файле: заголовок, автор, дата, описание, полный текст
(для видео — расшифровка речи нейросетью Whisper) и список всех
найденных ссылок.

Запуск:  python archiver.py
"""

import os
import re
import sys
import glob
import queue
import shutil
import tempfile
import threading
import datetime
import urllib.parse

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, scrolledtext, messagebox
    HAS_GUI = True
except ImportError:      # окно недоступно (например, сервер без GUI) —
    HAS_GUI = False      # логика всё равно работает через archiver_web.py

# ----------------------------------------------------------------------
# Вспомогательные функции
# ----------------------------------------------------------------------

URL_RE = re.compile(r'https?://[^\s<>"\')\]\}]+')

def sanitize(name: str, maxlen: int = 100) -> str:
    """Превращает произвольный текст в безопасное имя файла/папки."""
    if not name:
        return "без_названия"
    name = re.sub(r'[\\/:*?"<>|\r\n\t#]', ' ', name)
    name = re.sub(r'\s+', ' ', name).strip().strip('.')
    return name[:maxlen] or "без_названия"


def extract_links(*texts) -> list:
    """Собирает все уникальные ссылки из переданных текстов."""
    links = []
    for t in texts:
        if not t:
            continue
        for m in URL_RE.findall(t):
            m = m.rstrip('.,;:!?)»"\'')
            if m and m not in links:
                links.append(m)
    return links


def unique_path(path: str) -> str:
    """Если файл существует — добавляет (2), (3)..."""
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    i = 2
    while os.path.exists(f"{base} ({i}){ext}"):
        i += 1
    return f"{base} ({i}){ext}"


def links_section(links: list) -> str:
    if not links:
        return "## Ссылки\n\n_Ссылок не найдено._\n"
    return "## Ссылки\n\n" + "\n".join(f"- {l}" for l in links) + "\n"


def now_str() -> str:
    return datetime.datetime.now().strftime("%d.%m.%Y %H:%M")


# ----------------------------------------------------------------------
# Whisper (загружается один раз, лениво)
# ----------------------------------------------------------------------

_whisper_models = {}

def get_whisper(model_size: str, log):
    if model_size not in _whisper_models:
        log(f"Загружаю модель Whisper «{model_size}» "
            f"(при первом запуске скачивается из интернета, подождите)...")
        from faster_whisper import WhisperModel
        _whisper_models[model_size] = WhisperModel(
            model_size, device="cpu", compute_type="int8"
        )
        log("Модель загружена.")
    return _whisper_models[model_size]


def transcribe_detailed(path: str, model_size: str, log,
                        total_sec: int = 0) -> list:
    """Расшифровка с таймкодами: список словарей {start, end, text}.
    Показывает прогресс каждые ~20 секунд."""
    import time
    model = get_whisper(model_size, log)
    segments, winfo = model.transcribe(path, vad_filter=True)
    result = []
    last_report = time.time()
    for seg in segments:
        t = seg.text.strip()
        if t:
            result.append({"start": float(seg.start),
                           "end": float(seg.end), "text": t})
        if time.time() - last_report >= 20:
            last_report = time.time()
            done = int(seg.end)
            m, s = divmod(done, 60)
            if total_sec:
                pct = min(99, done * 100 // int(total_sec))
                log(f"...расшифровано {m}:{s:02d} из видео ({pct}%)")
            else:
                log(f"...расшифровано {m}:{s:02d}")
    log(f"Расшифровка готова (язык: {winfo.language}).")
    return result


def transcribe_file(path: str, model_size: str, log,
                    total_sec: int = 0) -> str:
    """Расшифровка одним текстом (для коротких видео)."""
    segs = transcribe_detailed(path, model_size, log, total_sec)
    return "\n".join(s["text"] for s in segs)


def timed_text(segs: list) -> str:
    """Транскрипт с таймкодами [мм:сс] — для анализа."""
    lines = []
    for s in segs:
        m, sec = divmod(int(s["start"]), 60)
        lines.append(f"[{m}:{sec:02d}] {s['text']}")
    return "\n".join(lines)


def grab_frame(video_path: str, t_sec: float, out_path: str) -> bool:
    """Сохраняет кадр видео в момент t_sec как JPEG (через PyAV)."""
    try:
        import av
        with av.open(video_path) as container:
            container.seek(int(max(t_sec, 0) * 1_000_000))
            for frame in container.decode(video=0):
                if frame.time is not None and frame.time >= t_sec - 0.5:
                    img = frame.to_image()
                    img.thumbnail((1280, 1280))
                    img.save(out_path, quality=85)
                    return True
    except Exception:  # noqa: BLE001
        return False
    return False


def try_analyze(kind: str, title: str, author: str,
                description: str, body: str, log):
    """Умный анализ локальным ИИ (Ollama); None — если он не установлен."""
    try:
        import analyzer
        model = analyzer.available()
        if not model:
            return None
        log(f"Анализирую содержимое локальным ИИ ({model})... "
            "это может занять несколько минут.")
        return analyzer.analyze(kind, title, author, description, body, log)
    except Exception as e:  # noqa: BLE001
        log(f"Умный анализ не удался ({e}). Сохраняю в обычном виде.")
        return None


ACTION_RE = re.compile(
    r"(нажм|кликн|жм[её]м|щ[её]лк|вот сюда|вот здесь|вот тут|"
    r"открыва(ем|йте)|заход(им|ите)|перейд[её]м|переход(им|ите)|"
    r"выбира(ем|йте)|выберите|введите|вводим|вписыва|прописыва|"
    r"ставим галочк|эта кнопк|этот пункт|в настройках|"
    r"появи(тся|лось) окно|видите (вот|это|здесь)|как видите|на экране)",
    re.I)


def find_action_moments(segs: list, max_shots: int = 10,
                        min_gap: float = 20.0) -> list:
    """Моменты, где автор показывает что-то на экране (без ИИ, по словам)."""
    moments, last = [], -min_gap
    for s in segs:
        if ACTION_RE.search(s["text"]) and s["start"] - last >= min_gap:
            moments.append((s["start"] + 1.5, s["text"].strip()))
            last = s["start"]
            if len(moments) >= max_shots:
                break
    return moments


FILLER_RE = re.compile(
    r"(?<![а-яёa-z])(кхм|э-?э+|эм+|ммм+|угу|ага|окей|так[- ]так)"
    r"(?![а-яёa-z])[,.!…]*\s*", re.I)


CMD_RE = re.compile(
    r"(?:^|[\s>«\"'(])((?:sudo|apt(?:-get)?|pip3?|python3?|git|"
    r"docker(?:-compose)?|ollama|npm|npx|yarn|node|curl|wget|ssh|scp|"
    r"systemctl|service|chmod|chown|mkdir|winget|choco|flatpak|snap|"
    r"pacman|dnf|yum|brew|make|cmake|cargo|go (?:run|build|get|install)|"
    r"adb|ffmpeg)\s+[A-Za-z0-9\"'\-.]"
    r"[A-Za-z0-9 .\-_/:=~+@$#\"'{}\[\]]{0,90})", re.M)


def extract_commands(*texts) -> list:
    """Находит команды терминала в описании/расшифровке (без ИИ)."""
    out = []
    for t in texts:
        for m in CMD_RE.findall(t or ""):
            c = m.strip().rstrip(" .,:)\"'")
            if len(c.split()) >= 2 and c not in out:
                out.append(c)
    return out[:20]


def clean_transcript(text: str) -> str:
    """Убирает междометия и склеивает реплики в абзацы."""
    t = FILLER_RE.sub("", text or "")
    t = re.sub(r"[ \t]{2,}", " ", t)
    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    paras, buf, size = [], [], 0
    for ln in lines:
        buf.append(ln)
        size += len(ln)
        if size > 450:
            paras.append(" ".join(buf))
            buf, size = [], 0
    if buf:
        paras.append(" ".join(buf))
    return "\n\n".join(paras)


def download_file(file_url: str, path: str):
    """Скачивает файл по прямой ссылке."""
    import requests
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    r = requests.get(file_url, headers=headers, timeout=60)
    r.raise_for_status()
    with open(path, "wb") as f:
        f.write(r.content)


# ----------------------------------------------------------------------
# 1. ВИДЕО (YouTube, VK, Rutube и сотни других сайтов через yt-dlp)
# ----------------------------------------------------------------------

def process_video(url: str, out_root: str, log, model_size: str,
                  keep_raw: bool = False, ydl_extra: dict = None) -> str:
    import yt_dlp

    log("Получаю информацию о видео...")
    base_opts = {"quiet": True, "noplaylist": True, "no_warnings": True}
    if ydl_extra:
        base_opts.update(ydl_extra)
    with yt_dlp.YoutubeDL(base_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    title = info.get("title") or "Без названия"
    channel = info.get("uploader") or info.get("channel") or "Неизвестный автор"
    description = info.get("description") or ""
    upload_date = info.get("upload_date")  # ГГГГММДД
    if upload_date and len(upload_date) == 8:
        upload_date = f"{upload_date[6:8]}.{upload_date[4:6]}.{upload_date[0:4]}"
    duration = info.get("duration")
    dur_str = ""
    if duration:
        m, s = divmod(int(duration), 60)
        h, m = divmod(m, 60)
        dur_str = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
    page_url = info.get("webpage_url") or url

    log(f"Видео: «{title}» — {channel}" + (f", {dur_str}" if dur_str else ""))

    # если задан ключ Claude API — качаем видео целиком (нужны кадры
    # для скриншотов), иначе достаточно аудио
    use_ai = False
    try:
        import analyzer
        use_ai = bool(analyzer.available())
    except Exception:  # noqa: BLE001
        pass

    tmpdir = tempfile.mkdtemp(prefix="archiver_")
    segs, analysis, local_shots = [], None, []
    try:
        log("Скачиваю видео (кадры пригодятся для скриншотов)...")
        fmt = "best[height<=720][acodec!=none][vcodec!=none]/best"
        ydl_opts = {
            "format": fmt,
            "outtmpl": os.path.join(tmpdir, "media.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
        }
        if ydl_extra:
            ydl_opts.update(ydl_extra)
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        media_files = glob.glob(os.path.join(tmpdir, "media.*"))
        if not media_files:
            raise RuntimeError("Не удалось скачать видео/аудио.")
        media = media_files[0]

        log("Расшифровываю речь нейросетью Whisper... "
            "На это нужно время (обычно ~10–30% от длины видео).")
        segs = transcribe_detailed(media, model_size, log,
                                   int(duration or 0))
        transcript = "\n".join(s["text"] for s in segs)

        if use_ai:
            analysis = try_analyze("видео", title, channel, description,
                                   timed_text(segs), log)

        # --- выбор папки и имени ---
        if analysis:
            topic = sanitize(analysis.get("topic") or "Разное")
            sub = sanitize(analysis.get("subtopic") or "") \
                if (analysis.get("subtopic") or "").strip() else ""
            folder = os.path.join(out_root, topic, sub) if sub \
                else os.path.join(out_root, topic)
            final_title = analysis.get("title") or title
        else:
            import analyzer
            topic = analyzer.detect_topic_keywords(
                f"{title}\n{description}\n{transcript}")
            log(f"Тема (по ключевым словам): {topic}")
            folder = os.path.join(out_root, sanitize(topic))
            final_title = title
        os.makedirs(folder, exist_ok=True)
        md_path = unique_path(os.path.join(folder,
                                           sanitize(final_title) + ".md"))
        base_name = os.path.splitext(os.path.basename(md_path))[0]

        # --- скриншоты ---
        summary = ""
        extra_shots = []   # кадры, которые пойдут отдельным разделом
        if analysis:
            summary = analysis.get("summary_md") or ""
            ai_shots = analysis.get("screenshots") or []
            for sc in ai_shots:
                try:
                    n = int(sc.get("n") or 0)
                    t = float(sc.get("time") or 0)
                    cap = str(sc.get("caption") or "").strip() \
                        or "момент из видео"
                except (TypeError, ValueError, AttributeError):
                    continue
                if t <= 0:
                    continue
                m, s = divmod(int(t), 60)
                img_name = f"{base_name}_кадр_{n or len(extra_shots) + 1}.jpg"
                if not grab_frame(media, t, os.path.join(folder, img_name)):
                    continue
                log(f"Скриншот: [{m}:{s:02d}] {cap}")
                marker = "{{SCREENSHOT_%d}}" % n
                if n and marker in summary:
                    summary = summary.replace(
                        marker,
                        f"![{cap}]({img_name})\n*{cap} — [{m}:{s:02d}]*")
                else:
                    extra_shots.append((t, cap, img_name))
            summary = re.sub(r"\{\{SCREENSHOT_\d+\}\}", "", summary)
            if not ai_shots:
                # ИИ кадров не предложил — ловим по словам-триггерам
                for i, (t, quote) in enumerate(find_action_moments(segs), 1):
                    img_name = f"{base_name}_кадр_с{i}.jpg"
                    if grab_frame(media, t, os.path.join(folder, img_name)):
                        extra_shots.append((t, quote, img_name))
                if extra_shots:
                    log(f"Кадры по ключевым словам: {len(extra_shots)}.")
        else:
            # без ИИ: ловим моменты «нажмите/откройте/введите...» по словам
            moments = find_action_moments(segs)
            if moments:
                log(f"Нашёл {len(moments)} момент(ов) с действиями "
                    f"на экране, сохраняю кадры...")
            for i, (t, quote) in enumerate(moments, 1):
                img_name = f"{base_name}_кадр_{i}.jpg"
                if grab_frame(media, t, os.path.join(folder, img_name)):
                    local_shots.append((t, quote, img_name))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    links = extract_links(description, transcript, summary)

    md = [f"# {final_title}", ""]
    md.append(f"**Источник:** {page_url}  ")
    md.append(f"**Канал / автор:** {channel}  ")
    if upload_date:
        md.append(f"**Дата публикации:** {upload_date}  ")
    if dur_str:
        md.append(f"**Длительность:** {dur_str}  ")
    if analysis:
        theme = analysis.get("topic", "")
        if analysis.get("subtopic"):
            theme += f" / {analysis['subtopic']}"
        md.append(f"**Тема:** {theme}  ")
    md.append(f"**Сохранено:** {now_str()}")
    md.append("")

    if analysis and summary.strip():
        md += ["## Конспект", "", summary.strip(), ""]
        if extra_shots:
            md += ["## Действия на экране", ""]
            for t, cap, img_name in extra_shots:
                m2, s2 = divmod(int(t), 60)
                md.append(f"**[{m2}:{s2:02d}]** {cap}")
                md.append(f"![Кадр {m2}:{s2:02d}]({img_name})")
                md.append("")
        cmds = extract_commands(description, transcript)
        if cmds and "```" not in summary:
            md += ["## Команды (автопоиск)", "", "```"] + cmds + ["```", ""]
        if description.strip():
            md += ["## Описание под видео", "", description.strip(), ""]
        md.append(links_section(links))
        md += ["", "<details><summary>Полная расшифровка "
               "(автоматическая)</summary>", "",
               transcript.strip() or "_Речь не обнаружена._", "",
               "</details>", ""]
    else:
        md += ["## Конспект (очищенная расшифровка)", "",
               clean_transcript(transcript) or "_Речь не обнаружена._", ""]
        if local_shots:
            md += ["## Действия на экране", ""]
            for t, quote, img_name in local_shots:
                m2, s2 = divmod(int(t), 60)
                md.append(f"**[{m2}:{s2:02d}]** «{quote}»")
                md.append(f"![Кадр {m2}:{s2:02d}]({img_name})")
                md.append("")
        if description.strip():
            md += ["## Описание под видео", "", description.strip(), ""]
        cmds = extract_commands(description, transcript)
        if cmds:
            md += ["## Команды (автопоиск)", "", "```"] + cmds + ["```", ""]
        md.append(links_section(links))
        md += ["", "<details><summary>Полная расшифровка "
               "(автоматическая)</summary>", "",
               transcript.strip() or "_Речь не обнаружена._", "",
               "</details>", ""]

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    return md_path


# ----------------------------------------------------------------------
# 2. TELEGRAM (публичные посты вида https://t.me/канал/123)
# ----------------------------------------------------------------------

def process_telegram(url: str, out_root: str, log) -> str:
    import requests
    from bs4 import BeautifulSoup

    # разбираем ссылку: t.me/канал/номер  или  t.me/s/канал/номер
    m = re.search(r"t\.me/(?:s/)?([A-Za-z0-9_]+)/(\d+)", url)
    if not m:
        raise RuntimeError(
            "Не удалось разобрать ссылку Telegram. Нужна ссылка на "
            "конкретный пост публичного канала, например https://t.me/durov/123"
        )
    channel, msg_id = m.group(1), m.group(2)

    log(f"Загружаю пост {msg_id} из канала @{channel}...")
    embed_url = f"https://t.me/{channel}/{msg_id}?embed=1&mode=tme"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    resp = requests.get(embed_url, headers=headers, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    if soup.select_one(".tgme_widget_message_error"):
        raise RuntimeError("Пост недоступен (приватный канал или пост удалён).")

    # текст поста + ссылки из него
    text_el = soup.select_one(".tgme_widget_message_text")
    post_text, links = "", []
    if text_el:
        for br in text_el.find_all("br"):
            br.replace_with("\n")
        post_text = text_el.get_text("\n").strip()
        for a in text_el.find_all("a", href=True):
            href = a["href"]
            if href.startswith("http") and href not in links:
                links.append(href)

    # имя канала и дата
    author_el = soup.select_one(".tgme_widget_message_owner_name")
    author = author_el.get_text(" ", strip=True) if author_el else channel
    time_el = soup.select_one("time")
    post_date = ""
    if time_el and time_el.get("datetime"):
        try:
            dt = datetime.datetime.fromisoformat(time_el["datetime"])
            post_date = dt.strftime("%d.%m.%Y %H:%M")
        except ValueError:
            pass

    # заголовок файла — первая строка текста
    first_line = (post_text.splitlines() or ["Пост"])[0]
    title = sanitize(first_line, 80) if first_line.strip() else f"Пост {msg_id}"

    analysis = try_analyze("пост", first_line, f"{author} (@{channel})",
                           "", post_text, log)
    if analysis:
        topic = sanitize(analysis.get("topic") or "Разное")
        sub = sanitize(analysis.get("subtopic") or "") \
            if (analysis.get("subtopic") or "").strip() else ""
        folder = os.path.join(out_root, topic, sub) if sub \
            else os.path.join(out_root, topic)
        if analysis.get("title"):
            title = sanitize(analysis["title"], 80)
    else:
        import analyzer
        kw_topic = analyzer.detect_topic_keywords(post_text)
        folder = os.path.join(out_root, sanitize(kw_topic))
    os.makedirs(folder, exist_ok=True)
    md_path = unique_path(os.path.join(folder, f"{title}.md"))
    base_name = os.path.splitext(os.path.basename(md_path))[0]

    # фотографии
    photo_md = []
    photo_wraps = soup.select(
        ".tgme_widget_message_photo_wrap, .tgme_widget_message_video_thumb"
    )
    for i, wrap in enumerate(photo_wraps, start=1):
        style = wrap.get("style", "")
        pm = re.search(r"background-image:\s*url\('([^']+)'\)", style)
        if not pm:
            continue
        img_url = pm.group(1)
        try:
            log(f"Скачиваю фото {i}...")
            img = requests.get(img_url, headers=headers, timeout=30)
            img.raise_for_status()
            img_name = f"{base_name}_фото_{i}.jpg"
            with open(os.path.join(folder, img_name), "wb") as f:
                f.write(img.content)
            photo_md.append(f"![Фото {i}]({img_name})")
        except Exception as e:  # noqa: BLE001
            log(f"Не удалось скачать фото {i}: {e}")

    links = extract_links(post_text) + [l for l in links
                                        if l not in extract_links(post_text)]

    md = [f"# {first_line.strip() or 'Пост ' + msg_id}", ""]
    md.append(f"**Источник:** https://t.me/{channel}/{msg_id}  ")
    md.append(f"**Канал:** {author} (@{channel})  ")
    if post_date:
        md.append(f"**Дата публикации:** {post_date}  ")
    if analysis:
        theme = analysis.get("topic", "")
        if analysis.get("subtopic"):
            theme += f" / {analysis['subtopic']}"
        md.append(f"**Тема:** {theme}  ")
    md.append(f"**Сохранено:** {now_str()}")
    md.append("")
    if photo_md:
        md += ["## Фото", ""] + photo_md + [""]
    md += ["## Текст поста", "", post_text or "_Текст отсутствует._", ""]
    md.append(links_section(links))

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    return md_path


# ----------------------------------------------------------------------
# 3. INSTAGRAM (посты, Reels, видео)
# ----------------------------------------------------------------------

def process_instagram(url: str, out_root: str, log, model_size: str,
                      insta_login: str = "") -> str:
    import instaloader

    m = re.search(
        r"instagram\.com/(?:[A-Za-z0-9_.]+/)?(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)",
        url)
    if not m:
        raise RuntimeError(
            "Нужна ссылка на конкретный пост или Reels, например "
            "https://www.instagram.com/p/Cabc123/ или .../reel/Cabc123/")
    shortcode = m.group(1)

    log("Подключаюсь к Instagram...")
    L = instaloader.Instaloader(
        quiet=True, download_pictures=False, download_videos=False,
        download_video_thumbnails=False, save_metadata=False,
        compress_json=False)

    if insta_login:
        try:
            L.load_session_from_file(insta_login)
            log(f"Вошёл как @{insta_login} (сохранённая сессия).")
        except FileNotFoundError:
            log(f"Сессия @{insta_login} не найдена. Один раз выполните в "
                f"терминале:  instaloader --login {insta_login}")

    try:
        post = instaloader.Post.from_shortcode(L.context, shortcode)
        caption = post.caption or ""
        author = post.owner_username
        post_date = post.date_local.strftime("%d.%m.%Y %H:%M")
    except Exception as e:  # noqa: BLE001
        log(f"Instaloader не смог получить пост: {e}")
        log("Пробую запасной способ (yt-dlp)...")
        last_err = None
        for browser in (None, "chrome", "firefox", "edge"):
            try:
                if browser:
                    log(f"...с куки из браузера {browser}")
                extra = {"cookiesfrombrowser": (browser,)} if browser else {}
                return process_video(url, out_root, log, model_size,
                                     ydl_extra=extra)
            except Exception as e2:  # noqa: BLE001
                last_err = e2
        raise RuntimeError(
            "Instagram не отдал пост. Что попробовать по порядку:\n"
            "  1) обновите библиотеки:  pip install -U instaloader yt-dlp\n"
            "  2) войдите в аккаунт: выполните в терминале  "
            "instaloader --login ВАШ_ЛОГИН  и впишите этот логин "
            "в поле «Логин Instagram» в программе;\n"
            "  3) откройте instagram.com в Chrome и войдите в аккаунт — "
            "тогда сработает запасной способ через куки браузера.\n"
            f"Ошибка instaloader: {e}\n"
            f"Ошибка запасного способа: {last_err}")

    first_line = (caption.splitlines() or [""])[0].strip()
    title = sanitize(first_line, 80) if first_line else f"Пост {shortcode}"

    analysis = try_analyze("пост", first_line or f"Пост {shortcode}",
                           f"@{author}", "", caption, log)
    if analysis:
        topic = sanitize(analysis.get("topic") or "Разное")
        sub = sanitize(analysis.get("subtopic") or "") \
            if (analysis.get("subtopic") or "").strip() else ""
        folder = os.path.join(out_root, topic, sub) if sub \
            else os.path.join(out_root, topic)
        if analysis.get("title"):
            title = sanitize(analysis["title"], 80)
    else:
        import analyzer
        kw_topic = analyzer.detect_topic_keywords(caption)
        folder = os.path.join(out_root, sanitize(kw_topic))
    os.makedirs(folder, exist_ok=True)
    md_path = unique_path(os.path.join(folder, f"{title}.md"))
    base_name = os.path.splitext(os.path.basename(md_path))[0]

    # список медиа: карусель или одиночный пост
    if post.typename == "GraphSidecar":
        nodes = list(post.get_sidecar_nodes())
    else:
        nodes = [post]

    photo_md, transcripts = [], []
    tmpdir = tempfile.mkdtemp(prefix="archiver_ig_")
    try:
        pi = vi = 0
        for node in nodes:
            if getattr(node, "is_video", False):
                vi += 1
                video_url = node.video_url
                if not video_url:
                    continue
                log(f"Скачиваю видео {vi}...")
                vpath = os.path.join(tmpdir, f"video_{vi}.mp4")
                download_file(video_url, vpath)
                log(f"Расшифровываю речь из видео {vi} (Whisper)...")
                t = transcribe_file(vpath, model_size, log)
                transcripts.append((vi, t))
            else:
                pi += 1
                img_url = getattr(node, "display_url", None) or \
                          getattr(node, "url", None)
                if not img_url:
                    continue
                try:
                    log(f"Скачиваю фото {pi}...")
                    img_name = f"{base_name}_фото_{pi}.jpg"
                    download_file(img_url, os.path.join(folder, img_name))
                    photo_md.append(f"![Фото {pi}]({img_name})")
                except Exception as e:  # noqa: BLE001
                    log(f"Не удалось скачать фото {pi}: {e}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    all_transcript = "\n\n".join(t for _, t in transcripts)
    links = extract_links(caption, all_transcript)

    md = [f"# {first_line or 'Пост ' + shortcode}", ""]
    md.append(f"**Источник:** https://www.instagram.com/p/{shortcode}/  ")
    md.append(f"**Автор:** @{author}  ")
    md.append(f"**Дата публикации:** {post_date}  ")
    if analysis:
        theme = analysis.get("topic", "")
        if analysis.get("subtopic"):
            theme += f" / {analysis['subtopic']}"
        md.append(f"**Тема:** {theme}  ")
    md.append(f"**Сохранено:** {now_str()}")
    md.append("")
    if photo_md:
        md += ["## Фото", ""] + photo_md + [""]
    if caption.strip():
        md += ["## Текст поста", "", caption.strip(), ""]
    if transcripts:
        md += ["## Текст видео (расшифровка)", ""]
        for vi, t in transcripts:
            if len(transcripts) > 1:
                md.append(f"### Видео {vi}")
                md.append("")
            md.append(t.strip() or "_Речь не обнаружена._")
            md.append("")
    md.append(links_section(links))

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    return md_path


# ----------------------------------------------------------------------
# 4. СТАТЬИ (любая веб-страница)
# ----------------------------------------------------------------------

def process_article(url: str, out_root: str, log) -> str:
    import trafilatura

    log("Загружаю страницу...")
    html = trafilatura.fetch_url(url)
    if not html:
        raise RuntimeError("Не удалось загрузить страницу.")

    log("Извлекаю текст статьи...")
    text = trafilatura.extract(
        html, url=url, output_format="markdown",
        include_links=True, include_images=True, include_tables=True,
    )
    meta = trafilatura.extract_metadata(html)

    if not text:
        raise RuntimeError("Не удалось извлечь текст со страницы.")

    title = (meta.title if meta and meta.title else None) or "Статья"
    author = meta.author if meta and meta.author else ""
    date = meta.date if meta and meta.date else ""
    site = urllib.parse.urlparse(url).netloc.replace("www.", "")

    analysis = try_analyze("статья", title, author or site, "", text, log)

    links = extract_links(text)

    if analysis:
        topic = sanitize(analysis.get("topic") or "Разное")
        sub = sanitize(analysis.get("subtopic") or "") \
            if (analysis.get("subtopic") or "").strip() else ""
        folder = os.path.join(out_root, topic, sub) if sub \
            else os.path.join(out_root, topic)
        final_title = analysis.get("title") or title
    else:
        import analyzer
        kw_topic = analyzer.detect_topic_keywords(f"{title}\n{text}")
        folder = os.path.join(out_root, sanitize(kw_topic))
        final_title = title
    os.makedirs(folder, exist_ok=True)
    md_path = unique_path(os.path.join(folder, sanitize(final_title) + ".md"))

    md = [f"# {final_title}", ""]
    md.append(f"**Источник:** {url}  ")
    md.append(f"**Сайт:** {site}  ")
    if author:
        md.append(f"**Автор:** {author}  ")
    if date:
        md.append(f"**Дата публикации:** {date}  ")
    if analysis:
        theme = analysis.get("topic", "")
        if analysis.get("subtopic"):
            theme += f" / {analysis['subtopic']}"
        md.append(f"**Тема:** {theme}  ")
    md.append(f"**Сохранено:** {now_str()}")
    if analysis and (analysis.get("summary_md") or "").strip():
        md += ["", "## Конспект", "", analysis["summary_md"].strip()]
    md += ["", "## Текст статьи", "", text.strip(), ""]
    md.append(links_section(links))

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    return md_path


# ----------------------------------------------------------------------
# Определение типа ссылки и общая обработка
# ----------------------------------------------------------------------

def process_url(url: str, out_root: str, log, model_size: str,
                insta_login: str = "") -> str:
    url = url.strip()
    if not url.startswith("http"):
        url = "https://" + url
    host = urllib.parse.urlparse(url).netloc.lower()

    if "t.me" in host or "telegram.me" in host:
        return process_telegram(url, out_root, log)

    if "instagram.com" in host:
        return process_instagram(url, out_root, log, model_size, insta_login)

    # пробуем как видео (yt-dlp поддерживает сотни сайтов)
    video_hosts = ("youtube.", "youtu.be", "rutube.", "vimeo.",
                   "vk.com/video", "dzen.ru/video", "twitch.")
    looks_like_video = any(h in url.lower() for h in video_hosts)

    if looks_like_video:
        return process_video(url, out_root, log, model_size)

    # неизвестный сайт: сначала пробуем yt-dlp, если нет — статья
    try:
        import yt_dlp
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True,
                               "noplaylist": True}) as ydl:
            info = ydl.extract_info(url, download=False, process=False)
        if info and info.get("duration"):
            return process_video(url, out_root, log, model_size)
    except Exception:  # noqa: BLE001
        pass

    return process_article(url, out_root, log)


# ----------------------------------------------------------------------
# Графический интерфейс
# ----------------------------------------------------------------------

class App:
    def __init__(self, root):
        self.root = root
        root.title("Архиватор ссылок → Markdown")
        root.geometry("720x520")
        root.minsize(560, 420)

        self.queue = queue.Queue()
        self.busy = False

        default_out = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "Архив")
        self.out_var = tk.StringVar(value=default_out)
        self.url_var = tk.StringVar()
        self.model_var = tk.StringVar(value="small")
        self.insta_var = tk.StringVar()

        pad = {"padx": 10, "pady": 5}
        frm = ttk.Frame(root)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="Ссылка (видео, Telegram, Instagram, статья) — "
                            "вставьте кнопкой «📋 Вставить» или Ctrl+V:")\
            .pack(anchor="w", **pad)
        row1 = ttk.Frame(frm); row1.pack(fill="x", **pad)
        self.url_entry = ttk.Entry(row1, textvariable=self.url_var)
        self.url_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(row1, text="📋 Вставить",
                   command=self.paste_url).pack(side="left", padx=(8, 0))
        self.go_btn = ttk.Button(row1, text="Сохранить", command=self.start)
        self.go_btn.pack(side="left", padx=(8, 0))
        self.url_entry.bind("<Return>", lambda e: self.start())

        row2 = ttk.Frame(frm); row2.pack(fill="x", **pad)
        ttk.Label(row2, text="Папка для архива:").pack(side="left")
        self.out_entry = ttk.Entry(row2, textvariable=self.out_var)
        self.out_entry.pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(row2, text="Выбрать...",
                   command=self.choose_folder).pack(side="left")

        row3 = ttk.Frame(frm); row3.pack(fill="x", **pad)
        ttk.Label(row3, text="Качество расшифровки (Whisper):")\
            .pack(side="left")
        combo = ttk.Combobox(row3, textvariable=self.model_var, width=10,
                             state="readonly",
                             values=["tiny", "base", "small", "medium"])
        combo.pack(side="left", padx=8)
        ttk.Label(row3, text="tiny — быстро/грубо, medium — медленно/точно")\
            .pack(side="left")

        row4 = ttk.Frame(frm); row4.pack(fill="x", **pad)
        ttk.Label(row4, text="Логин Instagram (необязательно):")\
            .pack(side="left")
        self.insta_entry = ttk.Entry(row4, textvariable=self.insta_var,
                                     width=22)
        self.insta_entry.pack(side="left", padx=8)
        ttk.Label(row4, text="нужен, если Instagram требует вход")\
            .pack(side="left")

        # буфер обмена: правая кнопка мыши + Ctrl+V при любой раскладке
        for w in (self.url_entry, self.out_entry, self.insta_entry):
            self.enable_clipboard(w)

        ttk.Label(frm, text="Журнал:").pack(anchor="w", **pad)
        self.log_box = scrolledtext.ScrolledText(
            frm, height=14, state="disabled", wrap="word")
        self.log_box.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self.log("Готов к работе. Вставьте ссылку и нажмите «Сохранить».")
        self.root.after(100, self.poll_queue)
        threading.Thread(target=self._check_ai, daemon=True).start()

    def _check_ai(self):
        try:
            import analyzer
            model = analyzer.available()
        except Exception:  # noqa: BLE001
            model = ""
        if model:
            self.log(f"Локальный ИИ найден ({model}): будут умные выжимки, "
                     f"темы и скриншоты по смыслу.")
        else:
            self.log("Локальный ИИ (Ollama) не найден — темы определю по "
                     "ключевым словам, конспект будет без ИИ-выжимки. "
                     "Как включить умный режим — в ИНСТРУКЦИИ, раздел "
                     "«Умная выжимка».")

    # ---------- служебное ----------
    def paste_url(self):
        """Кнопка «Вставить»: берёт ссылку из буфера обмена."""
        try:
            text = self.root.clipboard_get().strip()
        except tk.TclError:
            messagebox.showinfo(
                "Буфер пуст",
                "Сначала скопируйте ссылку (Ctrl+C или «Копировать ссылку»).")
            return
        self.url_var.set(text)
        self.url_entry.icursor("end")
        self.url_entry.focus_set()

    def enable_clipboard(self, widget):
        """Меню правой кнопки мыши + горячие клавиши при любой раскладке."""
        menu = tk.Menu(widget, tearoff=0)
        menu.add_command(label="Вставить",
                         command=lambda: widget.event_generate("<<Paste>>"))
        menu.add_command(label="Копировать",
                         command=lambda: widget.event_generate("<<Copy>>"))
        menu.add_command(label="Вырезать",
                         command=lambda: widget.event_generate("<<Cut>>"))
        menu.add_separator()
        menu.add_command(
            label="Выделить всё",
            command=lambda: widget.select_range(0, "end"))

        def show_menu(event):
            widget.focus_set()
            menu.tk_popup(event.x_root, event.y_root)
        widget.bind("<Button-3>", show_menu)

        # Ctrl+V/C/X/A по ФИЗИЧЕСКОЙ клавише — работает и на русской
        # раскладке (коды: Windows / Linux)
        keymap = {
            (86, 55): "<<Paste>>",      # V
            (67, 54): "<<Copy>>",       # C
            (88, 53): "<<Cut>>",        # X
        }
        def on_ctrl(event):
            for codes, action in keymap.items():
                if event.keycode in codes:
                    widget.event_generate(action)
                    return "break"
            if event.keycode in (65, 38):   # A — выделить всё
                widget.select_range(0, "end")
                widget.icursor("end")
                return "break"
        widget.bind("<Control-KeyPress>", on_ctrl)

    def choose_folder(self):
        d = filedialog.askdirectory()
        if d:
            self.out_var.set(d)

    def log(self, msg: str):
        self.queue.put(msg)

    def poll_queue(self):
        try:
            while True:
                msg = self.queue.get_nowait()
                self.log_box.configure(state="normal")
                stamp = datetime.datetime.now().strftime("%H:%M:%S")
                self.log_box.insert("end", f"[{stamp}] {msg}\n")
                self.log_box.see("end")
                self.log_box.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(150, self.poll_queue)

    # ---------- запуск обработки ----------
    def start(self):
        if self.busy:
            messagebox.showinfo("Подождите", "Предыдущая ссылка ещё обрабатывается.")
            return
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("Нет ссылки", "Вставьте ссылку в поле выше.")
            return
        self.busy = True
        self.go_btn.configure(state="disabled")
        threading.Thread(target=self.worker, args=(url,), daemon=True).start()

    def worker(self, url: str):
        try:
            out_root = self.out_var.get().strip()
            os.makedirs(out_root, exist_ok=True)
            path = process_url(url, out_root, self.log,
                               self.model_var.get(),
                               self.insta_var.get().strip())
            self.log("ГОТОВО ✅  Файл сохранён:")
            self.log(path)
        except Exception as e:  # noqa: BLE001
            self.log(f"ОШИБКА ❌  {e}")
        finally:
            self.busy = False
            self.queue.put("— — —")
            self.root.after(0, lambda: self.go_btn.configure(state="normal"))


def main():
    if not HAS_GUI:
        print("Модуль окон (tkinter) не установлен.")
        print("Linux: выполните  sudo apt install python3-tk")
        print("Либо используйте веб-версию:  python archiver_web.py")
        sys.exit(1)
    root = tk.Tk()
    try:
        style = ttk.Style()
        if "clam" in style.theme_names():
            style.theme_use("clam")
    except Exception:  # noqa: BLE001
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()

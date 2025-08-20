import os
import re
import shutil
import urllib.parse
import warnings
import hashlib
from bs4 import BeautifulSoup, Comment, XMLParsedAsHTMLWarning
from datetime import datetime
from pathlib import Path
import posixpath 

try:
    from PIL import Image, ImageFile, UnidentifiedImageError
    ImageFile.LOAD_TRUNCATED_IMAGES = True
    PIL_AVAILABLE = True
except Exception:
    PIL_AVAILABLE = False

# ===================== ЛОГИ =====================
def log(msg):
    print(f"[LOG] {msg}")

def log_step(msg):
    print(f"\n=== {msg} ===")

def log_warn(msg):
    print(f"[WARN] {msg}")

def log_err(msg):
    print(f"[ERR] {msg}")

# --- подавляем предупреждение BS4 про XML
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)



# ========= Вспомогалки =========
def map_put(mapping: dict, fname: str, rel: str):
    mapping[fname] = norm_slashes(rel)
    mapping[fname.lower()] = norm_slashes(rel)




def norm_slashes(path: str) -> str:
    return os.path.normpath(path).replace("\\", "/")



def unquote_all(u: str) -> str:
    prev = u
    for _ in range(3):
        u = urllib.parse.unquote(u)
        if u == prev:
            break
        prev = u
    return u



def strip_wayback(url: str) -> str:
    if not url:
        return url
    parsed = urllib.parse.urlparse(url)
    if "web.archive.org" in parsed.netloc.lower() and "/web/" in parsed.path:
        tail = parsed.path.split("/web/", 1)[-1]
        if "/" in tail:
            maybe = tail.split("/", 1)[-1]
            if maybe.startswith("http://") or maybe.startswith("https://"):
                return maybe + (("#" + parsed.fragment) if parsed.fragment else "")
    return url

def strip_wayback_v2(url: str) -> str:
    """Убираем префиксы web.archive.org"""
    # пример: https://web.archive.org/web/20250517130256/https://wanakatriketours.co.nz/page
    if "web.archive.org" in url:
        parts = url.split("/", 5)
        if len(parts) >= 6:
            return "https://" + parts[5]
    return url


def url_basename(url: str) -> str:
    u = strip_wayback(url)
    u = u.split("#", 1)[0].split("?", 1)[0]
    u = norm_slashes(u)
    return os.path.basename(u)


def is_img(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in {".png",".jpg",".jpeg",".gif",".webp",".svg",".bmp",".ico",".avif"}



def is_css(name: str) -> bool:
    return name.lower().endswith(".css")



def is_js(name: str) -> bool:
    return name.lower().endswith(".js")




def is_font(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in {".woff",".woff2",".ttf",".otf",".eot"}



def pick_parser(path: str) -> str:
    low = path.lower()
    return "xml" if (low.endswith(".xhtml") or low.endswith(".xml")) else "html.parser"




def make_rel_from(path_from: str, site_root: str, rel_to: str) -> str:
    abs_target = os.path.join(site_root, rel_to)
    rel = os.path.relpath(abs_target, start=os.path.dirname(path_from))
    return norm_slashes(rel)



def merge_dir(src: str, dst: str, on_conflict: str = "keep_dest"):
    os.makedirs(dst, exist_ok=True)
    for name in os.listdir(src):
        s = os.path.join(src, name)
        d = os.path.join(dst, name)

        if os.path.isdir(s):
            merge_dir(s, d, on_conflict=on_conflict)
            try:
                os.rmdir(s)
            except OSError:
                pass
        else:
            os.makedirs(os.path.dirname(d), exist_ok=True)
            if os.path.exists(d):
                try:
                    if file_sha256(s) == file_sha256(d):
                        os.remove(s)
                        log(f"DUPE: {os.path.relpath(s)} ~ {os.path.relpath(d)} — источник удалён")
                        continue
                except Exception as e:
                    log_warn(f"Не удалось сравнить хэши '{s}' и '{d}': {e}")

                if on_conflict == "keep_dest":
                    log_warn(f"Конфликт: {d} уже существует. Оставляем существующий, '{s}' удаляем.")
                    try:
                        os.remove(s)
                    except Exception as e:
                        log_warn(f"Не удалось удалить '{s}': {e}")
                elif on_conflict == "overwrite":
                    log_warn(f"Конфликт: {d} уже существует. Перезаписываем.")
                    try:
                        os.replace(s, d)
                    except Exception:
                        shutil.copy2(s, d)
                        os.remove(s)
                elif on_conflict == "rename":
                    base, ext = os.path.splitext(d)
                    k = 1
                    cand = f"{base} ({k}){ext}"
                    while os.path.exists(cand):
                        k += 1
                        cand = f"{base} ({k}){ext}"
                    shutil.move(s, cand)
                    log_warn(f"Конфликт: {d}. Новый сохранён как {cand}.")
                else:
                    log_warn(f"Неизвестная стратегия on_conflict='{on_conflict}', используем keep_dest.")
                    try:
                        os.remove(s)
                    except Exception as e:
                        log_warn(f"Не удалось удалить '{s}': {e}")
            else:
                shutil.move(s, d)




def find_domain_root(site_dir_abs: str) -> str | None:
    site_dir_abs = os.path.abspath(site_dir_abs)

    # 1) проверяем корень
    root_files = {f.lower() for f in os.listdir(site_dir_abs) if os.path.isfile(os.path.join(site_dir_abs, f))}
    if "index.html" in root_files or "index.htm" in root_files:
        return site_dir_abs

    # 2) собираем кандидатов
    candidates = []
    for root, dirs, files in os.walk(site_dir_abs):
        lower = {f.lower() for f in files}
        if "index.html" in lower or "index.htm" in lower:
            # считаем количество html внутри
            html_count = sum(1 for f in files if f.lower().endswith((".html", ".htm")))
            depth = len(os.path.relpath(root, site_dir_abs).split(os.sep))
            candidates.append((depth, -html_count, root))

    if not candidates:
        return None

    candidates.sort(key=lambda x: (x[0], x[1]))
    return candidates[0][2]



def file_sha256(path: str, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()





def clean_domain(user_input: str) -> str:
    if not re.match(r'^https?://', user_input):
        user_input = "http://" + user_input
    
    parsed = urllib.parse.urlparse(user_input)
    domain = parsed.netloc.lower()

    # убираем www.
    if domain.startswith("www."):
        domain = domain[4:]

    return domain


# =======  глобальные переменные
domain_full = input("Введите домен (например: https://mizura-vugalo.sbs): ").strip()
domain = clean_domain(domain_full)
domain = domain.split(":")[0]
site_dir = "site"
site_abs = os.path.abspath(site_dir)
script_name = os.path.basename(__file__)


# ========= Определяем папки
candidates = [d for d in os.listdir(".") if os.path.isdir(d) and d not in {"site","__pycache__"}]
if len(candidates) != 1:
    log_err(f"Должна быть ровно ОДНА папка-источник рядом со скриптом. Нашёл: {candidates}")
    raise SystemExit(1)
donor_dir = candidates[0]

if os.path.exists(site_dir):
    shutil.rmtree(site_dir)

log_step(f"Копируем донора '{donor_dir}' → '{site_dir}'")
shutil.copytree(donor_dir, site_dir)


domain_root = find_domain_root(site_abs)
if domain_root and os.path.abspath(domain_root) != os.path.abspath(site_abs):
    print(f"[i] Найден root: {domain_root}")
    merge_dir(domain_root, site_abs)
    try:
        shutil.rmtree(domain_root)
    except Exception as e:
        print(f"[!] Не удалось удалить {domain_root}: {e}")




# ========= Расплющиваем корень
log_step("Расплющивание структуры (ht/<domain> → site/)")
domain_root = find_domain_root(site_abs)
if domain_root and os.path.abspath(domain_root) != site_abs:
    log(f"Найден доменный корень: {domain_root}")
    cur = domain_root
    site_abs_norm = os.path.normpath(site_abs)

    while os.path.normpath(cur) != site_abs_norm:
        try:
            if not os.listdir(cur):
                osrmdir = cur
                os.rmdir(cur)
                log(f"Удалена пустая оболочка: {osrmdir}")
            cur = os.path.dirname(cur)
        except Exception as e:
            log_warn(f"Не удалось удалить '{cur}': {e}")
            break
else:
    log("Доменный корень не найден — оставляем как есть.")





# ========= Готовим папки assets
assets_dir   = os.path.join(site_abs, "assets")
assets_img   = os.path.join(assets_dir, "images")
assets_css   = os.path.join(assets_dir, "css")
assets_js    = os.path.join(assets_dir, "js")
assets_fonts = os.path.join(assets_dir, "fonts")
for p in (assets_img, assets_css, assets_js, assets_fonts):
    os.makedirs(p, exist_ok=True)


img_map   = {}
css_map   = {}
js_map    = {}
font_map  = {}





# ========= Перенос ресурсов в assets
log_step("Сбор ресурсов в assets/*")
for root, dirs, files in os.walk(site_abs):
    abs_root = os.path.abspath(root)
    if abs_root.startswith(os.path.abspath(assets_dir)):
        continue
    for fname in files:
        src = os.path.join(root, fname)
        if is_img(fname):
            dst = os.path.join(assets_img, fname)
            if os.path.abspath(src) != os.path.abspath(dst):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                try:
                    shutil.move(src, dst)
                    log(f"IMG  {fname} → assets/images/")
                except shutil.Error:
                    shutil.copy2(src, dst)
                    try:
                        os.remove(src)
                    except OSError:
                        pass
                    log(f"IMG  {fname} скопирован → assets/images/ (move не удался)")
            map_put(img_map, fname, os.path.join("assets", "images", fname))
        elif is_css(fname):
            dst = os.path.join(assets_css, fname)
            if os.path.abspath(src) != os.path.abspath(dst):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                try:
                    shutil.move(src, dst)
                    log(f"CSS  {fname} → assets/css/")
                except shutil.Error:
                    shutil.copy2(src, dst)
                    try:
                        os.remove(src)
                    except OSError:
                        pass
                    log(f"CSS  {fname} скопирован → assets/css/ (move не удался)")
            map_put(css_map, fname, os.path.join("assets", "css", fname))
        elif is_js(fname):
            dst = os.path.join(assets_js, fname)
            if os.path.abspath(src) != os.path.abspath(dst):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                try:
                    shutil.move(src, dst)
                    log(f"JS   {fname} → assets/js/")
                except shutil.Error:
                    shutil.copy2(src, dst)
                    try:
                        os.remove(src)
                    except OSError:
                        pass
                    log(f"JS   {fname} скопирован → assets/js/ (move не удался)")
            map_put(js_map, fname, os.path.join("assets", "js", fname))
        elif is_font(fname):
            dst = os.path.join(assets_fonts, fname)
            if os.path.abspath(src) != os.path.abspath(dst):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                try:
                    shutil.move(src, dst)
                    log(f"FONT {fname} → assets/fonts/")
                except shutil.Error:
                    shutil.copy2(src, dst)
                    try:
                        os.remove(src)
                    except OSError:
                        pass
                    log(f"FONT {fname} скопирован → assets/fonts/ (move не удался)")
            map_put(font_map, fname, os.path.join("assets", "fonts", fname))





# ========= Удаляем дубликаты в assets и корректируем карты
def dedupe_folder(folder_abs: str, rel_prefix: str, mapping: dict):
    log_step(f"Дедупликация: {rel_prefix}")
    seen = {}
    removed = 0
    for fname in sorted(os.listdir(folder_abs)):
        fpath = os.path.join(folder_abs, fname)
        if not os.path.isfile(fpath):
            continue
        h = file_sha256(fpath)
        if h not in seen:
            seen[h] = fname
            continue
        # дубликат — удаляем
        canon = seen[h]
        try:
            os.remove(fpath)
            removed += 1
            log(f"DUPE удалён: {fname} → используем {canon}")
            # перенаправляем ссылки на дубликат к канону
            mapping[fname.lower()] = norm_slashes(os.path.join(rel_prefix, canon))
        except Exception as e:
            log_warn(f"Не удалось удалить дубликат '{fname}': {e}")
    if removed == 0:
        log("Дубликатов не обнаружено.")
    else:
        log(f"Удалено дубликатов: {removed}")

# прогоняем по всем папкам assets/*
dedupe_folder(assets_img,   os.path.join("assets","images"), img_map)
dedupe_folder(assets_css,   os.path.join("assets","css"),    css_map)
dedupe_folder(assets_js,    os.path.join("assets","js"),     js_map)
dedupe_folder(assets_fonts, os.path.join("assets","fonts"),  font_map)





# ========= Оптимизация изображений
def optimize_image(path: str) -> tuple[bool, int, int]:
    """
    Возвращает (was_optimized, old_size, new_size)
    """
    if not PIL_AVAILABLE:
        return (False, 0, 0)
    ext = os.path.splitext(path)[1].lower()
    if ext not in {".png", ".jpg", ".jpeg", ".webp"}:
        return (False, 0, 0)
    old_size = os.path.getsize(path)
    try:
        with Image.open(path) as im:
            fmt = (im.format or "").upper()
            temp_path = path + ".opt.tmp"

            if fmt == "JPEG":
                im = im.convert("RGB")
                im.save(temp_path, format="JPEG", quality=85, optimize=True, progressive=True)
            elif fmt == "PNG":
                im.save(temp_path, format="PNG", optimize=True)
            elif fmt == "WEBP":
                im.save(temp_path, format="WEBP", quality=80, method=4)
            else:
                return (False, 0, 0)

            new_size = os.path.getsize(temp_path)
            if new_size < old_size:
                os.replace(temp_path, path)
                return (True, old_size, new_size)
            else:
                os.remove(temp_path)
                return (False, old_size, new_size)
    except UnidentifiedImageError:
        return (False, old_size, old_size)
    except Exception as e:
        log_warn(f"Оптимизация '{path}' не удалась: {e}")
        return (False, old_size, old_size)

log_step("Оптимизация изображений (если доступен Pillow)")
if not PIL_AVAILABLE:
    log_warn("Pillow не установлен — шаг оптимизации пропущен.")
else:
    saved_total = 0
    for fname in os.listdir(assets_img):
        fpath = os.path.join(assets_img, fname)
        if not os.path.isfile(fpath):
            continue
        ok, old_s, new_s = optimize_image(fpath)
        if ok:
            saved = old_s - new_s
            saved_total += saved
            log(f"OPTIMIZED {fname}: {old_s} → {new_s} байт (-{saved})")
    if saved_total:
        log(f"Итого экономия: {saved_total} байт")
    else:
        log("Экономии нет или все изображения уже оптимальны.")






# ========= Функции замены URL
def replace_single(url_val: str, html_abs: str) -> str:
    """
    Преобразует web.archive ссылку в локальный относительный путь.
    html_abs — полный путь текущего HTML (нужно для построения относительных ссылок).
    """
    clean = strip_wayback_v2(url_val)
    parsed = urllib.parse.urlparse(clean)

    # Определяем имя файла
    if parsed.path == "" or parsed.path.endswith("/"):
        file_name = "index.html"
    else:
        file_name = os.path.basename(parsed.path)

    # Определяем папку
    folder = parsed.netloc.replace(":", "_")  # домен → папка
    rel_path = f"../{folder}/{file_name}"

    return rel_path

def replace_srcset(val: str, html_abs: str):
    parts = [p.strip() for p in (val or "").split(",") if p.strip()]
    out = []
    for p in parts:
        segs = p.split()
        if not segs:
            continue
        u = segs[0]
        rest = " ".join(segs[1:]) if len(segs) > 1 else ""
        new_u = replace_single(u, html_abs)
        out.append(new_u + ((" " + rest) if rest else ""))
    return ", ".join(out)











# ========= переписываем пути HTML/XHTML/XML
log_step("Переписываем пути в HTML/XHTML/XML")
HTML_EXTS = (".html",".htm",".xhtml",".xml")
html_files_count = 0
rewritten_refs = 0

for root, dirs, files in os.walk(site_abs):
    for fname in files:
        if not fname.lower().endswith(HTML_EXTS):
            continue
        html_abs = os.path.join(root, fname)
        parser = pick_parser(html_abs)
        with open(html_abs, "r", encoding="utf-8", errors="ignore") as f:
            soup = BeautifulSoup(f, features=parser)

        changed = False

        for tag in soup.find_all(True):
            if tag.name == "img":
                if tag.has_attr("src"):
                    old = tag["src"]; nv = replace_single(old, html_abs)
                    if nv != old: tag["src"] = nv; changed = True; rewritten_refs += 1
                if tag.has_attr("srcset"):
                    old = tag["srcset"]; nv = replace_srcset(old, html_abs)
                    if nv != old: tag["srcset"] = nv; changed = True; rewritten_refs += 1
                for a in ("data-src","data-original","data-lazy","data-srcset"):
                    if tag.has_attr(a):
                        val = tag[a]
                        nv = replace_srcset(val, html_abs) if "srcset" in a else replace_single(val, html_abs)
                        if nv != val: tag[a] = nv; changed = True; rewritten_refs += 1

            elif tag.name == "source":
                for a in ("src","srcset","data-src","data-srcset"):
                    if tag.has_attr(a):
                        val = tag[a]
                        nv = replace_srcset(val, html_abs) if "srcset" in a else replace_single(val, html_abs)
                        if nv != val: tag[a] = nv; changed = True; rewritten_refs += 1

            elif tag.name == "link":
                if tag.has_attr("href"):
                    old = tag["href"]; nv = replace_single(old, html_abs)
                    if nv != old: tag["href"] = nv; changed = True; rewritten_refs += 1

            elif tag.name == "script":
                if tag.has_attr("src"):
                    old = tag["src"]; nv = replace_single(old, html_abs)
                    if nv != old: tag["src"] = nv; changed = True; rewritten_refs += 1

            elif tag.name == "video" and tag.has_attr("poster"):
                old = tag["poster"]; nv = replace_single(old, html_abs)
                if nv != old: tag["poster"] = nv; changed = True; rewritten_refs += 1

            elif tag.name == "input" and tag.get("type","").lower() == "image" and tag.has_attr("src"):
                old = tag["src"]; nv = replace_single(old, html_abs)
                if nv != old: tag["src"] = nv; changed = True; rewritten_refs += 1

            if tag.name == "image":
                for a in ("href","xlink:href"):
                    if tag.has_attr(a):
                        old = tag[a]; nv = replace_single(old, html_abs)
                        if nv != old: tag[a] = nv; changed = True; rewritten_refs += 1

            if tag.has_attr("style"):
                style_val = tag["style"]
                def repl(m):
                    inside = m.group(1).strip(' \'"')
                    new_u = replace_single(inside, html_abs)
                    return f"url({new_u})"
                new_style = re.sub(r"url\((.*?)\)", repl, style_val, flags=re.I)
                if new_style != style_val:
                    tag["style"] = new_style; changed = True; rewritten_refs += 1

        if changed:
            with open(html_abs, "w", encoding="utf-8") as f:
                f.write(str(soup))
        html_files_count += 1

log(f"Обработано HTML файлов: {html_files_count}")
log(f"Переписано ссылок/ресурсов: {rewritten_refs}")



# ========== чистим код и структурируем ==========
def clean_trash_code(file_path: str) -> bool:
    changed = False

    WAYBACK_TRASH_PATTERNS = [
        "web.archive.org",
        "web-static.archive.org",
        "/_static/",
        "/__wb/",
        "wombat.js",
        "bundle-playback.js",
        "athena.js",
        "ruffle.js",
        "banner-styles.css",
        "iconochive.css",
        "gmpg.org/xfn",
        "archive.org",
    ]

    INLINE_TRASH_PATTERNS = [
        "window.ruffleplayer",
        "athena.js",
        "wombat.js",
        "bundle-playback.js"
    ]

    def is_trash_url(url: str) -> bool:
        return url and any(p.lower() in url.lower() for p in WAYBACK_TRASH_PATTERNS)

    def looks_wayback_text(s: str) -> bool:
        sl = s.lower()
        return any(p in sl for p in WAYBACK_TRASH_PATTERNS) or "wayback" in sl

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            html = f.read()
    except FileNotFoundError:
        return False

    soup = BeautifulSoup(html, "html.parser")

    # ====== Чистим <script> и <link> ======
    for tag in list(soup.find_all(["script", "link"])):
        if not tag:
            continue

        url_attr = "src" if tag.name == "script" else "href"
        u = tag.get(url_attr)

        # Игнорируем wp-json
        if u and "wp-json" in u:
            continue

        # Удаляем Web Archive / Ruffle / Athena / Wombat
        if is_trash_url(u):
            tag.decompose()
            changed = True
            continue

        # Проверяем inline JS
        if tag.name == "script" and tag.string:
            if any(p.lower() in tag.string.lower() for p in INLINE_TRASH_PATTERNS):
                tag.decompose()
                changed = True
                continue

        # JSON скрипты не трогаем
        if tag.name == "script" and tag.get("type") == "application/ld+json":
            continue

    # ====== Удаляем комментарии и текст с Wayback ======
    for c in list(soup.find_all(string=lambda t: isinstance(t, (str, Comment)))):
        try:
            if looks_wayback_text(c):
                c.extract()
                changed = True
        except Exception:
            pass

    # ====== Сохраняем обратно ======
    if changed:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(str(soup))

    return changed


# Прогоняем по всем HTML в папке
def clean_trash_in_dir(site_dir: str):
    for root, dirs, files in os.walk(site_dir):
        for file in files:
            if file.lower().endswith(".html"):
                file_path = os.path.join(root, file)
                if clean_trash_code(file_path):
                    print(f"✔ Почистил мусор Wayback в {file_path}")


HTML_EXTS = (".html", ".htm", ".xhtml")

def consolidate_meta(file_path: str) -> bool:
    changed = False

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            html = f.read()
    except Exception as e:
        print(f"⚠ Ошибка при открытии {file_path}: {e}")
        return False

    soup = BeautifulSoup(html, "html.parser")

    # создаём head если нет
    if not soup.head:
        head = soup.new_tag("head")
        if soup.html:
            soup.html.insert(0, head)
        else:
            soup.insert(0, head)
        changed = True
    else:
        head = soup.head

    # собираем все meta
    all_meta = soup.find_all("meta")
    if not all_meta:
        return False

    # удаляем из документа
    for m in all_meta:
        m.extract()
        changed = True

    # создаём временный контейнер и собираем все meta туда
    temp_soup = BeautifulSoup("", "html.parser")
    for m in all_meta:
        temp_soup.append(m)
        temp_soup.append("\n")  # перенос строки после каждого meta

    # вставляем блок после <title> или в начало head
    title_tag = head.title
    if title_tag:
        title_tag.insert_after(temp_soup)
    else:
        head.insert(0, temp_soup)

    # сохраняем обратно
    if changed:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(str(soup))

    return changed



def consolidate_meta_in_dir(site_dir: str):
    for root, dirs, files in os.walk(site_dir):
        for file in files:
            if not file.lower().endswith(HTML_EXTS):
                continue
            file_path = os.path.join(root, file)
            if consolidate_meta(file_path):
                print(f"✔ Meta собраны в head: {file_path}")
            else:
                print(f"— Изменений не было: {file_path}")


clean_trash_in_dir(site_dir)
consolidate_meta_in_dir(site_dir)



# ========= переписываем пути CSS: url(...) и @import
log_step("Переписываем пути в CSS (@import, url(...))")

def css_rewrite_paths(css_abs: str):
    with open(css_abs, "r", encoding="utf-8", errors="ignore") as f:
        txt = f.read()
    changed = False

    def repl_import(m):
        nonlocal changed
        urlq = (m.group(1) or m.group(2)).strip().strip('"\'')
        clean = strip_wayback(urlq)
        key = url_basename(clean).lower()

        if key in css_map:
            target = css_map[key]
        elif clean in css_map:
            target = css_map[clean]
        else:
            return m.group(0)

        new_rel = os.path.relpath(
            os.path.join(site_abs, target),
            start=os.path.dirname(css_abs)
        )
        changed = True
        return f'@import "{norm_slashes(new_rel)}";'

    txt2 = re.sub(
        r'@import\s+(?:(?:url\(\s*[\'"]?(.*?)[\'"]?\s*\))|[\'"](.*?)[\'"])\s*;',
        repl_import, txt, flags=re.I
    )

    # url(...)
    def repl_url(m):
        nonlocal changed
        inside = m.group(1).strip().strip('"\'')
        clean = strip_wayback(inside).split("#", 1)[0].split("?", 1)[0]
        key = url_basename(clean).lower()

        if key in img_map:
            target = img_map[key]
        elif clean in img_map:
            target = img_map[clean]
        elif key in font_map:
            target = font_map[key]
        elif clean in font_map:
            target = font_map[clean]
        else:
            if clean != inside:
                changed = True
                return f'url({clean})'
            return m.group(0)

        new_rel = os.path.relpath(
            os.path.join(site_abs, target),
            start=os.path.dirname(css_abs)
        )
        changed = True
        return f'url({norm_slashes(new_rel)})'

    txt3 = re.sub(r'url\((.*?)\)', repl_url, txt2, flags=re.I)

    if txt3 != txt:
        changed = True
        with open(css_abs, "w", encoding="utf-8") as f:
            f.write(txt3)
    return changed


css_changed_count = 0
for fname in os.listdir(assets_css):
    if is_css(fname):
        changed = css_rewrite_paths(os.path.join(assets_css, fname))
        if changed:
            css_changed_count += 1
            log(f"CSS обновлён: {fname}")

log(f"CSS файлов переписано: {css_changed_count}")







# ======== удалим мусорные папки и файлы
def prepare_site_structure(site_base: str = "site"):
    site_abs = os.path.abspath(site_base)
    domain_root = find_domain_root(site_abs)
    if not domain_root:
        raise RuntimeError("Не удалось найти корень сайта")

    if os.path.abspath(domain_root) != site_abs:
        merge_dir(domain_root, site_abs)
        shutil.rmtree(domain_root, ignore_errors=True)

    trash_dirs = {"wp-content", "wp-includes", "analytics", "google-analytics", "tagmanager", "www.googletagmanager.com", "wp-json"}
    trash_exts = {".php", ".asp", ".jsp"}

    for root, dirs, files in os.walk(site_abs, topdown=True):
        # удаляем папки-мусор
        for d in dirs[:]:
            if d.lower() in trash_dirs:
                p = os.path.join(root, d)
                shutil.rmtree(p, ignore_errors=True)
                log(f"Удалена папка мусор: {p}")
                dirs.remove(d)

        # удаляем файлы-мусор
        for f in files:
            if any(f.lower().endswith(ext) for ext in trash_exts):
                p = os.path.join(root, f)
                try:
                    os.remove(p)
                    log(f"Удалён файл мусор: {p}")
                except Exception as e:
                    log_warn(f"Не удалось удалить {p}: {e}")

    log(f"Структура сайта подготовлена: {site_base}")

prepare_site_structure("site")






# ========= Удалим пустые папки
log_step("Удаляем пустые папки")
removed_dirs = 0
for root, dirs, files in os.walk(site_abs, topdown=False):
    for d in dirs:
        p = os.path.join(root, d)
        if os.path.abspath(p).startswith(os.path.abspath(assets_dir)):
            continue
        try:
            if not os.listdir(p):
                os.rmdir(p)
                removed_dirs += 1
                log(f"Папка удалена: {p}")
        except OSError as e:
            log_warn(f"Не удалось удалить '{p}': {e}")





# ======= генерация sitemap и robots
def generate_robots_and_sitemap(site_base: str = "site", domain: str = None):

    site_abs = os.path.abspath(site_base)

    # --- robots.txt ---
    robots_path = os.path.join(site_abs, "robots.txt")
    with open(robots_path, "w", encoding="utf-8") as f:
        f.write(f"""User-agent: *
Allow: /
Sitemap: https://{domain}/sitemap.xml
""")
    log(f"robots.txt создан: {robots_path}")

    # --- sitemap.xml ---
    urls = []
    today = datetime.today().strftime("%Y-%m-%d")

    for root, dirs, files in os.walk(site_abs):
        for fname in files:
            if fname.lower().endswith((".html", ".htm")):
                abs_path = os.path.join(root, fname)
                rel_path = os.path.relpath(abs_path, site_abs)
                rel_url = rel_path.replace(os.sep, "/")
                if fname.lower() in ("index.html", "index.htm"):
                    if os.path.dirname(rel_url) == "":
                        loc = f"https://{domain}/"
                    else:
                        folder = os.path.dirname(rel_url)
                        loc = f"https://{domain}/{folder}/"
                else:
                    loc = f"https://{domain}/{rel_url}"

                urls.append((loc, today))

    sitemap_path = os.path.join(site_abs, "sitemap.xml")
    with open(sitemap_path, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n')
        for loc, lastmod in urls:
            f.write("    <url>\n")
            f.write(f"        <loc>{loc}</loc>\n")
            f.write(f"        <lastmod>{lastmod}</lastmod>\n")
            f.write("        <changefreq>weekly</changefreq>\n")
            f.write("        <priority>0.8</priority>\n")
            f.write("    </url>\n")
        f.write("</urlset>\n")

    log(f"sitemap.xml создан: {sitemap_path} (всего {len(urls)} страниц)")

generate_robots_and_sitemap("site")




RESOURCE_EXTS = {
    ".css", ".js", ".mjs", ".json", ".map",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico",
    ".ttf", ".otf", ".eot", ".woff", ".woff2",
    ".mp3", ".mp4", ".webm", ".ogg",
    ".pdf", ".txt", ".xml", ".rss",
}

MAX_STRIP_PREFIX_SEGMENTS = 2

def url_host(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc.lower()
    except Exception:
        return ""

def same_site(host: str, base_host: str) -> bool:
    if not host:
        return False
    host = host.lower()
    base_host = base_host.lower()
    return host == base_host or host.endswith("." + base_host)

def build_file_map(site_dir: str):
    files = set()
    page_dirs = set()
    for root, _, fnames in os.walk(site_dir):
        for fn in fnames:
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, site_dir)
            rel = norm_slashes(rel)
            files.add(rel)
            if fn.lower() == "index.html":
                page_dirs.add(norm_slashes(os.path.relpath(root, site_dir)))
    return files, page_dirs

def is_resource_path(path_no_qf: str) -> bool:
    _, ext = os.path.splitext(path_no_qf.lower())
    return ext in RESOURCE_EXTS

def split_qf(url: str):
    parsed = urllib.parse.urlparse(url)
    base = urllib.parse.urlunparse((
        parsed.scheme, parsed.netloc, parsed.path, "", "", ""
    ))
    return base, parsed.query, parsed.fragment

def clean_root_path(p: str) -> str:
    p = p.lstrip("/")
    norm = posixpath.normpath(p)
    return "" if norm == "." else norm

def try_variants_in_map(rel_path: str, file_map: set) -> str | None:
    rel_path = norm_slashes(rel_path)

    if rel_path in file_map:
        return rel_path

    if rel_path.endswith("/") and (rel_path + "index.html") in file_map:
        return rel_path + "index.html"

    base = posixpath.basename(rel_path)
    if "." not in base:
        candidate = rel_path.rstrip("/") + "/index.html"
        if candidate in file_map:
            return candidate

    if not rel_path.lower().endswith(".html") and (rel_path + ".html") in file_map:
        return rel_path + ".html"

    return None

def resolve_local_target(rel_path: str, file_map: set) -> str | None:
    hit = try_variants_in_map(rel_path, file_map)
    if hit:
        return hit

    parts = [p for p in rel_path.split("/") if p]
    for cut in range(1, min(MAX_STRIP_PREFIX_SEGMENTS, len(parts)) + 1):
        candidate = "/".join(parts[cut:])
        hit = try_variants_in_map(candidate, file_map)
        if hit:
            return hit

    return None

def make_relative(from_file: str, to_rel: str) -> str:
    from_dir = Path(from_file).parent
    target = Path(site_dir) / to_rel
    rel = os.path.relpath(target, from_dir)
    return norm_slashes(rel)

def should_skip_scheme(url: str) -> bool:
    if not url or url.startswith("#"):
        return True
    low = url.lower()
    return low.startswith(("mailto:", "tel:", "javascript:", "data:", "blob:", "about:"))

def fix_local_links_in_site(site_dir: str, domain: str):
    file_map, page_dirs = build_file_map(site_dir)

    base_host = url_host(domain)
    if not base_host:
        print("Введён домен без хоста. Пример корректного ввода: https://example.com")
        return

    total_files = 0
    changed_links = 0

    for root, _, files in os.walk(site_dir):
        for file in files:
            if not file.lower().endswith(".html"):
                continue

            file_path = os.path.join(root, file)
            total_files += 1

            with open(file_path, "r", encoding="utf-8") as f:
                html = f.read()

            soup = BeautifulSoup(html, "html.parser")

            def rewrite_attr(tag, attr):
                nonlocal changed_links
                val = tag.get(attr)
                if not val or should_skip_scheme(val):
                    return
                val = strip_wayback(val)
                parsed = urllib.parse.urlparse(val)
                if parsed.scheme in ("http", "https"):
                    if not same_site(parsed.netloc, base_host):
                        return
                elif val.startswith("//"):
                    host2 = url_host("https:" + val)
                    if not same_site(host2, base_host):
                        return
                if parsed.scheme in ("http", "https") or val.startswith("//"):
                    root_rel = parsed.path
                elif val.startswith("/"):
                    root_rel = parsed.path
                else:
                    return

                base_no_qf, _query, fragment = split_qf(root_rel)

                cleaned = clean_root_path(base_no_qf)

                target_rel = None
                if is_resource_path(cleaned):
                    if cleaned in file_map:
                        target_rel = cleaned
                else:
                    target_rel = resolve_local_target(cleaned, file_map)

                if not target_rel:
                    return

                rel_out = make_relative(file_path, target_rel)

                if fragment:
                    rel_out = f"{rel_out}#{fragment}"

                if rel_out != val:
                    tag[attr] = rel_out
                    changed_links += 1

            # Переписываем href/src
            for a in soup.find_all(href=True):
                rewrite_attr(a, "href")
            for s in soup.find_all(src=True):
                rewrite_attr(s, "src")

            with open(file_path, "w", encoding="utf-8") as f:
                f.write(str(soup))

            print(f"✔ Обработан: {norm_slashes(os.path.relpath(file_path, site_dir))}")

    print("\n===== Готово =====")
    print(f"HTML-файлов обработано: {total_files}")
    print(f"Ссылок переписано:      {changed_links}")

fix_local_links_in_site(site_dir, domain_full)





# --------- Минификация

def _strip_html_comments_but_keep_conditionals(html: str) -> str:
    return re.sub(r"<!--(?!\s*\[if\b).*?-->", "", html, flags=re.DOTALL | re.IGNORECASE)

def _strip_css_comments_keep_licenses(css: str) -> str:
    def repl(m):
        comment = m.group(0)
        return comment if comment.startswith("/*!") else ""
    return re.sub(r"/\*.*?\*/", repl, css, flags=re.DOTALL)

def _strip_js_comments_preserve_strings(js: str) -> str:
    OUT, SQUOTE, DQUOTE, TEMPLATE, REGEX, LINE_COMMENT, BLOCK_COMMENT = range(7)
    state = OUT
    out = []
    i = 0
    escaped = False

    def is_regex_context(prev_non_ws):
        return prev_non_ws in (None, '(', '[', '{', ',', ':', '=', '!', '+', '-', '*', '%', '&', '|', '^', '~', '?', '<', '>', ';')

    prev_non_ws = None

    while i < len(js):
        ch = js[i]
        nxt = js[i+1] if i+1 < len(js) else ''

        if state == OUT:
            if ch in ' \t\r\n':
                out.append(ch)
                i += 1
                continue

            if ch == '/' and nxt == '/':
                state = LINE_COMMENT
                i += 2
                continue
            if ch == '/' and nxt == '*':
                state = BLOCK_COMMENT
                i += 2
                continue

            if ch == "'":
                state = SQUOTE; out.append(ch); i += 1; escaped = False; continue
            if ch == '"':
                state = DQUOTE; out.append(ch); i += 1; escaped = False; continue
            if ch == '`':
                state = TEMPLATE; out.append(ch); i += 1; escaped = False; continue

            if ch == '/' and is_regex_context(prev_non_ws):
                state = REGEX; out.append(ch); i += 1; escaped = False; continue

            out.append(ch)
            if not ch.isspace():
                prev_non_ws = ch
            i += 1
            continue

        elif state == LINE_COMMENT:
            if ch in '\r\n':
                out.append(ch)
                state = OUT
            i += 1
            continue

        elif state == BLOCK_COMMENT:
            # сохраняем лицензионные /*! ... */ комментарии
            if js[i-2:i] == '/*' and i-2 >= 0 and js[i-2:i] == '/*' and (i-2 == 0 or js[i-3] != '!'):
                pass
            if ch == '*' and nxt == '/':
                # если это /*! ... */, вернём его
                start = i
                # найдём начало комментария назад
                j = i - 1
                is_license = False
                while j >= 1:
                    if js[j-1:j+1] == '/*':
                        is_license = (j < len(js) and j < len(js) and js[j] == '!')
                        break
                    j -= 1
                if is_license:
                    # вставляем исходный комментарий назад
                    out.append('/*' + js[j+1:i] + '*/')
                i += 2
                state = OUT
            else:
                i += 1
            continue

        elif state in (SQUOTE, DQUOTE, TEMPLATE, REGEX):
            out.append(ch)
            if state == SQUOTE:
                if not escaped and ch == "'":
                    state = OUT
                escaped = (not escaped and ch == '\\')
            elif state == DQUOTE:
                if not escaped and ch == '"':
                    state = OUT
                escaped = (not escaped and ch == '\\')
            elif state == TEMPLATE:
                if not escaped and ch == '`':
                    state = OUT
                escaped = (not escaped and ch == '\\')
            elif state == REGEX:
                if not escaped and ch == '/':
                    state = OUT
                escaped = (not escaped and ch == '\\')
            i += 1
            continue

    return ''.join(out)

def minify_code(content: str, filetype: str) -> str:
    if filetype in ("html", "htm"):
        content = _strip_html_comments_but_keep_conditionals(content)
    elif filetype == "css":
        content = _strip_css_comments_keep_licenses(content)
    elif filetype == "js":
        content = _strip_js_comments_preserve_strings(content)
    content = re.sub(r"\n[ \t]+\n", "\n\n", content)
    content = re.sub(r"\n{3,}", "\n\n", content)
    return content.strip()

def run_minification(site_abs: str):
    changed_files = 0
    for root, _, files in os.walk(site_abs):
        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in (".html", ".htm", ".css", ".js"):
                continue
            # пропускаем уже минифицированные
            low = fname.lower()
            if low.endswith(".min.css") or low.endswith(".min.js"):
                continue

            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    original = f.read()
                kind = "html" if ext in (".html", ".htm") else "css" if ext == ".css" else "js"
                minimized = minify_code(original, kind)
                if minimized != original and minimized.strip():
                    with open(fpath, "w", encoding="utf-8") as f:
                        f.write(minimized)
                    changed_files += 1
                    log(f"Minified: {os.path.relpath(fpath)}")
            except Exception as e:
                log_warn(f"Минификация провалена для {fpath}: {e}")

    log(f"Итого минифицировано файлов: {changed_files}")

run_minification(site_abs)




log_step("ГОТОВО")

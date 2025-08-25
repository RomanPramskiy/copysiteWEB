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
def map_put(m, fname, path):
    # чистое имя без ?#
    clean_name = urllib.parse.unquote(fname.split("?", 1)[0].split("#", 1)[0])
    clean_low = clean_name.lower()

    # сохраняем как есть
    m[clean_low] = path

    # если в имени есть @ver=... — делаем алиас без него
    if "@ver=" in clean_low:
        base_name = clean_low.split("@ver=", 1)[0] + os.path.splitext(clean_low)[1]
        m[base_name] = path



def norm_slashes(path: str) -> str:
    return os.path.normpath(path).replace("\\", "/")



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

    root_files = {f.lower() for f in os.listdir(site_dir_abs) if os.path.isfile(os.path.join(site_dir_abs, f))}
    if "index.html" in root_files or "index.htm" in root_files:
        return site_dir_abs

    candidates = []
    for root, dirs, files in os.walk(site_dir_abs):
        lower = {f.lower() for f in files}
        if "index.html" in lower or "index.htm" in lower:
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

    if domain.startswith("www."):
        domain = domain[4:]

    return domain


# =======  глобальные переменные
domain_full = input("Введите домен (например: https://mizura-vugalo.sbs): ").strip()
domain = clean_domain(domain_full)
site_dir = "site"
site_abs = os.path.abspath(site_dir)
script_name = os.path.basename(__file__)
HTML_EXTS = (".html", ".htm", ".xhtml")


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
    print(f"Найден root: {domain_root}")
    merge_dir(domain_root, site_abs)
    try:
        shutil.rmtree(domain_root)
    except Exception as e:
        print(f"Не удалось удалить {domain_root}: {e}")




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
moved_map = {}
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
        canon = seen[h]
        try:
            os.remove(fpath)
            removed += 1
            log(f"DUPE удалён: {fname} → используем {canon}")
            mapping[fname.lower()] = norm_slashes(os.path.join(rel_prefix, canon))
        except Exception as e:
            log_warn(f"Не удалось удалить дубликат '{fname}': {e}")
    if removed == 0:
        log("Дубликатов не обнаружено.")
    else:
        log(f"Удалено дубликатов: {removed}")


dedupe_folder(assets_img,   os.path.join("assets","images"), img_map)
dedupe_folder(assets_css,   os.path.join("assets","css"),    css_map)
dedupe_folder(assets_js,    os.path.join("assets","js"),     js_map)
dedupe_folder(assets_fonts, os.path.join("assets","fonts"),  font_map)





# ========= Оптимизация изображений
def optimize_image(path: str) -> tuple[bool, int, int]:
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
                im.save(temp_path, format="JPEG", optimize=True, progressive=True)
            elif fmt == "PNG":
                im.save(temp_path, format="PNG", optimize=True)
            elif fmt == "WEBP":
                im.save(temp_path, format="WEBP", method=4)
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
            log(f"Оптимизировано {fname}: {old_s} → {new_s} байт (-{saved})")
    if saved_total:
        log(f"Итого экономия: {saved_total} байт")
    else:
        log("Экономии нет или все изображения уже оптимальны.")





def externalize_built_in_styles(site_base="site", strict_per_file=True, class_prefix="bi"):
    site_abs = os.path.abspath(site_base)
    assets_css_dir = os.path.join(site_abs, "assets", "css")
    os.makedirs(assets_css_dir, exist_ok=True)

    def _norm(p: str) -> str:
        return p.replace("\\", "/")

    processed = 0

    for root, _, files in os.walk(site_abs):
        for fname in files:
            if not fname.lower().endswith((".html", ".htm", ".xhtml")):
                continue

            html_abs = os.path.join(root, fname)
            try:
                with open(html_abs, "r", encoding="utf-8", errors="ignore") as f:
                    html = f.read()
            except Exception as e:
                print(f"[built-in][WARN] Не зміг відкрити {html_abs}: {e}")
                continue

            soup = BeautifulSoup(html, "html.parser")

            style_chunks = []
            for st in soup.find_all("style"):
                t = (st.get("type") or "text/css").lower()
                if t not in ("", "text/css", "css"):
                    continue
                css_text = st.string if st.string is not None else st.get_text()
                if css_text and css_text.strip():
                    style_chunks.append(css_text.strip())
                st.decompose()

            inline_map = {}
            for el in soup.find_all(True):
                if not el.has_attr("style"):
                    continue
                style_text = el["style"].strip().rstrip(";")
                if not style_text:
                    del el["style"]
                    continue
                key = re.sub(r"\s+", " ", style_text)
                if key not in inline_map:
                    h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]
                    inline_map[key] = f"{class_prefix}-{h}"
                clsname = inline_map[key]

                existing = el.get("class")
                if isinstance(existing, list):
                    classes = existing
                elif isinstance(existing, str):
                    classes = existing.split()
                else:
                    classes = []
                if clsname not in classes:
                    classes.append(clsname)
                    el["class"] = classes

                del el["style"]

            css_parts = []
            if style_chunks:
                css_parts.append("/* extracted <style> blocks */\n" + "\n\n".join(style_chunks))
            if inline_map:
                rules = [f".{cls} {{{rules}}}" for rules, cls in inline_map.items()]
                css_parts.append("/* rules created from style=\"...\" */\n" + "\n".join(rules))
            css_text_final = ("\n\n".join(css_parts)).strip()
            if not css_text_final:
                rel_html = _norm(os.path.relpath(html_abs, site_abs))
                dir_rel = os.path.dirname(rel_html)
                html_base = os.path.splitext(os.path.basename(rel_html))[0]
                if dir_rel == "":
                    css_name = f"{html_base}-built-in.css"
                else:
                    folder = os.path.basename(dir_rel.rstrip("/"))
                    css_name = f"{folder}-{html_base}-built-in.css" if strict_per_file else f"{folder}-built-in.css"
                css_abs = os.path.join(assets_css_dir, css_name)
                rel_href = _norm(os.path.relpath(css_abs, start=os.path.dirname(html_abs)))
                head = soup.head or soup.new_tag("head")
                if not soup.head:
                    (soup.html or soup).insert(0, head)
                updated = False
                for lnk in head.find_all("link", href=True):
                    rels = [r.lower() for r in (lnk.get("rel") or [])]
                    if "stylesheet" in rels and os.path.basename(_norm(lnk["href"])) == css_name:
                        if _norm(lnk["href"]) != rel_href:
                            lnk["href"] = rel_href
                        updated = True
                        break
                if not updated:
                    head.append(soup.new_tag("link", rel="stylesheet", href=rel_href))
                with open(html_abs, "w", encoding="utf-8") as f:
                    f.write(str(soup))
                continue

            rel_html = _norm(os.path.relpath(html_abs, site_abs))
            dir_rel = os.path.dirname(rel_html)
            html_base = os.path.splitext(os.path.basename(rel_html))[0]
            if dir_rel == "":
                css_name = f"{html_base}-built-in.css"
            else:
                folder = os.path.basename(dir_rel.rstrip("/"))
                css_name = f"{folder}-{html_base}-built-in.css" if strict_per_file else f"{folder}-built-in.css"

            css_abs = os.path.join(assets_css_dir, css_name)
            rel_href = _norm(os.path.relpath(css_abs, start=os.path.dirname(html_abs)))

            with open(css_abs, "w", encoding="utf-8") as cf:
                cf.write(css_text_final + "\n")

            head = soup.head
            if not head:
                head = soup.new_tag("head")
                if soup.html:
                    soup.html.insert(0, head)
                else:
                    soup.insert(0, head)

            href_patched = False
            for lnk in head.find_all("link", href=True):
                rels = [r.lower() for r in (lnk.get("rel") or [])]
                if "stylesheet" in rels and os.path.basename(_norm(lnk["href"])) == css_name:
                    lnk["href"] = rel_href
                    href_patched = True
                    break
            if not href_patched:
                head.append(soup.new_tag("link", rel="stylesheet", href=rel_href))

            with open(html_abs, "w", encoding="utf-8") as f:
                f.write(str(soup))

            processed += 1
            print(f"[built-in] {_norm(rel_html)} → assets/css/{css_name}")

    print(f"[built-in] Готово. Оброблено HTML: {processed}")

externalize_built_in_styles("site", strict_per_file=True, class_prefix="bi")



# =========== Перезапись путей
RESOURCE_EXTS = {
    ".css", ".js", ".mjs", ".json", ".map",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico",
    ".ttf", ".otf", ".eot", ".woff", ".woff2",
    ".mp3", ".mp4", ".webm", ".ogg",
    ".pdf", ".txt", ".xml", ".rss",
}

MAX_STRIP_PREFIX_SEGMENTS = 2


def build_file_map(site_dir: str):
    file_map = {}
    page_dirs = set()
    for root, _, files in os.walk(site_dir):
        for file in files:
            full_path = os.path.join(root, file)
            rel_path = norm_slashes(os.path.relpath(full_path, site_dir))
            file_map[rel_path.lower()] = rel_path
            if file.lower().endswith(HTML_EXTS):
                page_dirs.add(os.path.dirname(rel_path))
    return file_map, page_dirs

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


def extract_path_after_domain(url_str: str, base_host: str) -> tuple[str | None, str | None]:
    s = url_str
    s_low = s.lower()
    host_low = base_host.lower()
    i = s_low.find(host_low)
    if i == -1:
        return None, None
    k = i + len(base_host)
    if k < len(s) and s[k] == ":":
        k += 1
        while k < len(s) and s[k].isdigit():
            k += 1
    slash_pos = s.find("/", k)
    if slash_pos == -1:
        path_plus = "/"
    else:
        path_plus = s[slash_pos:]

    base_no_qf, _q, frag = split_qf(path_plus)
    return base_no_qf or "/", frag



def rewrite_srcset(tag, img_map, file_path):
    if not tag.has_attr("srcset"):
        return

    srcset_val = tag["srcset"]
    new_parts = []

    for part in srcset_val.split(","):
        part = part.strip()
        if not part:
            continue

        segments = part.split()
        url = segments[0]
        size = " ".join(segments[1:]) if len(segments) > 1 else ""

        file_name = os.path.basename(url)
        clean_name = urllib.parse.unquote(file_name)

        if clean_name.lower() in img_map:
            new_url = make_relative(file_path, img_map[clean_name.lower()])
        else:
            new_url = url

        new_parts.append(f"{new_url} {size}".strip())

    tag["srcset"] = ", ".join(new_parts)


def rewrite_srcset_call(soup, img_map, file_path):
    for img in soup.find_all("img"):
        # переписываем обычный src
        if img.has_attr("src"):
            file_name = os.path.basename(img["src"])
            clean_name = urllib.parse.unquote(file_name).lower()
            if clean_name in img_map:
                img["src"] = make_relative(file_path, img_map[clean_name])

        # переписываем srcset через функцию
        rewrite_srcset(img, img_map, file_path)


for root, _, files in os.walk(site_dir):
    for file in files:
        if not file.lower().endswith(HTML_EXTS):
            continue

        file_path = os.path.join(root, file)

        with open(file_path, "r", encoding="utf-8") as f:
            html = f.read()

        soup = BeautifulSoup(html, "html.parser")
        rewrite_srcset_call(soup, img_map, file_path)

        # и не забудь записать обратно!
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(str(soup))



ROOT_DIR = "site"
HTML_EXTS = (".html", ".htm")

LINK_RE = re.compile(r'''(src|href)\s*=\s*["']([^"'?#]+)(\?[^"'#]+)?(["'])''', re.IGNORECASE)

RESOURCE_EXTS = (".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".bmp", ".ttf", ".woff", ".woff2", ".eot", ".mp4", ".webm")


def fix_filename(url: str, query: str | None) -> str:
    filename = os.path.basename(url)
    if query:
        dot_index = filename.rfind(".")
        if dot_index != -1:
            filename = f"{filename[:dot_index]}{filename[dot_index:]}@{query.lstrip('?')}{filename[dot_index:]}"
        else:
            filename = f"{filename}@{query.lstrip('?')}"
    return filename


def process_html_file(file_path: str):
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        html = f.read()

    def replacer(match):
        attr, url, query, quote = match.groups()
        filename = fix_filename(url, query)
        return f'{attr}="{filename}"'

    new_html = LINK_RE.sub(replacer, html)

    if new_html != html:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(new_html)
        print(f"Обновлено: {file_path}")


def process_resources_in_html(file_path: str):
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        html = f.read()

    def replacer(match):
        attr, url, query, quote = match.groups()

        ext = os.path.splitext(url)[1].lower()
        if not ext:
            return match.group(0)

        if not url.lower().endswith(RESOURCE_EXTS):
            return match.group(0)

        filename = fix_filename(url, query)

        if ext in (".css",):
            filename = f"assets/css/{filename}"
        elif ext in (".js",):
            filename = f"assets/js/{filename}"
        elif ext in (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".bmp"):
            filename = f"images/{filename}"

        return f'{attr}="{filename}"'

    new_html = LINK_RE.sub(replacer, html)

    if new_html != html:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(new_html)
        print(f"Ресурсы обновлены: {file_path}")


# ================== ТВОЙ МЕТОД ДЛЯ СТРАНИЦ ==================
def fix_local_links_in_site(site_dir: str, domain: str):
    file_map, _page_dirs = build_file_map(site_dir)
    total_files = 0
    changed_links = 0



    for root, _, files in os.walk(site_dir):
        for file in files:
            if not file.lower().endswith(HTML_EXTS):
                continue

            file_path = os.path.join(root, file)
            total_files += 1

            with open(file_path, "r", encoding="utf-8") as f:
                html = f.read()

            soup = BeautifulSoup(html, "html.parser")

            def rewrite_attr(tag, attr):
                nonlocal changed_links
                val = tag.get(attr)
                if not val:
                    return
                if should_skip_scheme(val):
                    return

                if domain_full.lower() not in val.lower():
                    return
                path_after, fragment = extract_path_after_domain(val, domain_full)
                if not path_after:
                    return
                cleaned = clean_root_path(path_after)
                target_rel = None
                if cleaned == "":
                    for cand in ("index.html", "index.htm"):
                        if cand in file_map:
                            target_rel = cand
                            break
                if not target_rel:
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

            for t in soup.find_all(href=True):
                rewrite_attr(t, "href")
            for t in soup.find_all(src=True):
                rewrite_attr(t, "src")

            with open(file_path, "w", encoding="utf-8") as f:
                f.write(str(soup))

            print(f"✔ Обработан: {norm_slashes(os.path.relpath(file_path, site_dir))}")

    print("\n===== Готово =====")
    print(f"HTML-файлов обработано: {total_files}")
    print(f"Ссылок переписано:      {changed_links}")


def walk_and_fix(root: str, domain: str):
    for dirpath, _, filenames in os.walk(root):
        for fn in filenames:
            if fn.lower().endswith(HTML_EXTS):
                abs_path = os.path.join(dirpath, fn)

                process_resources_in_html(abs_path)

    fix_local_links_in_site(root, domain)



walk_and_fix(ROOT_DIR, domain)







ROOT_DIR = "site"
HTML_EXTS = (".html", ".htm")

def normalize_css_filename(href: str, html_path: str, site_root: str = ROOT_DIR) -> str:
    href = re.sub(r"^https?://web\.archive\.org/web/\d+[^/]+/", "", href)

    href = re.sub(r"^https?:\/\/", "", href)

    base = os.path.basename(href)

    safe_name = base.replace("?", "@").replace("&", ",")

    if not safe_name.lower().endswith(".css"):
        safe_name += ".css"

    html_dir = os.path.dirname(os.path.relpath(html_path, site_root))
    depth = 0 if html_dir == "" else html_dir.count(os.sep) + 1
    prefix = "../" * depth

    return f"{prefix}assets/css/{safe_name}"


def process_html_file(file_path: str):
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        html = f.read()

    soup = BeautifulSoup(html, "html.parser")
    head = soup.find("head")
    if not head:
        print(f"Нет <head>: {file_path}")
        return

    new_links = []
    for link in soup.find_all("link", rel="stylesheet"):
        href = link.get("href")
        if not href:
            continue

        if href.lower().endswith("-built-in.css"):
            continue

        new_href = normalize_css_filename(href, file_path, ROOT_DIR)
        new_link = soup.new_tag("link", rel="stylesheet", href=new_href)
        new_links.append(new_link)

    for old_link in head.find_all("link", rel="stylesheet"):
        old_link.decompose()

    for l in new_links:
        head.append(l)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(str(soup))

    print(f"✔ Стили обновлены: {file_path}")


def process_site():
    for root, _, files in os.walk(ROOT_DIR):
        for fn in files:
            if fn.lower().endswith(HTML_EXTS):
                process_html_file(os.path.join(root, fn))


if __name__ == "__main__":
    process_site()


def consolidate_meta(file_path: str) -> bool:
    changed = False
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            html = f.read()
    except Exception as e:
        print(f"Ошибка при открытии {file_path}: {e}")
        return False
    soup = BeautifulSoup(html, "html.parser")
    if not soup.head:
        head = soup.new_tag("head")
        if soup.html:
            soup.html.insert(0, head)
        else:
            soup.insert(0, head)
        changed = True
    else:
        head = soup.head
    all_meta = soup.find_all("meta")
    if not all_meta:
        return False
    for m in all_meta:
        m.extract()
        changed = True
    temp_soup = BeautifulSoup("", "html.parser")
    for m in all_meta:
        temp_soup.append(m)
        temp_soup.append("\n")
    title_tag = head.title
    if title_tag:
        title_tag.insert_after(temp_soup)
    else:
        head.insert(0, temp_soup)
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
                print(f"Meta собраны в head: {file_path}")
            else:
                print(f"Изменений не было: {file_path}")


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

    trash_dirs = {"analytics", "google-analytics", "tagmanager", "www.googletagmanager.com", "wp-json"}
    trash_exts = {".asp", ".jsp"}

    for root, dirs, files in os.walk(site_abs, topdown=True):
        for d in dirs[:]:
            if d.lower() in trash_dirs:
                p = os.path.join(root, d)
                shutil.rmtree(p, ignore_errors=True)
                log(f"Удалена папка мусор: {p}")
                dirs.remove(d)
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

generate_robots_and_sitemap("site", domain)



# ============= Очистка кода
def clean_code(soup: BeautifulSoup) -> BeautifulSoup:
    for tag_id in ["wm-ipp-base", "wm-capinfo", "wm-logo", "wm-ipp-print", "www.googletagmanager.com"]:
        t = soup.find(id=tag_id)
        if t:
            t.decompose()

    for s in soup.find_all("script", src=True):
        if "web.archive.org" in s["src"] or "/_static/" in s["src"]:
            s.decompose()
    for s in soup.find_all("script"):
        if "WaybackMachine" in str(s) or "archive.org" in str(s):
            s.decompose()

    for link in soup.find_all("link", href=True):
        href = link["href"]
        rel = link.get("rel", [])
        type_attr = link.get("type", "")

        if (
            "/_static/" in href
            or "web.archive.org" in href
            or ("profile" in [r.lower() for r in rel])
            or (
                rel 
                and "alternate" in [r.lower() for r in rel] 
                and type_attr in ["application/json+oembed", "text/xml+oembed"]
            )
        ):
            link.decompose()

    for link in soup.find_all("link", rel=True):
        rels = [r.lower() for r in link.get("rel", [])]
        if any(r in ["dns-prefetch", "preconnect", "preload"] for r in rels):
            link.decompose()


    for style in soup.find_all("style", id=True):
        if "wm" in style["id"].lower():
            style.decompose()


    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        if "WAYBACK" in comment or "web.archive" in comment.lower():
            comment.extract()

    for tag in soup.find_all(True):
        attrs_to_remove = [a for a in tag.attrs if a.startswith("data-wm") or a.startswith("wm-")]
        for attr in attrs_to_remove:
            del tag[attr]

    for link in soup.find_all("link", rel=True):
        if "profile" in [r.lower() for r in link.get("rel", [])]:
            link.decompose()


    for s in soup.find_all("script"):
        s_text = str(s)
        if "RufflePlayer" in s_text:
            s.decompose()
            continue

        if "dataLayer" in s_text or "gtm4wp_datalayer_name" in s_text:
            s.decompose()
            continue

        if s.has_attr("data-cfasync") or s.has_attr("data-pagespeed-no-defer"):
            s.decompose()
            continue

    return soup


def clean_all_html_in_site():
    root_dir = os.path.join(os.getcwd(), "site")

    if not os.path.exists(root_dir):
        print(f"Папка '{root_dir}' не найдена!")
        return

    total_files = 0

    for subdir, _, files in os.walk(root_dir):
        for file in files:
            if file.lower().endswith(".html"):
                file_path = os.path.join(subdir, file)

                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        html = f.read()

                    soup = BeautifulSoup(html, "lxml")
                    soup = clean_code(soup)

                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write(str(soup))

                    total_files += 1
                    print(f"Очищен файл: {file_path}")

                except Exception as e:
                    print(f"Ошибка при обработке {file_path}: {e}")

    print(f"\nГотово! Очищено файлов: {total_files}")

clean_all_html_in_site()







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
            if js[i-2:i] == '/*' and i-2 >= 0 and js[i-2:i] == '/*' and (i-2 == 0 or js[i-3] != '!'):
                pass
            if ch == '*' and nxt == '/':
                j = i - 1
                is_license = False
                while j >= 1:
                    if js[j-1:j+1] == '/*':
                        is_license = (j < len(js) and j < len(js) and js[j] == '!')
                        break
                    j -= 1
                if is_license:
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
                    log(f"Минифицирован: {os.path.relpath(fpath)}")
            except Exception as e:
                log_warn(f"Минификация провалена для {fpath}: {e}")

    log(f"Итого минифицировано файлов: {changed_files}")

run_minification(site_abs)

log_step("ГОТОВО")

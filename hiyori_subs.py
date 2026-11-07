#!/usr/bin/env python3
"""hiyori-subs: stáhne české a slovenské titulky k anime z Hiyori (https://hiyori.cz).

Projde zadané soubory nebo složky s videi, spáruje je s anime na Hiyori a stáhne
titulky uložené přímo na Hiyori vedle videa jako <video>.cs.ass / <video>.sk.srt.
Používá jen oficiální Hiyori API v2 (https://api.hiyori.cz/scalar/v2/), nic nescrapuje.

Titulky, které Hiyori jen odkazuje na web fansubu, skript nestahuje, ale vypíše odkaz.

  hiyori_subs.py --login                       # jednou: získá API klíč
  hiyori_subs.py -n /media/anime               # nanečisto
  hiyori_subs.py /media/anime                  # stáhne, co chybí
  hiyori_subs.py --id 21389 "/media/anime/Frieren/Season 01"   # ruční párování
"""
import argparse, datetime, getpass, json, logging, os, re, sys, time
import requests

__version__ = "1.0"
API = "https://api.hiyori.cz"
UA = "hiyori-subs/%s (+https://github.com/ancestor93/hiyori-subs)" % __version__
VIDEO_EXT = (".mkv", ".mp4", ".avi", ".m4v", ".webm", ".mov", ".ts", ".wmv")
SUB_EXT = (".ass", ".ssa", ".srt", ".vtt", ".sub")
LANG_CODE = {"CZ": "cs", "SK": "sk"}
LANG_TAGS = {"cs": ("cs", "cz", "cze", "ces", "czech"), "sk": ("sk", "slk", "slo", "slovak")}
SEASON_DIR = re.compile(r"^(?:season|s[ée]rie|serie|řada|rada|s)[ ._-]*0*(\d+)$", re.I)
SPECIAL_DIR = re.compile(r"^(?:specials?|extras?|ovas?|season[ ._-]*0+)$", re.I)
TAG_HIYORI = re.compile(r"[\[{(]hiyori(?:id)?[-= ]?(\d+)[\]})]", re.I)
TAG_MAL = re.compile(r"[\[{(]mal(?:id)?[-= ]?(\d+)[\]})]", re.I)
log = logging.getLogger("hiyori-subs")


# ------------------------------------------------------------------ soubory
def xdg(var, default, *parts):
    return os.path.join(os.environ.get(var) or os.path.expanduser(default), *parts)


def jload(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def jsave(path, data, private=False):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600 if private else 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def to_text(raw):
    """Text titulků jako UTF-8 (CP1250 a UTF-16 převede)."""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1250", errors="replace")


def guess_ext(name, fmt, text):
    ext = os.path.splitext(name or "")[1].lower()
    if ext in SUB_EXT:
        return ext
    if fmt and "." + fmt.lower() in SUB_EXT:
        return "." + fmt.lower()
    if "[Script Info]" in text[:2000]:
        return ".ass"
    if text.lstrip().startswith("WEBVTT"):
        return ".vtt"
    return ".srt"


# ------------------------------------------------------------------ názvy souborů
def clean_title(t):
    t = re.sub(r"[\[{(][^\]})]*[\]})]", " ", t or "")          # [1080p], (2023), {tvdb-1}
    t = re.sub(r"[._]", " ", t)
    t = re.sub(r"\s+-\s*$|^\s*-\s+", " ", t.strip())
    return re.sub(r"\s+", " ", t).strip(" -")


def year_of(name):
    m = re.search(r"\((19\d\d|20\d\d)\)", name or "")
    return int(m.group(1)) if m else None


def parse_video(path):
    """Z názvu souboru vytáhne epizodu, řadu, release group a název."""
    name = os.path.splitext(os.path.basename(path))[0]
    group, rest = None, name
    m = re.match(r"^\[([^\]]+)\]\s*", name)
    if m:
        group, rest = m.group(1), name[m.end():]
    else:
        m = re.search(r"(?:\]|[^\s-])-([A-Za-z][A-Za-z0-9]+)$", name)     # ...x264-KAF, ...[AAC]-SubsPlease
        if m:
            group = m.group(1)
    season = ep = None
    title = rest
    for rx in (r"\bS(\d{1,2})[ ._-]?E(\d{1,4})(?:v\d)?\b",
               r"\s-\s(\d{1,4})(?:v\d)?(?=\s|$|[\[(])",
               r"(?:\b|_)(?:E|EP|Episode|Epizoda|D[ií]l)[ ._]?(\d{1,4})(?:v\d)?\b"):
        m = re.search(rx, rest, re.I)
        if m:
            g = m.groups()
            season, ep = (int(g[0]), int(g[1])) if len(g) == 2 else (None, int(g[0]))
            title = rest[:m.start()]
            break
    return {"ep": ep, "season": season, "group": group, "title": clean_title(title),
            "fansub_style": bool(re.match(r"^\[[^\]]+\]", name))}


def series_of(video, info):
    """Kam video patří: klíč pro párování, název pro hledání, řada, složky s tagy."""
    d = os.path.dirname(os.path.abspath(video))
    base = os.path.basename(d)
    season, series_dir, special = info["season"], d, False
    m = SEASON_DIR.match(base)
    if m:
        series_dir = os.path.dirname(d)
        season = season or int(m.group(1))
    elif SPECIAL_DIR.match(base):
        series_dir, special = os.path.dirname(d), True
    sname = os.path.basename(series_dir)
    if info["fansub_style"] and info["title"]:
        # [Group] Název S2 - 05.mkv: název ze souboru (funguje i pro jednu složku s více anime)
        name, fs = info["title"], None
        m = re.search(r"\s+(?:S|Season\s*)0*(\d+)$|\s+0*(\d+)(?:st|nd|rd|th)\s+Season$", name, re.I)
        if m:
            name, fs = name[:m.start()], int(m.group(1) or m.group(2))
        return {"key": d + "|" + norm(info["title"]), "name": name, "season": fs,
                "year": None, "dirs": [d], "special": special}
    return {"key": d, "name": clean_title(sname), "season": season, "year": year_of(sname),
            "dirs": [d] if d == series_dir else [d, series_dir], "special": special}


ORD = {1: "1st", 2: "2nd", 3: "3rd"}


def season_names(name, season):
    if not season or season == 1:
        return {norm(name), norm(name + " season 1"), norm(name + " 1st season")}
    n = season
    return {norm(x) for x in (name + " %d" % n, name + " season %d" % n, name + " s%d" % n,
                              name + " %s season" % ORD.get(n, "%dth" % n), name + " part %d" % n)}


# ------------------------------------------------------------------ Hiyori API
class Hiyori:
    def __init__(self, cfg, cfg_path, delay):
        self.cfg, self.cfg_path, self.delay = cfg, cfg_path, delay
        self.s = requests.Session()
        self.s.headers["User-Agent"] = UA
        self.jwt_until = 0
        self.last = 0
        self.calls = 0

    @staticmethod
    def _json(r):
        try:
            return r.json()
        except ValueError:
            return {}

    def _creds(self):
        user = self.cfg.get("username") or os.environ.get("HIYORI_USERNAME")
        pw = self.cfg.get("password") or os.environ.get("HIYORI_PASSWORD")
        return (user, pw) if user and pw else (None, None)

    def fetch_key(self, user, pw):
        r = self.s.post(API + "/v2/login/get-api-key", json={"username": user, "password": pw}, timeout=60)
        d = self._json(r)
        if not d.get("success"):
            raise SystemExit("Hiyori: získání API klíče selhalo (HTTP %d) %s" % (r.status_code, d.get("message") or ""))
        self.cfg["api_key"] = d["data"]["api_key"]
        self.cfg["api_key_expires"] = d["data"].get("expiration_date") or ""
        jsave(self.cfg_path, self.cfg, private=True)
        log.info("Hiyori: nový API klíč, platí do %s", self.cfg["api_key_expires"][:10] or "?")

    def check_key(self):
        """Doplní platnost ručně vloženého API klíče (/v2/login/check-api-key)."""
        r = self.s.post(API + "/v2/login/check-api-key", json=self.cfg["api_key"], timeout=60)
        d = self._json(r)
        exp = ((d.get("data") or {}).get("expiration_date") or "") if d.get("success") else ""
        if exp:
            self.cfg["api_key_expires"] = exp
            jsave(self.cfg_path, self.cfg, private=True)

    def api_key(self, force=False):
        key = self.cfg.get("api_key")
        if key and not self.cfg.get("api_key_expires") and not force:
            self.check_key()
        exp = (self.cfg.get("api_key_expires") or "")[:10]
        today = datetime.date.today()
        soon = (today + datetime.timedelta(days=14)).isoformat()
        if key and not force and (not exp or exp > soon):
            return key
        user, pw = self._creds()
        if user:
            self.fetch_key(user, pw)
            return self.cfg["api_key"]
        if key and not force and exp >= today.isoformat():
            log.warning("Hiyori: API klíč vyprší %s, obnov ho: hiyori_subs.py --login", exp)
            return key
        raise SystemExit("Hiyori: chybí platný API klíč. Spusť: hiyori_subs.py --login")

    def login(self, retry=True):
        r = self.s.post(API + "/v2/login/token", json={"api_key": self.api_key()}, timeout=60)
        d = self._json(r)
        if not d.get("success"):
            if retry and r.status_code in (401, 403):
                self.api_key(force=True)
                return self.login(retry=False)
            raise SystemExit("Hiyori: přihlášení selhalo (HTTP %d) %s" % (r.status_code, d.get("message") or ""))
        self.s.headers["Authorization"] = "Bearer " + d["data"]["token"]
        self.jwt_until = time.time() + 25 * 60
        log.debug("Hiyori: přihlášen %s, scopes: %s", d["data"].get("username"), ", ".join(d["data"].get("scopes") or []))

    def get(self, path, raw=False, **params):
        """GET na API. 404 = None, 429 = počká podle Retry-After."""
        if time.time() > self.jwt_until:
            self.login()
        r = None
        for attempt in range(4):
            time.sleep(max(0.0, self.delay - (time.time() - self.last)))
            self.last = time.time()
            self.calls += 1
            r = self.s.get(API + path, params=params, timeout=60)
            if r.status_code == 401 and attempt == 0:
                self.login()
                continue
            if r.status_code == 429:
                wait = min(int(r.headers.get("retry-after") or 60), 900)
                log.warning("Hiyori: překročen limit požadavků, čekám %d s", wait)
                time.sleep(wait)
                continue
            break
        if r.status_code == 404:
            return None
        if r.status_code == 403:
            raise SystemExit("Hiyori: 403 u %s, účtu chybí scope (viz README) – napiš adminům Hiyori" % path)
        r.raise_for_status()
        return r if raw else r.json()

    def get_all(self, path, **params):
        out, page = [], 1
        while True:
            j = self.get(path, page=page, page_size=50, **params)
            if not j:
                return out
            out += j.get("data") or []
            if not (j.get("pagination") or {}).get("has_next"):
                return out
            page += 1


# ------------------------------------------------------------------ aplikace
class App:
    def __init__(self, args, cfg):
        self.args, self.cfg = args, cfg
        self.state = jload(args.state, {})
        for k in ("map", "search", "rows", "subs"):
            self.state.setdefault(k, {})
        self.api = Hiyori(cfg, args.config, cfg.get("request_delay", 2.0))
        self.langs = [x.upper() for x in (args.lang.split(",") if args.lang else cfg.get("languages", ["CZ", "SK"]))]
        self.ttl = cfg.get("rows_ttl_h", 24) * 3600
        self.anime_cache = {}
        self.rows_mem = {}
        self.latest_done = False
        self.stats = dict.fromkeys(("staženo", "nahrazeno", "jen_externí", "bez_titulků", "nespárováno", "chyby"), 0)
        self.unmatched = {}
        self.external = []

    # ---- anime
    @staticmethod
    def names(a):
        return [v for v in (a.get("names") or {}).values() if v]

    @staticmethod
    def title(a):
        n = a.get("names") or {}
        return n.get("romaji") or n.get("english") or n.get("czech") or "?"

    def anime(self, hid):
        if hid not in self.anime_cache:
            j = self.api.get("/v2/anime/%d" % hid)
            self.anime_cache[hid] = (j or {}).get("data")
        return self.anime_cache[hid]

    def by_mal(self, mal):
        j = self.api.get("/v2/anime/mal/%d" % mal)
        a = (j or {}).get("data")
        if a:
            self.anime_cache[a["id"]] = a
        return a

    def search(self, name):
        c = self.state["search"].get(norm(name))
        if c and time.time() - c["t"] < 7 * 86400:
            return c["res"]
        j = self.api.get("/v2/anime/search", name=name, page_size=50) or {}
        keep = ("id", "names", "type", "episodes", "airing_from", "mal_id")
        res = [{k: a.get(k) for k in keep} for a in j.get("data") or []]
        self.state["search"][norm(name)] = {"t": time.time(), "res": res}
        return res

    def resolve(self, ser):
        """Hiyori anime pro skupinu videí: ruční/uložené párování > tag ve složce > hledání podle názvu."""
        m = self.state["map"].get(ser["key"])
        if m:
            return m
        for i, d in enumerate(ser["dirs"]):
            if i and ser["season"] not in (None, 1):
                break                          # tag na složce seriálu platí jen pro 1. řadu
            b = os.path.basename(d)
            t = TAG_HIYORI.search(b)
            if t:
                return {"id": int(t.group(1)), "offset": 0}
            t = TAG_MAL.search(b)
            if t:
                a = self.by_mal(int(t.group(1)))
                if a:
                    return self._remember(ser, a)
        if ser["special"] or not ser["name"]:
            return None
        want = season_names(ser["name"], ser["season"])
        res, cand = [], []
        queries = [ser["name"]] + [q for q in (re.split(r"\s+-\s+|:\s", ser["name"])[0],) if q != ser["name"] and len(q) >= 3]
        for q in queries:                  # dlouhé anglické názvy API nenajde celé -> zkusit část před " - "
            res += [a for a in self.search(q) if a["id"] not in {b["id"] for b in res}]
            cand = [a for a in res if want & {norm(n) for n in self.names(a)}]
            if cand:
                break
        if len(cand) > 1 and ser["year"]:
            cand = [a for a in cand if (a.get("airing_from") or "")[:4] == str(ser["year"])] or cand
        if len(cand) > 1:
            cand = [a for a in cand if a.get("type") == "TV Série"] or cand
        if len(cand) == 1:
            return self._remember(ser, cand[0])
        self.unmatched[ser["key"]] = (ser, cand or res[:5])
        return None

    def _remember(self, ser, a):
        m = {"id": a["id"], "offset": 0, "title": self.title(a), "auto": True}
        self.state["map"][ser["key"]] = m
        log.info("%s → Hiyori %d %s", ser["name"], a["id"], self.title(a))
        return m

    # ---- titulky
    def rows(self, hid):
        if hid in self.rows_mem:
            return self.rows_mem[hid]
        if not self.latest_done:
            self.latest_done = True
            if self.state["rows"]:
                for s in (self.api.get("/v2/subtitles/latest") or {}).get("data") or []:
                    c = self.state["rows"].get(str(s.get("anime_id")))
                    if c and (s.get("added_at") or "") > c.get("newest", ""):
                        c["t"] = 0
        c = self.state["rows"].get(str(hid))
        if not c or time.time() - c["t"] > self.ttl:
            keep = ("id", "episode", "language", "release", "version", "format", "url", "translator", "added_at")
            subs = []
            for x in self.api.get_all("/v2/subtitles/anime/%d" % hid):
                y = {k: x.get(k) for k in keep}
                y["fansub"] = (x.get("fansub") or {}).get("name") or x.get("translator") or ""
                subs.append(y)
            c = {"t": time.time(), "subs": subs, "newest": max((x.get("added_at") or "" for x in subs), default="")}
            self.state["rows"][str(hid)] = c
        self.rows_mem[hid] = c["subs"]
        return c["subs"]

    def score(self, row, group):
        s = (len(self.langs) - self.langs.index(row["language"])) * 100
        if group and norm(group) and norm(group) in norm(row.get("release")):
            s += 1000
        try:
            s += min(float(row.get("version") or 1), 9)
        except ValueError:
            s += 1
        return s

    @staticmethod
    def existing_subs(video):
        d, stem = os.path.split(video)
        found = {}
        for n in os.listdir(d):
            if n.startswith(stem + ".") and n.lower().endswith(SUB_EXT):
                tags = n[len(stem) + 1:].lower().split(".")[:-1]
                for code, aliases in LANG_TAGS.items():
                    if any(t in aliases for t in tags):
                        found[code] = os.path.join(d, n)
        return found

    def handle(self, video, info, m):
        a = self.anime(m["id"]) if m.get("title") is None else None
        title = m.get("title") or (self.title(a) if a else "Hiyori %d" % m["id"])
        ep = info["ep"]
        if ep is None:
            a = a or self.anime(m["id"])
            if a and (a.get("episodes") or 0) <= 1:
                ep = 1                         # film / jednodílné
            else:
                log.debug("%s: nepoznám číslo epizody, přeskakuji", os.path.basename(video))
                return
        hep = ep - m.get("offset", 0)
        label = "%s %02d" % (title, hep)
        rows = [r for r in self.rows(m["id"])
                if (r.get("episode") == hep or (r.get("episode") is None and hep == 1))
                and (r.get("language") or "").upper() in self.langs]
        for r in rows:
            r["language"] = r["language"].upper()
        hosted = sorted(((self.score(r, info["group"]), r) for r in rows if not (r.get("url") or "").strip()),
                        key=lambda x: -x[0])
        rec = self.state["subs"].get(video)
        have = self.existing_subs(video)
        if have and not (rec and rec.get("path") in have.values()):
            log.debug("%s: už má titulky %s, přeskakuji", label, ", ".join(have))
            return
        if not hosted:
            ext = [r for r in rows if (r.get("url") or "").strip()]
            if ext and not rec:
                self.stats["jen_externí"] += 1
                self.external.append((label, video, ext))
            elif not rec:
                self.stats["bez_titulků"] += 1
                log.debug("%s: na Hiyori zatím titulky nejsou", label)
            return
        best_s, best = hosted[0]
        if rec and rec.get("score", 0) >= best_s and os.path.exists(rec.get("path", "")):
            return
        what = "%s %s v%s [%s]" % (best["language"], best.get("release") or "?", best.get("version") or "1", best["fansub"])
        if self.args.dry_run:
            log.info("[nanečisto] %s: %s", label, what)
            return
        try:
            r = self.api.get("/v2/subtitles/%d/download" % best["id"], raw=True)
        except (requests.RequestException, SystemExit) as e:
            log.warning("%s: stažení selhalo: %s", label, e)
            self.stats["chyby"] += 1
            return
        if r is None or not r.content:
            log.warning("%s: Hiyori vrátilo prázdný soubor", label)
            self.stats["chyby"] += 1
            return
        text = to_text(r.content)
        cd = r.headers.get("content-disposition", "")
        fm = re.search(r"filename\*=UTF-8''([^;]+)", cd) or re.search(r'filename="?([^";]+)"?', cd)
        name = requests.utils.unquote(fm.group(1)) if fm else ""
        target = os.path.splitext(video)[0] + "." + LANG_CODE.get(best["language"], best["language"].lower()) \
            + guess_ext(name, best.get("format"), text)
        if rec and rec.get("path") and rec["path"] != target and os.path.exists(rec["path"]):
            os.remove(rec["path"])
        with open(target, "w", encoding="utf-8") as f:
            f.write(text)
        self.state["subs"][video] = {"id": best["id"], "score": best_s, "path": target, "release": best.get("release"),
                                     "version": best.get("version"), "fansub": best["fansub"],
                                     "time": datetime.datetime.now().isoformat(timespec="seconds")}
        self.stats["nahrazeno" if rec else "staženo"] += 1
        log.info("%s: %s %s → %s", label, "NAHRAZENO" if rec else "staženo", what, os.path.basename(target))

    # ---- běh
    def videos(self):
        for p in self.args.paths:
            p = os.path.abspath(p)
            if os.path.isfile(p):
                if p.lower().endswith(VIDEO_EXT):
                    yield p
                continue
            if not os.path.isdir(p):
                log.warning("%s neexistuje", p)
                continue
            for root, dirs, files in os.walk(p):
                dirs[:] = sorted(d for d in dirs if not d.startswith("."))
                for f in sorted(files):
                    if f.lower().endswith(VIDEO_EXT) and not f.startswith("."):
                        yield os.path.join(root, f)

    def run(self):
        groups = {}
        for v in self.videos():
            info = parse_video(v)
            ser = series_of(v, info)
            groups.setdefault(ser["key"], (ser, []))[1].append((v, info))
        if not groups:
            log.warning("Nenašel jsem žádná videa.")
            return 0
        if self.args.id:
            for key in groups:
                self.state["map"][key] = {"id": self.args.id, "offset": self.args.offset}
            a = self.anime(self.args.id)
            if not a:
                raise SystemExit("Hiyori: anime %d neexistuje" % self.args.id)
            for key in groups:
                self.state["map"][key]["title"] = self.title(a)
            log.info("Spárováno ručně: %d složek → Hiyori %d %s", len(groups), self.args.id, self.title(a))
        elif self.args.forget:
            for key in groups:
                self.state["map"].pop(key, None)
        try:
            for key, (ser, vids) in sorted(groups.items()):
                m = self.resolve(ser)
                if not m and ser["special"]:
                    log.debug("%s: speciály bez ručního párování přeskakuji", ser["dirs"][0])
                    continue
                if not m:
                    self.stats["nespárováno"] += len(vids)
                    continue
                for v, info in sorted(vids, key=lambda x: (x[1]["ep"] or 0, x[0])):
                    self.handle(v, info, m)
        finally:
            jsave(self.args.state, self.state)
        self.report()
        if self.stats["staženo"] + self.stats["nahrazeno"] and not self.args.dry_run:
            self.bazarr_scan()
        return 0

    def bazarr_scan(self):
        """Řekne Bazarru, ať znovu načte titulky z disku (jinak by je viděl až při noční indexaci)."""
        b = self.cfg.get("bazarr") or {}
        if not (b.get("url") and b.get("apikey")):
            return
        for task in b.get("tasks") or ("series_full_scan_subtitles", "movies_full_scan_subtitles"):
            try:
                r = requests.post(b["url"].rstrip("/") + "/api/system/tasks", data={"taskid": task},
                                  headers={"X-API-KEY": b["apikey"], "User-Agent": UA}, timeout=30)
                r.raise_for_status()
                log.info("Bazarr: spuštěno %s", task)
            except requests.RequestException as e:
                log.warning("Bazarr: %s selhalo: %s", task, e)

    def report(self):
        for ser, cand in self.unmatched.values():
            log.warning("NESPÁROVÁNO: %s%s (%s)", ser["name"],
                        " – řada %d" % ser["season"] if ser["season"] else "", ser["dirs"][0])
            for a in cand[:5]:
                log.warning("   možná: --id %-6d %s (%s, %s, %s ep.)", a["id"], self.title(a),
                            (a.get("airing_from") or "")[:4], a.get("type") or "?", a.get("episodes") or "?")
        if self.external and self.args.links:
            log.info("Titulky jen na webech fansubů (stáhni ručně):")
            for label, video, ext in self.external:
                for r in ext:
                    log.info("  %s  %s %s [%s]  %s", label, r["language"], r.get("release") or "", r["fansub"], r["url"])
        s = self.stats
        log.info("Hotovo: staženo %d, nahrazeno %d, jen na webech fansubů %d%s, bez titulků %d, nespárováno %d, chyby %d"
                 " (požadavků na API: %d)", s["staženo"], s["nahrazeno"], s["jen_externí"],
                 "" if self.args.links or not s["jen_externí"] else " (odkazy: --links)",
                 s["bez_titulků"], s["nespárováno"], s["chyby"], self.api.calls)


def do_login(args, cfg):
    print("Přihlášení k Hiyori (účet musí mít povolený přístup k API).")
    user = input("Uživatel: ").strip()
    pw = getpass.getpass("Heslo: ")
    Hiyori(cfg, args.config, 0).fetch_key(user, pw)
    if input("Uložit i jméno a heslo pro automatickou obnovu klíče? [a/N] ").strip().lower() in ("a", "ano", "y", "yes"):
        cfg["username"], cfg["password"] = user, pw
        jsave(args.config, cfg, private=True)
    print("Uloženo do %s (práva 600)." % args.config)


def main():
    p = argparse.ArgumentParser(description="Stáhne CZ/SK titulky k anime z Hiyori (hiyori.cz) přes API v2.")
    p.add_argument("paths", nargs="*", help="soubory nebo složky s videi (prochází se rekurzivně)")
    p.add_argument("-n", "--dry-run", action="store_true", help="nic nestahovat, jen vypsat")
    p.add_argument("-l", "--lang", help="jazyky podle priority, např. CZ,SK (výchozí z configu)")
    p.add_argument("--id", type=int, help="ruční párování: Hiyori ID anime pro zadané složky (zapamatuje se)")
    p.add_argument("--offset", type=int, default=0,
                   help="s --id: o kolik je číslo dílu v souboru vyšší než na Hiyori (např. 12 pro 2. řadu číslovanou 13–24)")
    p.add_argument("--forget", action="store_true", help="zapomenout párování zadaných složek a spárovat znovu")
    p.add_argument("--links", action="store_true", help="vypsat odkazy na titulky, které jsou jen na webech fansubů")
    p.add_argument("--login", action="store_true", help="přihlásit se a uložit API klíč")
    p.add_argument("--config", default=os.environ.get("HIYORI_SUBS_CONFIG") or xdg("XDG_CONFIG_HOME", "~/.config", "hiyori-subs", "config.json"))
    p.add_argument("--state", default=os.environ.get("HIYORI_SUBS_STATE") or xdg("XDG_STATE_HOME", "~/.local/state", "hiyori-subs", "state.json"))
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("-q", "--quiet", action="store_true", help="jen varování a chyby")
    p.add_argument("--version", action="version", version="hiyori-subs " + __version__)
    args = p.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING if args.quiet else logging.INFO,
                        format="%(message)s")
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    cfg = jload(args.config, {})
    if args.login:
        do_login(args, cfg)
        if not args.paths:
            return 0
    if not args.paths:
        p.error("zadej soubor nebo složku s videi (nebo --login)")
    if args.offset and args.id is None:
        p.error("--offset jde jen s --id")
    return App(args, cfg).run()


if __name__ == "__main__":
    sys.exit(main())

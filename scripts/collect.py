#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
雷达站自动采集器 v2（只用 Python 标准库）。

额度护栏（写死的设计规矩）：本站是纯静态站，数据只存在本仓库里；
本脚本只读写仓库文件，不调用任何 Cloudflare 运行时存储
（KV / Workers / D1 一律不用），不跟其他项目抢 CF 额度。

在 v1 刷新能力之上加了：发现新条目、自动替换死条目、自动重排、
语言/下载地区/能否改中文标注、双安全阀、多源容错。

每次运行做这些事：
  1) 验活刷新现有条目（带浏览器 UA、并发 10）：details 记「最近检查」；
     GitHub 仓库补「最近更新」「星标数」；直链文件数条数（站点数/频道数/
     书源数/节点数/条数）；苹果应用补「现价」；缺图条目补 favicon。
  2) 语言标注：details「语言」；英文条目缺中文说明的补 details「中文说明」。
     苹果/安卓 APP 另标「下载地区」和「能否改中文」（实测来源，不猜）。
  3) 发现：按 scripts/feeds.json 的源逐栏拉候选（单源失败只跳过记日志），
     网址规范化 + 名称近似双去重；新候选验活后深挖内容（iTunes 完整介绍 /
     F-Droid 介绍 / GitHub README / 网页正文），凑齐「是什么、干什么、
     有什么用、适合谁」四样才收；深挖后仍无实义内容的放弃并记录原因。
     每栏每次最多新增 15 条，每栏总量上限 300 条。
  4) 替换：scripts/state.json 记每条连续验活失败次数，连续不通自动移除
     （苹果栏目 3 次、其他栏目 6 次；每 6 小时跑一轮，约合 18/36 小时），
     从同栏候选池按新鲜度补位；新增/移除/挪组清单写进 changes.txt
     （提交信息用）。苹果限免组每轮按现价复核：不是免费了就降组或移除。
  5) 重排：每栏按（验活通、新鲜、条数多、星标高）重算 rank 和 score。
  6) 双安全阀（任一触发 → 非零退出、不写任何文件、保住旧数据）：
     - 全站超过 30% 条目验活不通；
     - 本次移除超过全站总数的 25%（新增不拦：不重复的新条目全数加进）。

GitHub API：有环境变量 GITHUB_TOKEN 就带上提限额；没有就自动降速
（搜索请求间隔拉长、每页条数减少），不用任何私人令牌。

用法：python3 scripts/collect.py [data.json 路径]（默认仓库根目录 data.json）
"""
import base64
import concurrent.futures
import datetime
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
TIMEOUT = 12                # 单次连接/读超时（秒）
DEADLINE = 30               # 单个请求总时限（秒），防慢速滴漏把进程挂死
MAX_BYTES = 6 * 1024 * 1024
CST = datetime.timezone(datetime.timedelta(hours=8))
GH_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
MAX_PER_CAT = 300           # 每栏总量上限
MAX_ADD_PER_CAT = MAX_PER_CAT  # 每栏每次新增不另设上限：搜到多少不重复的新条目就加多少（站长 2026-11-23 定），只受每栏总量上限约束
MAX_CANDIDATES_TRY = MAX_PER_CAT  # 每栏最多深挖多少个候选（与总量上限齐平，实际等于不限）

STATS = {"checked_ok": 0, "dead": 0, "content_dead": 0, "github": 0, "counted": 0, "imaged": 0,
         "lang": 0, "region": 0, "cnswitch": 0, "cnswitch_en": 0,
         "deep_ok": 0}
_lock = threading.Lock()
_repo_cache = {}
_repo_lock = threading.Lock()
_itunes_cache = {}
_itunes_lock = threading.Lock()
_search_lock = threading.Lock()
_last_search_at = [0.0]
FEED_LOG = []               # 每个 feed 的结果（给运行日志/报告用）
FEED_CANDIDATES = {}        # slug -> 候选数
ADDED = []                  # (slug, name, url)
REMOVED = []                # (slug, name, url, reason)
MOVED = []                  # (slug, name, url, note)
ABANDONED = []              # (name, url, reason)


def bump(key, n=1):
    with _lock:
        STATS[key] = STATS.get(key, 0) + n


def log(msg):
    print(msg, flush=True)


def today_cst():
    return datetime.datetime.now(CST).strftime("%Y-%m-%d")


def now_iso_z():
    n = datetime.datetime.now(datetime.timezone.utc)
    return n.strftime("%Y-%m-%dT%H:%M:%S.") + f"{n.microsecond // 1000:03d}Z"


# ---------------- 基础网络 ----------------

def _headers(extra=None):
    h = {"User-Agent": UA}
    if extra:
        h.update(extra)
    return h


def _read_limited(resp, max_bytes, start, deadline):
    chunks, got = [], 0
    while got <= max_bytes:
        if time.monotonic() - start > deadline:
            break
        try:
            chunk = resp.read(min(65536, max_bytes + 1 - got))
        except Exception:
            break
        if not chunk:
            break
        chunks.append(chunk)
        got += len(chunk)
    return b"".join(chunks)[:max_bytes]


def fetch(url, max_bytes=MAX_BYTES, deadline=DEADLINE, headers=None):
    """GET 一个网址，返回 (status, content_type, body_bytes)。
    HTTP 错误码也算拿到了响应；网络层失败抛异常。带总时限防挂死。"""
    req = urllib.request.Request(url, headers=_headers(headers))
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = _read_limited(resp, max_bytes, start, deadline)
            return resp.status, resp.headers.get_content_type() or "", body
    except urllib.error.HTTPError as e:
        try:
            body = e.read(65536)
        except Exception:
            body = b""
        return e.code, (e.headers.get_content_type() or "") if e.headers else "", body


def gh_fetch(url, max_bytes=512 * 1024):
    """GitHub API 专用：有令牌带令牌；搜索接口无令牌时自动降速。"""
    headers = {"Accept": "application/vnd.github+json"}
    if GH_TOKEN:
        headers["Authorization"] = f"Bearer {GH_TOKEN}"
    if "/search/" in url and not GH_TOKEN:
        with _search_lock:
            wait = 7.0 - (time.monotonic() - _last_search_at[0])
            if wait > 0:
                time.sleep(wait)
            _last_search_at[0] = time.monotonic()
    return fetch(url, max_bytes=max_bytes, headers=headers)


# ---------------- details 读写 ----------------

def set_detail(item, label, value):
    details = item.get("details")
    if not isinstance(details, list):
        details = []
        item["details"] = details
    for d in details:
        if isinstance(d, dict) and d.get("label") == label:
            d["value"] = value
            return
    details.append({"label": label, "value": value})


def get_detail(item, label):
    for d in item.get("details") or []:
        if isinstance(d, dict) and d.get("label") == label:
            return str(d.get("value") or "")
    return ""


# ---------------- GitHub ----------------

GH_RE = re.compile(r"^https?://github\.com/([^/]+)/([^/#?]+)", re.I)
GH_SKIP_OWNERS = {"features", "about", "pricing", "topics", "marketplace",
                  "sponsors", "settings", "login", "join", "explore"}


def github_repo_of(url):
    if not url:
        return None
    m = GH_RE.match(url.strip())
    if not m:
        return None
    owner, repo = m.group(1), m.group(2)
    if repo.endswith(".git"):
        repo = repo[:-4]
    if owner.lower() in GH_SKIP_OWNERS:
        return None
    return f"{owner}/{repo}"


def github_info(repo):
    """仓库信息（带缓存）：pushed_at、星标数、简介、默认分支。无令牌也能调。"""
    with _repo_lock:
        if repo in _repo_cache:
            return _repo_cache[repo]
    info = None
    try:
        status, _ct, body = gh_fetch(f"https://api.github.com/repos/{repo}")
        if status == 200:
            data = json.loads(body.decode("utf-8", "replace"))
            info = {
                "pushed_at": (data.get("pushed_at") or "")[:10],
                "stars": data.get("stargazers_count"),
                "description": data.get("description") or "",
                "default_branch": data.get("default_branch") or "main",
            }
    except Exception:
        info = None
    with _repo_lock:
        _repo_cache[repo] = info
    return info


_release_cache = {}
_release_lock = threading.Lock()


def github_release(repo):
    """最新 release 的 (tag, 发布日期)，没有 release 返回 None（带缓存）。"""
    with _release_lock:
        if repo in _release_cache:
            return _release_cache[repo]
    rel = None
    try:
        status, _ct, body = gh_fetch(
            f"https://api.github.com/repos/{repo}/releases/latest")
        if status == 200:
            data = json.loads(body.decode("utf-8", "replace"))
            if data.get("tag_name"):
                rel = (str(data["tag_name"]),
                       (data.get("published_at") or "")[:10])
    except Exception:
        rel = None
    with _release_lock:
        _release_cache[repo] = rel
    return rel


def github_readme(repo, info=None):
    """抓 README 开头几段纯文本（深挖用）。拿不到返回空串。"""
    info = info or github_info(repo)
    branch = (info or {}).get("default_branch") or "main"
    for name in ("README.md", "readme.md", "README.MD"):
        try:
            status, _ct, body = fetch(
                f"https://raw.githubusercontent.com/{repo}/{branch}/{name}",
                max_bytes=512 * 1024)
            if status == 200 and body:
                return md_to_text(body.decode("utf-8", "replace"))
        except Exception:
            continue
    return ""


def md_to_text(md):
    """Markdown 粗转纯文本：去图片/链接语法/标题井号/代码块，留正文段落。"""
    md = re.sub(r"```.*?```", " ", md, flags=re.S)
    md = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", md)
    md = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", md)
    md = re.sub(r"<[^>]+>", " ", md)
    md = re.sub(r"^#{1,6}\s*", "", md, flags=re.M)
    md = re.sub(r"[*_>`|]", "", md)
    paras = []
    for p in re.split(r"\n\s*\n", md):
        p = re.sub(r"\s+", " ", p).strip()
        if len(p) >= 25 and not p.lower().startswith(("http", "---")):
            paras.append(p)
        if len(paras) >= 3:
            break
    return "\n".join(paras)[:900]


# ---------------- 数条数（v1 沿用） ----------------

def count_content(url, body):
    """按内容数条数，返回 (label, count) 或 None。数不出来返回 None。"""
    if not body:
        return None
    text = body.decode("utf-8", "replace")
    path = urllib.parse.urlparse(url).path.lower()

    if path.endswith((".m3u", ".m3u8")) or "#EXTINF" in text[:4096]:
        n = text.count("#EXTINF")
        if n > 0:
            return ("频道数", n)

    stripped = text.lstrip()
    if stripped.startswith(("{", "[")):
        try:
            data = json.loads(text)
        except Exception:
            data = None
        if isinstance(data, dict) and isinstance(data.get("sites"), list):
            return ("站点数", len(data["sites"]))
        if isinstance(data, dict) and isinstance(data.get("list"), list) \
                and data["list"]:
            return ("条数", len(data["list"]))
        if isinstance(data, list) and data:
            return ("书源数", len(data))

    schemes = ("vmess://", "vless://", "trojan://", "ss://")
    n = sum(text.count(s) for s in schemes)
    if n > 0:
        return ("节点数", n)
    compact = re.sub(r"\s+", "", text)
    if len(compact) > 40 and re.fullmatch(r"[A-Za-z0-9+/=]+", compact or " "):
        try:
            decoded = base64.b64decode(compact).decode("utf-8", "replace")
            n = sum(decoded.count(s) for s in schemes)
            if n > 0:
                return ("节点数", n)
        except Exception:
            pass

    if path.endswith(".txt"):
        n = sum(1 for line in text.splitlines() if line.strip())
        if n > 0:
            return ("条数", n)
    return None


# ---------------- 网页解析（标题/简介/正文/语言） ----------------

class PageParser(HTMLParser):
    SKIP_TAGS = {"script", "style", "nav", "footer", "header", "aside",
                 "form", "noscript", "svg", "button"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lang = ""
        self.title = ""
        self.meta_desc = ""
        self.og_desc = ""
        self.paras = []
        self.hrefs = []
        self._skip = 0
        self._in_title = False
        self._title_buf = []
        self._p_buf = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "html":
            self.lang = a.get("lang", "") or ""
        if tag in self.SKIP_TAGS:
            self._skip += 1
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            key = (a.get("name") or a.get("property") or "").lower()
            if key == "description" and a.get("content"):
                self.meta_desc = a["content"].strip()
            elif key == "og:description" and a.get("content"):
                self.og_desc = a["content"].strip()
        elif tag == "a" and a.get("href"):
            self.hrefs.append(a["href"])
        elif tag == "p" and self._skip == 0:
            self._p_buf = []

    def handle_endtag(self, tag):
        if tag in self.SKIP_TAGS and self._skip > 0:
            self._skip -= 1
        if tag == "title":
            self._in_title = False
            self.title = re.sub(r"\s+", " ", "".join(self._title_buf)).strip()
        elif tag == "p" and self._p_buf is not None:
            text = re.sub(r"\s+", " ", "".join(self._p_buf)).strip()
            if len(text) >= 30:
                self.paras.append(text)
            self._p_buf = None

    def handle_data(self, data):
        if self._skip > 0:
            return
        if self._in_title:
            self._title_buf.append(data)
        if self._p_buf is not None:
            self._p_buf.append(data)


def parse_page(body):
    """解析 HTML 字节，返回 PageParser；不是 HTML 或解析失败返回 None。"""
    if not body:
        return None
    head = body[:2048].decode("utf-8", "replace").lower()
    if "<html" not in head and "<title" not in head and "<!doctype" not in head:
        return None
    p = PageParser()
    try:
        p.feed(body.decode("utf-8", "replace"))
    except Exception:
        pass
    return p


def judge_language(text, html_lang=""):
    """按文字构成判语言：英文 / 中文 / 中英双语 / None（判不出）。"""
    if html_lang:
        hl = html_lang.lower()
        if hl.startswith("zh"):
            base = "中文"
        elif hl.startswith("en"):
            base = "英文"
        else:
            base = None
    else:
        base = None
    if not text:
        return base
    cjk = len(re.findall(r"[一-鿿]", text))
    lat = len(re.findall(r"[A-Za-z]", text))
    if cjk + lat < 20:
        return base
    if cjk >= 30 and lat >= 30 and min(cjk, lat) >= 0.25 * max(cjk, lat):
        return "中英双语"
    return "中文" if cjk > lat else "英文"


def has_zh_switch(html_text, page):
    """页面有没有语言切换入口或中文版路径（实测证据）。"""
    if not html_text:
        return False
    low = html_text.lower()
    for pat in ('hreflang="zh', "hreflang='zh", "/zh-cn/", "/zh_cn/", "/zh-tw/",
                'lang="zh', "lang='zh", "/zh/", "简体中文", "繁體中文", "中文版"):
        if pat in low:
            return True
    if page:
        for h in page.hrefs:
            hl = h.lower()
            if "/zh" in hl or "lang=zh" in hl or "locale=zh" in hl:
                return True
    return False


LANG_NAME = {"ZH": "中文", "EN": "英文", "JA": "日文", "KO": "韩文",
             "FR": "法文", "DE": "德文", "ES": "西班牙文", "RU": "俄文",
             "PT": "葡萄牙文", "IT": "意大利文", "AR": "阿拉伯文",
             "NL": "荷兰文", "SV": "瑞典文", "PL": "波兰文", "TR": "土耳其文"}


def lang_label_from_codes(codes):
    """iTunes languages 代码表 → 「语言」标注（如实写支持的语言）。"""
    if not codes:
        return None
    ups = []
    for c in codes:
        u = str(c).upper()[:2]
        if u not in ups:
            ups.append(u)
    has_zh = "ZH" in ups
    has_en = "EN" in ups
    if ups == ["ZH"]:
        return "中文"
    if ups == ["EN"]:
        return "英文"
    if set(ups) == {"ZH", "EN"}:
        return "中英双语"
    names = [LANG_NAME.get(u, u) for u in ups[:4]]
    return "、".join(names) + ("等" if len(ups) > 4 else "")


# ---------------- iTunes / F-Droid ----------------

def itunes_lookup(app_id, country=None):
    """iTunes lookup（带缓存）。country=None 用默认（美区）。返回结果 dict 或 None。"""
    key = (app_id, country or "")
    with _itunes_lock:
        if key in _itunes_cache:
            return _itunes_cache[key]
    result = None
    try:
        u = f"https://itunes.apple.com/lookup?id={app_id}"
        if country:
            u += f"&country={country}"
        status, _ct, body = fetch(u, max_bytes=512 * 1024)
        if status == 200:
            data = json.loads(body.decode("utf-8", "replace"))
            results = data.get("results") or []
            if results:
                result = results[0]
    except Exception:
        result = None
    with _itunes_lock:
        _itunes_cache[key] = result
    return result


def apple_id_of(url):
    m = re.search(r"/id(\d+)", url or "")
    return m.group(1) if m else None


def fdroid_package_of(url):
    m = re.search(r"f-droid\.org/(?:[a-z-]{2,5}/)?packages/([A-Za-z0-9_.]+)", url or "")
    return m.group(1) if m else None


def fdroid_has_package(pkg):
    """F-Droid 官方 API 查有没有这个包。返回 True/False/None（查不到）。"""
    try:
        status, _ct, _b = fetch(f"https://f-droid.org/api/v1/packages/{pkg}",
                                max_bytes=64 * 1024)
        if status == 200:
            return True
        if status == 404:
            return False
    except Exception:
        pass
    return None


def fdroid_version(pkg):
    """F-Droid 官方 API 取最新版本号和更新日期，返回 (version, date) 或 None。"""
    try:
        status, _ct, body = fetch(f"https://f-droid.org/api/v1/packages/{pkg}",
                                  max_bytes=64 * 1024)
        if status == 200:
            data = json.loads(body.decode("utf-8", "replace"))
            ver = data.get("suggestedVersionName") or ""
            ts = data.get("lastUpdated") or 0
            date = ""
            if ts:
                date = datetime.datetime.fromtimestamp(
                    ts / 1000, CST).strftime("%Y-%m-%d")
            if ver or date:
                return (str(ver), date)
    except Exception:
        pass
    return None


def fdroid_page_text(pkg):
    """F-Droid 包页正文（介绍+元数据），深挖/多语言证据用。"""
    try:
        status, _ct, body = fetch(f"https://f-droid.org/en/packages/{pkg}/",
                                  max_bytes=1024 * 1024)
        if status == 200:
            page = parse_page(body)
            if page:
                return "\n".join(page.paras)
    except Exception:
        pass
    return ""


CN_EVIDENCE = ("中文", "简体", "繁體", "繁体", "chinese", "多语言", "多語言",
               "multilingual", "multi-language", "i18n", "localization",
               "translations", "语言包")


def cn_switch_from_evidence(text):
    """安卓：元数据/README 里有明确多语言/中文证据才标可切换；明说只有英文才标改不了。"""
    if not text:
        return "未公布"
    low = text.lower()
    if any(k.lower() in low for k in CN_EVIDENCE):
        return "可切换中文"
    if "english only" in low or "only english" in low:
        return "只有英文，改不了"
    return "未公布"


# ---------------- 图标（v1 沿用） ----------------

def find_image(url):
    """imageUrl 为空时找图标：先站点自己的 favicon，再 Google favicon 服务。
    实际请求确认是图片（content-type image 且 >200 字节）才返回该地址。"""
    try:
        parts = urllib.parse.urlparse(url)
        if not parts.netloc:
            return None
        origin = f"{parts.scheme}://{parts.netloc}"
        candidates = [
            origin + "/favicon.ico",
            "https://www.google.com/s2/favicons?domain="
            + urllib.parse.quote(parts.netloc) + "&sz=128",
        ]
    except Exception:
        return None
    for cand in candidates:
        try:
            status, ct, body = fetch(cand, max_bytes=512 * 1024, deadline=15)
            if status == 200 and ct.startswith("image") and len(body) > 200:
                return cand
        except Exception:
            continue
    return None


# ---------------- 栏目语境（模板用） ----------------

CAT_CONTEXT = {
    "ai": ("AI 工具/服务", "免费体验或调用 AI 能力，不用一上来就花钱买会员/额度", "想低成本用上 AI 的人"),
    "apple": ("苹果应用", "在 iPhone/iPad 上完成它擅长的那件事，限免/降价时入手最划算", "用苹果手机/平板的人"),
    "video": ("影视源/配置", "给 TVBox、影视仓这类软件提供能看的片源，解决没源可看的问题", "用 TVBox/影视仓看片的人"),
    "music": ("音乐源/插件", "给音乐软件提供能搜能播的音源，解决装了软件没歌听的问题", "用洛雪/MusicFree 听歌的人"),
    "book": ("阅读书源", "给「阅读」App 提供小说来源，解决书荒和源失效的问题", "用阅读 App 看小说的人"),
    "live": ("直播源", "给播放器提供电视台直播流，解决想看电视没信号的问题", "想看电视直播的人"),
    "service": ("免费服务", "白嫖一项原本要花钱的在线服务，解决个人项目缺工具/缺额度的问题", "搭个人站、做小项目的人"),
    "domain": ("免费域名来源", "免费领一个能用的域名，解决搭站没域名的问题", "想搭站又不想先花钱买域名的人"),
    "cfip": ("优选 IP 来源", "挑出速度快的 Cloudflare IP，解决自建节点速度慢的问题", "用 edgetunnel/CF 自建节点的人"),
    "node": ("节点订阅来源", "免费拿到一批能用的代理节点，解决临时上网需求", "需要免费节点应急的人"),
    "app": ("手机应用", "在安卓手机上完成它擅长的事，免费又没广告打扰", "安卓手机用户"),
    "adult": ("成人内容来源", "免费看成人内容，解决到处找入口的问题", "有这类需求的成年人"),
    "proxy": ("代理工具软件", "把节点和订阅变成能用的上网通道，解决有节点没客户端的问题", "自建节点、手里有订阅的人"),
}

PROXY_PLATFORMS = {"android": "安卓", "ios": "iOS", "windows": "Windows",
                   "mac": "macOS", "linux": "Linux", "router": "路由器",
                   "all": "全平台"}

# 代理/工具知识库：（识别词, 配套规则家族, 规则格式, 模块支持, 分流方式, 可加的东西）
# 家族对应 Peter 自己的规则包 YiMing918/proxy-app-rules 的七个文件夹。
PROXY_KB = [
    (("sub-store", "substore"), None,
     "无（订阅管理工具，负责生成各客户端的订阅）", "无", "无（不直接代理流量）", "无"),
    (("subconverter", "sub-converter"), None,
     "无（订阅转换工具，负责格式转换）", "无", "无（不直接代理流量）", "无"),
    (("shadowrocket",), "shadowrocket",
     "Shadowrocket 配置规则（.conf）", "支持模块（.module）",
     "规则分流 / 全局 / 直连可切换，支持远程配置订阅更新",
     "规则集：blackmatrix7/ios_rule_script（github.com/blackmatrix7/ios_rule_script）；"
     "模块与规则：LOWERTOP/Shadowrocket（github.com/LOWERTOP/Shadowrocket）"),
    (("quantumult",), "quantumultx",
     "Quantumult X 配置（filter 规则）", "支持重写（rewrite）和脚本",
     "规则分流 / 全局 / 直连可切换，filter 支持远程订阅更新",
     "重写与规则：KOP-XIAO/QuantumultX（github.com/KOP-XIAO/QuantumultX）；"
     "规则集：blackmatrix7/ios_rule_script（github.com/blackmatrix7/ios_rule_script）"),
    (("surge",), "surge",
     "Surge 配置（.conf，含 RULE-SET 远程规则集）", "支持 Surge 模块（.sgmodule）",
     "规则分流 / 全局 / 直连可切换，RULE-SET 支持远程订阅更新",
     "模块：Surge 官方文档模块说明（kb.nssurge.com）；"
     "规则集：blackmatrix7/ios_rule_script（github.com/blackmatrix7/ios_rule_script）、"
     "ACL4SSR（github.com/ACL4SSR/ACL4SSR）"),
    (("loon",), "loon",
     "Loon 配置规则", "支持 Loon 插件（.plugin）",
     "规则分流 / 全局 / 直连可切换，支持远程规则订阅更新",
     "插件：Loon 官网插件说明（loonapp.app）；"
     "规则：blackmatrix7/ios_rule_script（github.com/blackmatrix7/ios_rule_script）"),
    (("sing-box", "singbox", "karing", "nekobox", "hiddify"), "sing-box",
     "sing-box JSON（route 路由 + rule_set 规则集）", "无模块系统，只有配置文件",
     "按 route 规则分流，rule_set 支持远程订阅更新",
     "规则集：官方 sing-geosite（github.com/SagerNet/sing-geosite）、"
     "sing-geoip（github.com/SagerNet/sing-geoip）"),
    (("mihomo", "clashmeta", "clash-meta", "flclash", "clashx", "stash", "clash"),
     "mihomo",
     "mihomo/Clash YAML（rule-provider 远程规则集）", "无插件模块，靠覆写脚本和配置扩展",
     "规则分流 / 全局 / 直连可切换，rule-provider 支持远程订阅更新",
     "规则集：MetaCubeX/meta-rules-dat（github.com/MetaCubeX/meta-rules-dat）；"
     "规则与覆写参考：ACL4SSR（github.com/ACL4SSR/ACL4SSR）"),
    (("xray", "v2rayng", "v2ray"), "xray",
     "Xray/v2ray 路由规则（geosite/geoip）", "无模块系统，只有配置文件",
     "按 routing 规则分流，geo 数据文件随客户端更新（无远程规则集订阅）",
     "geo 数据：Loyalsoldier/v2ray-rules-dat（github.com/Loyalsoldier/v2ray-rules-dat）"),
]
PROXY_FAMILY_URL = "https://gcore.jsdelivr.net/gh/YiMing918/proxy-app-rules@main/{}/"

TOOL_STEPS = {
    "shadowrocket": "1) 加规则：打开小火箭 → 配置 → 选中当前配置 → 规则里添加远程规则集，粘贴规则链接；"
                    "2) 加模块：设置 → 模块 → 粘贴 .module 链接安装；3) 保存后回首页重连一次生效。",
    "quantumultx": "1) 加分流：设置 → 分流 → 引用，粘贴远程 filter 链接；"
                   "2) 加重写：设置 → 重写 → 引用，粘贴重写链接；3) 保存后打开开关重连生效。",
    "surge": "1) 加规则：设置 → 规则 → 添加远程 RULE-SET，粘贴规则链接；"
             "2) 加模块：设置 → 模块 → 安装新模块，粘贴 .sgmodule 链接；3) 保存并重连生效。",
    "loon": "1) 加规则：配置 → 规则 → 远程规则，粘贴订阅链接；"
            "2) 加插件：配置 → 插件 → 加号粘贴 .plugin 链接安装；3) 保存后重连生效。",
    "mihomo": "1) 加规则集：在配置文件或覆写里给 rule-provider 填远程规则集链接；"
              "2) 也可以直接用「配套规则」里那条现成链接填进配置；"
              "3) 保存后重启内核或重连生效；4) 它没有模块，扩展靠覆写脚本，在覆写里粘贴脚本链接。",
    "sing-box": "1) 加规则集：在配置 JSON 的 route.rule_set 里填远程规则集地址；"
                "2) 用图形客户端（如 Karing）就在设置 → 规则集里粘贴链接并更新；"
                "3) 保存后重连生效；4) 它没有模块，功能都靠配置文件。",
    "xray": "1) 加规则（以 v2rayNG 为例）：设置 → 路由设置 → 添加规则集，粘贴规则或选 geosite 分组；"
            "2) geo 文件在设置里点更新；3) 保存后重连生效。",
    None: "这个工具不用往里加规则和模块，打开按它自己的界面用就行。",
}


def kb_match(item):
    """按名称/网址识别已知工具，返回知识库条目或 None。"""
    probe = ((item.get("name") or "") + " " + (item.get("url") or "")
             + " " + (item.get("sourceUrl") or "")).lower()
    for entry in PROXY_KB:
        if any(k in probe for k in entry[0]):
            return entry
    return None


def ensure_tool_fields(item):
    """工具类四项说明 + 可加的东西：已知工具按知识库如实写；
    认不出的写未公布/暂无配套，label 不许缺。返回命中的知识库条目或 None。"""
    entry = kb_match(item)
    if entry:
        _keys, fam, rule, module, split, addons = entry
        set_detail(item, "规则格式", rule)
        set_detail(item, "模块支持", module)
        set_detail(item, "分流方式", split)
        set_detail(item, "配套规则",
                   PROXY_FAMILY_URL.format(fam) if fam else "暂无配套")
        set_detail(item, "可加的东西", addons)
        return entry
    for lb in ("规则格式", "模块支持", "分流方式", "可加的东西"):
        if not get_detail(item, lb):
            set_detail(item, lb, "未公布")
    if not get_detail(item, "配套规则"):
        set_detail(item, "配套规则", "暂无配套")
    return None


VERSION_REQUIRED_SLUGS = ("app", "apple", "proxy")


def cn_explain(item, slug):
    """给英文条目拼一段大白话中文说明（模板句把关键事实讲清）。"""
    kind, use, who = CAT_CONTEXT.get(slug, ("网站/工具", "解决实际问题", "有需要的人"))
    name = item.get("name") or "这个条目"
    summary = (item.get("summary") or "").strip()
    head = f"{name} 是一个{kind}。"
    mid = f"它主要用来{use}。"
    if summary and judge_language(summary) in ("中文", "中英双语"):
        mid = f"{summary.rstrip('。')}。它主要用来{use}。"
    return f"{head}{mid}适合{who}。页面是英文的，可以用浏览器自动翻译看。"


# ---------------- 免费服务/域名：固定四问字段 ----------------

DOMAIN_Q = ["免费时长", "能否续费", "续费价格", "能否进 CF"]
HOST_Q = ["免费时长", "机器配置", "申请要求", "能干什么"]
EMAIL_Q = ["免费时长", "容量与发信量", "申请要求", "能干什么"]
OTHER_Q = ["免费时长", "额度", "申请要求", "能干什么"]


def field_set_for(slug, item):
    """按栏目/子类决定四问是哪四个 label。"""
    if slug == "domain":
        return "domain", DOMAIN_Q
    sub = get_detail(item, "类别")
    if "域名" in sub:
        return "domain", DOMAIN_Q
    if "主机" in sub or "托管" in sub:
        return "host", HOST_Q
    if "邮箱" in sub:
        return "email", EMAIL_Q
    return "other", OTHER_Q


def _apply_reqs(t, out):
    reqs = []
    for k, label in (("免实名", "免实名"), ("不用绑卡", "不用绑卡"),
                     ("实名", "要实名"), ("绑卡", "要绑卡"),
                     ("信用卡", "要信用卡"), ("国外手机号", "要国外手机号"),
                     ("手机号", "要手机号")):
        if k in t and label not in reqs:
            reqs.append(label)
    # 「免实名」含「实名」、「不用绑卡」含「绑卡」，冲突时只留免的
    if "免实名" in reqs and "要实名" in reqs:
        reqs.remove("要实名")
    if "不用绑卡" in reqs and "要绑卡" in reqs:
        reqs.remove("要绑卡")
    if reqs:
        out["申请要求"] = "、".join(reqs[:3])


def extract_fields(kind, text):
    """从正文里找四问的答案（只认明写的数），找不到的键不出现。"""
    t = text or ""
    out = {}
    if re.search(r"永久免费|长期免费|一直免费", t):
        out["免费时长"] = "长期/永久免费"
    else:
        m = re.search(r"免费\s*\d+\s*(?:年|个月|月|天)|试用\s*\d+\s*天|免费一年|一年免费", t)
        if m:
            out["免费时长"] = m.group(0).replace(" ", "")
    if kind == "domain":
        if re.search(r"不能续费|不可续费|无法续费", t):
            out["能否续费"] = "不能续费"
        elif re.search(r"免费续费|续费免费", t):
            out["能否续费"] = "能，免费续费"
        elif "续费" in t:
            out["能否续费"] = "能续费"
        if re.search(r"免费续费|续费免费|续费不要钱", t):
            out["续费价格"] = "免费"
        else:
            m = re.search(r"续费[^。\n]{0,12}?(?:\$|¥|￥)\s*\d+(?:\.\d+)?"
                          r"|续费[^。\n]{0,12}?\d+\s*(?:元|美元)", t)
            if m:
                out["续费价格"] = m.group(0)
        if re.search(r"(?:整户|整个域名|根域名)[^。\n]{0,14}?(?:加进|加入|接入)\s*Cloudflare"
                     r"|加进\s*Cloudflare|加入\s*Cloudflare|整户加入", t):
            out["能否进 CF"] = "能整户加进 Cloudflare"
        elif re.search(r"(?:不能|不支持|没法)[^。\n]{0,10}?(?:加进|加入)?\s*Cloudflare"
                       r"|不是根域名", t):
            out["能否进 CF"] = "不能整户加进 Cloudflare"
    elif kind == "host":
        cfgs = []
        for c in re.findall(r"\d+\s*(?:核|vCPU|GB|MB|TB|核心)", t):
            if c not in cfgs:
                cfgs.append(c)
        if cfgs:
            out["机器配置"] = "、".join(cfgs[:4])
        _apply_reqs(t, out)
    elif kind == "email":
        caps = []
        for c in re.findall(r"\d+\s*(?:GB|MB|封/天|封/月|封/日|条/天|个别名|个邮箱)", t):
            if c not in caps:
                caps.append(c)
        if caps:
            out["容量与发信量"] = "、".join(caps[:4])
        _apply_reqs(t, out)
    else:
        m = re.search(r"免费额度[^。\n]{0,16}|\d+\s*(?:GB|TB|次|个|条)\s*(?:免费|额度)", t)
        if m:
            out["额度"] = m.group(0).strip()
        _apply_reqs(t, out)
    return out


def ensure_service_fields(slug, item, text):
    """四问 label 必须齐：已有真实值不动，未公布可被新查到的覆盖，
    缺的先填查到的、实在查不到填「未公布」。不许留空、不许瞎编。"""
    kind, labels = field_set_for(slug, item)
    found = extract_fields(kind, text)
    if kind in ("host", "email", "other") and (item.get("summary") or "").strip():
        found.setdefault("能干什么", (item["summary"] or "").strip()[:60])
    for label in labels:
        cur = get_detail(item, label)
        if cur and cur != "未公布":
            continue
        if found.get(label):
            set_detail(item, label, found[label])
        elif not cur:
            set_detail(item, label, "未公布")


def _price_amount(s):
    """价格字符串 → 数字（Free/免费 = 0），解析不出返回 None。"""
    if not s:
        return None
    if "Free" in s or "免费" in s:
        return 0.0
    m = re.search(r"(\d+(?:\.\d+)?)", s)
    return float(m.group(1)) if m else None


# ---------------- 条目标注：语言 / 下载地区 / 能否改中文 ----------------

def label_item(slug, item, page=None, html_text=""):
    """给一条条目补「语言」「下载地区」「能否改中文」（只用实测证据）。
    page/html_text 是验活时抓到的页面（可为空）。"""
    url = (item.get("url") or "").strip()
    host = urllib.parse.urlparse(url).netloc.lower()
    app_id = apple_id_of(url) if "apple.com" in host else None

    # --- 苹果应用：iTunes 说了算 ---
    if app_id and "apps.apple.com" in host:
        rec = itunes_lookup(app_id)
        if not rec:
            # 商店里查不到这个应用了（多半已下架）：按内容失效记账
            item["_content_fail"] = True
        if rec:
            if rec.get("formattedPrice"):
                fp = str(rec["formattedPrice"])
                set_detail(item, "现价", "免费" if fp == "Free" else fp)
                set_detail(item, "收费", "免费" if fp == "Free" else f"收费（{fp}）")
            if rec.get("version"):
                set_detail(item, "最新版本", str(rec["version"]))
            if rec.get("currentVersionReleaseDate"):
                set_detail(item, "版本日期",
                           str(rec["currentVersionReleaseDate"])[:10])
            codes = rec.get("languageCodesISO2A") or rec.get("languages") or []
            label = lang_label_from_codes(codes)
            if label:
                set_detail(item, "语言", label)
                bump("lang")
            ups = [str(c).upper()[:2] for c in codes]
            if ups:
                set_detail(item, "能否改中文",
                           "可切换中文" if "ZH" in ups else "只有英文，改不了")
                bump("cnswitch")
                if "ZH" not in ups:
                    bump("cnswitch_en")
            cn_rec = itunes_lookup(app_id, country="CN")
            if cn_rec:
                set_detail(item, "下载地区", "国内 ID 可下")
            else:
                set_detail(item, "下载地区", "需国外 ID")
            bump("region")
        return

    # --- 安卓应用（app 栏） ---
    if slug == "app":
        pkg = fdroid_package_of(url)
        play_pkg = None
        if "play.google.com" in host:
            q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            play_pkg = (q.get("id") or [None])[0]
        if pkg:
            set_detail(item, "下载地区", "国内 ID 可下")
            bump("region")
            ver = fdroid_version(pkg)
            if ver:
                if ver[0]:
                    set_detail(item, "最新版本", ver[0])
                if ver[1]:
                    set_detail(item, "版本日期", ver[1])
            text = fdroid_page_text(pkg)
            repo = github_repo_of(item.get("sourceUrl") or "") or github_repo_of(url)
            if repo:
                text += "\n" + github_readme(repo)
            set_detail(item, "能否改中文", cn_switch_from_evidence(text))
            bump("cnswitch")
            lang = judge_language(text) if text else None
            if lang:
                set_detail(item, "语言", lang)
                bump("lang")
            return
        if play_pkg:
            has = fdroid_has_package(play_pkg)
            if has is True:
                set_detail(item, "下载地区", "国内 ID 可下")
                bump("region")
                text = fdroid_page_text(play_pkg)
                set_detail(item, "能否改中文", cn_switch_from_evidence(text))
                bump("cnswitch")
            elif has is False:
                set_detail(item, "下载地区", "需国外 ID")
                bump("region")
            return
        # 其他来源（官网/GitHub）：查 F-Droid 搜索接口有没有这个应用
        repo = github_repo_of(url) or github_repo_of(item.get("sourceUrl") or "")
        if repo:
            text = github_readme(repo)
            set_detail(item, "能否改中文", cn_switch_from_evidence(text))
            bump("cnswitch")
            lang = judge_language(text) if text else None
            if lang:
                set_detail(item, "语言", lang)
                bump("lang")
        return

    # --- 苹果栏的 TestFlight 等非商店链接：退回页面判语言 ---
    # --- 通用网站/文件：页面文字判语言 ---
    text = ""
    if page:
        text = "\n".join([page.title, page.meta_desc, page.og_desc] + page.paras[:4])
    if not text.strip():
        text = " ".join([item.get("name") or "", item.get("summary") or "",
                         item.get("descriptionOriginal") or ""])
    lang = judge_language(text, page.lang if page else "")
    if lang:
        set_detail(item, "语言", lang)
        bump("lang")
    # 能否改中文（网站类）
    if lang == "中文" or lang == "中英双语":
        set_detail(item, "能否改中文", "可切换中文")
        bump("cnswitch")
    elif lang == "英文":
        if has_zh_switch(html_text, page):
            set_detail(item, "能否改中文", "可切换中文")
        elif page is not None:
            set_detail(item, "能否改中文", "只有英文，改不了（浏览器可自动翻译）")
        else:
            set_detail(item, "能否改中文", "未公布")
        bump("cnswitch")
    # 英文条目缺中文说明 → 补一条，不动原 description
    if lang == "英文" and not (item.get("descriptionOriginal") or "").strip():
        desc = item.get("description") or ""
        if judge_language(desc) == "英文" and not get_detail(item, "中文说明"):
            set_detail(item, "中文说明", cn_explain(item, slug))


# ---------------- 现有条目刷新（v1 + 标注） ----------------

def process_item(slug, item, state_fails):
    """刷新一条现有条目，返回是否验活通。state_fails 在这里更新计数。"""
    url = (item.get("url") or "").strip()
    key = norm_url(url) if url else ""
    if not url:
        bump("dead")
        return False
    today = today_cst()
    # 记住进来之前的连续失败次数：内容级失效要在旧账上接着记，
    # 不能被本轮的「网络通」先清零，否则永远攒不够移除次数
    prev_fails = int(state_fails.get(key, 0))

    alive = False
    body = b""
    ct = ""
    try:
        status, ct, body = fetch(url)
        # 404/410 是资源明确没了，算不通；403 可能是防爬，不算
        alive = status < 500 and status not in (404, 410)
    except Exception:
        alive = False
    set_detail(item, "最近检查", f"{today} {'通' if alive else '不通'}")
    if alive:
        bump("checked_ok")
        item["verifiedAt"] = today
        state_fails[key] = 0
    else:
        bump("dead")
        state_fails[key] = int(state_fails.get(key, 0)) + 1

    if alive and body:
        counted = count_content(url, body)
        if counted:
            label, n = counted
            if label != "书源数" or slug == "book":
                set_detail(item, label, str(n))
                bump("counted")
            if n == 0:
                # 内容级验活：解得开但条数为 0 = 假活，按失败对待
                item["_content_fail"] = True
        elif slug in ("video", "music", "book", "live", "node", "cfip"):
            # 这些栏目的直链文件本应数得出条数；解不开同样按失败对待
            path = urllib.parse.urlparse(url).path.lower()
            if path.endswith((".json", ".m3u", ".m3u8", ".txt")):
                item["_content_fail"] = True

    # 直链文件栏目返回空内容（0 字节）同样是假活，按内容失效记账
    if alive and not body and slug in ("video", "music", "book", "live", "node", "cfip"):
        item["_content_fail"] = True

    # 下载链接硬规矩：旧版本固定直链改成最新版发布页
    if slug in VERSION_REQUIRED_SLUGS:
        m = re.match(r"(https?://github\.com/[^/]+/[^/]+)/releases/download/", url)
        if m:
            url = m.group(1) + "/releases"
            item["url"] = url

    repo = github_repo_of(url) or github_repo_of(item.get("sourceUrl") or "")
    if repo:
        info = github_info(repo)
        if info:
            changed = False
            if info.get("pushed_at"):
                set_detail(item, "最近更新", info["pushed_at"])
                changed = True
            if info.get("stars") is not None:
                set_detail(item, "星标数", str(info["stars"]))
                changed = True
            if changed:
                bump("github")
        # GitHub 托管的软件：查最新 release 的版本和日期
        if slug in VERSION_REQUIRED_SLUGS:
            rel = github_release(repo)
            if rel:
                set_detail(item, "最新版本", rel[0])
                if rel[1]:
                    set_detail(item, "版本日期", rel[1])

    # 代理工具栏：平台/官方下载/收费（只认官方来源）+ 四项必备说明
    if slug == "proxy":
        plat = PROXY_PLATFORMS.get(item.get("section") or "")
        if plat:
            set_detail(item, "平台", plat)
        if repo:
            set_detail(item, "官方下载", f"https://github.com/{repo}/releases")
            if not get_detail(item, "收费"):
                set_detail(item, "收费", "免费（开源）")
        ensure_tool_fields(item)

    page = parse_page(body) if (alive and body and "html" in ct) else None
    # 网站类：标题/介绍变了就更新原文介绍
    if page is not None and (item.get("descriptionOriginal") or "").strip():
        new_intro = (page.og_desc or page.meta_desc or "").strip()
        if new_intro and new_intro != item["descriptionOriginal"].strip():
            item["descriptionOriginal"] = new_intro
    # 免费服务/域名：固定四问字段补齐（查不到写未公布）
    if slug in ("service", "domain"):
        search_text = ""
        if page is not None:
            search_text = "\n".join([page.title, page.meta_desc,
                                     page.og_desc] + page.paras[:4])
        search_text += "\n" + (item.get("description") or "") \
            + "\n" + (item.get("summary") or "")
        ensure_service_fields(slug, item, search_text)
    html_text = ""
    if page is not None:
        try:
            html_text = body.decode("utf-8", "replace")
        except Exception:
            html_text = ""
    label_item(slug, item, page=page, html_text=html_text)

    # 苹果价格死规矩：现价 label 必带；TestFlight 写免费参加；降价组算降幅；
    # 零元购组固定字段补齐（查不到写未公布）
    if slug == "apple":
        if "testflight.apple.com" in url:
            set_detail(item, "现价", "免费参加")
            if not get_detail(item, "名额状态"):
                set_detail(item, "名额状态", "未公布")
        elif not get_detail(item, "现价"):
            set_detail(item, "现价", "未公布")
        if (item.get("section") or "") == "discounts":
            if not get_detail(item, "原价"):
                set_detail(item, "原价", "未公布")
            cur = _price_amount(get_detail(item, "现价"))
            orig = _price_amount(get_detail(item, "原价"))
            if cur is not None and orig and cur < orig:
                pct = round((1 - cur / orig) * 100)
                set_detail(item, "降幅",
                           f"原价 {get_detail(item, '原价')}，现价 "
                           f"{get_detail(item, '现价')}，降了约 {pct}%")
        if (item.get("section") or "") == "iapfree":
            for lb in ("内购项目", "原价", "买断性质"):
                if not get_detail(item, lb):
                    set_detail(item, lb, "未公布")
            if not get_detail(item, "现价"):
                set_detail(item, "现价", "未公布")

    # 版本硬规矩：app/apple/proxy 三栏的最新版本、版本日期 label 不许缺
    if slug in VERSION_REQUIRED_SLUGS:
        if not get_detail(item, "最新版本"):
            set_detail(item, "最新版本", "未公布")
        if not get_detail(item, "版本日期"):
            set_detail(item, "版本日期", "未公布")

    # 工具类条目（apple/app 里的代理/脚本类工具）：四项说明+可加的东西+上手步骤
    if slug in ("apple", "app", "proxy"):
        entry = kb_match(item)
        if entry:
            ensure_tool_fields(item)
            if slug in ("apple", "app") and "工具类" not in (item.get("tags") or []):
                item.setdefault("tags", []).append("工具类")
            steps = TOOL_STEPS.get(entry[1])
            if steps and not get_detail(item, "上手步骤"):
                set_detail(item, "上手步骤", steps)

    if not (item.get("imageUrl") or "").strip():
        img = find_image(url)
        if img:
            item["imageUrl"] = img
            bump("imaged")
    # 内容级验活汇总：假活按不通补记（连续失败计数、验活阀都算它）
    if item.pop("_content_fail", False) and alive:
        bump("content_dead")
        bump("dead")  # 30% 验活安全阀也要算上它，不然阀门看不见这类失效
        set_detail(item, "最近检查", f"{today} 不通")
        # 在旧账上 +1（进来时 prev_fails），不许被本轮「网络通」的清零洗掉
        state_fails[key] = prev_fails + 1
        return False
    return alive


# ---------------- 去重（网址规范化 + 名称近似） ----------------

TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term",
                   "utm_content", "ref", "referrer", "spm", "from", "fbclid",
                   "gclid", "share", "source"}


def norm_url(url):
    """网址规范化：小写协议/主机、去尾斜杠、去跟踪参数、去锚点。"""
    try:
        p = urllib.parse.urlparse((url or "").strip())
        scheme = (p.scheme or "https").lower()
        host = (p.hostname or "").lower()
        port = f":{p.port}" if p.port and p.port not in (80, 443) else ""
        path = p.path or "/"
        if path != "/" and path.endswith("/"):
            path = path.rstrip("/")
        qs = [(k, v) for k, v in urllib.parse.parse_qsl(p.query)
              if k.lower() not in TRACKING_PARAMS]
        query = urllib.parse.urlencode(sorted(qs))
        return f"{scheme}://{host}{port}{path}" + (f"?{query}" if query else "")
    except Exception:
        return (url or "").strip().lower()


def norm_name(name):
    """名称规范化：小写、去空格标点，只留字母数字和中文。"""
    s = re.sub(r"[^0-9a-z一-鿿]+", "", (name or "").lower())
    return s


def name_similar(a, b):
    """名称近似：规范化后相等，或一方包含另一方（长度>=4 时）。"""
    na, nb = norm_name(a), norm_name(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if min(len(na), len(nb)) >= 4 and (na in nb or nb in na):
        return True
    return False


# ---------------- 发现：拉 feed 候选 ----------------

def feed_candidates_github_search(feed):
    """GitHub 搜索：至少翻前 3 页，把长尾也拉全（不只取热门第一页）。"""
    per_page = 20 if GH_TOKEN else 10
    q = urllib.parse.quote(feed.get("query") or "")
    out = []
    pages = 0
    for page in range(1, 4):
        url = (f"https://api.github.com/search/repositories?q={q}"
               f"&sort=updated&order=desc&per_page={per_page}&page={page}")
        status, _ct, body = gh_fetch(url, max_bytes=1024 * 1024)
        if status != 200:
            if page == 1:
                raise RuntimeError(f"GitHub 搜索返回 {status}")
            break
        pages += 1
        data = json.loads(body.decode("utf-8", "replace"))
        items = data.get("items") or []
        for r in items:
            out.append({"name": r.get("full_name") or r.get("name") or "",
                        "url": r.get("html_url") or "",
                        "desc": r.get("description") or "",
                        "updated": (r.get("pushed_at") or "")[:10],
                        "stars": r.get("stargazers_count")})
        if len(items) < per_page:
            break
    log(f"GitHub 搜索「{feed.get('query')}」翻了 {pages} 页，{len(out)} 个候选")
    return out


def _generic_json_candidates(data):
    """repo_json 的通用解析：认 list / sites / results / packages 几种结构。"""
    out = []
    if isinstance(data, list):
        for e in data:
            if isinstance(e, dict):
                name = e.get("name") or e.get("title") or ""
                u = e.get("url") or e.get("link") or e.get("api") or e.get("href") or ""
                if name and u:
                    out.append({"name": str(name), "url": str(u),
                                "desc": str(e.get("desc") or e.get("description") or "")})
            elif isinstance(e, str) and e.startswith("http"):
                out.append({"name": e, "url": e, "desc": ""})
    elif isinstance(data, dict):
        if isinstance(data.get("sites"), list):
            for s in data["sites"]:
                if isinstance(s, dict) and s.get("name") and s.get("api"):
                    out.append({"name": str(s["name"]), "url": str(s["api"]),
                                "desc": "", "kind_hint": "api"})
        feed = data.get("feed") if isinstance(data.get("feed"), dict) else {}
        results = data.get("results") or feed.get("results") or []
        for e in results:
            if isinstance(e, dict) and e.get("name") and e.get("url"):
                out.append({"name": str(e["name"]), "url": str(e["url"]),
                            "desc": str(e.get("artistName") or "")})
        plugins = data.get("plugins")
        if isinstance(plugins, list):
            for e in plugins:
                if isinstance(e, dict) and e.get("name"):
                    u = e.get("url") or ""
                    if u:
                        out.append({"name": str(e["name"]), "url": str(u),
                                    "desc": str(e.get("desc") or "")})
        packages = data.get("packages")
        if isinstance(packages, dict):
            for pkg in list(packages.keys())[:1000]:
                out.append({"name": pkg,
                            "url": f"https://f-droid.org/en/packages/{pkg}/",
                            "desc": ""})
    return out


def feed_candidates_repo_json(feed):
    status, _ct, body = fetch(feed["url"], max_bytes=MAX_BYTES)
    if status != 200:
        raise RuntimeError(f"清单返回 {status}")
    data = json.loads(body.decode("utf-8", "replace"))
    return _generic_json_candidates(data)


def feed_candidates_rss(feed):
    status, _ct, body = fetch(feed["url"], max_bytes=2 * 1024 * 1024)
    if status != 200:
        raise RuntimeError(f"RSS 返回 {status}")
    text = body.decode("utf-8", "replace")
    if text.lstrip().startswith("{"):
        return _generic_json_candidates(json.loads(text))
    out = []
    for m in re.finditer(r"<item>(.*?)</item>", text, re.S):
        chunk = m.group(1)
        t = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", chunk, re.S)
        l = re.search(r"<link>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</link>", chunk, re.S)
        if t and l:
            out.append({"name": re.sub(r"<[^>]+>", "", t.group(1)).strip(),
                        "url": l.group(1).strip(), "desc": ""})
    return out


LINK_RE = re.compile(r'<a\s[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.S | re.I)


def feed_candidates_page_links(feed):
    status, _ct, body = fetch(feed["url"], max_bytes=2 * 1024 * 1024)
    if status != 200:
        raise RuntimeError(f"导航页返回 {status}")
    text = body.decode("utf-8", "replace")
    base_host = urllib.parse.urlparse(feed["url"]).netloc.lower()
    out = []
    for href, inner in LINK_RE.findall(text):
        name = re.sub(r"<[^>]+>", "", inner)
        name = re.sub(r"\s+", " ", name).strip()
        full = urllib.parse.urljoin(feed["url"], href)
        p = urllib.parse.urlparse(full)
        if p.scheme not in ("http", "https") or not p.netloc:
            continue
        if p.netloc.lower() == base_host:
            continue
        if len(name) < 2 or len(name) > 60:
            continue
        if any(k in full.lower() for k in ("login", "signup", "register", "share?",
                                           "twitter.com/intent", "facebook.com/sharer")):
            continue
        out.append({"name": name, "url": full, "desc": ""})
    return out


FEED_FUNCS = {"github_search": feed_candidates_github_search,
              "repo_json": feed_candidates_repo_json,
              "rss": feed_candidates_rss,
              "page_links": feed_candidates_page_links}


def load_feeds(script_dir):
    path = os.path.join(script_dir, "feeds.json")
    try:
        with open(path, encoding="utf-8") as f:
            return (json.load(f).get("categories") or {})
    except Exception as e:
        log(f"feeds.json 读不了（{e}），本次跳过发现流程")
        return {}


def pull_candidates(slug, feeds):
    """逐个 feed 拉候选；单源失败只跳过记日志，绝不让整栏落空。"""
    out = []
    for feed in feeds:
        fid = feed.get("query") or feed.get("url") or "?"
        func = FEED_FUNCS.get(feed.get("type"))
        try:
            if func is None:
                raise RuntimeError(f"未知 feed 类型 {feed.get('type')}")
            cands = func(feed)
            for c in cands:
                if feed.get("section"):
                    c.setdefault("section", feed["section"])
                if feed.get("subcat"):
                    c.setdefault("subcat", feed["subcat"])
            FEED_LOG.append(f"{slug} | {feed.get('type')} | {fid} | 候选 {len(cands)}")
            out.extend(cands)
        except Exception as e:
            FEED_LOG.append(f"{slug} | {feed.get('type')} | {fid} | 失败：{e}")
            log(f"feed 失败（跳过）：{slug} {feed.get('type')} {fid}：{e}")
    FEED_CANDIDATES[slug] = len(out)
    return out


# ---------------- 深挖：把候选挖出实义内容 ----------------

PARKED_HINTS = ("parked", "domain for sale", "buy this domain", "该域名",
                "域名停放", "domain parking", "coming soon")


def deep_dive(slug, cand):
    """深挖一个候选，返回 (material, extra) 或 (None, reason)。
    material: {'title','text','lang','counted','github','itunes'}"""
    url = cand["url"]
    host = urllib.parse.urlparse(url).netloc.lower()

    # 苹果应用：iTunes 完整介绍
    app_id = apple_id_of(url) if "apps.apple.com" in host else None
    if app_id:
        rec = itunes_lookup(app_id)
        if not rec:
            return None, "iTunes 查不到这个应用"
        text = (rec.get("description") or "").strip()
        if len(text) < 20:
            return None, "iTunes 介绍太短"
        return {"title": rec.get("trackName") or cand["name"], "text": text[:1500],
                "lang": lang_label_from_codes(rec.get("languageCodesISO2A") or []),
                "counted": None, "github": None, "itunes": rec}, None

    # F-Droid 应用：包页完整介绍
    pkg = fdroid_package_of(url)
    if pkg:
        text = fdroid_page_text(pkg)
        if len(text) < 40:
            return None, "F-Droid 介绍太短"
        return {"title": cand["name"], "text": text[:1500],
                "lang": judge_language(text), "counted": None,
                "github": None, "itunes": None}, None

    # GitHub 仓库：仓库简介 + README 开头几段
    repo = github_repo_of(url)
    if repo:
        info = github_info(repo)
        if not info:
            return None, "GitHub 仓库信息拿不到"
        readme = github_readme(repo, info)
        text = "\n".join(x for x in [info.get("description") or "", readme] if x)
        if len(text.strip()) < 25:
            return None, "仓库没简介、README 也没内容"
        return {"title": cand["name"], "text": text[:1500],
                "lang": judge_language(text), "counted": None,
                "github": info, "itunes": None}, None

    # 普通网址/直链：验活 + 抓页面
    try:
        status, ct, body = fetch(url)
    except Exception as e:
        return None, f"打不开：{e}"
    if status >= 500:
        return None, f"站点返回 {status}"
    counted = count_content(url, body) if body else None
    if counted and (counted[0] != "书源数" or slug == "book"):
        # 直链文件本身就是内容（如一份配置/一份名单），用条数当实义材料
        label, n = counted
        return {"title": cand["name"],
                "text": f"这是一个可以直接导入使用的文件，实测里面共有 {n} 条内容（{label}）。",
                "lang": judge_language(cand.get("desc") or "") or "中文",
                "counted": counted, "github": None, "itunes": None}, None
    page = parse_page(body) if body else None
    if page is None:
        return None, "页面不是网页、也没有可数的内容"
    low = (page.title + " " + (page.paras[0] if page.paras else "")).lower()
    if any(h in low for h in PARKED_HINTS) and len(page.paras) < 2:
        return None, "疑似停放域名/空壳页"
    text = "\n".join(x for x in [page.meta_desc or page.og_desc] + page.paras[:3] if x)
    if len(text.strip()) < 40:
        return None, "页面正文太少，挖不出实义内容"
    return {"title": page.title or cand["name"], "text": text[:1500],
            "lang": judge_language(text, page.lang), "counted": None,
            "github": None, "itunes": None, "page": page}, None


def build_item(slug, cand, material, new_id):
    """用深挖材料拼新条目：四样齐全（定位/功能/用途/适合谁）。"""
    kind, use, who = CAT_CONTEXT.get(slug, ("网站/工具", "解决实际问题", "有需要的人"))
    name = (material.get("title") or cand["name"]).strip()[:80]
    url = cand["url"]
    host = urllib.parse.urlparse(url).netloc
    original = re.sub(r"\s+", " ", material["text"]).strip()
    lang = material.get("lang")
    is_english = (lang == "英文") or (lang and "英文" in str(lang) and "中文" not in str(lang))

    item = {
        "access": "direct", "builtinConfigName": "", "builtinConfigUrl": "",
        "description": "", "descriptionOriginal": "", "details": [],
        "id": new_id, "imageUrl": "", "name": name, "rank": 999,
        "region": "", "score": 0, "screenshots": [],
        "section": cand.get("section") or "main",
        "sourceUrl": "", "summary": "", "tags": ["自动收录"],
        "url": url, "usageKind": "", "usageNote": "",
        "verifiedAt": today_cst(), "warning": "", "auto": True,
    }
    position = f"{name} 是{host} 上的一个{kind}"
    if material.get("github"):
        position = f"{name} 是 GitHub 上的一个开源{kind}"
    if is_english:
        item["descriptionOriginal"] = original[:1200]
        item["description"] = (
            f"{position}。它主要用来{use}。适合{who}。"
            f"上面一段是它自己的英文原文介绍，页面也是英文的，可以用浏览器自动翻译看。")
        item["summary"] = f"{kind}（英文）。主要用来{use}，适合{who}。"
    else:
        item["description"] = (
            f"{position}。{original[:800]}。"
            f"它主要用来{use}。适合{who}。")
        item["summary"] = original[:70].rstrip("。") + "。" if len(original) > 10 \
            else f"{kind}。主要用来{use}，适合{who}。"
    # details：实测数据
    set_detail(item, "最近检查", f"{today_cst()} 通")
    if material.get("counted"):
        set_detail(item, material["counted"][0], str(material["counted"][1]))
    gh = material.get("github")
    if gh:
        if gh.get("pushed_at"):
            set_detail(item, "最近更新", gh["pushed_at"])
        if gh.get("stars") is not None:
            set_detail(item, "星标数", str(gh["stars"]))
    # usageKind 默认规则：四栏直链文件=config、单个接口=api，拿不准留空
    if slug in ("video", "music", "book", "live"):
        if cand.get("kind_hint") == "api":
            item["usageKind"] = "api"
        elif material.get("counted"):
            item["usageKind"] = "config"
    elif slug == "node" and material.get("counted"):
        item["usageKind"] = "config"
    # 免费服务：子类标进 details「类别」；服务/域名两栏补齐四问
    if slug == "service" and cand.get("subcat"):
        set_detail(item, "类别", cand["subcat"])
    if slug in ("service", "domain"):
        ensure_service_fields(slug, item,
                              original + " " + (cand.get("desc") or ""))
    # 破解类条目强制带标准风险警告
    if "cracked" in (item.get("section") or ""):
        item["warning"] = CRACKED_WARNING
    # IPA 自签包：标「类型：正版包/破解包」，破解包带风险警告，介绍带自签方法指向
    if slug == "apple" and item.get("section") == "ipasign":
        probe = (name + " " + original + " " + (cand.get("desc") or "")).lower()
        cracked_keys = ("破解", "注入", "解锁", "去广告", "修改版",
                        "cracked", "modded", "patched", "hacked")
        is_cracked = any(k in probe for k in cracked_keys)
        set_detail(item, "类型", "破解包" if is_cracked else "正版包")
        if is_cracked:
            item["warning"] = ("破解包来源不明，可能被塞东西，证书会掉签；"
                               "只收相对知名的来源，重要设备别装，拿不准就别下")
        item["description"] += ("自签后就能装到自己手机上用。方法：用轻松签、"
                                "AltStore 或 SideStore 这类工具自己签名安装，不用越狱。")
    # 标注（语言/地区/能否改中文）
    label_item(slug, item, page=material.get("page"))
    item.pop("_content_fail", None)  # 新条目的内容失效标记不落盘
    # 工具类（apple/app/proxy 里认得出的可扩展工具）：四项说明+可加的东西，
    # 上手步骤直接写进简介，新手照着能做
    if slug in ("apple", "app", "proxy"):
        entry = kb_match(item)
        if entry:
            ensure_tool_fields(item)
            if slug in ("apple", "app") and "工具类" not in item["tags"]:
                item["tags"].append("工具类")
            steps = TOOL_STEPS.get(entry[1])
            if steps and "上手步骤" not in item["description"]:
                item["description"] += "上手步骤：" + steps
    mlang = material.get("lang")
    if mlang in ("英文", "中文", "中英双语"):
        set_detail(item, "语言", mlang)
    # 版本硬规矩 + 代理栏字段 + 苹果价格兜底（新条目同样适用）
    if slug in VERSION_REQUIRED_SLUGS:
        if material.get("github"):
            repo = github_repo_of(item["url"])
            rel = github_release(repo) if repo else None
            if rel:
                set_detail(item, "最新版本", rel[0])
                if rel[1]:
                    set_detail(item, "版本日期", rel[1])
        if not get_detail(item, "最新版本"):
            set_detail(item, "最新版本", "未公布")
        if not get_detail(item, "版本日期"):
            set_detail(item, "版本日期", "未公布")
    if slug == "proxy":
        plat = PROXY_PLATFORMS.get(item.get("section") or "")
        if plat:
            set_detail(item, "平台", plat)
        repo = github_repo_of(item["url"])
        if repo:
            set_detail(item, "官方下载", f"https://github.com/{repo}/releases")
            if not get_detail(item, "收费"):
                set_detail(item, "收费", "免费（开源）")
        ensure_tool_fields(item)
    if slug == "apple":
        if "testflight.apple.com" in item["url"]:
            set_detail(item, "现价", "免费参加")
            set_detail(item, "名额状态", "未公布")
        elif not get_detail(item, "现价"):
            set_detail(item, "现价", "未公布")
        if item.get("section") == "iapfree":
            for lb in ("内购项目", "原价", "买断性质"):
                if not get_detail(item, lb):
                    set_detail(item, lb, "未公布")
    if not item["imageUrl"]:
        img = find_image(url)
        if img:
            item["imageUrl"] = img
    return item


# ---------------- 栏目结构归一 + 破解警告 ----------------

CRACKED_WARNING = ("破解软件来源不明，可能带病毒或后门；只收相对知名的来源，"
                   "重要设备别装，拿不准就别下")
RISK_WORDS = ("风险", "病毒", "后门", "不明", "别装", "谨慎", "中毒")
APPLE_SECTION_MAP = {"limited_free": "deals", "price_drop": "discounts",
                     "main": "deals"}
APP_SECTIONS = {"android-free", "android-cracked", "pc-free", "pc-cracked"}


def normalize_sections(data):
    """栏目结构归一：apple 旧分组名映射到五类；app 现有条目默认归安卓免费组；
    cracked 分组条目强制带标准风险警告。"""
    cats = data.get("categories") or {}
    apple = cats.get("apple") or {}
    for item in apple.get("items") or []:
        s = item.get("section") or ""
        if s in APPLE_SECTION_MAP:
            item["section"] = APPLE_SECTION_MAP[s]
    app = cats.get("app") or {}
    for item in app.get("items") or []:
        if (item.get("section") or "") not in APP_SECTIONS:
            item["section"] = "android-free"
    for cat in cats.values():
        for item in cat.get("items") or []:
            if "cracked" in (item.get("section") or ""):
                w = item.get("warning") or ""
                if not any(k in w for k in RISK_WORDS):
                    item["warning"] = CRACKED_WARNING


# ---------------- 自动重排 ----------------

def _detail_num(item):
    for label in ("站点数", "频道数", "书源数", "节点数", "条数"):
        v = get_detail(item, label)
        m = re.search(r"\d+", v or "")
        if m:
            return int(m.group())
    return 0


def _recency_days(item):
    v = get_detail(item, "最近更新")
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", v or "")
    if not m:
        return None
    try:
        d = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return (datetime.datetime.now(CST).date() - d).days
    except Exception:
        return None


def _score_of(item, alive, days):
    s = 0
    if alive:
        s += 35
    if days is None:
        s += 5
    elif days <= 30:
        s += 30
    elif days <= 90:
        s += 22
    elif days <= 180:
        s += 15
    elif days <= 365:
        s += 8
    n = _detail_num(item)
    if n >= 200:
        s += 20
    elif n >= 100:
        s += 16
    elif n >= 50:
        s += 12
    elif n >= 20:
        s += 8
    elif n >= 1:
        s += 4
    stars = get_detail(item, "星标数")
    m = re.search(r"\d+", stars or "")
    st = int(m.group()) if m else 0
    if st >= 1000:
        s += 10
    elif st >= 100:
        s += 7
    elif st >= 10:
        s += 4
    elif st >= 1:
        s += 2
    if (item.get("imageUrl") or "").strip():
        s += 5
    if not alive:
        s = min(s, 15)
    return max(0, min(100, int(s)))


def rerank(categories):
    """每栏按 section 分组重排：通的靠前、新的靠前、条数多的靠前；停更沉底、不通垫底。"""
    for slug, cat in categories.items():
        items = cat.get("items") or []
        groups = {}
        for item in items:
            groups.setdefault(item.get("section") or "", []).append(item)
        for group_items in groups.values():
            def sort_key(item):
                alive = not get_detail(item, "最近检查").endswith("不通")
                days = _recency_days(item)
                item["score"] = _score_of(item, alive, days)
                stale = days is not None and days > 365
                grp = 2 if not alive else (1 if stale else 0)
                # 同分时按最近更新排（越新越靠前）
                return (grp, -item["score"],
                        days if days is not None else 9999,
                        item.get("rank") or 999)
            group_items.sort(key=sort_key)
            for idx, item in enumerate(group_items, 1):
                item["rank"] = idx


# ---------------- state.json ----------------

def load_state(script_dir):
    path = os.path.join(script_dir, "state.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        fails = data.get("fails") or {}
        return {str(k): int(v) for k, v in fails.items()}, path
    except Exception:
        return {}, path


def save_state(path, fails, keep_urls):
    data = {"fails": {u: n for u, n in fails.items() if u in keep_urls and n > 0},
            "updatedAt": now_iso_z()}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(tmp, path)


# ---------------- 主流程 ----------------

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    default_path = os.path.join(os.path.dirname(script_dir), "data.json")
    path = sys.argv[1] if len(sys.argv) > 1 else default_path
    root_dir = os.path.dirname(os.path.abspath(path))

    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    categories = data.get("categories") or {}
    state_fails, state_path = load_state(script_dir)

    normalize_sections(data)

    jobs = []
    for slug, cat in categories.items():
        for item in cat.get("items") or []:
            jobs.append((slug, item))
    total_before = len(jobs)
    log(f"共 {total_before} 条，开始验活刷新（并发 10）……")

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        list(ex.map(lambda j: process_item(j[0], j[1], state_fails), jobs))
    log(f"验活：通 {STATS['checked_ok']} / 不通 {STATS['dead']}")

    # 安全阀一：超过 30% 不通 → 中止，不写任何文件
    if total_before and STATS["dead"] > total_before * 0.30:
        log(f"安全阀触发：{STATS['dead']}/{total_before} 条不通（超过 30%），"
            "中止并保留旧数据，不写文件。")
        return 2

    # ---- 发现：逐栏拉候选（单源失败只跳过） ----
    feeds = load_feeds(script_dir)
    pools = {}
    existing_urls = set()
    existing_names = []
    for cat in categories.values():
        for item in cat.get("items") or []:
            if item.get("url"):
                existing_urls.add(norm_url(item["url"]))
            if item.get("sourceUrl"):
                existing_urls.add(norm_url(item["sourceUrl"]))
            if item.get("name"):
                existing_names.append(item["name"])
    def _pull_pool(slug):
        raw = pull_candidates(slug, feeds.get(slug) or [])
        seen = set()
        pool = []
        for cand in raw:
            if not cand.get("url") or not cand.get("name"):
                continue
            nu = norm_url(cand["url"])
            if nu in existing_urls or nu in seen:
                continue
            if any(name_similar(cand["name"], n) for n in existing_names):
                continue
            if any(name_similar(cand["name"], p["name"]) for p in pool):
                continue
            seen.add(nu)
            pool.append(cand)
        # 新鲜优先：有更新日期的排前面且越新越靠前
        pool.sort(key=lambda c: c.get("updated") or "", reverse=True)
        pools[slug] = pool
        log(f"{slug}：候选 {len(raw)} 个，去重后 {len(pool)} 个")

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(_pull_pool, list(categories.keys())))

    # ---- 苹果限免过期复核：deals 里现价不是免费的，降组或移除 ----
    removed_keys = set()
    apple_cat = categories.get("apple") or {}
    for item in apple_cat.get("items") or []:
        if (item.get("section") or "") != "deals":
            continue
        aid = apple_id_of(item.get("url") or "")
        if not aid:
            continue
        rec = itunes_lookup(aid)
        if not rec:
            continue
        price = str(rec.get("formattedPrice") or "")
        if price in ("", "Free"):
            continue
        set_detail(item, "现价", price)
        cur_amt = _price_amount(price)
        orig_amt = _price_amount(get_detail(item, "原价"))
        if orig_amt and cur_amt is not None and cur_amt < orig_amt:
            item["section"] = "discounts"
            MOVED.append(("apple", item.get("name") or "", item.get("url") or "",
                          f"限免结束，现价 {price}，降到降价组"))
        else:
            removed_keys.add(id(item))
            REMOVED.append(("apple", item.get("name") or "", item.get("url") or "",
                            f"限免已过期，现价 {price}"))

    # ---- 移除：连续不通（苹果 3 次、其他栏目 6 次） ----
    for slug, cat in categories.items():
        threshold = 3 if slug == "apple" else 6
        for item in cat.get("items") or []:
            if id(item) in removed_keys:
                continue
            if int(state_fails.get(norm_url(item.get("url") or ""), 0)) >= threshold:
                removed_keys.add(id(item))
                REMOVED.append((slug, item.get("name") or "", item.get("url") or "",
                                f"连续 {threshold} 次验活不通"))

    # ---- 新增：先补位、再扩充；不重复的新条目全数加进（每栏总量上限 300） ----
    max_id = [0]
    _id_lock = threading.Lock()
    for cat in categories.values():
        for item in cat.get("items") or []:
            if isinstance(item.get("id"), int):
                max_id[0] = max(max_id[0], item["id"])

    def _add_for_slug(slug, cat):
        items = cat.get("items") or []
        removed_here = sum(1 for it in items if id(it) in removed_keys)
        room = MAX_PER_CAT - (len(items) - removed_here)
        cap = max(0, min(MAX_ADD_PER_CAT, room))
        if cap == 0:
            return
        added_here = 0
        tried = 0
        for cand in pools.get(slug) or []:
            if added_here >= cap or tried >= MAX_CANDIDATES_TRY:
                break
            tried += 1
            material, reason = deep_dive(slug, cand)
            if material is None:
                ABANDONED.append((cand.get("name") or "", cand.get("url") or "",
                                  reason or "无内容"))
                log(f"放弃候选：{cand.get('name')}（{reason}）")
                continue
            with _id_lock:
                max_id[0] += 1
                new_id = max_id[0]
            new_item = build_item(slug, cand, material, new_id)
            # 新收的限免也要复核现价：已经收费的不要挂进限免组
            if slug == "apple" and new_item.get("section") == "deals":
                aid = apple_id_of(new_item["url"])
                rec = itunes_lookup(aid) if aid else None
                if rec and str(rec.get("formattedPrice") or "") not in ("", "Free"):
                    ABANDONED.append((new_item["name"], new_item["url"],
                                      f"限免已过期，现价 {rec.get('formattedPrice')}"))
                    continue
            items.append(new_item)
            existing_names.append(new_item["name"])
            ADDED.append((slug, new_item["name"], new_item["url"]))
            added_here += 1
            bump("deep_ok")

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(lambda kv: _add_for_slug(kv[0], kv[1]),
                    list(categories.items())))

    # 真正执行移除
    for cat in categories.values():
        if cat.get("items"):
            cat["items"] = [it for it in cat["items"] if id(it) not in removed_keys]

    # 安全阀二：只拦「移除」——一次移除超过全站 25% 多半是源头异常，中止保数据。
    # 新增永不拦：搜到多少不重复的新条目就加多少（站长 2026-11-23 定死）。
    if total_before and len(REMOVED) > total_before * 0.25:
        log(f"移除量安全阀触发：移除 {len(REMOVED)} 条，超过全站 {total_before} 条的 25%，"
            "判定源头异常，中止不写数据。")
        return 3
    if ADDED:
        log(f"本轮新增 {len(ADDED)} 条新条目（不设上限，全数加进）。")

    # ---- 重排 + 总览更新 ----
    rerank(categories)
    ts = now_iso_z()
    overview = data.get("overview") or {}
    for oc in overview.get("categories") or []:
        slug = oc.get("slug")
        if slug in categories:
            oc["count"] = len(categories[slug].get("items") or [])
            oc["lastSuccessAt"] = ts
    for slug, cat in categories.items():
        inner = cat.get("category")
        if isinstance(inner, dict):
            inner["count"] = len(cat.get("items") or [])
            inner["lastSuccessAt"] = ts

    # ---- 落盘：data.json（原子写）+ state.json + changes.txt ----
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(tmp_path, path)

    keep_urls = set()
    for cat in categories.values():
        for item in cat.get("items") or []:
            if item.get("url"):
                keep_urls.add(norm_url(item["url"]))
    save_state(state_path, state_fails, keep_urls)

    lines = [f"新增 {len(ADDED)} 条："]
    lines += [f"- [{s}] {n}（{u}）" for s, n, u in ADDED] or ["- 无"]
    lines.append(f"移除 {len(REMOVED)} 条：")
    lines += [f"- [{s}] {n}（{u}）— {r}" for s, n, u, r in REMOVED] or ["- 无"]
    if MOVED:
        lines.append(f"挪组 {len(MOVED)} 条：")
        lines += [f"- [{s}] {n}（{u}）— {r}" for s, n, u, r in MOVED]
    with open(os.path.join(root_dir, "changes.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    # ---- 运行日志（报告用） ----
    log("---- feed 明细 ----")
    for line in FEED_LOG:
        log("FEED " + line)
    log("---- 汇总 ----")
    log(f"刷新成功 {STATS['checked_ok']} 条；GitHub 数据更新 {STATS['github']} 条；"
        f"数出条数 {STATS['counted']} 条；补上图标 {STATS['imaged']} 条；"
        f"验活不通 {STATS['dead']} 条")
    log(f"标上语言 {STATS['lang']} 条；标上下载地区 {STATS['region']} 条；"
        f"标上能否改中文 {STATS['cnswitch']} 条（其中只有英文改不了 {STATS['cnswitch_en']} 条）")
    log(f"新增 {len(ADDED)} 条（深挖达标 {STATS['deep_ok']} 条）；"
        f"移除 {len(REMOVED)} 条；放弃候选 {len(ABANDONED)} 条")
    for name, url, reason in ABANDONED:
        log(f"ABANDONED {name} | {url} | {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

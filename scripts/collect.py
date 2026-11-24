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
     新增不设上限（搜到多少不重复的新条目就加多少），每栏总量上限 300 条。
  4) 替换：scripts/state.json 记每条连续验活失败次数，连续不通自动移除
     （苹果栏目 3 次、其他栏目 6 次；每 6 小时跑一轮，约合 18/36 小时），
     从同栏候选池按新鲜度补位；新增/移除/挪组清单写进 changes.txt
     （提交信息用）。苹果限免组每轮按现价复核：不是免费了就降组或移除。
  5) 重排：每栏按（验活通、新鲜、条数多、星标高）重算 rank 和 score。
  6) 双安全阀（只拦「删」，从不整轮作废）：
     - 全站超过 30% 条目验活不通 → 本轮跳过删除，刷新与新增照常写盘；
     - 本次移除超过全站总数的 25% → 只删够 25% 的名额，剩下的下轮再删。
     （新增不拦：不重复的新条目全数加进。）

GitHub API：有环境变量 GITHUB_TOKEN 就带上提限额；没有就自动降速
（搜索请求间隔拉长、每页条数减少），不用任何私人令牌。

FIX14（2026-11-24，A 地址解析 + B 真实简介）：
  - github_info 多取 homepage；README 原文缓存后抽链接（resolve 用）。
  - build_item / process_item 跑 resolve_usable_url：可用地址进 url，
    仓库主页挪进 sourceUrl（已有值保留、只补缺），整份配置文件直链进
    builtinConfigUrl（已有值保留），node 栏再记 clientLinks（按客户端分行）。
  - 英文条目简介/介绍改取原文首句/首段实质内容（带相关性校验，不相关
    的不硬用，走“简介待补”进待补清单）；中文条目简介按句界截断清洗，
    不再 original[:70] 硬切（不断半个英文词、不留碎片）。
  - cn_explain 接进真正能走到的位置（label_item 的条件修好），
    “中文说明”不再全站为 0；app 电脑组不用安卓模板。
  - service/domain 合集仓库拆成单条服务再收，合集本身不收。
  - 待补清单进 state.json 的 pending；每轮删掉的条目写 screened.json
    （“今天筛掉了什么”）；核查时间戳沿用 verifiedAt，不新造字段。
  - process_item 不再拿 GitHub 页面 og:description（短句 + " - owner/repo"
    后缀）覆盖 README 攒下的长原文。

FIX14（2026-11-24，C 截图采集 + D AI 额度字段）：
  - D：fill_ai_credit_fields 按 ai-kb-spec.md 填 AI 栏 7 项额度字段：
    先查 scripts/ai_kb.json（域名精确匹配 → 仓库名匹配；github.com
    裸 host 不参与域名匹配，防所有仓库被误算成 Copilot），命中填表值；
    未命中只从 descriptionOriginal 正则抽明确写出的数字，抽不到填
    “未公布”，绝不猜数；7 项已有真实值只补缺不洗掉。核实状态单独记
    details「额度核实」（待核实/已核实），不洗 7 项原值；简介里的数
    只在已核实时写，且只写 details 里已有的数。表缺失时优雅降级（只走
    正则，不报错、不中断）。推送时须把 fix14/ai_kb.json 作为新文件推到
    仓库 scripts/ai_kb.json。
  - C：截图只从 iTunes screenshotUrls（取前 3 张）和 F-Droid 包页
    <img alt="app screenshot">（取前 3 张）提；Google Play 本脚本没有
    现成的页面抓取（Play 页需 JS 且常挡爬虫），不硬做；GitHub README
    里的图绝不当应用截图。截图 URL 只做格式校验（https + 图片扩展名/
    已知图床域名），不全量下载验活。新条目走 deep_dive 材料；老条目
    apple 在 label_item 里用缓存的 iTunes 数据补、app 用预算（每轮 60
    条）补 F-Droid 截图。

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
MAX_BYTES = 32 * 1024 * 1024  # 单次抓取上限：书源全量文件可达 20MB，6MB 会截断导致误判失效
CST = datetime.timezone(datetime.timedelta(hours=8))
GH_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
MAX_PER_CAT = 300           # 每栏总量上限
MAX_ADD_PER_CAT = MAX_PER_CAT  # 每栏每次新增不另设上限：搜到多少不重复的新条目就加多少（站长 2026-11-23 定），只受每栏总量上限约束
MAX_CANDIDATES_TRY = MAX_PER_CAT  # 每栏最多深挖多少个候选（与总量上限齐平，实际等于不限）

# 跨栏去重优先级：标题能看出来是什么的归专栏，免费服务是兜底栏、放最后。
# （站长 2026-11-24 定：AI 免费额度只放 AI 栏，别往免费服务里放。）
DEDUP_PRIORITY = ["apple", "adult", "ai", "app", "live", "video", "music",
                  "book", "domain", "cfip", "node", "proxy", "service"]
_DEDUP_RANK = {s: i for i, s in enumerate(DEDUP_PRIORITY)}
# 抓页面标题时撞上的通用报错标题前缀：这类标题不算真名。
ERROR_TITLE_PREFIXES = ("page not found", "404", "not found", "error",
                        "出错", "无法访问", "找不到")

STATS = {"checked_ok": 0, "dead": 0, "content_dead": 0, "github": 0, "counted": 0, "imaged": 0,
         "lang": 0, "region": 0, "cnswitch": 0, "cnswitch_en": 0,
         "deep_ok": 0, "resolved": 0, "pending": 0, "split": 0,
         "summary_fixed": 0, "ai_credit": 0, "shots": 0}
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
    """GitHub API 专用：有令牌带令牌；搜索接口自动降速（无令牌 7 秒一次，
    有令牌 2.5 秒一次，搜索限速 30 次/分钟，6 个 worker 齐发会撞上限）。"""
    headers = {"Accept": "application/vnd.github+json"}
    if GH_TOKEN:
        headers["Authorization"] = f"Bearer {GH_TOKEN}"
    if "/search/" in url:
        interval = 7.0 if not GH_TOKEN else 2.5
        with _search_lock:
            wait = interval - (time.monotonic() - _last_search_at[0])
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
                # FIX14 A：仓库主页 about 栏的官网链接（ai/service/domain
                # 的可用地址优先从这里拿）。
                "homepage": (data.get("homepage") or "").strip(),
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
    """抓 README 开头几段纯文本（深挖用）。拿不到返回空串。
    分支按顺序试：仓库默认分支 -> main -> master。旧写法只认默认分支，
    一旦 github_info 拿不到（API 限流/仓库改名），就默认 main——
    v2rayN、TUIC、v2ray-core 这类默认分支是 master 的仓库就永远抓不到 README。
    （FIX14：原文走 github_readme_raw 的缓存，与链接抽取共用一次请求。）"""
    raw = github_readme_raw(repo, info)
    return md_to_text(raw) if raw else ""


_readme_raw_cache = {}
_readme_raw_lock = threading.Lock()


def github_readme_raw(repo, info=None):
    """README 原文（markdown，带链接语法）：供链接抽取 / 合集拆分用。
    与 github_readme 同一套分支回退（默认分支 -> main -> master），
    成功抓到的原文进缓存，同一轮里不再重复请求。"""
    with _readme_raw_lock:
        if repo in _readme_raw_cache:
            return _readme_raw_cache[repo]
    info = info or github_info(repo)
    branches = []
    if info and info.get("default_branch"):
        branches.append(info["default_branch"])
    for b in ("main", "master"):
        if b not in branches:
            branches.append(b)
    raw = ""
    for branch in branches:
        for name in ("README.md", "readme.md", "README.MD"):
            try:
                status, _ct, body = fetch(
                    f"https://raw.githubusercontent.com/{repo}/{branch}/{name}",
                    max_bytes=512 * 1024)
                if status == 200 and body:
                    raw = body.decode("utf-8", "replace")
                    break
            except Exception:
                continue
        if raw:
            break
    with _readme_raw_lock:
        _readme_raw_cache[repo] = raw
    return raw


def _clean_link_url(u):
    u = (u or "").strip().rstrip(".,;:!?)>]")
    return u


def extract_md_links(raw):
    """按文档顺序抽 README 里的链接：[文字](url) + 裸 URL。
    返回 [{"url", "label", "context"}]，context 是链接前 60 字原文
    （看有没有“订阅/下载/Subscribe”这类标注）。图片、徽章、锚点不要。"""
    out, seen = [], set()
    if not raw:
        return out
    pat = re.compile(
        r"\[([^\]]{0,80})\]\((https?://[^)\s]+)\)"
        r"|(?P<bare>https?://[^\s\)\]\}\"'<>`]+)")
    for m in pat.finditer(raw):
        if m.group("bare"):
            url, label = m.group("bare"), ""
        else:
            label, url = (m.group(1) or "").strip(), m.group(2)
        url = _clean_link_url(url)
        low = url.lower()
        if not url or url in seen:
            continue
        if low.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
                         ".webp", ".md")):
            continue
        host = urllib.parse.urlparse(url).netloc.lower()
        if host in ("shields.io", "img.shields.io") or "/workflows/" in low:
            continue
        if label.startswith("!"):
            continue
        ctx = re.sub(r"\s+", " ", raw[max(0, m.start() - 60):m.start()]).strip()
        seen.add(url)
        out.append({"url": url, "label": label, "context": ctx})
    return out


def readme_links(repo, info=None):
    """仓库 README 里的链接（按文档顺序），走原文缓存，零重复请求。"""
    return extract_md_links(github_readme_raw(repo, info))


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

def _try_json(text):
    """宽容解析 JSON：标准解析失败时，再试「放宽控制字符」和「去掉整行 //
    注释」（TVBox 配置常见这两种写法，标准 json.loads 会直接报错，
    导致好好的源被误判为内容失效）。仍解析不了返回 None。"""
    try:
        return json.loads(text, strict=False)
    except Exception:
        pass
    cleaned = "\n".join(
        ln for ln in text.splitlines()
        if not ln.lstrip().startswith("//")
    )
    try:
        return json.loads(cleaned, strict=False)
    except Exception:
        return None


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
        data = _try_json(text)
        if isinstance(data, dict) and isinstance(data.get("sites"), list):
            return ("站点数", len(data["sites"]))
        if isinstance(data, dict) and isinstance(data.get("list"), list) \
                and data["list"]:
            return ("条数", len(data["list"]))
        if isinstance(data, dict) and isinstance(data.get("bookSources"), list) \
                and data["bookSources"]:
            return ("书源数", len(data["bookSources"]))
        if isinstance(data, dict) and isinstance(data.get("plugins"), list) \
                and data["plugins"]:
            return ("条数", len(data["plugins"]))
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


# ---------------- FIX14 C：截图采集（只做格式校验，不下载验活） ----------------
# 截图只认两个来源：iTunes lookup 的 screenshotUrls、F-Droid 包页上
# alt="app screenshot" 的图。GitHub README 里的图绝不当应用截图。
# URL 只校验格式（https + 图片扩展名 / 已知图床域名），不逐张下载验活：
# 全站几百个应用每轮都下载验活成本太大，格式对了就先收，坏图由展示层
# 兜底（onerror 隐藏）；Google Play 本脚本没有现成的页面抓取逻辑
# （Play 页重 JS 且常挡爬虫），不硬做，见 fdroid_screenshots 的注释。

_SHOT_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")


def _is_screenshot_url(u):
    """截图 URL 格式校验：https + 图片。
    已知图床域名也要看路径（f-droid.org 上还有 .svg 图标，不能无条件放行）；
    其他域名只认图片扩展名。只做格式校验，不下载验活。"""
    if not isinstance(u, str):
        return False
    u = u.strip()
    if not u.startswith("https://"):
        return False
    try:
        host = urllib.parse.urlparse(u).netloc.lower()
        path = urllib.parse.urlparse(u).path.lower()
    except Exception:
        return False
    if host.endswith("googleusercontent.com"):
        return True
    if host.endswith("mzstatic.com"):
        return path.endswith(_SHOT_EXTS)
    if host == "f-droid.org" or host.endswith(".f-droid.org"):
        return "screenshot" in path or path.endswith(_SHOT_EXTS)
    return path.endswith(_SHOT_EXTS)


def _valid_screenshot_urls(urls, max_n=3):
    """只留格式合格的截图，去重，取前 max_n 张。"""
    out = []
    for u in urls or []:
        if _is_screenshot_url(u) and u not in out:
            out.append(u)
        if len(out) >= max_n:
            break
    return out


def itunes_screenshots(rec, max_n=3):
    """iTunes lookup 结果里的截图（screenshotUrls），取前 max_n 张。
    iTunes 分支本来就拿 rec.get("description") 当深挖材料，这里顺着它取。"""
    if not isinstance(rec, dict):
        return []
    return _valid_screenshot_urls(rec.get("screenshotUrls") or [], max_n)


def fdroid_page_html(pkg):
    """F-Droid 包页原始 HTML（截图提取用；正文提取仍走 fdroid_page_text）。"""
    try:
        status, _ct, body = fetch(f"https://f-droid.org/en/packages/{pkg}/",
                                  max_bytes=1024 * 1024)
        if status == 200:
            return body.decode("utf-8", "replace")
    except Exception:
        pass
    return ""


def fdroid_screenshots(pkg, max_n=3):
    """F-Droid 包页截图：只认 <img alt="app screenshot"> 或 src 里带
    screenshots 的图（实测形如 /repo/<pkg>/en-US/phoneScreenshots/00.png），
    取前 max_n 张。包页上图标/捐赠图标等其他 <img> 一律不要。
    Google Play：本脚本没有 Play 页面抓取逻辑（label_item 只从 Play 链接里
    抠包名、再到 F-Droid 查有没有同款），Play 页本身是重 JS 页面且常挡
    爬虫，截图不从 Play 提——Play 应用若同时在 F-Droid 上架，走这条路补。"""
    html = fdroid_page_html(pkg)
    if not html:
        return []
    found = []
    for m in re.finditer(r"<img\b[^>]*>", html, re.I):
        tag = m.group(0)
        sm = re.search(r"""src\s*=\s*["']([^"']+)["']""", tag, re.I)
        if not sm:
            continue
        src = sm.group(1)
        is_shot = ("app screenshot" in tag.lower()
                   or "screenshot" in src.lower())
        if not is_shot:
            continue
        full = urllib.parse.urljoin("https://f-droid.org/", src)
        if _is_screenshot_url(full) and full not in found:
            found.append(full)
        if len(found) >= max_n:
            break
    return found


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
    (("throne", "nekoray"), "sing-box",
     "sing-box JSON（route 路由 + rule_set 规则集）", "无模块系统，只有配置文件",
     "按 route 规则分流，rule_set 支持远程订阅更新",
     "规则集：官方 sing-geosite（github.com/SagerNet/sing-geosite）、"
     "sing-geoip（github.com/SagerNet/sing-geoip）"),
    (("egern",), "egern",
     "Egern 规则集（域名/正则/geo/CIDR/URL 正则等，支持远程规则集）",
     "支持模块 + 脚本 + 统一重写引擎",
     "规则分流 / 全局 / 直连可切换，策略组支持选择/自动测速/故障转移/负载均衡，支持远程规则集更新",
     "社区规则与模块：github.com/topics/egern"),
    (("streisand",), "xray",
     "Xray 路由规则（geosite/geoip，图形界面配置）", "无模块系统，只有配置文件",
     "按 routing 规则分流，订阅导入更新",
     "geo 数据：Loyalsoldier/v2ray-rules-dat（github.com/Loyalsoldier/v2ray-rules-dat）"),
    (("happ",), "xray",
     "Xray 路由规则（geosite/geoip）", "无模块系统，只有配置文件",
     "按 routing 规则分流，支持加密/隐藏订阅导入更新",
     "geo 数据：Loyalsoldier/v2ray-rules-dat（github.com/Loyalsoldier/v2ray-rules-dat）"),
    (("v2box",), "xray",
     "Xray 路由规则（geosite/geoip）", "无模块系统，只有配置文件",
     "按 routing 规则分流，订阅导入更新",
     "geo 数据：Loyalsoldier/v2ray-rules-dat（github.com/Loyalsoldier/v2ray-rules-dat）"),
    (("hysteria",), "hysteria",
     "YAML 配置 + ACL 分流规则", "无模块系统，只有配置文件",
     "按 ACL 规则分流",
     "暂无（多作为协议被 sing-box/mihomo/v2rayN 内置，单独用得少）"),
    (("tuic",), "tuic",
     "JSON 配置", "无模块系统，只有配置文件",
     "分流靠上层客户端路由",
     "暂无（多作为协议被 sing-box/mihomo/v2rayN 内置，单独用得少）"),
]
PROXY_FAMILY_URL = "https://gcore.jsdelivr.net/gh/YiMing918/proxy-app-rules@main/{}/"
# 配套规则只给仓库里真有的七个家族；其他家族（egern/hysteria/tuic 等）写暂无配套，
# 别拼出一个不存在的文件夹链接。
PROXY_RULE_FAMILIES = {"mihomo", "sing-box", "xray", "shadowrocket", "surge",
                       "loon", "quantumultx"}

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
    "egern": "1) 加规则：配置 → 规则集，粘贴远程规则集链接；"
             "2) 加模块：模块 → 添加，粘贴模块链接安装；3) 保存后重连生效。",
    "hysteria": "这个是协议内核，一般不用单独装，装个支持它的客户端（如 sing-box、mihomo、v2rayN）就能用；单独用就按它的 YAML 配置写 ACL 分流规则。",
    "tuic": "这个是协议内核，一般不用单独装，装个支持它的客户端（如 sing-box、mihomo、v2rayN）就能用；单独用就按它的 JSON 配置来。",
    None: "这个工具不用往里加规则和模块，打开按它自己的界面用就行。",
}

# 代理栏中文名 + 中文介绍：（识别词, 中文名, 中文介绍）。
# 顺序有讲究：更具体的识别词在前（如 v2rayng 在 v2rayn 前，clash-party 在 mihomo 前），
# 因为有的名字是另一个的子串（v2rayng 含 v2rayn，clash-party 的仓库地址含 mihomo）。
PROXY_CN = [
    (("v2rayng",), "v2rayNG 安卓客户端",
     "v2rayNG 是安卓上最常用的 Xray 客户端，中文开发者 2dust 的作品，界面支持中文。它支持 VLESS、VMess、Trojan、Shadowsocks 等协议，订阅导入后按 Xray 路由规则（geosite/geoip）分流，规则分流、全局、直连可以切换，geo 数据文件在设置里点更新。免费开源，新手首选。适合安卓用户和刚入门的新手。"),
    (("v2rayn",), "v2rayN 桌面客户端",
     "v2rayN 是 Windows、Linux、macOS 上的默认 Xray/sing-box 图形客户端，中文开发者 2dust 的作品，界面支持中文。它可以切换 14 种内核（Xray、sing-box、mihomo、Hysteria、TUIC 等），用图形界面生成 Xray/sing-box 的 JSON 路由规则，按规则分流，订阅远程更新。免费开源，功能最全的桌面客户端之一。适合桌面用户和喜欢折腾多种内核的人。"),
    (("v2ray-core", "v2ray core"), "V2Ray 内核",
     "V2Ray 是 V2Ray 的原版内核，仍在维护但功能已经冻结，没有 Reality、QUIC 系这些新协议。它用和 Xray 同款的 geosite/geoip 路由规则分流。一般用户直接用 Xray-core 就行，不用专门装它。免费开源。适合只需要基础 V2Ray 功能、不追新协议的人。"),
    (("nekobox",), "NekoBox 安卓客户端",
     "NekoBox（猫箱）是安卓上的 sing-box 图形客户端，中文开发者 MatsuriDayo 的作品，界面支持中文，插件丰富、界面现代。它用 sing-box 的 JSON 配置，route 路由加 rule_set 规则集分流，规则集支持远程订阅更新。免费开源。注意别从 Google Play 下，那个版本 2024 年起由第三方控制、不是开源的，用 GitHub Releases 里的包。适合安卓用户和喜欢 sing-box、现代界面的人。"),
    (("karing",), "Karing 全平台客户端",
     "Karing（卡林）是 Flutter 写的全平台客户端，安卓、iOS、Windows、macOS、Linux、tvOS 都有，订阅兼容性最强。它用改过的 sing-box 内核，支持 Clash 风格路由规则和 sing-box rule_set，规则分流、全局、直连可以切换，内置 geo-IP、geo-site、ACL 和规则集，支持远程规则订阅更新。免费开源，中文界面。注意它用的是改过的内核，不能换内核。适合多设备用户和看重订阅兼容性的人。"),
    (("hiddify",), "Hiddify 全平台客户端",
     "Hiddify 是 sing-box 的全平台图形客户端，安卓、iOS、Windows、macOS、Linux 都有，主打简单易用，自带免费的 WARP 和 Psiphon。它用 sing-box 的 JSON 配置，route 路由加 rule_set 规则集分流，订阅自动更新，安卓上支持按应用分流。免费开源，支持中文界面。适合新手和想要简单易用、免费 WARP 的人。"),
    (("throne",), "Throne 桌面客户端",
     "Throne（王座）是 NekoRay 停更后的官方接班人，Qt 写的桌面客户端，Windows、Linux、macOS 都有。它用 sing-box 的 JSON 配置，route 路由加 rule_set 规则集分流，内置 geoip/geosite 规则集远程更新，支持系统代理和 TUN。免费开源。适合桌面用户和原来用 NekoRay 的人。"),
    (("flclash",), "FlClash 全平台客户端",
     "FlClash 是 Flutter 写的跨平台客户端，内置 mihomo 内核，安卓、Windows、macOS、Linux 都能用，界面现代，支持 WebDAV 同步配置。它用 Clash 的 YAML 配置和 rule-provider 远程规则集分流，规则分流、全局、直连可以切换，没有插件模块，扩展靠覆写脚本。免费开源，中文界面。适合多平台用户和喜欢现代界面、WebDAV 同步的人。"),
    (("clash-verge",), "Clash Verge Rev 桌面客户端",
     "Clash Verge Rev 是桌面端首选的 Clash 客户端，Tauri 写的，内置 mihomo 内核，Windows、macOS、Linux 都有。它支持订阅管理、可视化规则编辑，规则分流、全局、直连可以切换，支持 TUN 和服务模式，rule-provider 远程规则集自动更新。没有插件，扩展靠配置合并和 JS 增强脚本。免费开源，中文界面。适合桌面用户和想要可视化规则编辑的人。"),
    (("clash-party",), "Clash Party 桌面客户端",
     "Clash Party 是 Tauri 写的桌面客户端，内置 mihomo 内核和 Sub-Store，Windows、macOS、Linux 都有，还有 Win7 和老 macOS 的构建。它用 Clash 的 YAML 配置和 rule-provider 远程规则集分流，规则分流、全局、直连可以切换，订阅管理直接用内置的 Sub-Store。免费开源，中文界面。适合桌面用户和想要内置 Sub-Store、老系统支持的人。"),
    (("mihomo",), "mihomo 内核",
     "mihomo 是 Clash Meta 延续下来的开源代理内核，是现在大多数 Clash 系客户端的引擎。它用 Clash 的 YAML 配置，支持 rule-provider 远程规则集，规则分流、全局代理、直连三种模式可以切换，订阅更新后规则自动跟着更新。它本身没有界面，要配个客户端（如 FlClash、Clash Verge Rev、Stash、Clash Party）才能用。适合想自己搭规则、要精细控制分流的人。"),
    (("sing-box", "singbox"), "sing-box 内核",
     "sing-box 是新一代通用代理平台，协议覆盖最广的开源内核，支持 VLESS、VMess、Trojan、Hysteria2、TUIC 等几乎所有主流协议。它用 JSON 配置，route 路由加 rule_set 规则集，规则分流、全局、直连可以切换，规则集支持远程订阅更新。它本身是命令行工具，要配个图形客户端（如 NekoBox、Karing、Hiddify、Throne）才能方便用。适合想要协议最全、愿意配个图形客户端的人。"),
    (("xray",), "Xray 内核",
     "Xray-core 是 V2Ray 延续下来的开源代理内核，是 VLESS、Reality、Vision、XHTTP 这些新协议的源头实现，功能最强的 V2Ray 系内核。它用 JSON 配置，靠 geosite/geoip 路由规则分流，geo 数据文件随客户端更新。它本身没有界面，要配个客户端（如 v2rayNG、v2rayN、Streisand、Happ、V2Box）才能用。适合要用 VLESS、Reality 这些新协议的人。"),
    (("streisand",), "Streisand iOS 客户端",
     "Streisand 是免费开源的 iOS Xray 客户端，内置 Xray-core，支持 VLESS 和 Reality，对新手友好。它用 Xray 的路由规则（geosite/geoip）分流，图形界面配置，订阅用链接或二维码导入更新。免费，界面支持中文。适合 iOS 新手和想要免费 Xray 客户端的人。"),
    (("happ",), "Happ 全平台客户端",
     "Happ 是免费的 Xray 系客户端，Windows、macOS、Linux、iOS、安卓、电视都有。它用 Xray 的路由规则分流，支持加密和隐藏订阅导入更新。免费，闭源。适合多平台用户和想要免费 Xray 客户端的人。"),
    (("v2box",), "V2Box iOS 客户端",
     "V2Box 是简单免费的 iOS、macOS V2Ray 系客户端。它用 Xray 的路由规则分流，订阅导入更新。免费，界面支持中文。适合 iOS、macOS 用户和想要简单免费客户端的人。"),
    (("subconverter", "sub-converter"), "subconverter 订阅转换",
     "subconverter 是订阅转换工具，把一份机场订阅转换成 Clash、sing-box、Surge 等各种客户端能用的格式。它本身不代理流量，所以没有规则、分流和模块，打开按它自己的界面用就行。免费开源。适合要把一份订阅转成各种客户端格式的人。"),
    (("sub-store", "substore"), "Sub-Store 订阅管理",
     "Sub-Store 是订阅管理、转换和合并工具，节点筛选和过滤功能很强，可以给订阅做过滤、重命名和合并，再生成各客户端能用的订阅。它本身不代理流量，所以没有规则和分流，但支持用脚本操作节点。免费开源，界面支持中文。适合要管理、筛选、合并多个订阅的人。"),
    (("hysteria",), "Hysteria2 内核",
     "Hysteria2 是基于 QUIC 的高速代理协议和内核，弱网和大带宽表现好。它用 YAML 配置加 ACL 规则分流，没有模块系统。一般不用单独装，sing-box、mihomo、v2rayN 这些客户端都内置了它。免费开源。适合弱网、大带宽、想要高速的人。"),
    (("tuic",), "TUIC 内核",
     "TUIC 是基于 QUIC 的低延迟代理协议，UDP 优化做得好。它用 JSON 配置，分流靠上层客户端路由。一般不用单独装，sing-box、mihomo、v2rayN 这些客户端都内置了它。免费开源。适合想要低延迟、UDP 优化的人。"),
    (("shadowrocket",), "Shadowrocket iOS 客户端",
     "Shadowrocket（小火箭）是 iOS 上规则代理的标杆，轻量稳定，中文开发者，界面支持中文。它用 .conf 配置规则，支持 .module 模块，规则分流、全局、直连可以切换，支持远程配置订阅更新。$2.99 一次性买断，没有内购和广告，新手首选的付费客户端。注意国区 App Store 没有，要用外区账号下载。适合 iOS 用户和刚入门的新手。"),
    (("surge",), "Surge iOS/macOS 客户端",
     "Surge 是专业级的网络调试工具，规则、抓包、MITM 全套，适合开发者和高级用户。它用 .conf 配置和 RULE-SET 远程规则集，支持 .sgmodule 模块，规则分流、全局、直连可以切换。iOS 和 Mac 分开买，大约 $49.99 一次性买断（含 1 年更新），界面支持中文。"),
    (("loon",), "Loon iOS 客户端",
     "Loon 是功能全面的 iOS 代理工具，支持抓包、脚本和高级路由，性价比高。它用自己的配置规则，支持 .plugin 插件，规则分流、全局、直连可以切换，支持远程规则订阅更新。$7.99 一次性买断，界面支持中文。适合 iOS 用户和想要抓包、脚本、高级路由的人。"),
    (("quantumult",), "Quantumult X iOS 客户端",
     "Quantumult X（圈X）是 iOS 老牌代理工具，重写和脚本能力强。它用 filter 规则，支持 HTTP 重写和 JS 脚本，规则分流、全局、直连可以切换，filter 支持远程订阅更新。$9.99 一次性买断，界面支持中文。注意国区 App Store 没有，要用外区账号下载。适合 iOS 用户和想要重写、脚本能力的人。"),
    (("stash",), "Stash iOS 客户端",
     "Stash 是 iOS 上对 Clash 配置支持最完整的客户端，中文团队开发，界面支持中文。它完整支持 Clash Premium 配置、脚本和高级策略组，用 Clash 的 YAML 配置和 rule-provider 远程规则集分流，规则分流、全局、直连可以切换，还有 SSID 策略组和按需连接。没有 .module 系统，但支持 JS 脚本、HTTP 重写和 MITM。iOS 版 $5.99 一次性买断，Mac 版是订阅制。适合 iOS 用户和想要最完整 Clash 配置支持的人。"),
    (("egern",), "Egern iOS 客户端",
     "Egern（松鼠）是功能很全的免费 iOS 网络工具，支持抓包、重写和 Tailscale。它用自己的规则集（域名、正则、geo、CIDR、URL 正则等），支持远程规则集，支持模块、脚本和统一重写引擎，分流有规则、全局、直连三种模式，策略组支持选择、自动测速、故障转移和负载均衡。免费下载，Pro 内购 $5.99，界面支持简体中文。适合 iOS 用户和想要免费全能网络工具的人。"),
]

# 代理栏里确认支持中文界面的应用：返回 (语言, 能否改中文)。
# 这些应用的 README 可能是英文，但 APP 界面确实有中文，别被语言检测误判
# （v2rayNG 就是这么被错标成“只有英文”的）。
PROXY_LANG_OVERRIDE = {
    "v2rayng": ("中英双语", "可切换中文"),
    "v2rayn": ("中英双语", "可切换中文"),
    "nekobox": ("中英双语", "可切换中文"),
    "flclash": ("中英双语", "可切换中文"),
    "karing": ("中英双语", "可切换中文"),
    "hiddify": ("中英双语", "可切换中文"),
    "clash-verge": ("中英双语", "可切换中文"),
    "clash-party": ("中英双语", "可切换中文"),
    "shadowrocket": ("中英双语", "可切换中文"),
    "surge": ("中英双语", "可切换中文"),
    "loon": ("中英双语", "可切换中文"),
    "quantumult": ("中英双语", "可切换中文"),
    "stash": ("中英双语", "可切换中文"),
    "egern": ("中英双语", "可切换中文"),
    "streisand": ("中英双语", "可切换中文"),
    "v2box": ("中英双语", "可切换中文"),
}


def proxy_cn_for(slug, name, url):
    """代理栏中文名+中文介绍：按识别词匹配，返回 (中文名, 中文介绍) 或 None。"""
    if slug != "proxy":
        return None
    probe = ((name or "") + " " + (url or "")).lower()
    for keys, cn_name, cn_desc in PROXY_CN:
        if any(k in probe for k in keys):
            return (cn_name, cn_desc)
    return None


def proxy_lang_for(item):
    """代理栏里确认支持中文界面的应用：返回 (语言, 能否改中文)，否则 None。"""
    probe = ((item.get("name") or "") + " " + (item.get("url") or "")
             + " " + (item.get("sourceUrl") or "")).lower()
    for key, (lang, cnswitch) in PROXY_LANG_OVERRIDE.items():
        if key in probe:
            return (lang, cnswitch)
    return None


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
                   PROXY_FAMILY_URL.format(fam) if fam in PROXY_RULE_FAMILIES else "暂无配套")
        set_detail(item, "可加的东西", addons)
        return entry
    for lb in ("规则格式", "模块支持", "分流方式", "可加的东西"):
        if not get_detail(item, lb):
            set_detail(item, lb, "未公布")
    if not get_detail(item, "配套规则"):
        set_detail(item, "配套规则", "暂无配套")
    return None


VERSION_REQUIRED_SLUGS = ("app", "apple", "proxy")


# ================= FIX14：真实简介 / 地址解析 =================

def cat_context_for(slug, section=""):
    """栏目语境（模板用）：app 电脑组不用安卓模板（LOOK 反例：
    FrigadeHQ/yogurt 是 Mac/Windows/Linux 软件，简介却说“适合安卓手机用户”）。"""
    kind, use, who = CAT_CONTEXT.get(slug, ("网站/工具", "解决实际问题", "有需要的人"))
    if slug == "app" and (section or "") in ("pc-free", "pc-cracked"):
        return ("电脑软件", "在电脑上完成它擅长的事，免费又没广告打扰",
                "用 Windows/Mac/Linux 电脑的人")
    return (kind, use, who)


def _truncate_at_sentence(text, limit):
    """按句界截断（LOOK 截断 bug 的修法）：先找句末标点，找不到退到
    空格/顿号/逗号，最后还不行就退掉断掉的半个英文词。绝不断在半个词上，
    绝不补 "..."（旧数据里 559 条 descriptionOriginal 正好 200 字加 "…"，
    就是老版本硬截断的遗留；现行代码里没有 [:200]，index.html 也不截断）。"""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    idx = max(cut.rfind("。"), cut.rfind("！"), cut.rfind("？"),
              cut.rfind("!"), cut.rfind("?"))
    if idx >= 12:
        return cut[:idx + 1]
    sp = cut.rfind(" ")
    if sp >= 12:
        return cut[:sp].rstrip(" ,，、；:")
    for sep in ("、", "，", ",", "；", ";"):
        j = cut.rfind(sep)
        if j >= 12:
            return cut[:j]
    m = re.search(r"[A-Za-z]+$", cut)
    if m:
        return cut[:m.start()].rstrip()
    return cut.rstrip(" ,，、；:")


_SENT_SPLIT_RE = re.compile(r"(?<=[。！？])|(?<=[.!?])\s+")
_BOILERPLATE_RES = (
    re.compile(r"contribute to .* development by creating an account", re.I),
    re.compile(r"buy this domain|domain for sale|domain parking|parked", re.I),
)
_KIND_TOKENS = {
    "ai": {"ai", "llm", "api", "model", "gpt", "chat", "token", "agent", "image"},
    "apple": {"app", "ios", "iphone", "ipad", "mac", "apple"},
    "video": {"tvbox", "video", "vod", "movie", "film", "stream", "play"},
    "music": {"music", "lxmusic", "musicfree", "audio", "song", "lyric"},
    "book": {"book", "novel", "read", "reading", "legado", "ebook"},
    "live": {"iptv", "live", "tv", "channel", "m3u", "epg", "stream"},
    "service": {"free", "api", "host", "cloud", "service", "server", "storage",
                "database"},
    "domain": {"domain", "dns", "free", "register", "tld", "subdomain", "name"},
    "cfip": {"cloudflare", "ip", "cdn", "cf", "proxy", "speed"},
    "node": {"node", "vless", "vmess", "trojan", "ss", "shadowsocks", "proxy",
             "subscri", "clash", "sing-box", "singbox", "config"},
    "app": {"app", "android", "windows", "mac", "linux", "ios", "computer",
            "software", "desktop", "tool", "client"},
    "adult": {"video", "adult", "porn", "xxx"},
    "proxy": {"proxy", "clash", "v2ray", "sing-box", "singbox", "socks", "tun",
              "vpn", "xray", "hysteria"},
}
_STOPWORDS = {"the", "a", "an", "and", "for", "with", "that", "this", "from",
              "your", "you", "are", "can", "use", "free", "open", "source",
              "simple", "based", "using", "into", "more", "also", "its", "it",
              "is", "of", "to", "in", "on", "by", "as", "at", "be", "or", "all",
              "app", "apps", "one", "new", "best"}


def _name_tokens(name, repo):
    out = set()
    for src in (name or "", repo or ""):
        for t in re.split(r"[^A-Za-z0-9]+", src.lower()):
            if len(t) >= 4:
                out.add(t)
        for t in re.findall(r"[一-鿿]{2,}", src):
            out.add(t)
    return out


def _content_words(s):
    words = [w for w in re.findall(r"[a-z]{3,}", s.lower())
             if w not in _STOPWORDS]
    words += re.findall(r"[一-鿿]{2,}", s)
    return words


def _word_matches_kind(w, slug):
    """种类词匹配：整词相等，或种类词（>=4 字）是该词的子串
    （subscribe 含 subscri、nodes 含 node，复数/派生都算）。"""
    ks = _KIND_TOKENS.get(slug, set())
    if w in ks:
        return True
    return any(len(k) >= 4 and k in w for k in ks)


def _sentence_relevant(sentence, slug, name, repo, body):
    """首句相关性校验（LOOK 反例的修法）：首句要跟条目本身相关才用。
    拿句子的实义词去 README/正文里比对——同名撞车（如 mihomo 的星穹铁道
    数据模型，仓库简介写的是另一个项目）会在这里被拦下，不进简介。
    body 里已扣掉句子本身，避免自己跟自己比对出 100% 相关。"""
    s_low = (sentence or "").lower()
    if not s_low.strip():
        return False
    for rx in _BOILERPLATE_RES:
        if rx.search(s_low):
            return False
    words = _content_words(sentence)
    if not words:
        return False
    body_low = (body or "").lower()
    if body_low and len(body_low) >= 50:
        hits = 0
        for w in words:
            if re.search(r"[一-鿿]", w):
                if w in body_low:
                    hits += 1
            elif re.search(r"\b" + re.escape(w) + r"\b", body_low):
                hits += 1
        overlap = hits / len(words)
        toks = set(re.findall(r"[a-z]{3,}", s_low))
        _nt = _name_tokens(name, repo)
        name_hit = bool(toks & _nt) or any(
            t for t in _nt if re.search(r"[一-鿿]", t) and t in sentence)
        kind_words = [w for w in words if _word_matches_kind(w, slug)]
        kind_hit = bool(kind_words)
        # 种类词本身也是相关证据：句子用了本栏的词（domain、nodes……），
        # 即使它在 README 里只出现一次，也算沾边。分数 = 正文命中 + 种类词。
        score = (hits + len(kind_words)) / len(words)
        if overlap >= 0.30 or score >= 0.30:
            return True
        if score >= 0.15 and (name_hit or kind_hit):
            return True
        return False
    # 正文太短没法比对（材料几乎只有这一句）：只认种类词——这句用了本栏
    # 的词（book、domain、nodes……），就当它是说这东西的；只含名字不含
    # 种类词的不要（mihomo 的星穹铁道句就是只含名字、不含任何代理种类词）。
    toks = set(re.findall(r"[a-z]{3,}", s_low))
    return any(_word_matches_kind(w, slug) for w in toks)


def _split_sentences(text):
    parts = _SENT_SPLIT_RE.split(text or "")
    out = []
    for p in parts:
        p = re.sub(r"\s+", " ", p).strip(" \t#*>-`")
        p = re.sub(r"^[\W_]+(?=[A-Za-z0-9一-鿿])", "", p)
        if p:
            out.append(p)
    return out


def first_substantive_sentence(text, max_len=70, slug=None, name="", repo="",
                               body=None):
    """第一句实质内容：去营销空话、去 GitHub 默认 boilerplate、过相关性校验，
    按句界截到 max_len 左右。找不到返回 ""（调用方走“简介待补”，不硬凑）。"""
    if body is None:
        body = text or ""
    for sent in _split_sentences(text):
        if len(sent) < 12:
            continue
        if sent.lower().startswith(("http://", "https://", "www.")):
            continue
        rest = (body or "").replace(sent, "", 1)
        if slug and not _sentence_relevant(sent, slug, name, repo, rest):
            continue
        return _truncate_at_sentence(sent, max_len)
    return ""


def first_paragraph(text, max_len=300, slug=None, name="", repo=""):
    """首段实质内容（介绍用）：取第一个像正文的段落，按句界截断。"""
    for para in re.split(r"\n\s*\n|\n", text or ""):
        p = re.sub(r"\s+", " ", para).strip(" \t#*>-`")
        if len(p) < 25:
            continue
        if _BOILERPLATE_RES[0].search(p.lower()):
            continue
        return _truncate_at_sentence(p, max_len)
    return ""


def clean_zh_summary(text, slug=None, name="", repo=""):
    """中文简介清洗：先取首句实质内容；没有成句时（如 v2rayfree 的关键词串）
    做去重——同一词的不同写法只留一个，最多 3 段，按顿号截断，不留半个词。"""
    s = first_substantive_sentence(text, 70, slug, name, repo, text)
    if s:
        return s
    raw = re.sub(r"\s+", "", (text or "").strip())
    if not raw:
        return ""
    parts = [p for p in re.split(r"[、，,；;]", raw) if p]
    kept = []
    for p in parts:
        if any(p in k or k in p for k in kept):
            continue
        kept.append(p)
        if len(kept) >= 3:
            break
    joined = "、".join(kept)
    if not joined:
        return ""
    return _truncate_at_sentence(joined, 70).rstrip("、，,") + "。"


def _is_template_summary(summary):
    """是不是 12 句英文模板简介之一（形如“XX（英文）。主要用来……”）。"""
    return bool(re.match(r".+（英文）。主要用来", (summary or "").strip()))


def _pick_github_text(desc, readme, repo, slug):
    """仓库简介 + README 二选一：简介首句与 README 无关（如 mihomo 的简介
    写的是星穹铁道数据模型，而 README 是 Clash 内核）就扔掉简介、只用 README。"""
    desc = (desc or "").strip()
    readme = (readme or "").strip()
    if not desc:
        return readme
    first = _split_sentences(desc)
    if first and _sentence_relevant(first[0], slug, repo, repo, readme):
        return (desc + "\n" + readme).strip()
    return readme


def cn_explain(item, slug):
    """给英文条目拼一段大白话中文说明。
    FIX14：原来只在 descriptionOriginal 为空时才被 label_item 调用，
    而 build_item 给英文条目都会填 descriptionOriginal，所以条件几乎
    走不到，全站“中文说明”为 0。现在内容改用真实首句（模板简介不算），
    实在没有才回落到种类 + 用途的通用句。"""
    kind, use, who = cat_context_for(slug, item.get("section") or "")
    name = item.get("name") or "这个条目"
    head = f"{name} 是一个{kind}。"
    real = (item.get("summary") or "").strip()
    if _is_template_summary(real):
        real = ""
    if not real:
        real = first_substantive_sentence(
            item.get("descriptionOriginal") or "", 80, slug, name, "",
            item.get("descriptionOriginal") or "")
    if real:
        return f"{head}{real}适合{who}。"
    return f"{head}它主要用来{use}。适合{who}。页面是英文的，可以用浏览器自动翻译看。"


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


# 免费服务子类推断：先看名字（最准），名字没信号再看介绍里 distinctive 的词。
# 注意：ssl/https/域名这类词介绍里到处都是，不能用来推断——只认名字里的。
# 推不出来就留空，别硬猜错类（错类比没类更糟：筛选项会把条目带到错的分组里）。
SERVICE_SUBCAT_NAME_KW = [
    ("免费证书", ["ssl", "letsencrypt", "acme", "证书"]),
    ("免费邮箱", ["mail", "email", "smtp", "邮箱", "resend", "brevo", "sendgrid", "mailgun", "postmark"]),
    ("免费数据库", ["database", "postgres", "mysql", "mongo", "sqlite", "redis", "supabase", "neon", "turso", "firebase", "planetscale", "cockroach", "数据库"]),
    ("免费域名", ["domain", "dns", "域名", "freenom"]),
    ("免费存储/图床", ["storage", "s3", "r2", "图床", "cloudinary", "imgur", "存储"]),
    ("免费主机/托管", ["pages", "hosting", "host", "vps", "vercel", "netlify", "render", "railway", "heroku", "workers", "云", "服务器", "部署", "compute", "oracle", "aws", "azure"]),
    ("免费 API", ["api", "接口"]),
]
SERVICE_SUBCAT_TEXT_KW = [
    ("免费数据库", ["数据库", "database", "postgres", "mysql", "mongodb", "sqlite", "redis"]),
    ("免费邮箱", ["邮箱", "邮件", "email", "发信"]),
    ("免费存储/图床", ["对象存储", "图床", "文件存储"]),
    ("免费主机/托管", ["部署", "托管", "虚拟机", "云服务器", "静态站"]),
]


def guess_service_subcat(item, text=""):
    """给缺「类别」的免费服务条目推断子类。返回子类名或空串。"""
    name = (item.get("name") or "").lower()
    for subcat, kws in SERVICE_SUBCAT_NAME_KW:
        for kw in kws:
            if kw.lower() in name:
                return subcat
    t = ((text or "") + "\n" + (item.get("summary") or "")
         + "\n" + (item.get("description") or "")).lower()
    for subcat, kws in SERVICE_SUBCAT_TEXT_KW:
        for kw in kws:
            if kw.lower() in t:
                return subcat
    return ""


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
            # FIX14 C：iTunes 截图补到 3 张（lookup 已缓存，不新增请求；
            # 老条目原有截图保留，只追加格式合格的、不重复的）。
            shots = itunes_screenshots(rec)
            if shots:
                cur = item.get("screenshots") or []
                merged = _valid_screenshot_urls(list(cur) + shots)
                if len(merged) > len(cur):
                    item["screenshots"] = merged
                    bump("shots")
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

    # --- 代理栏：确认支持中文界面的应用，直接写死，别被英文 README 误判 ---
    # （App Store 的应用走上面 iTunes 分支，语言以商店为准，不会走到这里。）
    if slug == "proxy":
        _lg = proxy_lang_for(item)
        if _lg:
            set_detail(item, "语言", _lg[0])
            bump("lang")
            set_detail(item, "能否改中文", _lg[1])
            bump("cnswitch")
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
    # 英文条目缺中文说明 → 补一条，不动原 description。
    # FIX14：旧条件要求 descriptionOriginal 为空，但 build_item 给英文条目
    # 都会填它，所以这行几乎走不到，全站“中文说明”为 0。改成缺了就补。
    if lang == "英文" and not get_detail(item, "中文说明"):
        set_detail(item, "中文说明", cn_explain(item, slug))


# ---------------- 现有条目刷新（v1 + 标注） ----------------

def process_item(slug, item, state_fails):
    """刷新一条现有条目，返回是否验活通。state_fails 在这里更新计数。"""
    url = (item.get("url") or "").strip()
    key = norm_url(url) if url else ""
    if not url:
        bump("dead")
        return False
    # FIX14：去掉旧流程写进 descriptionOriginal 的 " - owner/repo" 后缀
    #（GitHub 仓库页 og:description 的格式，实测 1021 条带这个后缀）。
    # 只去后缀本身，不动正文；长原文的重建走下面预算内的 _rebuild_item_text。
    _o = item.get("descriptionOriginal") or ""
    _m = re.search(r"\s+-\s+([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)$", _o)
    if _m and github_repo_of(url) == _m.group(1):
        item["descriptionOriginal"] = _o[:_m.start()]
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

    # FIX14 A+B：老条目逐步解析可用地址 + 重建模板简介（每轮预算 60 条，
    # 只动裸仓库地址的条目；已有 sourceUrl/builtinConfigUrl 的只补缺的，
    # 绝不洗掉手写种子带的值；改地址后验活在下一轮走正常流程）。
    if _resolve_budget_take() and _is_bare_repo_url(url):
        _repo2 = github_repo_of(url)
        _info2 = github_info(_repo2)
        _mat2 = {"text": github_readme(_repo2, _info2), "github": _info2,
                 "links": readme_links(_repo2, _info2),
                 "readme_raw": github_readme_raw(_repo2, _info2),
                 "homepage": (_info2 or {}).get("homepage") or ""}
        _res2 = resolve_usable_url(
            slug, {"name": item.get("name") or "", "url": url}, _mat2)
        if _res2.get("found") and _res2["url"] != url:
            if not (item.get("sourceUrl") or "").strip():
                item["sourceUrl"] = f"https://github.com/{_repo2}"
            item["url"] = _res2["url"]
            url = _res2["url"]
            if _res2.get("config_url") and not (item.get("builtinConfigUrl") or "").strip():
                item["builtinConfigUrl"] = _res2["config_url"]
            if slug == "node" and _res2.get("client_links") and not item.get("clientLinks"):
                item["clientLinks"] = _res2["client_links"]
            pending_discard(url)
            bump("resolved")
        elif _res2.get("pending_reason"):
            pending_add(url, item.get("name") or "", slug, _res2["pending_reason"])
        if _rebuild_item_text(slug, item, _mat2):
            bump("summary_fixed")

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
        _cn = proxy_cn_for(slug, item.get("name") or "", item.get("url") or "")
        if _cn:
            item["name"] = _cn[0]
            item["description"] = _cn[1]
            item["summary"] = _cn[1].split("。")[0] + "。" if "。" in _cn[1] else _cn[1]
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
    # FIX14：GitHub 仓库页的 og:description 只有一句话还带 " - owner/repo"
    # 后缀（实测 1021 条原文介绍就是这么被覆盖短的），别拿它覆盖 README
    # 攒下的长原文；只有新介绍更长时才换。
    if page is not None and (item.get("descriptionOriginal") or "").strip():
        new_intro = (page.og_desc or page.meta_desc or "").strip()
        new_intro = re.sub(r"\s+-\s+[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", "",
                           new_intro)
        old_intro = item["descriptionOriginal"].strip()
        if new_intro and new_intro != old_intro and len(new_intro) > len(old_intro):
            item["descriptionOriginal"] = new_intro
    # 免费服务/域名：固定四问字段补齐（查不到写未公布）
    if slug in ("service", "domain"):
        search_text = ""
        if page is not None:
            search_text = "\n".join([page.title, page.meta_desc,
                                     page.og_desc] + page.paras[:4])
        search_text += "\n" + (item.get("description") or "") \
            + "\n" + (item.get("summary") or "")
        # 免费服务缺「类别」的先补上（首页筛选 chip 靠它分组；四问字段也按它选）；
        # 推不出来就留空，别硬猜。
        if slug == "service" and not get_detail(item, "类别"):
            sub = guess_service_subcat(item, search_text)
            if sub:
                set_detail(item, "类别", sub)
        ensure_service_fields(slug, item, search_text)
    html_text = ""
    if page is not None:
        try:
            html_text = body.decode("utf-8", "replace")
        except Exception:
            html_text = ""
    label_item(slug, item, page=page, html_text=html_text)

    # FIX14 D：AI 老条目刷新额度字段（纯本地计算：查表 + 正则，不联网，
    # 不占解析预算；手写种子只补缺不洗值、简介不动；自动条目已核实的
    # 数字才进简介）。
    if slug == "ai":
        fill_ai_credit_fields(item, write_summary=bool(item.get("auto")))
    # FIX14 C：app 老条目缺截图时补 F-Droid 包页截图（每轮预算 60 条，
    # 只补空的；apple 在 label_item 里已用缓存的 iTunes 数据补过；
    # Play 链接先查 F-Droid 有没有同款包，有才补——Play 本身不提图）。
    if slug == "app" and not (item.get("screenshots") or []) \
            and _shot_budget_take():
        _pkg = fdroid_package_of(url) or fdroid_package_of(
            item.get("sourceUrl") or "")
        if not _pkg and "play.google.com" in urllib.parse.urlparse(
                url).netloc.lower():
            _q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            _pp = (_q.get("id") or [None])[0]
            if _pp and fdroid_has_package(_pp) is True:
                _pkg = _pp
        if _pkg:
            _shots = fdroid_screenshots(_pkg)
            if _shots:
                item["screenshots"] = _shots
                bump("shots")

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
        if (item.get("section") or "") == "discounts":
            if not get_detail(item, "原价"):
                set_detail(item, "原价", "未公布")

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
    """网址规范化：小写协议/主机、去尾斜杠、去跟踪参数、去锚点。
    App Store 链接只认应用 ID：同一个应用换个名字 slug 就换了网址
    （v2box 和 v2box-v2ray-client 都是 id6446814690），不归一化去重就认不出是同一个。"""
    try:
        u = (url or "").strip()
        m = re.search(r"apps\.apple\.com/[a-z-]*/app/[^/]*/id(\d+)", u, re.I)
        if m:
            return f"https://apps.apple.com/app/id{m.group(1)}"
        p = urllib.parse.urlparse(u)
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
    """名称近似：规范化后相等，或短名包含在长名里（短名>=6 字且占长名一半以上）。
    旧规则（>=4 字就判包含）太松：Loon/Stash/Happ 这类短名会撞上无关长名里的
    子串（loon 藏在 yuloong 里、stash 藏在 upstash 里、happ 藏在 happy 里），
    导致正经新条目在拉候选阶段就被当重名扔掉。"""
    na, nb = norm_name(a), norm_name(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    short, long = (na, nb) if len(na) <= len(nb) else (nb, na)
    if len(short) >= 6 and len(short) / len(long) >= 0.5 and short in long:
        return True
    return False


def is_error_title(title):
    """抓到的页面标题是不是通用报错标题（不是真名）。"""
    t = (title or "").strip().lower()
    if not t:
        return True
    return t.startswith(ERROR_TITLE_PREFIXES)


def name_from_url(url):
    """从链接取个名：取最后一段路径，没有就取主机名。"""
    try:
        p = urllib.parse.urlparse((url or "").strip())
        seg = (p.path or "").rstrip("/").rsplit("/", 1)[-1]
        seg = urllib.parse.unquote(seg)
        if seg:
            return seg[:80]
        return (p.hostname or url or "")[:80]
    except Exception:
        return (url or "")[:80]


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
        if status in (403, 429) and page == 1:
            # 撞上搜索限速：等 60 秒再试一次，还不行才放弃这个源
            log(f"GitHub 搜索被限速（{status}），等 60 秒重试一次：{feed.get('query')}")
            time.sleep(60)
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


def feed_candidates_github_repo(feed):
  """指定 GitHub 仓库：直接把仓库当候选，不走搜索（搜索搜不到大仓库）。"""
  repo = (feed.get("repo") or "").strip()
  if not repo or "/" not in repo:
    raise RuntimeError(f"github_repo 源缺 repo：{feed}")
  return [{"name": repo,
           "url": f"https://github.com/{repo}",
           "desc": ""}]


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
    data = _try_json(body.decode("utf-8", "replace"))
    if data is None:
        raise RuntimeError("清单不是有效 JSON")
    return _generic_json_candidates(data)


def feed_candidates_rss(feed):
    status, _ct, body = fetch(feed["url"], max_bytes=2 * 1024 * 1024)
    if status != 200:
        raise RuntimeError(f"RSS 返回 {status}")
    text = body.decode("utf-8", "replace")
    if text.lstrip().startswith("{"):
        data = _try_json(text)
        return _generic_json_candidates(data if data is not None else {})
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


def feed_candidates_direct(feed):
    """指定网址：直接把给定的网址当候选（闭源应用/官网，没有仓库可拉）。
    每条可自带 section，不带就用 feed 的 section。"""
    items = feed.get("items") or []
    out = []
    for it in items:
        name = (it.get("name") or "").strip()
        url = (it.get("url") or "").strip()
        if not name or not url:
            raise RuntimeError(f"direct 源缺 name/url：{it}")
        c = {"name": name, "url": url, "desc": it.get("desc") or ""}
        if it.get("section"):
            c["section"] = it["section"]
        out.append(c)
    return out


def feed_candidates_fdroid(feed):
    """F-Droid 应用列表页：抓 /en/packages/ 里的同站包链接当候选。
    旧的 repo_json 源（/api/v1/packages 全量接口）已被 F-Droid 关掉（403），
    而 page_links 只认外站链接，同站的包链接全被过滤掉，等于没有 F-Droid 来源。
    这个函数直接认 /en/packages/<包名>/ 这种同站链接，一页约 348 个包。"""
    status, _ct, body = fetch(feed["url"], max_bytes=2 * 1024 * 1024)
    if status != 200:
        raise RuntimeError(f"F-Droid 列表页返回 {status}")
    text = body.decode("utf-8", "replace")
    out = []
    seen = set()
    for pkg in re.findall(r'href="/en/packages/([A-Za-z0-9_.]+)/"', text):
        if pkg in seen:
            continue
        seen.add(pkg)
        out.append({"name": pkg,
                    "url": f"https://f-droid.org/en/packages/{pkg}/",
                    "desc": ""})
    return out


FEED_FUNCS = {"github_search": feed_candidates_github_search,
              "github_repo": feed_candidates_github_repo,
              "repo_json": feed_candidates_repo_json,
              "rss": feed_candidates_rss,
              "page_links": feed_candidates_page_links,
              "direct": feed_candidates_direct,
              "fdroid": feed_candidates_fdroid}


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


# ================= FIX14 A：地址解析 =================

PENDING = {}                    # norm_url -> {url,name,slug,reason,firstSeen,lastSeen,tries}
_pending_lock = threading.Lock()
_RESOLVE_BUDGET = {"left": 60}  # 每轮 process_item 里最多解析多少条老条目（逐步替换）
_resolve_lock = threading.Lock()
_SHOT_BUDGET = {"left": 60}     # 每轮 process_item 里最多补多少条老 app 条目的截图
_shot_lock = threading.Lock()


def _shot_budget_take():
    with _shot_lock:
        if _SHOT_BUDGET["left"] > 0:
            _SHOT_BUDGET["left"] -= 1
            return True
        return False
_NODE_CLIENT_LABELS = {"clash": "Clash", "v2ray": "V2Ray",
                       "shadowrocket": "小火箭", "singbox": "Sing-Box"}


def pending_add(url, name, slug, reason):
    """待补清单：东西本身可用、只是暂时没找到可用链接的条目，下次运行重试。"""
    key = norm_url(url or "")
    if not key:
        return
    today = today_cst()
    with _pending_lock:
        e = PENDING.get(key)
        if e:
            e["tries"] = int(e.get("tries") or 0) + 1
            e["lastTried"] = today
            e["reason"] = reason
        else:
            PENDING[key] = {"url": url, "name": name, "slug": slug,
                            "reason": reason, "firstSeen": today,
                            "lastTried": today, "tries": 1}
        bump("pending")


def pending_discard(url):
    with _pending_lock:
        PENDING.pop(norm_url(url or ""), None)


def _resolve_budget_take():
    with _resolve_lock:
        if _RESOLVE_BUDGET["left"] > 0:
            _RESOLVE_BUDGET["left"] -= 1
            return True
        return False


_BARE_REPO_RE = re.compile(r"^https?://github\.com/[^/]+/[^/]+/?$", re.I)


def _is_bare_repo_url(url):
    return bool(_BARE_REPO_RE.match((url or "").strip()))


def _is_store_url(url):
    host = urllib.parse.urlparse(url or "").netloc.lower()
    return ("apps.apple.com" in host or "f-droid.org" in host
            or "play.google.com" in host or "testflight.apple.com" in host)


def _match_config_link(url, slug):
    """链接本身是不是该栏目的整份配置文件：返回 kind（'file' / 'plugins' /
    'single'）或 None。"""
    low = (url or "").lower()
    path = urllib.parse.urlparse(url or "").path.lower()
    if slug == "node":
        if path.endswith((".yaml", ".yml", ".txt", ".json")) \
                or path.rstrip("/").endswith("/sub") or "subscribe" in low:
            return "file"
    elif slug == "video":
        if path.endswith(".json"):
            return "file"
    elif slug == "music":
        if path.endswith("plugins.json"):
            return "plugins"
        if path.endswith(".js"):
            return "single"
    elif slug == "book":
        if path.endswith(".json"):
            return "file"
    elif slug == "live":
        if path.endswith((".m3u", ".m3u8")):
            return "file"
    elif slug == "cfip":
        if path.endswith(".txt"):
            return "file"
    return None


def _classify_node_link(url):
    """node 订阅链接按客户端分：clash / v2ray / shadowrocket / singbox。
    只有一个文件时四个客户端行共用它（由 UI 按客户端分行展示，见报告）。"""
    low = (url or "").lower()
    if "sing-box" in low or "singbox" in low:
        return "singbox"
    if "shadowrocket" in low:
        return "shadowrocket"
    if "clash" in low or low.endswith((".yaml", ".yml")):
        return "clash"
    if "v2ray" in low or low.endswith(".txt") or low.rstrip("/").endswith("/sub"):
        return "v2ray"
    return None


def _count_config_loose(url, body, slug):
    """宽松数条数：count_content 数不出的配置文件格式。
    Clash YAML 里没有 vmess:// 这种 URI，只有结构化条目（- name: / server:），
    sing-box JSON 只有 outbounds 数组，洛雪 .js 根本不是 JSON——这三种
    按结构数，数不出才算验活失败。"""
    if not body:
        return None
    text = body.decode("utf-8", "replace")
    path = urllib.parse.urlparse(url).path.lower()
    if slug == "node" and path.endswith((".yaml", ".yml")):
        n = len(re.findall(r"(?m)^\s*-\s+name\s*:", text))
        if n == 0:
            n = text.count("server:")
        if n > 0:
            return ("节点数", n)
        return None
    if slug == "node" and path.endswith(".json"):
        try:
            d = json.loads(text)
        except Exception:
            return None
        if isinstance(d, dict) and isinstance(d.get("outbounds"), list):
            return ("节点数", len(d["outbounds"]))
        return None
    if slug == "music" and path.endswith(".js"):
        if len(body) > 200 and ("function" in text or "=>" in text
                                or "music" in text.lower()):
            return ("条数", 1)
        return None
    return None


def _verify_one(url, slug, verify):
    """验活一个候选地址：verify=True 走真实请求；False 跳过；callable 用它。
    配置文件栏还要当场数出条数（>0 才算活）。返回 (ok, counted)。"""
    if verify is False:
        return True, None
    if callable(verify):
        return verify(url, slug)
    try:
        status, _ct, body = fetch(url, max_bytes=MAX_BYTES, deadline=20)
    except Exception:
        return False, None
    if status != 200:
        return False, None
    if slug in ("video", "music", "book", "live", "node", "cfip"):
        counted = count_content(url, body) if body else None
        if not counted:
            counted = _count_config_loose(url, body, slug)
        if not counted or counted[1] <= 0:
            return False, counted
        return True, counted
    if not body or len(body) < 100:
        return False, None
    return True, None


def _is_labelled(link):
    text = ((link.get("label") or "") + " " + (link.get("context") or "")).lower()
    return any(k in text for k in ("订阅", "下载", "subscribe", "download",
                                   "配置", "全量", "最新", "导入", "install"))


def resolve_usable_url(slug, cand, material, verify=True):
    """按栏目优先级找可用地址。返回 {"url","source_url","config_url",
    "client_links","found","pending_reason"}。
    规则：App Store / F-Droid / Play 本身已是可用地址，原样保留；
    app/apple/proxy 的 GitHub 仓库 -> {repo}/releases；其余从 README/页面
    链接里按栏目找特征文件（node 订阅、video .json、music plugins.json、
    book 书源 .json、live .m3u、cfip .txt、ai/service/domain 找 homepage）；
    命中多个取 README 里最靠前或被“订阅/下载”标注的那个；候选必须当场
    验活才落盘，验不活就 fallback 到仓库地址并进待补清单。"""
    cand_url = (cand.get("url") or "").strip()
    repo = github_repo_of(cand_url)
    repo_url = f"https://github.com/{repo}" if repo else ""
    info = material.get("github") or {}
    homepage = (material.get("homepage") or info.get("homepage") or "").strip()
    links = material.get("links")
    if links is None and repo:
        links = readme_links(repo, info)
    links = links or []

    def _fallback(reason):
        return {"url": cand_url, "source_url": "",
                "config_url": "", "client_links": {}, "found": False,
                "pending_reason": reason}

    # 1) 商店链接 / 候选本身就是配置文件：原样保留（不验活：商店页常挡爬虫，
    # 配置文件在 deep_dive 里已数过条数；这里再验一次也行，但别挡住它）。
    if _is_store_url(cand_url):
        return {"url": cand_url, "source_url": repo_url,
                "config_url": "", "client_links": {}, "found": True,
                "pending_reason": ""}
    own_kind = _match_config_link(cand_url, slug)
    if own_kind:
        ok, _c = _verify_one(cand_url, slug, verify)
        if ok:
            cfg = cand_url if slug in ("video", "music", "book", "live", "node",
                                       "cfip") else ""
            cl = {}
            if slug == "node":
                cl = _node_client_links([cand_url], cand_url)
            return {"url": cand_url, "source_url": repo_url,
                    "config_url": cfg, "client_links": cl, "found": True,
                    "pending_reason": ""}
        return _fallback("候选配置文件验活失败")

    # 2) 从 README/页面链接里按栏目找候选
    cands = []  # (url, kind)
    for link in links:
        u = (link.get("url") or "").strip()
        if not u or norm_url(u) == norm_url(cand_url):
            continue
        if slug in ("ai", "service", "domain", "adult"):
            continue  # 这四栏走 homepage / 外链，不走配置文件
        kind = _match_config_link(u, slug)
        if kind:
            cands.append((u, kind, link))
    if cands:
        # 被“订阅/下载”标注的优先，其余按 README 里出现的顺序
        cands.sort(key=lambda t: (0 if _is_labelled(t[2]) else 1))
        verified = []
        for u, kind, _link in cands:
            ok, _c = _verify_one(u, slug, verify)
            if ok:
                verified.append((u, kind))
        if verified:
            # node：主地址优先通用订阅（v2ray），其余客户端的进 clientLinks
            if slug == "node":
                main = verified[0][0]
                for u, _k in verified:
                    if _classify_node_link(u) == "v2ray":
                        main = u
                        break
                cl = _node_client_links([u for u, _k in verified], main)
                cfg = ""
                for u, _k in verified:
                    if _classify_node_link(u) == "clash":
                        cfg = u
                        break
                return {"url": main, "source_url": repo_url,
                        "config_url": cfg or main, "client_links": cl,
                        "found": True, "pending_reason": ""}
            main, _kind = verified[0]
            return {"url": main, "source_url": repo_url,
                    "config_url": main if slug in ("video", "music", "book",
                                                   "live", "cfip") else "",
                    "client_links": {}, "found": True, "pending_reason": ""}
        return _fallback("README 里找到的配置文件验活都不通")

    # 3) ai/service/domain/adult：仓库 homepage，次选 README 里的官网/外链
    if slug in ("ai", "service", "domain", "adult"):
        if homepage and not homepage.startswith("https://github.com"):
            ok, _c = _verify_one(homepage, slug, verify)
            if ok:
                return {"url": homepage, "source_url": repo_url,
                        "config_url": "", "client_links": {}, "found": True,
                        "pending_reason": ""}
        for link in links:
            u = (link.get("url") or "").strip()
            host = urllib.parse.urlparse(u).netloc.lower()
            if not u or host in ("github.com", "raw.githubusercontent.com",
                                 "shields.io", "img.shields.io"):
                continue
            if host in ("twitter.com", "x.com", "facebook.com", "instagram.com",
                        "youtube.com", "youtu.be", "discord.com", "discord.gg",
                        "t.me", "telegram.me"):
                continue
            ok, _c = _verify_one(u, slug, verify)
            if ok:
                return {"url": u, "source_url": repo_url,
                        "config_url": "", "client_links": {}, "found": True,
                        "pending_reason": ""}
        return _fallback("没找到可用的官网/外链")

    # 4) app/apple/proxy 的 GitHub 仓库 -> releases
    if slug in ("app", "apple", "proxy") and repo:
        rel = repo_url + "/releases"
        ok, _c = _verify_one(rel, slug, verify)
        if ok:
            return {"url": rel, "source_url": repo_url,
                    "config_url": "", "client_links": {}, "found": True,
                    "pending_reason": ""}
        return _fallback("releases 页打不开")

    # 5) 其余：README 里被“订阅/下载”标注的通用链接
    for link in links:
        u = (link.get("url") or "").strip()
        if u and _is_labelled(link):
            ok, _c = _verify_one(u, slug, verify)
            if ok:
                return {"url": u, "source_url": repo_url,
                        "config_url": "", "client_links": {}, "found": True,
                        "pending_reason": ""}
    return _fallback("README 里没找到可用地址")


def _node_client_links(verified_urls, main_url):
    """node 按客户端分行：{clash, v2ray, shadowrocket, singbox} -> 直链。
    数据结构（供第二阶段 index.html 用）：item["clientLinks"] 就是这个 dict；
    某个客户端没有专属文件时，该行共用主订阅直链（UI 按四行展示，每行带
    复制按钮；key 缺失时 UI 回落到 item.url）。"""
    out = {}
    for u in verified_urls:
        k = _classify_node_link(u)
        if k and k not in out:
            out[k] = u
    for k in ("clash", "v2ray", "shadowrocket", "singbox"):
        out.setdefault(k, main_url)
    return out


# ---------------- service/domain 合集拆分 ----------------

_COLLECTION_NAME_HINTS = ("awesome", "free-for-dev", "free-stuff", "free-tier",
                          "free-services", "list", "大全", "合集",
                          "free-for-developers", "freebies")
_SOCIAL_HOSTS = {"twitter.com", "x.com", "facebook.com", "instagram.com",
                 "youtube.com", "youtu.be", "discord.com", "discord.gg",
                 "t.me", "telegram.me", "linkedin.com", "medium.com"}


def parse_collection_entries(readme_raw, repo_url=""):
    """拆合集仓库 README 里的服务/域名列表：认列表行（- / * / 数字 / 表格行）
    里的 [名字](链接)，返回 [{"name","url","desc"}]。"""
    entries, seen = [], set()
    if not readme_raw:
        return entries
    for line in readme_raw.splitlines():
        s = line.strip()
        if not s:
            continue
        if not (s.startswith(("-", "*", "+")) or s.startswith("|")
                or re.match(r"\d+[.)]\s", s)):
            continue
        m = re.search(r"\[([^\]]{1,80})\]\((https?://[^)\s]+)\)", s)
        url = name = desc = ""
        if m:
            name, url = m.group(1).strip(), _clean_link_url(m.group(2))
            desc = re.sub(r"^[\s\-–—:：|]+", "", s[m.end():]).strip()
        else:
            m2 = re.search(r"https?://[^\s\)\]\}]+", s)
            if not m2:
                continue
            url = _clean_link_url(m2.group(0))
            name = re.sub(r"^[\s\-–—:：\d.)]+", "", s[:m2.start()]).strip()[:60] \
                or name_from_url(url)
            desc = s[m2.end():].strip(" -–—:：|")
        host = urllib.parse.urlparse(url).netloc.lower()
        if not url or url in seen:
            continue
        if host in _SOCIAL_HOSTS or "shields.io" in host:
            continue
        if url.lower().endswith((".svg", ".png", ".jpg", ".md")):
            continue
        if repo_url and norm_url(url) == norm_url(repo_url):
            continue
        if "github.com" in host and "/search" in url:
            continue
        name = re.sub(r"^[^\w一-鿿]+", "", name).strip()[:60]
        if len(name) < 2:
            continue
        seen.add(url)
        entries.append({"name": name, "url": url, "desc": desc[:200]})
    return entries


def is_collection_repo(slug, cand, material):
    """是不是合集仓库（只看 service/domain）：README 里列表项链接占大头、
    且指向 ≥6 个不同外部域名（或名字带 awesome/list/大全等且 ≥4 个域名）。
    真服务的 README 链接多半指向自家文档/GitHub，不会满足这个条件。"""
    if slug not in ("service", "domain"):
        return False
    repo = github_repo_of(cand.get("url") or "")
    if not repo:
        return False
    raw = material.get("readme_raw") or ""
    if not raw:
        return False
    entries = parse_collection_entries(raw, cand.get("url") or "")
    if len(entries) < 5:
        return False
    domains = {urllib.parse.urlparse(e["url"]).netloc.lower()
               for e in entries}
    domains.discard("github.com")
    total_links = len(extract_md_links(raw))
    if total_links and len(entries) < 0.5 * total_links:
        return False
    hinted = any(h in (cand.get("name") or "").lower()
                 or h in (cand.get("desc") or "").lower()
                 for h in _COLLECTION_NAME_HINTS)
    if len(entries) >= 8 and len(domains) >= 6:
        return True
    if hinted and len(entries) >= 5 and len(domains) >= 4:
        return True
    return False


def split_collection_repo(slug, cand, material):
    """把合集仓库拆成单条候选（每条走正常 deep_dive + resolve 流程）。
    拆不出来（<3 条）时调用方直接放弃这个仓库，不收。"""
    entries = parse_collection_entries(material.get("readme_raw") or "",
                                       cand.get("url") or "")
    out = []
    for e in entries:
        c = {"name": e["name"], "url": e["url"], "desc": e["desc"]}
        if cand.get("subcat"):
            c["subcat"] = cand["subcat"]
        if cand.get("section"):
            c["section"] = cand["section"]
        out.append(c)
    return out


# ---------------- 深挖：把候选挖出实义内容 ----------------

PARKED_HINTS = ("parked", "domain for sale", "buy this domain", "该域名",
                "域名停放", "domain parking", "coming soon")


# ================= FIX14 D：AI 额度字段（ai-kb-spec.md） =================
# 填充顺序（规范 §3）：域名精确匹配 → 仓库名匹配 → 正则抽取 → “未公布”。
# 禁止按 name 模糊匹配、禁止按厂商合并。数字绝不猜。
# 核实状态单独记 details「额度核实」，不洗 7 项原值：展示层按它决定
# 是否把数字当确定事实展示（规范 §7：needs_check/stale 一律“待核实”）。

AI_CREDIT_LABELS = ["首次注册送", "每天额度", "其他途径", "总额度",
                    "额度有效期", "是否要绑卡", "领取门槛"]
_AI_KB = {"loaded": False, "entries": [], "conflicts": set()}
_ai_kb_lock = threading.Lock()


def _ai_kb_path():
    """知识表路径：collect.py 所在目录的 ai_kb.json。
    仓库里 collect.py 在 scripts/ 下，对应 scripts/ai_kb.json。"""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai_kb.json")


def load_ai_kb():
    """加载 AI 额度知识表（只读一次，线程安全）。
    文件缺失/损坏时返回 []，调用方优雅降级为只走正则抽取，不报错、不中断。"""
    with _ai_kb_lock:
        if _AI_KB["loaded"]:
            return _AI_KB["entries"]
        _AI_KB["loaded"] = True
        try:
            with open(_ai_kb_path(), encoding="utf-8") as f:
                data = json.load(f)
            entries = data.get("entries") or []
            _AI_KB["entries"] = [e for e in entries if isinstance(e, dict)]
            conf = set()
            for c in ((data.get("audit") or {}).get("internal_conflicts") or []):
                if isinstance(c, dict) and c.get("name"):
                    conf.add(c["name"])
            _AI_KB["conflicts"] = conf
        except Exception:
            _AI_KB["entries"] = []
            _AI_KB["conflicts"] = set()
        return _AI_KB["entries"]


def _ai_norm_host(url):
    """host 规范化：小写、去 www.、去端口、去路径/query（规范 §3）。"""
    try:
        h = urllib.parse.urlparse(url or "").netloc.lower()
    except Exception:
        return ""
    h = h.split("@")[-1].split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h


def _ai_kb_lookup(item):
    """按规范 §3 查表，命中返回 entry，否则 None。
    陷阱：KB 里 GitHub Copilot Free 条目的 domains 含 github.com（它的 url
    是产品页 https://github.com/copilot），裸 host 匹配会把所有 GitHub
    仓库都算成 Copilot——github.com 只认整条 URL 相等或仓库名匹配。"""
    entries = load_ai_kb()
    if not entries:
        return None
    url = (item.get("url") or "").strip()
    host = _ai_norm_host(url)
    if not host:
        return None
    if host == "github.com":
        for e in entries:
            if norm_url(url) == norm_url(e.get("url") or ""):
                return e
        repo = github_repo_of(url)
        if repo:
            for e in entries:
                if (e.get("repo") or "").lower() == repo.lower():
                    return e
        return None
    for e in entries:
        doms = []
        for d in (e.get("domains") or []):
            dd = str(d).lower()
            if dd.startswith("www."):
                dd = dd[4:]
            doms.append(dd)
        if host in doms:
            return e
    return None


def _ai_stale_reason(entry):
    """KB 条目是否按“待核实”处理（规范 §7），是则返回原因，否则返回 ""。
    source_date 为 unknown（v1 全部 29 条）→ 待核实；status 非 verified →
    待核实；source_date 超 90 天 → 待核实；种子内口径打架/旧口径过时 → 待核实。"""
    if not entry:
        return ""
    name = entry.get("name") or ""
    if name in _AI_KB.get("conflicts", set()):
        return "种子内口径打架/多口径并存，待复核"
    notes = " ".join(str(n) for n in (entry.get("notes") or []))
    if "打架" in notes or "过时" in notes:
        return "种子注明口径打架/旧口径过时，待复核"
    status = (entry.get("status") or "").strip()
    sd = (entry.get("source_date") or "").strip()
    if status != "verified":
        if sd in ("", "unknown"):
            return "数字未复核，来源日期未知"
        return "数字未复核"
    if sd in ("", "unknown"):
        return "来源日期不明"
    try:
        d = datetime.datetime.strptime(sd, "%Y-%m-%d").date()
        today = datetime.datetime.strptime(today_cst(), "%Y-%m-%d").date()
    except Exception:
        return "来源日期格式不明"
    if (today - d).days > 90:
        return "来源已超 90 天"
    return ""


_AI_SIGNUP_ATTRS = ("sign up", "sign-up", "new user", "new users",
                    "new account", "free trial", "promo", "get started")


def _ai_near_attr(text, start, end, attrs, window=120):
    """数字附近（前后 120 字内）有没有归属词（规范 §4.2），二者缺一不可。"""
    near = text[max(0, start - window):min(len(text), end + window)].lower()
    return any(a in near for a in attrs)


def _ai_extract_from_text(text):
    """未命中查表时，只从 descriptionOriginal 正则抽明确写出的数字（规范 §4）。
    只抽这几种格式；光有数字没有归属词不抽；单位不换算、不推导。
    返回 {中文 label: 抽到的原文片段}，抽不到的键不出现。"""
    t = (text or "")
    out = {}
    if not t.strip():
        return out
    # 首次注册送：$5 credit / 25 free credits，须带注册归属词
    for m in re.finditer(r"\$\s?\d+(?:\.\d+)?\s*(?:in\s+)?credits?\b", t, re.I):
        if _ai_near_attr(t, m.start(), m.end(), _AI_SIGNUP_ATTRS):
            out["首次注册送"] = m.group(0).strip()
            break
    if "首次注册送" not in out:
        for m in re.finditer(r"\d[\d,]*\s+free\s+(credits?|tokens?)\b", t, re.I):
            if _ai_near_attr(t, m.start(), m.end(), _AI_SIGNUP_ATTRS):
                out["首次注册送"] = m.group(0).strip()
                break
    # 每天额度：数字 + 单位 + /day|per day|daily。月额度/一次性不算。
    for m in re.finditer(
            r"\d[\d,]*\s*(?:requests?|tokens?|credits?|neurons?|messages?|images?)"
            r"\s*(?:/\s*day|per\s+day|daily\b)", t, re.I):
        out["每天额度"] = m.group(0).strip()
        break
    if "每天额度" not in out:
        for m in re.finditer(
                r"\bdaily\b[^.\n]{0,60}?\d[\d,]*\s*"
                r"(?:requests?|tokens?|credits?|neurons?)", t, re.I):
            out["每天额度"] = m.group(0).strip()
            break
    # 每天登录/签到奖励：只有原文明确写了 sign in/check in/daily login 才写，
    # 进“每天额度”（与可灵/即梦种子口径一致）；只知道每日限速时绝不反推。
    if "每天额度" not in out:
        for pat in (
                r"daily\s+(?:login|log[- ]in|sign[- ]in|check[- ]in)[^.\n]{0,80}?"
                r"\d[\d,]*\s*(?:credits?|tokens?|points?|coins?)",
                r"(?:log\s*in|sign\s*in|check\s*in)[^.\n]{0,80}?"
                r"\d[\d,]*\s*(?:credits?|tokens?|points?|coins?)\b"):
            m = re.search(pat, t, re.I)
            if m:
                out["每天额度"] = m.group(0).strip()
                break
    # 总额度：只有上下文明确写一次性（one-time）时才进
    for m in re.finditer(
            r"one[- ]time[^.\n]{0,60}?(\$\s?\d+(?:\.\d+)?|\d[\d,]*\s*credits?)",
            t, re.I):
        out["总额度"] = m.group(0).strip()
        break
    if "总额度" not in out:
        for m in re.finditer(
                r"(\$\s?\d+(?:\.\d+)?|\d[\d,]*\s*credits?)\s+one[- ]time",
                t, re.I):
            out["总额度"] = m.group(0).strip()
            break
    # 额度有效期：须带明确天数/月数
    for m in re.finditer(
            r"(?:valid\s+for|expires?\s+in|good\s+for)\s+\d+\s*(?:days?|months?|years?)",
            t, re.I):
        out["额度有效期"] = m.group(0).strip()
        break
    if "额度有效期" not in out:
        for m in re.finditer(r"\d+\s*[- ](?:day|month)\s+trial\b", t, re.I):
            out["额度有效期"] = m.group(0).strip()
            break
    # 是否要绑卡：只认明确写出的 no card / requires a credit card
    if re.search(r"\bno\s+(credit\s+)?card\s+(required|needed)\b|\bno\s+card\b",
                 t, re.I):
        out["是否要绑卡"] = "不要"
    elif re.search(r"\b(requires?|needs?)\s+(a\s+)?(valid\s+)?"
                   r"(credit\s+card|payment\s+method)\b"
                   r"|\bcredit\s+card\s+required\b"
                   r"|\bcard\s+required\b", t, re.I):
        out["是否要绑卡"] = "要"
    return out


def _ai_credit_sentence(item):
    """简介里写数的句子（规范 §6）：只写 details 里已有的、带数字的三项必填，
    按“首次注册送 / 每天额度 / 额度有效期”顺序串起来。没有带数字的三项
    就返回 ""（简介里不写数）。"""
    parts = []
    for label, prefix in (("首次注册送", "注册送"), ("每天额度", "每天额度"),
                          ("额度有效期", "有效期")):
        v = get_detail(item, label)
        if v and v != "未公布" and re.search(r"\d", v):
            parts.append(f"{prefix}：{v}")
    if not parts:
        return ""
    return "；".join(parts) + "。"


def fill_ai_credit_fields(item, write_summary=False):
    """FIX14 D：给一条 AI 栏条目填 7 项额度字段（details）。
    顺序：查 ai_kb.json → 未命中则从 descriptionOriginal 正则抽 → 抽不到
    填“未公布”。已有真实值（非空、非裸“未公布”）只补缺、绝不洗掉——手写
    种子的 7 项原值原样保留。核实状态记 details「额度核实」（待核实/已核实），
    不动 7 项原值。简介里的数只在已核实时写（write_summary=True 时），
    且只写 details 里已有的数。
    返回 {"source": "kb"|"regex"|"none", "entry": 命中条目名或 "",
          "stale": bool, "stale_reason": str, "filled": [补上的 label]}。"""
    if not isinstance(item, dict):
        return {"source": "none", "entry": "", "stale": False,
                "stale_reason": "", "filled": []}
    entry = _ai_kb_lookup(item)
    values = {}
    source = "none"
    entry_name = ""
    stale = False
    stale_reason = ""
    if entry:
        source = "kb"
        entry_name = entry.get("name") or ""
        credits = entry.get("credits") or {}
        for label in AI_CREDIT_LABELS:
            v = credits.get(label)
            if isinstance(v, str) and v.strip():
                values[label] = v.strip()
        stale_reason = _ai_stale_reason(entry)
        stale = bool(stale_reason)
    else:
        ext = _ai_extract_from_text(item.get("descriptionOriginal") or "")
        if ext:
            source = "regex"
            values.update(ext)
            # 抽取 ≠ 复核（规范 §4.3）：抽到的数字一律待核实。
            stale = True
            stale_reason = "正则抽取未经官方复核"
    filled = []
    for label in AI_CREDIT_LABELS:
        cur = get_detail(item, label)
        if cur and cur != "未公布":
            continue
        if label in values:
            set_detail(item, label, values[label])
            filled.append(label)
        elif not cur:
            set_detail(item, label, "未公布")
            filled.append(label)
    if source in ("kb", "regex"):
        if stale:
            set_detail(item, "额度核实",
                       "待核实" + (f"（{stale_reason}）" if stale_reason else ""))
        else:
            set_detail(item, "额度核实",
                       f"已核实（{(entry or {}).get('source_date') or today_cst()}）")
        bump("ai_credit")
    if write_summary and not stale and source == "kb":
        sent = _ai_credit_sentence(item)
        if sent and sent not in (item.get("summary") or ""):
            summary = (item.get("summary") or "").strip()
            item["summary"] = (summary + " " + sent).strip() if summary else sent
    return {"source": source, "entry": entry_name, "stale": stale,
            "stale_reason": stale_reason, "filled": filled}


def deep_dive(slug, cand):
    """深挖一个候选，返回 (material, extra) 或 (None, reason)。
    material: {'title','text','lang','counted','github','itunes','screenshots'}"""
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
                "counted": None, "github": None, "itunes": rec,
                # FIX14 C：iTunes screenshotUrls 顺着 rec 取，前 3 张
                "screenshots": itunes_screenshots(rec)}, None

    # F-Droid 应用：包页完整介绍
    pkg = fdroid_package_of(url)
    if pkg:
        text = fdroid_page_text(pkg)
        if len(text) < 40:
            return None, "F-Droid 介绍太短"
        return {"title": cand["name"], "text": text[:1500],
                "lang": judge_language(text), "counted": None,
                "github": None, "itunes": None,
                # FIX14 C：F-Droid 包页截图，前 3 张
                "screenshots": fdroid_screenshots(pkg)}, None

    # GitHub 仓库：仓库简介 + README 开头几段
    # 注意：github_info 拿不到（API 限流 403/仓库改名 301）时也别直接扔掉——
    # README 是走 raw.githubusercontent.com 抓的，不走 API，照样能当实义材料；
    # 只有简介和 README 都拿不到才算没内容。星标/更新时间缺了下次刷新再补。
    repo = github_repo_of(url)
    if repo:
        info = github_info(repo)
        readme = github_readme(repo, info)
        desc = (info or {}).get("description") or ""
        # FIX14：仓库简介首句与 README 无关时扔掉简介（mihomo 的简介写的是
        # 星穹铁道数据模型，README 才是 Clash 内核——简介是仓库自己的 About
        # 栏写错了，不是抓错页面；只用 README 就不会张冠李戴）。
        text = _pick_github_text(desc, readme, repo, slug)
        if len(text.strip()) < 25:
            return None, "仓库没简介、README 也没内容"
        return {"title": cand["name"], "text": text[:1500],
                "lang": judge_language(text), "counted": None,
                "github": info, "itunes": None,
                "links": readme_links(repo, info),
                "readme_raw": github_readme_raw(repo, info),
                "homepage": (info or {}).get("homepage") or ""}, None

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
    links = []
    for h in page.hrefs:
        full = urllib.parse.urljoin(url, h)
        if full.startswith(("http://", "https://")):
            links.append({"url": full, "label": "", "context": ""})
    return {"title": page.title or cand["name"], "text": text[:1500],
            "lang": judge_language(text, page.lang), "counted": None,
            "github": None, "itunes": None, "page": page,
            "links": links}, None


def build_item(slug, cand, material, new_id):
    """用深挖材料拼新条目：四样齐全（定位/功能/用途/适合谁）。"""
    kind, use, who = cat_context_for(slug, cand.get("section") or "")
    _t = (material.get("title") or "").strip()
    if is_error_title(_t):
        _t = ""
    name = (_t or cand["name"]).strip()[:80]
    url = cand["url"]
    # 代理栏：中文名（新条目从这里改名）
    _cn = proxy_cn_for(slug, name, url)
    if _cn:
        name = _cn[0]
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
    # FIX14 A：地址解析——可用地址进 url，仓库主页挪进 sourceUrl，
    # 整份配置文件直链进 builtinConfigUrl，node 再记 clientLinks。
    # 新条目的这三个字段本来就是空的，不存在洗掉已有值的问题。
    resolved = resolve_usable_url(slug, cand, material)
    url = resolved["url"]
    item["url"] = url
    if resolved.get("source_url"):
        item["sourceUrl"] = resolved["source_url"]
    if resolved.get("config_url"):
        item["builtinConfigUrl"] = resolved["config_url"]
        item["builtinConfigName"] = "整份配置"
    if resolved.get("client_links"):
        item["clientLinks"] = resolved["client_links"]
    if resolved.get("pending_reason"):
        item["_pending_reason"] = resolved["pending_reason"]
    elif resolved.get("found"):
        bump("resolved")
    # FIX14 C：截图只从深挖材料里拿（iTunes / F-Droid），README 里的图
    # 绝不当应用截图；其他栏目不写截图。
    if slug in ("apple", "app"):
        item["screenshots"] = _valid_screenshot_urls(
            material.get("screenshots") or [])
    # FIX14 B：真实简介——英文取原文首句/首段实质内容（带相关性校验），
    # 中文取原文首句并清洗，不再拼 "{种类}（英文）。主要用来{用途}，适合{人群}"
    # 的纯模板，也不再 original[:70] / original[:800] 硬切（不断半个英文词）。
    # 门禁只卡 description（≥40 字且含“适合”）：下面两条分支都保留了
    # “适合{who}”尾句，summary 不受门禁约束，放开写真实内容。
    _repo = github_repo_of(cand["url"]) or ""
    if is_english:
        item["descriptionOriginal"] = original[:1200]
        _first = first_substantive_sentence(original, 200, slug, name, _repo,
                                            original)
        _para = first_paragraph(original, 300, slug, name, _repo) or _first
        if not _first:
            item["description"] = f"{position}。英文项目，简介待补，适合{who}。"
            item["summary"] = "英文项目，简介待补。"
            item["_pending_reason"] = "英文简介待补（原文首句无实质内容或与条目无关）"
        else:
            item["description"] = (
                f"{position}。{_para}适合{who}。"
                "页面是英文的，可以用浏览器自动翻译看。")
            item["summary"] = (first_substantive_sentence(
                original, 70, slug, name, _repo, original) or _first)
    else:
        _para = first_paragraph(original, 300, slug, name, _repo) or original
        item["description"] = (
            f"{position}。{_para}它主要用来{use}。适合{who}。")
        item["summary"] = clean_zh_summary(original, slug, name, _repo) \
            or f"{kind}。主要用来{use}，适合{who}。"
    # 代理栏：中文介绍（ curated，覆盖上面按材料拼的通用介绍）
    if _cn:
        item["description"] = _cn[1]
        item["summary"] = _cn[1].split("。")[0] + "。" if "。" in _cn[1] else _cn[1]
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
    # 代理栏：语言以写死的为准（material 的语言可能被英文 README 带偏）
    if slug == "proxy":
        _lg = proxy_lang_for(item)
        if _lg:
            set_detail(item, "语言", _lg[0])
            set_detail(item, "能否改中文", _lg[1])
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
        if item.get("section") == "discounts":
            if not get_detail(item, "原价"):
                set_detail(item, "原价", "未公布")
    # FIX14 D：AI 新条目补 7 项额度字段（KB 命中 / 正则抽取 / 未公布）；
    # 已核实的数字才进简介，且只写 details 里已有的数。
    if slug == "ai":
        fill_ai_credit_fields(item, write_summary=True)
    if not item["imageUrl"]:
        img = find_image(url)
        if img:
            item["imageUrl"] = img
    return item


def _desc_orig_suspect(item, slug="", text=""):
    """descriptionOriginal 是不是可疑的旧值：老版本 200 字硬截断的遗留、
    GitHub 页面 og:description 覆盖时带的 " - owner/repo" 后缀、
    首句与新鲜 README 无关（张冠李戴）。是就返回 True，等重建。"""
    o = (item.get("descriptionOriginal") or "").strip()
    if not o:
        return False
    if len(o) == 200 and o.endswith(("...", "…")):
        return True
    repo = github_repo_of(item.get("url") or "") or \
        github_repo_of(item.get("sourceUrl") or "") or ""
    if repo and re.search(r"\s+-\s+" + re.escape(repo) + r"$", o):
        return True
    if text:
        first = _split_sentences(o)
        if first and not _sentence_relevant(first[0], slug,
                                            item.get("name") or "", repo, text):
            return True
    return False


def _rebuild_item_text(slug, item, material):
    """用新鲜 README 重建老条目的模板简介 / 可疑原文介绍。
    只动自动收录（auto）的条目、手写种子不动；只在简介是模板句，
    或 descriptionOriginal 可疑时才动手。返回是否改过。"""
    if not item.get("auto"):
        return False
    text = (material.get("text") or "").strip()
    if len(text) < 25:
        return False
    kind, use, who = cat_context_for(slug, item.get("section") or "")
    name = item.get("name") or ""
    repo = github_repo_of(item.get("url") or "") or \
        github_repo_of(item.get("sourceUrl") or "") or ""
    changed = False
    if _desc_orig_suspect(item, slug, text):
        item["descriptionOriginal"] = text[:1200]
        changed = True
    if _is_template_summary(item.get("summary") or ""):
        lang = judge_language(text)
        if lang == "英文":
            first = first_substantive_sentence(text, 70, slug, name, repo, text)
            if first:
                item["summary"] = first
                para = first_paragraph(text, 300, slug, name, repo) or first
                item["description"] = (
                    f"{name} 是 GitHub 上的一个开源{kind}。{para}适合{who}。"
                    "页面是英文的，可以用浏览器自动翻译看。")
                changed = True
        else:
            s = clean_zh_summary(text, slug, name, repo)
            if s:
                item["summary"] = s
                changed = True
    return changed


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
    """读 state.json：fails（连续验活失败次数）+ pending（待补清单）。
    返回 (fails, path, pending_list)。"""
    path = os.path.join(script_dir, "state.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        fails = data.get("fails") or {}
        pending = data.get("pending") or []
        if not isinstance(pending, list):
            pending = []
        return {str(k): int(v) for k, v in fails.items()}, path, pending
    except Exception:
        return {}, path, []


def save_state(path, fails, keep_urls, pending=None):
    """写 state.json：fails 只留还在站上的、次数 >0 的；pending（待补清单）
    只留还在站上的条目（已解析掉 / 已删掉的自然掉出），最多 1000 条。"""
    pend = []
    if pending:
        for e in pending:
            if isinstance(e, dict) and norm_url(e.get("url") or "") in keep_urls:
                pend.append(e)
    data = {"fails": {u: n for u, n in fails.items() if u in keep_urls and n > 0},
            "pending": pend[:1000],
            "updatedAt": now_iso_z()}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(tmp, path)


def write_screened(root_dir, removed):
    """FIX14 #8“今天筛掉了什么”：本轮实际删掉的条目（失效/过期/跨栏重复），
    写进仓库根目录 screened.json（data.json 旁边，工作流提交时一起提交），
    供第二阶段 index.html 渲染。只记、不拦；写失败也不影响本轮。"""
    try:
        screened = {"date": today_cst(),
                    "removed": [{"name": n, "url": u, "slug": s, "reason": r}
                                for s, n, u, r in removed],
                    "updatedAt": now_iso_z()}
        sp = os.path.join(root_dir, "screened.json")
        tmp = sp + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(screened, f, ensure_ascii=False, indent=1)
            f.write("\n")
        os.replace(tmp, sp)
        return sp
    except Exception as e:
        log(f"screened.json 写失败（不影响本轮）：{e}")
        return None


# ---------------- 主流程 ----------------

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    default_path = os.path.join(os.path.dirname(script_dir), "data.json")
    path = sys.argv[1] if len(sys.argv) > 1 else default_path
    root_dir = os.path.dirname(os.path.abspath(path))

    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    categories = data.get("categories") or {}
    state_fails, state_path, state_pending = load_state(script_dir)
    # FIX14：待补清单读进内存；本轮解析预算清零（逐步替换老条目）。
    for _e in state_pending:
        if isinstance(_e, dict) and _e.get("url"):
            PENDING[norm_url(_e["url"])] = _e
    _RESOLVE_BUDGET["left"] = 60
    _SHOT_BUDGET["left"] = 60

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

    # 安全阀一：超过 30% 不通 → 本轮不删任何条目（验活数据可能不可信），
    # 但刷新、新增、记账照常写盘——阀门只拦「删」，绝不整轮作废。
    valve1_tripped = bool(total_before and STATS["dead"] > total_before * 0.30)
    if valve1_tripped:
        log(f"安全阀一触发：{STATS['dead']}/{total_before} 条不通（超过 30%），"
            "本轮跳过删除，刷新与新增照常。")

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

    # 安全阀一落地：本轮验活不可信，不删任何条目（失败次数已记在 state.json，
    # 下轮复核时再按规矩删；苹果限免的降组不受影响，那是按现价核实的）。
    if valve1_tripped and REMOVED:
        log(f"安全阀一：本轮跳过删除 {len(REMOVED)} 条，留待下轮复核。")
        REMOVED.clear()
        removed_keys.clear()

    # 安全阀二落地：一次移除超过全站 25% 多半是源头异常——不中止，只删够
    # 25% 的名额，剩下的留到下轮（失败次数已落盘，下轮接着删）。
    if total_before and len(REMOVED) > total_before * 0.25:
        removal_cap = int(total_before * 0.25)
        log(f"移除量安全阀触发：本轮要移除 {len(REMOVED)} 条，超过全站 "
            f"{total_before} 条的 25%，只删 {removal_cap} 条，剩下的下轮再删。")
        keep_urls = {norm_url(u) for _s, _n, u, _r in REMOVED[:removal_cap] if u}
        REMOVED[:] = REMOVED[:removal_cap]
        removed_keys = {id(it) for cat in categories.values()
                        for it in (cat.get("items") or [])
                        if norm_url(it.get("url") or "") in keep_urls}

    # ---- 跨栏去重：同一链接只留一个栏（标题能看出来是什么的归专栏，免费服务兜底） ----
    _url_to_locs = {}
    for _slug, _cat in categories.items():
        for _it in _cat.get("items") or []:
            _u = norm_url(_it.get("url") or "")
            if _u:
                _url_to_locs.setdefault(_u, []).append((_slug, _it))
    _dedup_removed = 0
    for _u, _locs in _url_to_locs.items():
        if len(_locs) < 2:
            continue
        _locs.sort(key=lambda kv: _DEDUP_RANK.get(kv[0], 99))
        _keep_slug = _locs[0][0]
        for _slug, _it in _locs[1:]:
            if id(_it) in removed_keys:
                continue
            _cat = categories[_slug]
            _cat["items"] = [x for x in (_cat.get("items") or []) if x is not _it]
            REMOVED.append((_slug, _it.get("name") or "", _it.get("url") or "",
                            f"跨栏重复，保留在「{_keep_slug}」栏"))
            _dedup_removed += 1
    if _dedup_removed:
        log(f"跨栏去重：移除 {_dedup_removed} 条重复（保留在优先级更高的栏）。")
    # 顺手修重名：页面标题是通用报错的条目，名字改成链接文件名。
    _renamed = 0
    for _cat in categories.values():
        for _it in _cat.get("items") or []:
            if is_error_title(_it.get("name") or ""):
                _new = name_from_url(_it.get("url") or "")
                if _new and _new != _it.get("name"):
                    _it["name"] = _new
                    _renamed += 1
    if _renamed:
        log(f"标题修正：{_renamed} 条报错标题改成链接文件名。")

    # ---- 新增：先补位、再扩充；不重复的新条目全数加进（每栏总量上限 300） ----
    max_id = [0]
    _id_lock = threading.Lock()
    claimed_urls = set(existing_urls)
    for cat in categories.values():
        for item in cat.get("items") or []:
            if isinstance(item.get("id"), int):
                max_id[0] = max(max_id[0], item["id"])

    def _try_add_one(slug, items, cand):
        """深挖并收一条候选；成功返回 True。
        FIX14：service/domain 合集仓库先拆成单条服务再收（合集本身不收，
        拆不出来也不收）；收进来的条目带 _pending_reason 的进待补清单。"""
        if norm_url(cand["url"]) in claimed_urls:
            ABANDONED.append((cand.get("name") or "", cand.get("url") or "",
                              "本轮已在其他栏收录"))
            return False
        if any(name_similar(cand["name"], n) for n in existing_names):
            ABANDONED.append((cand.get("name") or "", cand.get("url") or "",
                              "与已有条目重名"))
            return False
        material, reason = deep_dive(slug, cand)
        if material is None:
            ABANDONED.append((cand.get("name") or "", cand.get("url") or "",
                              reason or "无内容"))
            log(f"放弃候选：{cand.get('name')}（{reason}）")
            return False
        if slug in ("service", "domain") and is_collection_repo(slug, cand, material):
            entries = split_collection_repo(slug, cand, material)
            if len(entries) < 3:
                ABANDONED.append((cand.get("name") or "", cand.get("url") or "",
                                  f"合集仓库拆不出条目（仅 {len(entries)} 条），不收"))
                return False
            bump("split")
            added = 0
            for e in entries:
                if _try_add_one(slug, items, e):
                    added += 1
            return added > 0
        with _id_lock:
            if norm_url(cand["url"]) in claimed_urls:
                ABANDONED.append((cand.get("name") or "", cand.get("url") or "",
                                  "本轮已在其他栏收录"))
                return False
            claimed_urls.add(norm_url(cand["url"]))
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
                return False
        items.append(new_item)
        existing_names.append(new_item["name"])
        ADDED.append((slug, new_item["name"], new_item["url"]))
        _pr = new_item.pop("_pending_reason", None)
        if _pr:
            pending_add(new_item["url"], new_item["name"], slug, _pr)
        bump("deep_ok")
        return True

    def _add_for_slug(slug, cat):
        items = cat.setdefault("items", [])
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
            before = len(items)
            if _try_add_one(slug, items, cand):
                added_here += len(items) - before

    # 两波新增：先收专栏，免费服务最后收——专栏和兜底栏同时抢到同一链接时，
    # 专栏赢；兜底栏只收专栏都不要的。
    _waves = [[kv for kv in categories.items() if kv[0] != "service"],
              [kv for kv in categories.items() if kv[0] == "service"]]
    for _wave in _waves:
        if not _wave:
            continue
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
            list(ex.map(lambda kv: _add_for_slug(kv[0], kv[1]), _wave))

    # 真正执行移除
    for cat in categories.values():
        if cat.get("items"):
            cat["items"] = [it for it in cat["items"] if id(it) not in removed_keys]

    # （安全阀二已在收集阶段按 25% 封顶，这里不再中止；新增永不拦，
    # 搜到多少不重复的新条目就加多少。）
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
    save_state(state_path, state_fails, keep_urls, pending=list(PENDING.values()))

    lines = [f"新增 {len(ADDED)} 条："]
    lines += [f"- [{s}] {n}（{u}）" for s, n, u in ADDED] or ["- 无"]
    lines.append(f"移除 {len(REMOVED)} 条：")
    lines += [f"- [{s}] {n}（{u}）— {r}" for s, n, u, r in REMOVED] or ["- 无"]
    if MOVED:
        lines.append(f"挪组 {len(MOVED)} 条：")
        lines += [f"- [{s}] {n}（{u}）— {r}" for s, n, u, r in MOVED]
    with open(os.path.join(root_dir, "changes.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    # FIX14 #8：今天筛掉了什么（本轮实际删掉的条目），写 screened.json。
    write_screened(root_dir, REMOVED)

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
    log(f"FIX14：地址解析成可用地址 {STATS['resolved']} 条；"
        f"简介重建成真实内容 {STATS['summary_fixed']} 条；"
        f"合集仓库拆分 {STATS['split']} 个；待补清单 {len(PENDING)} 条")
    for name, url, reason in ABANDONED:
        log(f"ABANDONED {name} | {url} | {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

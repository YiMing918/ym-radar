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
     新增不设上限（搜到多少不重复的新条目就加多少），每栏总量上限 500 条。
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

FIX14b（2026-11-24）：内容安全黑名单——错栏/有害条目永不收录。
  - 命中即拦：拉候选时过滤（pull_candidates）、收录时再拦一道
    （_try_add_one，防合集拆出来的条目绕过）；已有条目下轮随既有
    移除流程清掉（走同一批 REMOVED/安全阀/changes.txt/screened.json），
    绝不手改 data.json。
  - 拦两类：①精确 URL（phishdestroy/destroylist 等 7 个已核实地址）；
    ②china-dictatorship 家族的仓库名特征 + 正文指纹句（防改名/借壳复刻，
    同内容已扩散到 5 个书源条目 + 作者主页仓库）。

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
MAX_PER_CAT = 500           # 每栏总量上限（FIX17 二期：300→500，全部栏目统一）
MAX_ADD_PER_CAT = MAX_PER_CAT  # 每栏每次新增不另设上限：搜到多少不重复的新条目就加多少（站长 2026-11-23 定），只受每栏总量上限约束
MAX_CANDIDATES_TRY = MAX_PER_CAT  # 每栏最多深挖多少个候选（与总量上限齐平，实际等于不限）

# FIX17 二期·苹果方案 A：苹果栏分区上限与总量上限（方案 A 稳态约 190–270 条，
# 在 MAX_PER_CAT=500 内，没问题；分区上限是方案里的硬约束，防某分区独占）。
APPLE_TOTAL_CAP = 280
APPLE_SECTION_CAPS = {"deals": 80, "discounts": 80, "testflight": 80,
                      "ipasign": 120}
# FIX17 二期·TestFlight：每轮最多抓多少个 1xtest 应用详情页
# （sitemap 里 lastmod 全是近期的，过滤不住，靠这个预算 + 分区上限兜底）。
TF_DETAIL_BUDGET = 60

# 跨栏去重优先级：标题能看出来是什么的归专栏，免费服务是兜底栏、放最后。
# （站长 2026-11-24 定：AI 免费额度只放 AI 栏，别往免费服务里放。）
DEDUP_PRIORITY = ["apple", "adult", "ai", "app", "live", "video", "music",
                  "book", "domain", "cfip", "node", "proxy", "service"]
_DEDUP_RANK = {s: i for i, s in enumerate(DEDUP_PRIORITY)}
# 抓页面标题时撞上的通用报错标题前缀：这类标题不算真名。
ERROR_TITLE_PREFIXES = ("page not found", "404", "not found", "error",
                        "出错", "无法访问", "找不到")

# FIX14b（2026-11-24）：内容安全黑名单。命中即永不收录，已有条目下轮移除。
#   phishdestroy/destroylist：钓鱼域名黑名单，被 github_search“free domain”
#     误收进免费域名栏第 1 名，简介还写成“免费领一个能用的域名”，完全错位。
#     它本身是正经安全名单，只是不属于本站——按精确 URL 拦，不按名拦。
#   cirosantilli/china-dictatorship 一家：反共政治宣传库，被误收进书源栏
#     当“阅读书源”；同内容已扩散到 5 个书源条目 + 作者本人主页仓库
#     （改名 fork、借壳 fork 都有），所以除精确 URL 外，再按仓库名特征
#     和正文指纹句拦今后的改名/借壳复刻。
#   URL 存小写、无协议头、比对时同样处理：norm_url 不改路径大小写、
#     还保留 http/https 协议头，GitHub 仓库名大小写又不敏感，直接比对
#     会漏掉 PCL2 这类大小写不一致的和 http 变体。
BLOCKED_URLS = {
    "github.com/phishdestroy/destroylist",
    "github.com/cirosantilli/china-dictatorship",
    "github.com/mrfwq7lwnpzjavv5v6eo/cihna-dictattorshrip-8",
    "github.com/cirosantilli/china-dictatroship-7",
    "github.com/zpc1314521/pcl2",
    "github.com/panbinibn/openpacketfix_",
    "github.com/cirosantilli/cirosantilli",
}
# 仓库名里出现这些子串即拦（只拦 china-dictatorship 家族的改名 fork）。
BLOCKED_NAME_SUBSTRINGS = ("china-dictatorship", "china-dictatroship",
                            "cihna-dictattorshrip")
# 正文指纹句：该政治宣传 README 里独有的长句，防借壳复刻（仓库名换了，
# 正文还是同一篇）。30 字以上独有文本，正经条目不可能撞上。
BLOCKED_TEXT_SNIPPETS = ("大陆修宪香港恶法台湾武统朝鲜毁约美中冷战等都是王沪宁愚弄",)

STATS = {"checked_ok": 0, "dead": 0, "content_dead": 0, "github": 0, "counted": 0, "imaged": 0,
         "lang": 0, "region": 0, "cnswitch": 0, "cnswitch_en": 0,
         "deep_ok": 0, "resolved": 0, "pending": 0, "split": 0,
         "summary_fixed": 0, "ai_credit": 0, "shots": 0, "blocked": 0,
         # FIX17：published=发布时间回填数；shots_tried=补图真正发起抓取的
         # 次数（与 shots 命中数一起看命中率，排查补图通道用）。
         "published": 0, "shots_tried": 0}
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
                # FIX17 A1：发布时间（仓库创建时间），与 pushed_at 同一份
                # 接口数据，之前没存；process_item/build_item 用它写
                # publishedAt + 详情「发布时间」，存量靠刷新回填。
                "created_at": (data.get("created_at") or "")[:10],
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


def extract_md_links(raw, repo=""):
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
        try:
            host = urllib.parse.urlparse(url).netloc.lower()
        except ValueError:
            # FIX15：README 里写坏的链接（如 http://[::1）会让 urlparse 抛
            # ValueError，之前直接拖垮整轮；跳过并记日志
            log(f"[links] 跳过坏链接 repo={repo or '?'} url={url!r}")
            continue
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
    return extract_md_links(github_readme_raw(repo, info), repo)


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
    # FIX17 二期：同上，畸形网址的 urlparse 兜底（数不出来返回 None）。
    try:
        path = urllib.parse.urlparse(url).path.lower()
    except Exception:
        return None

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


_FDROID_HAS_CACHE = {}


def fdroid_has_package(pkg):
    """F-Droid 官方 API 查有没有这个包。返回 True/False/None（查不到）。
    FIX17 加深轮：本轮内同包只查一次（确定性答案 True/False 才缓存；
    None 是网络没定论，不缓存，下轮照常重试）。同一应用被多条条目
    以不同网址引用时，不再重复打 F-Droid API。"""
    if pkg in _FDROID_HAS_CACHE:
        return _FDROID_HAS_CACHE[pkg]
    res = None
    try:
        status, _ct, _b = fetch(f"https://f-droid.org/api/v1/packages/{pkg}",
                                max_bytes=64 * 1024)
        if status == 200:
            res = True
        elif status == 404:
            res = False
    except Exception:
        res = None
    if res is not None:
        _FDROID_HAS_CACHE[pkg] = res
    return res


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
        try:
            counted = count_content(url, body)
        except Exception:
            # FIX17 二期：畸形网址会让 urlparse 抛 ValueError（run #16 同类），
            # 这里兜住——数不出来就按“没数出来”走，不拖垮整轮。
            counted = None
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
            try:
                path = urllib.parse.urlparse(url).path.lower()
            except Exception:
                path = ""
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

    # FIX14 A+B：老条目逐步解析可用地址 + 重建模板简介（稳态每轮预算 300 条，
    # 首轮全量刷新不限量，见 main()；只动裸仓库地址的条目；已有 sourceUrl/
    # builtinConfigUrl 的只补缺的，绝不洗掉手写种子带的值；改地址后验活在
    # 下一轮走正常流程）。
    # FIX17 二期：先判地址、再扣预算。旧顺序是先 _resolve_budget_take()
    # 再判裸仓库地址——非仓库条目也会悄悄烧预算（每轮 120 条里相当一部分
    # 被根本不需要解析的条目白白占掉）。全量刷新轮里预算=全部，更要先判。
    if _is_bare_repo_url(url) and _resolve_budget_take():
        _repo2 = github_repo_of(url)
        _info2 = github_info(_repo2)
        try:
            _links2 = readme_links(_repo2, _info2)
        except Exception:
            # FIX15：单个条目的 README 链接解析整体出问题也不拖垮整轮
            _links2 = []
        _mat2 = {"text": github_readme(_repo2, _info2), "github": _info2,
                 "links": _links2,
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
            # FIX17 A1：发布时间回填——只补缺，写过的不动（created_at
            # 是仓库创建时间，不会变）。顶层字段 publishedAt + 详情行。
            # FIX17 加深轮：顶层和详情行分开补——原写法要求顶层为空才
            # 进整段，顶层已有值但详情行缺失（比如中间版本写过一半的
            # 数据）时详情行永远补不上，前端详情页就缺这一行。
            if info.get("created_at"):
                if not (item.get("publishedAt") or ""):
                    item["publishedAt"] = info["created_at"]
                    bump("published")
                if not get_detail(item, "发布时间"):
                    set_detail(item, "发布时间", info["created_at"])
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
    # FIX17 A12：先定包名、再扣预算。旧顺序是先 _shot_budget_take() 再算
    # 包名——app 栏 288 条无图条目里绝大多数是 GitHub 仓库地址，根本推不出
    # F-Droid 包名，60 条预算被并发刷新里先跑到的无资格条目烧光，真能补的
    # F-Droid/Play 条目轮不到，通道长期 0 产出。现在：推不出包名不扣预算；
    # 真发起抓取记 shots_tried，与 shots 一起看命中率。
    if slug == "app" and not (item.get("screenshots") or []):
        _pkg = fdroid_package_of(url) or fdroid_package_of(
            item.get("sourceUrl") or "")
        if not _pkg:
            # FIX17 加深轮：urlparse 对畸形网址（非法 IPv6 等）会抛
            # ValueError——FIX15 那次整轮崩溃就是这个类。FIX16 时这段
            # 藏在预算门后面每轮最多跑 60 次，FIX17 改成先定包名后，
            # 它对每条无图 app 条目都跑，暴露面变大，必须自己兜住：
            # 解析不了就当不是 Play 链接，跳过探测，绝不向上抛。
            try:
                _pu = urllib.parse.urlparse(url)
            except Exception:
                _pu = None
            if _pu is not None and "play.google.com" in _pu.netloc.lower():
                _q = urllib.parse.parse_qs(_pu.query)
                _pp = (_q.get("id") or [None])[0]
                if _pp and fdroid_has_package(_pp) is True:
                    _pkg = _pp
        if _pkg and _shot_budget_take():
            bump("shots_tried")
            _shots = fdroid_screenshots(_pkg)
            if _shots:
                item["screenshots"] = _shots
                bump("shots")

    # 苹果价格死规矩：现价 label 必带；TestFlight 写免费参加；降价组算降幅。
    # FIX17 二期：删「名额状态」标签（方案 A4：苹果从不公布名额数，与
    # 「招募状态/剩余名额/截止加入」重复；旧条目上的这行由迁移函数清理）。
    # iapfree 分区已废除（方案 A：整组淘汰），这里不再补它的字段。
    if slug == "apple":
        if "testflight.apple.com" in url:
            set_detail(item, "现价", "免费参加")
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
    # FIX17 二期第三步：模板简介重写（纯本地字符串操作，不联网、不占预算；
    # 在所有字段更新完之后跑，facts 用的是最新值；改过的简介不再命中模板检测）。
    if _rewrite_template_desc(slug, item):
        bump("desc_rewritten")
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


def _item_key(item):
    """FIX17 二期：removed_keys 的稳定键。

    绝不能用内置 id()——那是 CPython 对象地址。条目从列表里删掉后
    dict 被释放，新建的条目会复用同一个地址，最终移除时把活条目误杀，
    而且每次运行撞的条目数都不一样（离线模拟里苹果栏三次三个数：
    48/50/51）。数字 id 是全站唯一主键（新条目从 max+1 往下发，
    永不撞已删 id）；没有数字 id 的条目（极少数手工数据）用规范化
    网址兜底。"""
    i = item.get("id")
    if isinstance(i, int):
        return i
    return norm_url(item.get("url") or "")

def norm_name(name):
    """名称规范化：小写、去空格标点，只留字母数字和中文。"""
    s = re.sub(r"[^0-9a-z一-鿿]+", "", (name or "").lower())
    return s


def is_blocked(url, name=""):
    """FIX14b：黑名单命中吗？URL 精确比对（不区分大小写）+ 仓库名家族特征。
    改名 fork 换了 URL 但仓库名特征还在，照样拦。"""
    nu = re.sub(r"^https?://", "", norm_url(url or "").lower())
    if nu in BLOCKED_URLS:
        return True
    nn = norm_name(name or "")
    lu = (url or "").lower()
    for sub in BLOCKED_NAME_SUBSTRINGS:
        if norm_name(sub) in nn or sub in lu:
            return True
    return False


def text_blocked(text):
    """FIX14b：正文里有没有黑名单指纹句？有即拦（防借壳复刻：仓库名换了，
    正文还是同一篇政治宣传）。"""
    t = text or ""
    return any(snip in t for snip in BLOCKED_TEXT_SNIPPETS)


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
            # FIX14b：黑名单先拦一道（所有 feed 类型都在这汇合，合集拆分
            # 产生的条目由 _try_add_one 再拦一次）。
            kept = []
            for c in cands:
                if is_blocked(c.get("url") or "", c.get("name") or ""):
                    bump("blocked")
                    ABANDONED.append((c.get("name") or "", c.get("url") or "",
                                      "黑名单（内容安全）"))
                    continue
                kept.append(c)
            cands = kept
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
_RESOLVE_BUDGET_STEADY = 300  # 稳态每轮刷新预算（FIX17 二期：120→300；
                                # 首轮全量刷新除外，见 main() 里的 full_refresh_done 标记）
_RESOLVE_BUDGET = {"left": _RESOLVE_BUDGET_STEADY}  # 每轮 process_item 里最多解析多少条老条目（逐步替换）
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
    # FIX17 二期：畸形网址的 urlparse 兜底（run #16 同类崩溃）。
    try:
        host = urllib.parse.urlparse(url or "").netloc.lower()
    except Exception:
        return False
    return ("apps.apple.com" in host or "f-droid.org" in host
            or "play.google.com" in host or "testflight.apple.com" in host)


def _match_config_link(url, slug):
    """链接本身是不是该栏目的整份配置文件：返回 kind（'file' / 'plugins' /
    'single'）或 None。"""
    low = (url or "").lower()
    # FIX17 二期：畸形网址会让 urlparse 抛 ValueError（run #16 同类崩溃，
    # FIX15 只兜了 extract_md_links，这里是第二处暴露面）——解析不了就
    # 当不是配置文件，绝不向上抛。
    try:
        path = urllib.parse.urlparse(url or "").path.lower()
    except Exception:
        return None
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
        # FIX17 二期：MusicFree 订阅 JSON 不一定叫 plugins.json
        # （如 myPlugins.json），任意 .json 都按订阅文件验。
        if path.endswith(".json"):
            return "plugins"
        if path.endswith(".js"):
            return "single"
    elif slug == "book":
        if path.endswith(".json"):
            return "file"
    elif slug == "live":
        # FIX17 二期：live.txt 是常见的直播源清单（与 .m3u 同类），
        # 验活时另有内容嗅探（见 _verify_one），纯文字页进不来。
        if path.endswith((".m3u", ".m3u8", ".txt")):
            return "file"
    elif slug == "cfip":
        if path.endswith(".txt"):
            return "file"
    elif slug == "proxy":
        # FIX17 二期：proxy 之前没有分支，规则文件会掉到 releases；
        # 分流规则/模块文件按扩展名认。
        if path.endswith((".list", ".conf", ".sgmodule", ".snippet",
                          ".yaml", ".yml")):
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
    # FIX17 二期：同上，畸形网址的 urlparse 兜底（数不出来返回 None）。
    try:
        path = urllib.parse.urlparse(url).path.lower()
    except Exception:
        return None
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
        # FIX17 二期：live 的 .txt 清单必须像个播放列表（有 #EXTINF 或
        # 多个流媒体地址），否则一页纯文字说明也会被 count_content 按
        # “非空行数”误判成活的直播源。
        if slug == "live" and url.lower().endswith(".txt"):
            text = body.decode("utf-8", "replace") if body else ""
            if "#EXTINF" not in text and text.count("://") < 5:
                return False, counted
        return True, counted
    if not body or len(body) < 100:
        return False, None
    return True, None


def _is_labelled(link):
    text = ((link.get("label") or "") + " " + (link.get("context") or "")).lower()
    return any(k in text for k in ("订阅", "下载", "subscribe", "download",
                                   "配置", "全量", "最新", "导入", "install"))


def _looks_like_video_config_endpoint(u, link):
    """FIX17 二期：TVBox 配置地址常无扩展名（裸路径 /tv /api /config /json），
    扩展名认不出来。这类链接只在被“订阅/配置/TVBox/接口”标注时才列入
    候选，靠 _verify_one 的内容数数兜底（内容里没有 sites 列表就验不活，
    不会把普通网页当配置收进来）。"""
    low = (u or "").lower()
    path = urllib.parse.urlparse(u or "").path.lower()
    last = path.rstrip("/").rsplit("/", 1)[-1]
    if "." not in last or path.rstrip("/").endswith(("/tv", "/api", "/config",
                                                     "/json")):
        text = ((link.get("label") or "") + " "
                + (link.get("context") or "")).lower()
        return any(k in text for k in ("订阅", "配置", "tvbox", "影视仓",
                                       "接口", "subscribe", "config", "api"))
    return False


def _repo_raw_form(url):
    """github.com 的 /blob/、/raw/ 网页链接转 raw.githubusercontent 直链；
    不是这种形态返回空串。分支名取路径第一段（分支名带斜杠的极少数仓库
    会转错——验活不过自然回退，不会落盘错地址）。"""
    try:
        p = urllib.parse.urlparse(url or "")
    except Exception:
        return ""
    if p.netloc.lower() != "github.com":
        return ""
    parts = [x for x in p.path.split("/") if x]
    if len(parts) < 5 or parts[2] not in ("blob", "raw"):
        return ""
    rest = "/".join(parts[4:])
    return f"https://raw.githubusercontent.com/{parts[0]}/{parts[1]}/{parts[3]}/{rest}"


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
        if slug in ("cfip", "node"):
            # 第四步：README 里指向仓库内文件的多是 /blob/ 网页链接，它的
            # 路径同样以 .txt/.yaml 结尾会骗过下面的扩展名匹配，但真去验活
            # 抓到的是 HTML 网页、数不出条数必然被拒，白白浪费候选。先把
            # 这类链接统一转成 raw 直链再认。
            raw_u = _repo_raw_form(u)
            if raw_u:
                u = raw_u
        kind = _match_config_link(u, slug)
        if kind:
            cands.append((u, kind, link))
    # FIX17 二期：video 无扩展名配置地址的内容嗅探候选（每条最多 5 个，
    # 只认被标注过的；见 _looks_like_video_config_endpoint）。
    if slug == "video":
        _n_maybe = 0
        for link in links:
            if _n_maybe >= 5:
                break
            u = (link.get("url") or "").strip()
            if not u or norm_url(u) == norm_url(cand_url):
                continue
            if any(norm_url(u) == norm_url(c[0]) for c in cands):
                continue
            if _looks_like_video_config_endpoint(u, link):
                cands.append((u, "file", link))
                _n_maybe += 1
    # 第四步（cfip/node）：README 里挂的项目站点（pages.dev / github.io /
    # workers.dev）也当候选——不少优选/订阅项目把数据文件挂在站点路径下
    # （如 /api/all.json）。是不是真数据由 _verify_one 当场数条数把关，
    # 纯落地页/工具页数不出条数进不来。带查询串的一律不要：那种多半是
    # 别人贴出的个人订阅地址（带 uuid），不能当公开地址用。每条最多 3 个。
    if slug in ("cfip", "node"):
        _n_hosted = 0
        for link in links:
            if _n_hosted >= 3:
                break
            u = (link.get("url") or "").strip()
            if not u or norm_url(u) == norm_url(cand_url):
                continue
            if any(norm_url(u) == norm_url(c[0]) for c in cands):
                continue
            try:
                _pu = urllib.parse.urlparse(u)
            except Exception:
                continue
            if _pu.query:
                continue
            if _pu.netloc.lower().endswith((".pages.dev", ".github.io",
                                            ".workers.dev")):
                cands.append((u, "file", link))
                _n_hosted += 1
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
                    "额度有效期", "是否要绑卡", "领取门槛",
                    # FIX17 二期第三步：7 项 → 11 项（YM 要求加免费模型、
                    # 签到额度、每周额度、每月额度）。ai_kb.json 没有这 4 项的
                    # 结构化列——KB 命中时从条目自己的 credits/notes 原文里
                    # 正则抽（见 fill_ai_credit_fields），不手改知识表、不编数字；
                    # 抽不到填“未公布”。
                    "免费模型", "签到额度", "每周额度", "每月额度"]
_AI_NEW_LABELS = ("免费模型", "签到额度", "每周额度", "每月额度")
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


# FIX17 二期第三步：4 项新字段的抽取（与上面同款纪律：只写原文明确写出的，不猜）。
_AI_MODEL_TOKENS = (
    "Flash", "GPT-4", "GPT-5", "GPT-4o", "GPT-3", "Claude", "Gemini",
    "DeepSeek", "Qwen", "GLM", "Kimi", "Grok", "Llama", "gpt-oss", "Whisper",
    "Mistral", "Mixtral", "Gemma", "Phi", "Command", "Haiku", "Sonnet", "Opus",
    "DeepSeek-R1", "DeepSeek-V3", "Qwen3", "GLM-4", "Kimi-K2", "Doubao",
    "豆包", "通义", "文心", "星火", "混元", "盘古",
)
_AI_QUOTA_UNITS = ("次", "积分", "credits", "credit", "tokens", "token",
                   "请求", "张", "词", "分钟", "额度", "上限", "重置", "条", "个")


def _ai_sentences(text):
    """按句切（中文句号/英文句号/换行），去空白。"""
    out = []
    for s in re.split(r"(?<=[。！？])|(?<=[.!?])\s+|\n", text or ""):
        s = re.sub(r"\s+", " ", s).strip(" \t#*>-`")
        if s:
            out.append(s)
    return out


def _ai_extract_new_fields(text, free_context=False):
    """4 项新字段：免费模型、签到额度、每周额度、每月额度。
    免费模型：含“免费模型”且点名模型/给个数/给速率（带数字）的句子；
    或免费语境下提到模型名的句子。下线/停用/“不含 XX 模型”等反向表述、
    以及“未公布（免费模型不限次数…）”这种只说限额的句子不算。
    KB 文本本身就是免费档口径（free_context=True）时，提到“模型”或
    限速/额度的模型名句子也算。值取整句（按句界截到 120 字）。
    签到额度：含“签到”的句子；原文明确说无签到 → 写“无签到”；
    有数额取整句，无数额取片段 +“（数额未公布）”。没提签到不写——
    不能从“没提”反推“无签到”（与深审建议不同，见报告）。
    每周/每月额度：含“每周/每月”+ 额度单位（或额度/上限/重置/赠送/
    发放）的句子；只提“每周同步/更新/发布”的不算（那是同步频率不是额度）。
    明确说“无每周/每月额度” → 写“无每周/每月额度”。"""
    t = (text or "")
    out = {}
    if not t.strip():
        return out
    sents = _ai_sentences(t)
    # 免费模型：要么点名模型，要么给个数（“7 个定价 0 元的免费模型”），
    # 要么给免费模型的速率（带数字）。只提“免费模型不限次数/未公布”
    # 的句子不算（那是在说限额，不是在说哪些模型免费）；下线/停用、
    # “不含 XX 模型”这类反向表述的句子也跳过。
    for s in sents:
        if "免费模型" in s:
            if s.strip().startswith("未公布"):
                continue
            if re.search(r"下线|停用|不再提供|已关闭|停服", s):
                continue
            if any(tok.lower() in s.lower() for tok in _AI_MODEL_TOKENS) \
                    or re.search(r"\d+\s*个|定价\s*0\s*元", s) \
                    or re.search(r"\d[\d,]*\s*(?:次|请求|tokens?|credits?)", s):
                out["免费模型"] = _truncate_at_sentence(s, 120)
                break
            continue
        low = s.lower()
        if re.search(r"下线|停用|不再提供|已关闭|停服|不含|不包括|不提供|除外|仅限付费", s):
            continue
        if any(tok.lower() in low for tok in _AI_MODEL_TOKENS) and (
                "免费" in s or "模型" in s or free_context
                or ("free" in low and "model" in low)):
            if re.search(r"免费|模型|额度|限速|每天|每周|每月|free|model|limit|quota|daily|weekly|monthly", s, re.I):
                out["免费模型"] = _truncate_at_sentence(s, 120)
                break
    # 签到额度（中文“签到”或英文 sign in / check in / daily login）
    for s in sents:
        if "签到" in s:
            if re.search(r"无签到|没有签到|无每日签到|不签到|无需签到", s):
                out["签到额度"] = "无签到"
                break
            if re.search(r"\d[\d,]*\s*(?:credits?|tokens?|积分|次|张|词|点|金币|coins?|points?)", s):
                out["签到额度"] = _truncate_at_sentence(s, 120)
            else:
                out["签到额度"] = _truncate_at_sentence(s, 60) + "（数额未公布）"
            break
        if re.search(r"\b(sign|check)[- ]in\b|\bdaily\s+login\b", s, re.I):
            if re.search(r"\bno\s+(sign|check)[- ]in\b|\bwithout\s+(sign|check)[- ]in\b", s, re.I):
                out["签到额度"] = "无签到"
                break
            if re.search(r"\d[\d,]*\s*(?:credits?|tokens?|points?|coins?)", s, re.I):
                out["签到额度"] = _truncate_at_sentence(s, 120)
            else:
                out["签到额度"] = _truncate_at_sentence(s, 60) + "（数额未公布）"
            break
    # 每周/每月额度
    for label, word in (("每周额度", "每周"), ("每月额度", "每月")):
        for s in sents:
            if word not in s:
                continue
            if re.search(r"非" + word + r"|不是" + word + r"|不按" + word[0] + r"|非按" + word[0], s):
                continue
            if re.search(r"无" + word + r"额度", s):
                out[label] = "无" + word + "额度"
                break
            if re.search(r"同步|更新|发布|推送", s) and not re.search(
                    r"额度|上限|重置|赠送|发放|免费", s):
                continue
            if any(u in s for u in _AI_QUOTA_UNITS) or re.search(
                    r"额度|上限|重置|赠送|发放", s):
                out[label] = _truncate_at_sentence(s, 120)
                break
    return out


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
    # FIX17 二期第三步：4 项新字段（正则路径，free_context=False；
    # 只返回 4 个新 label，不碰上面 7 项）。
    out.update(_ai_extract_new_fields(t))
    return out


def _ai_credit_sentence(item):
    """简介里写数的句子（规范 §6）：只写 details 里已有的、带数字的三项必填，
    按“首次注册送 / 每天额度 / 额度有效期”顺序串起来。没有带数字的三项
    就返回 ""（简介里不写数）。
    FIX17 二期第三步：4 项新字段也进简介（只写已有的实值）：免费模型写
    模型名（截 60 字），签到/每周/每月额度只写带数字的。"""
    parts = []
    for label, prefix in (("首次注册送", "注册送"), ("每天额度", "每天额度"),
                          ("额度有效期", "有效期")):
        v = get_detail(item, label)
        if v and v != "未公布" and re.search(r"\d", v):
            parts.append(f"{prefix}：{v}")
    v = get_detail(item, "免费模型")
    if v and v != "未公布":
        parts.append(f"免费模型：{v[:60]}")
    for label, prefix in (("签到额度", "签到"), ("每周额度", "每周"),
                          ("每月额度", "每月")):
        v = get_detail(item, label)
        if v and v != "未公布" and re.search(r"\d", v):
            parts.append(f"{prefix}：{v[:60]}")
    if not parts:
        return ""
    return "；".join(parts) + "。"


def fill_ai_credit_fields(item, write_summary=False):
    """FIX14 D：给一条 AI 栏条目填 11 项额度字段（details，二期第三步 7→11）。
    顺序：查 ai_kb.json → 未命中则从 descriptionOriginal 正则抽 → 抽不到
    填“未公布”。已有真实值（非空、非裸“未公布”）只补缺、绝不洗掉——手写
    种子的 11 项原值原样保留。核实状态记 details「额度核实」（待核实/已核实），
    不动 11 项原值。简介里的数只在已核实时写（write_summary=True 时），
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
        # 二期第三步：4 项新字段没有结构化列——从条目自己的 credits/notes
        # 原文里抽（free_context=True：整段就是免费档口径）。抽不到不填，
        # 由下面统一补“未公布”。
        _kb_text = "\n".join(
            [str(v) for v in credits.values()]
            + [str(n) for n in (entry.get("notes") or [])])
        for _lb, _v in _ai_extract_new_fields(_kb_text, free_context=True).items():
            if _lb not in values:
                values[_lb] = _v
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
        # FIX17 A1：发布时间（GitHub 仓库 created_at / 无来源时空字符串）
        "publishedAt": "",
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
        # FIX17 A1：新条目入库就带上发布时间（同 gh 数据，零额外请求）
        if gh.get("created_at"):
            item["publishedAt"] = gh["created_at"]
            set_detail(item, "发布时间", gh["created_at"])
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
        elif not get_detail(item, "现价"):
            set_detail(item, "现价", "未公布")
        if item.get("section") == "discounts":
            if not get_detail(item, "原价"):
                set_detail(item, "原价", "未公布")
    # FIX14 D：AI 新条目补 11 项额度字段（KB 命中 / 正则抽取 / 未公布）；
    # 已核实的数字才进简介，且只写 details 里已有的数。
    if slug == "ai":
        fill_ai_credit_fields(item, write_summary=True)
    if not item["imageUrl"]:
        img = find_image(url)
        if img:
            item["imageUrl"] = img
    # FIX17 二期第三步：新条目也不留模板框（刚拼好的简介若命中模板检测就地重写）。
    _rewrite_template_desc(slug, item)
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


# ---------------- FIX17 二期第三步：description 模板重写 ----------------
# 看板口径：全站 description 模板率 82.9%（拼装句式
# “{名} 是{站} 上的一个{种类}。”+“它主要用来…。适合…”/
# “上面一段是它自己的英文原文介绍…”）。重写只用条目已有的真实材料
# （descriptionOriginal 首段 / 已存简介去框后的正文 / details 事实行），
# 不编造；改后仍须过门禁（≥40 字且含“适合”），否则不动。纯字符串操作，
# 不联网、不占预算、幂等（改过的简介不再命中模板检测）。

_DESC_HEAD_RE = re.compile(r"^[^。]{0,120}?是\s*\S+ 上的一个[^。]*。")
_DESC_TAIL_RES = (
    re.compile(r"它主要用来[^。]*。适合[^。]*。"),
    re.compile(r"上面一段是它自己的英文原文介绍[^。]*。"),
    re.compile(r"页面是英文的，可以用浏览器自动翻译看。"),
    re.compile(r"适合[^。]*。$"),
)
_DESC_EN_NOTE = "（英文介绍，可用浏览器自动翻译）"
_DESC_FACT_SKIP = {"最近检查", "最近更新", "星标数", "语言", "能否改中文",
                   "发布时间", "中文说明", "额度核实", "最近探测存活"}
_DESC_FACT_PLACEHOLDERS = {"", "未公布", "暂无配套", "未知", "无"}


def _is_template_desc(desc):
    """description 是不是模板拼装句式（与看板口径对齐）：
    开头是“是 X 上的一个…”定位句，或含“上面一段是它自己的英文原文介绍”、
    “它主要用来”这两句拼装尾巴。"""
    d = (desc or "").strip()
    if not d:
        return False
    if _DESC_HEAD_RE.search(d):
        return True
    if "上面一段是它自己的英文原文介绍" in d:
        return True
    if "它主要用来" in d:
        return True
    return False


def _real_intro(text, max_len=300):
    """原文介绍里取第一段像正文的内容：跳过标题式短段（<40 字且无句末标点），
    最多拼两段，按句界截到 max_len。找不到返回 ""。"""
    paras = []
    for para in re.split(r"\n\s*\n|\n", text or ""):
        p = re.sub(r"\s+", " ", para).strip(" \t#*>-`")
        if not p:
            continue
        if len(p) < 40 and not re.search(r"[。！？!?]", p):
            continue
        if _BOILERPLATE_RES[0].search(p.lower()):
            continue
        paras.append(p)
        if len(paras) >= 2:
            break
    if not paras:
        return ""
    if len(paras) == 1:
        return _truncate_at_sentence(paras[0], max_len)
    sep = "" if paras[0][-1] in "。！？!?" else " "
    return _truncate_at_sentence(sep.join(paras), max_len)


def _join_sentences(sents):
    """句级拼回：中文句号后直接拼，英文句号/!/? 后补一个空格
    （_split_sentences 会吃掉英文句号后的空格，直接拼会粘成
    libraries.JavaScript）。"""
    out = ""
    for s in sents:
        if out and out[-1] in ".!?":
            out += " "
        out += s
    return out


def _strip_desc_frame(desc):
    """把已存简介的拼装框拆掉：去定位句头、去三句拼装尾、句级去重、
    修掉尾部半句话。返回剩下的正文（可能为空）。"""
    d = (desc or "").strip()
    d = _DESC_HEAD_RE.sub("", d, count=1)
    for rx in _DESC_TAIL_RES:
        d = rx.sub("", d)
    sents = [s for s in _split_sentences(d)]
    seen, kept = set(), []
    for s in sents:
        k = re.sub(r"\s+", "", s)
        if k and k not in seen:
            seen.add(k)
            kept.append(s)
    d = _join_sentences(kept).strip()
    if d and not re.search(r"[。！？!?]$", d):
        idx = max(d.rfind("。"), d.rfind("！"), d.rfind("？"),
                  d.rfind("!"), d.rfind("?"))
        if idx >= 12:
            d = d[:idx + 1]
        else:
            d = ""
    return d


def _facts_sentence(item, core=""):
    """从 details 里拼一句事实（已有真实值的字段，每值 ≤30 字，不与正文重复，
    最多 3 条，总长 ≤120 字）。没事实返回 ""。"""
    parts = []
    total = 0
    for dd in item.get("details") or []:
        if not isinstance(dd, dict):
            continue
        lb = (dd.get("label") or "").strip()
        v = str(dd.get("value") or "").strip()
        if lb in _DESC_FACT_SKIP or v in _DESC_FACT_PLACEHOLDERS:
            continue
        if len(v) > 30 or not v:
            continue
        if core and v in core:
            continue
        frag = f"{lb}：{v}"
        if total + len(frag) > 120:
            continue
        parts.append(frag)
        total += len(frag)
        if len(parts) >= 3:
            break
    if not parts:
        return ""
    return "；".join(parts) + "。"


def _is_mostly_english(text):
    """正文是否以英文为主（ASCII 字母占比超 60% 且长度 ≥20）。"""
    t = (text or "").strip()
    if len(t) < 20:
        return False
    letters = sum(1 for ch in t if ch.isascii() and ch.isalpha())
    return letters / max(len(t), 1) > 0.6


def _rewrite_template_desc(slug, item):
    """重写一条模板简介（只动自动收录、简介命中模板检测的条目）。
    材料优先级：descriptionOriginal 首段 → 已存简介去框正文 → details 事实句；
    拼成“正文 + 适合{人群}。”；英文为主的正文末尾加一句短注（不用被禁的
    “上面一段是它自己的英文原文介绍”写法）。改后仍命中模板检测、
    或 <40 字、或缺“适合”时不动。返回是否改过。"""
    if not item.get("auto"):
        return False
    desc = (item.get("description") or "").strip()
    if not _is_template_desc(desc):
        return False
    kind, use, who = cat_context_for(slug, item.get("section") or "")
    core = _real_intro(item.get("descriptionOriginal") or "")
    if len(core) < 30:
        core = _strip_desc_frame(desc)
    if len(core) < 30:
        return False
    # 句级去重（已存简介常把同一段重复两遍）
    sents = _split_sentences(core)
    seen, kept = set(), []
    for s in sents:
        k = re.sub(r"\s+", "", s)
        if k and k not in seen:
            seen.add(k)
            kept.append(s)
    core = _join_sentences(kept).strip()
    if len(core) < 30:
        return False
    facts = _facts_sentence(item, core) if len(core) < 120 else ""
    composed = (core + facts).strip() if facts else core
    # 正文没以句末标点收尾时补一个句号再接“适合…”（否则英文片段会和“适合”粘在一起）。
    if not re.search(r"[。！？!?\.]$", composed):
        composed += "。"
    new_desc = composed + f"适合{who}。"
    if _is_mostly_english(composed):
        new_desc += _DESC_EN_NOTE
    if len(new_desc) < 40 or "适合" not in new_desc:
        return False
    if _is_template_desc(new_desc):
        return False
    item["description"] = new_desc
    return True


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
    # FIX17 A8：旧公式地板太高（存活 35 + 30 天内更新 30 + 有图 5 = 70 分
    # 地板，星标只 ±2/4/7/10），自动条目被压成 5 个台阶、1907 条挤在
    # 70/72/74；更糟的是尺度倒挂——人工种子没有 GitHub 更新时间，按
    # 「天数未知 +5」封顶在 45 分，系统性低于 0 星自动仓库（70 分）。
    # 改法：存活 35→25 压低地板；星标权重拉开（2/5/9/12）；天数未知时
    # 区分来源——自动条目仍 +5，人工种子 +25 并给 62 分保底，保证活着的
    # 人工种子不低于「0 星、无实测条数」的自动仓库（新公式下最高 60 分：
    # 25+30+0+0+5）。自动条目若有实测条数（节点数/频道数等）仍可凭内容
    # 超过种子，那是有真数据支撑，不算倒挂。
    s = 0
    if alive:
        s += 25
    if days is None:
        s += 5 if item.get("auto") else 25
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
        s += 12
    elif st >= 100:
        s += 9
    elif st >= 10:
        s += 5
    elif st >= 1:
        s += 2
    if (item.get("imageUrl") or "").strip():
        s += 5
    if alive and not item.get("auto"):
        s = max(s, 62)
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


# ================= FIX16：AI 福利四来源（懒猫 AI / AIHot / 赛博鸡蛋 / 白嫖计） =================
# 纯增量、故障隔离：四个来源各是一个「抓列表页 → 解析」的函数，任何异常
# 都记日志并把该来源标成失败（WELFARE_RESULTS[key]["ok"]=False），绝不让
# 整个采集崩（FIX15 教训）。现有流程、门禁、去重、刷新预算一概不动。
#   - 分类：四来源都是 AI 免费额度，一律只进 ai 栏（站长定：标题能看出来
#     是什么的归专栏，AI 免费额度只放 AI 栏）。
#   - 地址：item.url 必须是能直接用的官方/服务链接。AIHot 列表里就是官方
#     直链；其余三家只有详情页链接——只对新条目抓详情页提官方领取链接，
#     每轮总上限 40 个（WELFARE_DETAIL_BUDGET），提不到用详情页本身兜底。
#     sourceUrl 填来源列表页地址。
#   - 描述：只用来源给的真实字段拼（额度/门槛/地区/核实日期），按现有条目
#     文风写，数字不编；拼完按 validate.py 同款门禁自检（auto 条目
#     description >= 40 字且含「适合」、name/url/summary 非空），不过不收。
#   - 去重：官方链接、来源详情页链接（feedUrl）都不许撞已有的 url /
#     sourceUrl / feedUrl，再加现有的名称近似去重；同一链接全站只出现一次。
#   - 过期自动删：条目带 feed / feedUrl / miss 三个标记字段（validate.py
#     只校验必要字段，多余字段不拒）。来源抓成功的那一轮，标记条目不在
#     最新列表里 miss+1，连续 miss>=2 才删（走既有 REMOVED / removed_keys，
#     照样受两个安全阀约束）；抓失败的那一轮绝不计数、绝不删。

WELFARE_LIST_URLS = {
    "lanmao": "https://lanmao.ai/zh/offers.md",
    "aihot": "https://aihot.news/free-tokens",
    "jidan": "https://jidan.si/c/credit",
    "baipiaoji": "https://baipiaoji.com/limits.json",
}
WELFARE_NAMES = {"lanmao": "懒猫 AI", "aihot": "AIHot",
                 "jidan": "赛博鸡蛋", "baipiaoji": "白嫖计"}
WELFARE_RESULTS = {}            # key -> {"ok": bool, "keys": set(norm_url 身份链接)}
WELFARE_DETAIL_BUDGET = {"left": 40}   # 每轮最多抓多少个详情页提官方链接


def _wf_unescape(s):
    return ((s or "").replace("&amp;", "&").replace("&quot;", '"')
            .replace("&#39;", "'").replace("&lt;", "<").replace("&gt;", ">")
            .replace("&nbsp;", " "))


def _wf_clean(s):
    """来源文本清洗：去 markdown 加粗、合并空白。"""
    s = (s or "").replace("**", "")
    return re.sub(r"\s+", " ", s).strip()


def _wf_first_sentences(text, n=2, limit=220):
    """白嫖计 quota 是长文：取前 1-2 句要点，不整段塞。"""
    text = _wf_clean(text)
    if not text:
        return ""
    parts = re.split(r"(?<=[。！？])", text)
    out = ""
    for p in parts:
        if not p:
            continue
        if out and len(out) >= 80:
            break
        out += p
        if out.count("。") + out.count("！") + out.count("？") >= n:
            break
    return _truncate_at_sentence(out, limit) if len(out) > limit else out


# ---------- 四个来源的解析（纯函数：列表页文本 -> 条目 dict 列表） ----------

def parse_lanmao_entries(text):
    """懒猫 AI：Markdown 管道表，列为 名字|免费额度|门槛|地区|核实日期|详情。"""
    out = []
    for line in (text or "").splitlines():
        if "|" not in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 6:
            continue
        detail, verified = parts[-1], parts[-2]
        if not detail.startswith("http"):
            continue
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", verified or ""):
            continue
        name = parts[0]
        if not name or name == "名字":
            continue
        out.append({"feed": "lanmao", "name": name[:80],
                    "quota": _wf_clean("|".join(parts[1:-4])),
                    "threshold": _wf_clean(parts[-4]),
                    "region": _wf_clean(parts[-3]),
                    "verified": verified,
                    "detail_url": detail, "official_url": "", "original": ""})
    return out


def parse_baipiaoji_entries(text):
    """白嫖计：limits.json 的 tools[]（name/category/quota 长文/checked/page）。"""
    data = json.loads(text)
    out = []
    for t in data.get("tools") or []:
        if not isinstance(t, dict):
            continue
        name = (t.get("name") or "").strip()
        page = (t.get("page") or "").strip()
        if not name or not page:
            continue
        quota_full = _wf_clean(t.get("quota") or "")
        # FIX17 加深轮：核实日期必须与懒猫同口径校验成 YYYY-MM-DD 才收。
        # 原先原样透传——回填靠字符串比大小决定 verifiedAt 取谁，源站
        # 哪天把格式改成 2026/11/20 之类，比较结果就会静默出错，还会把
        # 非 ISO 日期写进 data.json（前端 fmtVerified 按 ISO 解析）。
        checked = (t.get("checked") or "").strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", checked):
            checked = ""
        out.append({"feed": "baipiaoji", "name": name[:80],
                    "quota": _wf_first_sentences(quota_full),
                    "threshold": "", "region": "",
                    "verified": checked,
                    "detail_url": page, "official_url": "",
                    "original": quota_full[:1200]})
    return out


def _wf_aihot_date(month, day):
    """AIHot 只写「X月X日」：按当前年份推，落到未来就用上一年。"""
    today = datetime.datetime.now(CST).date()
    for year in (today.year, today.year - 1):
        try:
            d = datetime.date(year, month, day)
        except ValueError:
            return ""
        if d <= today:
            return d.isoformat()
    return ""


def parse_aihot_entries(text):
    """AIHot：free-tokens 页每条是一个 <li>，含标题、额度说明、官方直链
    （「去领」按钮 / 「X月X日核实于官方页面」链接，同一个地址）。"""
    out = []
    chunks = re.split(r'<li class="flex ', text or "")
    for ch in chunks[1:]:
        if "核实于官方页面" not in ch:
            continue
        m = re.search(r'text-ui-body font-semibold text-ink">([^<]+)<', ch)
        name = _wf_unescape(m.group(1)).strip() if m else ""
        mo = re.search(r'href="(https?://[^"]+)"[^>]*>\s*\d+月\d+日核实于官方页面', ch)
        if not mo:
            mo = re.search(r'href="(https?://[^"]+)"[^>]*>去领', ch)
        official = _wf_unescape(mo.group(1)) if mo else ""
        mv = re.search(r"(\d+)月(\d+)日核实", ch)
        verified = _wf_aihot_date(int(mv.group(1)), int(mv.group(2))) if mv else ""
        quota = ""
        mq = re.search(r'<span title="([^"]+)" class="mono ', ch)
        if mq:
            quota = _wf_unescape(mq.group(1))
        if not quota:
            ms = re.search(r'<span title="([^"]+)" class="mt-1 block truncate', ch)
            if ms:
                quota = _wf_unescape(ms.group(1))
        badges = []
        for b in re.findall(r'<span class="whitespace-nowrap">([^<]+)</span>', ch):
            b = _wf_unescape(b).strip()
            if b and "核实" not in b:
                badges.append(b)
        paras = []
        mh = re.search(r'<div hidden="" class="basis-full[^"]*">(.*?)</li>', ch, re.S)
        if mh:
            for p in re.findall(r"<p[^>]*>(.*?)</p>", mh.group(1), re.S):
                t = _wf_clean(re.sub(r"<[^>]+>", "", p))
                if t:
                    paras.append(t)
        if not name or not official:
            continue
        out.append({"feed": "aihot", "name": name[:80],
                    "quota": _wf_clean(quota),
                    "threshold": "、".join(badges), "region": "",
                    "verified": verified,
                    "detail_url": "", "official_url": official,
                    "original": " ".join(paras)[:1200]})
    return out


def parse_jidan_entries(text):
    """赛博鸡蛋：/c/credit 页的 JSON-LD ItemList（name 是优惠标题、url 是
    详情页）；JSON-LD 没了就退到抓 /free/ 链接。鸡蛋没有核实日期，留空。"""
    out = []
    seen = set()

    def _add(name, url):
        if not name or not url or url in seen:
            return
        seen.add(url)
        out.append({"feed": "jidan", "name": name[:80], "quota": name[:80],
                    "threshold": "", "region": "", "verified": "",
                    "detail_url": url, "official_url": "", "original": ""})

    for b in re.findall(r'<script type="application/ld\+json">(.*?)</script>',
                        text or "", re.S):
        try:
            data = json.loads(b)
        except Exception:
            continue
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            continue
        for node in data:
            if not isinstance(node, dict) or node.get("@type") != "ItemList":
                continue
            for el in node.get("itemListElement") or []:
                if isinstance(el, dict):
                    _add((el.get("name") or "").strip(),
                         (el.get("url") or "").strip())
    if not out:
        for href, inner in LINK_RE.findall(text or ""):
            if "/free/" not in href:
                continue
            url = urllib.parse.urljoin("https://jidan.si/c/credit", href)
            _add(_wf_clean(re.sub(r"<[^>]+>", "", inner)), url)
    return out


_WELFARE_PARSERS = {"lanmao": parse_lanmao_entries,
                    "aihot": parse_aihot_entries,
                    "jidan": parse_jidan_entries,
                    "baipiaoji": parse_baipiaoji_entries}


def _welfare_fetch_entries(key):
    """抓一个来源的列表页并解析；成功/失败都记进 WELFARE_RESULTS。
    失败返回 [] 且 ok=False——过期删除那一步只认 ok=True 的结果。"""
    url = WELFARE_LIST_URLS[key]
    try:
        status, _ct, body = fetch(url, max_bytes=4 * 1024 * 1024, deadline=30)
        if status != 200:
            raise RuntimeError(f"列表页返回 {status}")
        entries = _WELFARE_PARSERS[key](body.decode("utf-8", "replace"))
        if not entries:
            raise RuntimeError("解析出 0 条")
        keys = set()
        for e in entries:
            ident = e.get("official_url") if key == "aihot" else e.get("detail_url")
            if ident:
                keys.add(norm_url(ident))
        WELFARE_RESULTS[key] = {"ok": True, "keys": keys}
        log(f"FIX16 来源「{WELFARE_NAMES[key]}」：列表 {len(entries)} 条")
        return entries
    except Exception as e:
        WELFARE_RESULTS[key] = {"ok": False, "keys": set()}
        log(f"FIX16 来源「{WELFARE_NAMES[key]}」抓取失败（跳过；本轮不计数、不删）：{e}")
        return []


def collect_welfare_entries():
    """依次抓四个来源（单源失败只跳过，绝不连累其他来源和整轮采集）。"""
    WELFARE_DETAIL_BUDGET["left"] = 40
    out = []
    for key in ("lanmao", "aihot", "baipiaoji", "jidan"):
        try:
            out.extend(_welfare_fetch_entries(key))
        except Exception as e:  # _welfare_fetch_entries 内部已兜底，这里防万一
            WELFARE_RESULTS[key] = {"ok": False, "keys": set()}
            log(f"FIX16 来源「{WELFARE_NAMES[key]}」异常（跳过）：{e}")
    return out


# ---------- 详情页提官方链接（只有新条目才抓，每轮总上限 40） ----------

_WF_SKIP_HOSTS = ("t.me", "telegram.me", "twitter.com", "x.com",
                  "facebook.com", "weibo.com", "youtube.com", "bilibili.com",
                  "apps.apple.com", "play.google.com")


def _welfare_extract_official(html_text, detail_url):
    """从来源详情页里挑官方领取链接：锚文本带「领取/直达官网」最优先，
    其次「官网/官方」，再退到第一个外站链接；社交/商店/同站链接不算。"""
    host = urllib.parse.urlparse(detail_url).netloc.lower()
    cands = []
    for href, inner in LINK_RE.findall(html_text or ""):
        u = _wf_unescape(href).strip()
        if not u.startswith(("http://", "https://")):
            continue
        uh = urllib.parse.urlparse(u).netloc.lower()
        if not uh or uh == host:
            continue
        if any(uh == s or uh.endswith("." + s) for s in _WF_SKIP_HOSTS):
            continue
        t = _wf_clean(re.sub(r"<[^>]+>", "", inner))
        if "领取" in t or "直达官网" in t or "领福利" in t:
            score = 3
        elif "官网" in t or "官方" in t:
            score = 2
        elif t:
            score = 1
        else:
            score = 0
        cands.append((score, u))
    if not cands:
        return ""
    cands.sort(key=lambda x: -x[0])   # 稳定排序：同分保留页面里的先后
    best = cands[0][1]
    p = urllib.parse.urlparse(best)
    qs = [(k, v) for k, v in urllib.parse.parse_qsl(p.query)
          if k.lower() not in TRACKING_PARAMS]
    return urllib.parse.urlunparse(
        p._replace(query=urllib.parse.urlencode(qs), fragment=""))


def _welfare_official_url(detail_url):
    """抓详情页提官方链接（有界：每轮 40 个、单页 15 秒/512KB、失败隔离）。
    提不到返回 ""，调用方用详情页本身兜底。"""
    if WELFARE_DETAIL_BUDGET["left"] <= 0:
        return ""
    WELFARE_DETAIL_BUDGET["left"] -= 1
    try:
        status, _ct, body = fetch(detail_url, max_bytes=512 * 1024, deadline=15)
        if status != 200:
            return ""
        return _welfare_extract_official(body.decode("utf-8", "replace"),
                                          detail_url)
    except Exception:
        return ""


# ---------- 拼条目（字段集与 build_item 对齐） ----------

def _welfare_description(e):
    """只用来源给的真实字段拼描述：额度/门槛（或使用条件）/地区/核实来源，
    尾巴是 ai 栏现成的适合人群句。数字全来自来源，不编。"""
    name = e["name"]
    quota = (e.get("quota") or "").rstrip("。；; …")
    parts = []
    if quota and quota != name:
        parts.append(f"{name}：{quota}")
    elif quota:
        parts.append(quota)
    else:
        parts.append(name)
    if e.get("threshold"):
        label = "领取门槛" if e["feed"] == "lanmao" else "使用条件"
        parts.append(f"{label}：{e['threshold']}")
    if e.get("region"):
        parts.append(f"适用地区：{e['region']}")
    if e.get("verified"):
        parts.append(f"这条信息由{WELFARE_NAMES[e['feed']]}在 {e['verified']} 核实")
    else:
        parts.append(f"这条信息由{WELFARE_NAMES[e['feed']]}收录")
    parts.append("适合想低成本用上 AI 的人先领来试")
    return "。".join(parts) + "。"


def _welfare_summary(e):
    s = _truncate_at_sentence(e.get("quota") or e["name"], 70)
    return s or e["name"]


def build_welfare_item(e, new_id):
    """把一条四来源条目拼成 ai 栏新条目。过不了门禁（validate.py 同款）
    返回 (None, 原因)。"""
    official = (e.get("official_url") or "").strip()
    if not official and e.get("detail_url"):
        official = _welfare_official_url(e["detail_url"]) or e["detail_url"]
    if not official:
        return None, "没有可用地址"
    desc = _welfare_description(e)
    summary = _welfare_summary(e)
    if len(desc) < 40 or "适合" not in desc:
        return None, "描述过不了门禁（<40 字或缺适合人群）"
    if not summary:
        return None, "简介为空"
    item = {
        "access": "direct", "builtinConfigName": "", "builtinConfigUrl": "",
        "description": desc,
        "descriptionOriginal": (e.get("original") or e.get("quota") or "")[:1200],
        "details": [],
        "id": new_id, "imageUrl": "", "name": e["name"], "rank": 999,
        "region": "", "score": 0, "screenshots": [],
        "section": "main",
        "sourceUrl": WELFARE_LIST_URLS[e["feed"]],
        "summary": summary,
        "tags": ["自动收录", WELFARE_NAMES[e["feed"]]],
        "url": official, "usageKind": "", "usageNote": "",
        "verifiedAt": e.get("verified") or "", "warning": "", "auto": True,
        # FIX17 A1：字段集与 build_item 对齐（四来源不是 GitHub，无发布时间）
        "publishedAt": "",
        "feed": e["feed"],
        "feedUrl": e.get("detail_url") or official,
        "miss": 0,
    }
    if e["feed"] == "lanmao":
        r = e.get("region") or ""
        if "国内" in r:
            item["region"] = "国内"
        elif "海外" in r:
            item["region"] = "海外"
    if e.get("quota"):
        set_detail(item, "免费额度", _truncate_at_sentence(e["quota"], 200))
    if e.get("threshold"):
        set_detail(item, "领取门槛" if e["feed"] == "lanmao" else "使用条件",
                   e["threshold"])
    if e.get("region"):
        set_detail(item, "适用地区", e["region"])
    set_detail(item, "信息来源", WELFARE_NAMES[e["feed"]])
    # 7 项标准额度字段走既有填充（KB 命中 / 正文正则 / 未公布，只补缺不洗已有值）
    fill_ai_credit_fields(item)
    return item, ""


def welfare_expire(categories, removed_keys):
    """FIX16 过期自动删：来源抓成功的那一轮，标记条目（feed 字段）不在该
    来源最新列表里 miss+1，连续 2 轮才移进 REMOVED；还在列表里 miss 清零。
    抓失败的来源整源跳过——不计数、不删。删除与其他移除走同一批
    REMOVED / removed_keys，照样受 main 里两个安全阀约束。"""
    ai_items = (categories.get("ai") or {}).get("items") or []
    dropped = 0
    for item in ai_items:
        key = item.get("feed")
        if key not in WELFARE_RESULTS:
            continue
        res = WELFARE_RESULTS[key]
        if not res.get("ok"):
            continue
        ident = norm_url(item.get("feedUrl") or item.get("url") or "")
        if ident and ident in res["keys"]:
            if item.get("miss"):
                item["miss"] = 0
            continue
        if not ident:
            continue
        item["miss"] = int(item.get("miss") or 0) + 1
        if item["miss"] >= 2 and _item_key(item) not in removed_keys:
            removed_keys.add(_item_key(item))
            REMOVED.append(("ai", item.get("name") or "", item.get("url") or "",
                            f"来源「{WELFARE_NAMES.get(key, key)}」已下架"
                            "（连续 2 轮不在列表里）"))
            dropped += 1
    if dropped:
        log(f"FIX16：四来源过期下架 {dropped} 条（连续 2 轮不在来源列表）")


def _wf_placeholder(v):
    """FIX17 A9：details 值算不算「没填」——空 / 未公布 / 待核实都算。"""
    v = (v or "").strip()
    return (not v) or v == "未公布" or v == "未公布。" \
        or v.startswith("待核实")


def welfare_backfill(item, e):
    """FIX17 A9：四来源条目撞上已有 AI 条目时回填（FIX16 §2 的补法）。
    只填当前为空/未公布/待核实的字段——「免费额度」「领取门槛/使用条件」
    「适用地区」「信息来源」；verifiedAt 取两者较新的一方；
    descriptionOriginal 为空才补。绝不覆盖真实值（人工种子、已核实数字
    原样保留）。补完调 fill_ai_credit_fields 让 7 项标准字段跟着补缺
    （它本身只补缺，安全）。名称近似撞上的不走这里（匹配不精确，
    容易认错人）。全程故障隔离：出任何问题只记日志、返回已填数。"""
    filled = 0
    try:
        if e.get("quota") and _wf_placeholder(get_detail(item, "免费额度")):
            set_detail(item, "免费额度",
                       _truncate_at_sentence(e["quota"], 200))
            filled += 1
        if e.get("threshold"):
            label = "领取门槛" if e["feed"] == "lanmao" else "使用条件"
            if _wf_placeholder(get_detail(item, label)):
                set_detail(item, label, e["threshold"])
                filled += 1
        if e.get("region") and _wf_placeholder(get_detail(item, "适用地区")):
            set_detail(item, "适用地区", e["region"])
            filled += 1
        if _wf_placeholder(get_detail(item, "信息来源")):
            set_detail(item, "信息来源", WELFARE_NAMES[e["feed"]])
            filled += 1
        nv = e.get("verified") or ""
        if nv and nv > (item.get("verifiedAt") or ""):
            item["verifiedAt"] = nv
        if not (item.get("descriptionOriginal") or "").strip():
            orig = e.get("original") or e.get("quota") or ""
            if orig:
                item["descriptionOriginal"] = orig[:1200]
        try:
            fill_ai_credit_fields(item)
        except Exception as exc:
            log(f"FIX17：回填后补额度字段失败（{item.get('name')}）：{exc}")
    except Exception as exc:
        log(f"FIX17：回填失败（{e.get('name')}）：{exc}")
    return filled


def _wf_same_entry(item, e):
    """已有条目就是这条四来源条目本身（同源同详情页）——它不是「撞上」，
    新鲜度由 welfare_expire 管，不回填。"""
    return item.get("feed") == e.get("feed") and bool(e.get("detail_url")) \
        and norm_url(item.get("feedUrl") or "") == norm_url(e["detail_url"])


def welfare_add(categories, entries, existing_urls, existing_names,
                claimed_urls, max_id, removed_keys):
    """FIX16 新增：四来源条目进 ai 栏。规矩与现有新增一致——官方链接 +
    来源详情页链接双去重（撞已有 url/sourceUrl/feedUrl 都算）、名称近似
    去重、黑名单、门禁自检、每栏 500 总量上限的 room（算法同 _add_for_slug）。"""
    if not entries:
        return
    cat = categories.get("ai") or {}
    items = cat.setdefault("items", [])
    removed_here = sum(1 for it in items if _item_key(it) in removed_keys)
    room = MAX_PER_CAT - (len(items) - removed_here)
    if room <= 0:
        # FIX17 A9：满栏只是新条目进不来，撞上已有条目的回填照常做
        # （回填不占名额），不再整轮早退。
        log(f"FIX16：ai 栏已满 {MAX_PER_CAT} 条，四来源新条目本轮不进"
            "（等移除腾位，下轮再进）；撞上已有条目的照常回填")
    seen = set(existing_urls) | set(claimed_urls)
    for c in categories.values():
        for it in c.get("items") or []:
            if it.get("feedUrl"):
                seen.add(norm_url(it["feedUrl"]))
    # FIX17 A9：norm_url -> 已有条目索引（撞链接时定位回填对象；
    # 本轮待删的条目不回填）。
    # FIX17 加深轮：索引只收 ai 栏条目。原写法遍历全部栏目——同一链接
    # 若同时挂在别的栏目（跨栏重复），回填会把 AI 的额度字段和
    # fill_ai_credit_fields 写进不相干栏目的条目里。回填的规矩本来
    # 就只针对 ai 栏（FIX16 §2），索引范围与之一致。
    by_url = {}
    for it in cat.get("items") or []:
        if _item_key(it) in removed_keys:
            continue
        for u in (it.get("url"), it.get("sourceUrl"), it.get("feedUrl")):
            nu = norm_url(u or "")
            if nu and nu not in by_url:
                by_url[nu] = it
    added = 0
    backfilled = 0
    for e in entries:
        # FIX17 加深轮：回填通道同样过黑名单——新增路径有 is_blocked
        # 拦着，回填原先没有；来源条目本身命中黑名单时，它的数据
        # （额度文案/地区/来源标注）不许借回填写进已有条目。
        e_blocked = is_blocked(
            e.get("official_url") or e.get("detail_url") or "",
            e.get("name") or "")
        ident = norm_url(e.get("detail_url") or e.get("official_url") or "")
        if ident and ident in seen:
            # FIX17 A9：链接精确撞上已有条目 → 回填，不新增
            tgt = by_url.get(ident)
            if tgt is not None and not e_blocked \
                    and not _wf_same_entry(tgt, e):
                backfilled += welfare_backfill(tgt, e)
            continue
        if e.get("official_url") and norm_url(e["official_url"]) in seen:
            tgt = by_url.get(norm_url(e["official_url"]))
            if tgt is not None and not e_blocked \
                    and not _wf_same_entry(tgt, e):
                backfilled += welfare_backfill(tgt, e)
            continue
        if added >= room:
            continue
        if is_blocked(e.get("official_url") or e.get("detail_url") or "",
                      e["name"]):
            bump("blocked")
            ABANDONED.append((e["name"], e.get("detail_url") or "",
                              "黑名单（内容安全）"))
            continue
        if any(name_similar(e["name"], n) for n in existing_names):
            ABANDONED.append((e["name"],
                              e.get("detail_url") or e.get("official_url") or "",
                              "与已有条目重名"))
            continue
        max_id[0] += 1
        item, reason = build_welfare_item(e, max_id[0])
        if item is None:
            max_id[0] -= 1
            ABANDONED.append((e["name"], e.get("detail_url") or "", reason))
            continue
        if norm_url(item["url"]) in seen:
            max_id[0] -= 1
            ABANDONED.append((e["name"], item["url"], "官方链接已存在"))
            continue
        items.append(item)
        existing_names.append(item["name"])
        claimed_urls.add(norm_url(item["url"]))
        seen.add(norm_url(item["url"]))
        seen.add(norm_url(item["feedUrl"]))
        ADDED.append(("ai", item["name"], item["url"]))
        bump("welfare")
        added += 1
    if added:
        log(f"FIX16：四来源新增 {added} 条进 ai 栏")
    if backfilled:
        log(f"FIX17：四来源撞上已有条目，回填 {backfilled} 个字段")





# ================= FIX17：分堆清理 / AI 形态标记 =================
# 依据 FIX17 深审报告 §十四（C2 域名分堆、C3 成人清理、C4 AI 工具降级）。
# 全部故障隔离：任一子环节出错只记日志跳过，绝不拖垮整轮；移除走既有
# removed_keys / REMOVED，与其他移除同批、同样受 main 里两个安全阀
# 约束；门禁、预算、安全阀语义一概不动。
#
# 域名判定表（285 条自动条目，来源：深审附件 domain_providers.json +
# domain_auto_desc.txt 逐条按名称+仓库自述复核）：
#   - DOMAIN_PROVIDER_REPOS：40 家真提供商（深审名单），留主区。
#   - DOMAIN_GUIDE_REPOS：139 条教程/清单/工具（含邮件域名清单），
#     降级到新分区 guide 展示，不再冒充提供商。
#   - DOMAIN_DROP_REPOS：106 条与域名注册完全无关的误收录（public
#     domain 版权义误匹配、Domain-Driven Design、钱包工具、许可证
#     全文仓、MDMA 教程等），移出栏目。
#   审计口径是 113 条移出；本表按自述证据复核得 106 条，另约 7 条
#   证据不足的边界条目保守留在 guide 分区，不硬删。
# 成人判定表（来源：深审附件 adult_mapping.json 口径）：57 条 main 里
# 51 个 GitHub 仓库全是 TVBox/IPTV/固件/Home Assistant 插件/有声书源
# 等误收录，3 条报错标题接口一并清掉；只留 3 条真成人站（见
# ADULT_KEEP_LONG_NAMES），按证据（MacCMS 点播 API）归长视频。
# 「国产/中文字幕」深审实测明示仅种子 2 条 / 0 条，没证据不标，本期不碰。

DOMAIN_PROVIDER_REPOS = frozenset((
    "aitji/id.thatako.net", "au-bcube/b-cube-subdomain-lending",
    "creepersbs/register", "digitalplatdev/freedomain",
    "electric-otter/connectblock", "free-domains/is-a.bot",
    "got-a-page/claim", "ilyap358/fluxcast-domains",
    "intentfreedomain/freedomain",
    "intentfreedomain/intentfreedomain.github.io",
    "intuinode/omnexlab-registry", "is-a-software/is-a-software",
    "is-app-top/register", "is-cod-in/register", "is-cool-me/register",
    "jn-aman/is-my.app", "js-org/js.org", "kasefuchs/domains",
    "katorlys/rweb.site", "kevinwhite-387/freedns-my",
    "kumarsumit68439-code/free-domain-hub", "lewisstarkov/ownowl",
    "lgbt-sh/lgbt-sh-v1", "moodyawhvh/freedomain-chinese",
    "mrsyaid/freedomain", "mujtbabbas/freedomain", "nsl-sh/mesh-router-root",
    "open-domains/register", "partofmyid/register", "pokymon/foo.ng",
    "rc-chuah/termi-domain", "rh-devlab/torch-dns",
    "shanmukhasaireddy13/go-live", "sofalegacy/lituk-register",
    "ternilabs/made-in.app", "thedev-me/register", "til-my-id/register",
    "tinybrickboy/register", "w3teal/ill.my.id", "zordhalo/runs-on.dev",))

DOMAIN_GUIDE_REPOS = frozenset((
    "0xdaniellopez/phishunt-feed", "13896167343/cloudflare_temp_email",
    "2439905184/free_domain_list", "27gscn/pcb.pub",
    "4bdulllahh/homingpigeon", "970thunder/hyfreedom",
    "abdullahdevrel/all-email-domains", "abpoyk28/xyz-domain-pricing",
    "adysec/top_1m_domains", "alexandrosmagos/dyn-dns-list",
    "amradawii/delivercheck", "andreee94/cert-manager-webhook-freenom",
    "apifyforge/bulk-email-verifier",
    "bert-janp/open-source-threat-intel-feeds", "bovachen/free-domain-email",
    "broom609/domain-intelligence-api",
    "canutethegreat/security-txt-auditor", "caripay/email-domain-lists",
    "collabray/domain-generator", "d3vniks/free-domain", "daotun6847/daye",
    "darin40/improvmx", "dev-shiki/domain-intel-mcp",
    "devmuhammad05/company-email-validator",
    "dreamdata-io/free-email-providers",
    "dreamhunter2333/cloudflare_temp_email", "dwightmulcahy/noip-bot",
    "einguterwaran/awesome-free-seo-backlinks",
    "email-check-app/free-email-providers", "eminkowski/freedom-list-sync",
    "faidodaisen/sitessaver", "federicodon/growthcab-gtm-skills",
    "fillup/freemail", "find-xposed-magisk/cloudflare_temp_email",
    "free-domain-name/free-domain-browser-cef",
    "free-domain-name/free-domain-name.github.io",
    "free-domain-name/tauri-browser",
    "free-website-domains/free-web-hosting", "funtionalfrost/frostmail",
    "gaozitian/digitalplat-renew", "glitchr-omnisense/omnidns-gandi",
    "glitchr-omnisense/omnidns-ovh", "glitchr-omnisense/omnidns-scaleway",
    "glitchr-omnisense/omnishield-disposable", "gricatan/devhub-domaines",
    "harys722/free-domains", "henricook/free-email-domains-java",
    "httpsok/httpsok", "ichtally/ichtally.github.io",
    "ihmpavel/free-email-domains-list", "instax-dutta/subdomainor",
    "ipythoning/ssl-guide-zh", "jiao77/llmflare",
    "jitao6666/jiwangwebsite-.ime.tech-", "jocab0212/primepidomainlease",
    "jonkbriggs/saa-email-exposure-check", "katamaze/leap",
    "kenryu42/openvmap", "ksravan0770/good-coupon-deals",
    "lancelotrar/best-cf-domains", "langyouge/langyouyuming",
    "lizilong0326/website-deploy-guide", "lukaswhite/email-checker",
    "lukemccrea/freesubdomains", "luri99/ycweb",
    "m-ali-raza-dev/company-puller", "mafulong/mafulong.github.io",
    "mapsim-co/mapsim_scanner_utility", "maritnbk/digitalplat-auto-renew",
    "marsandsea/excelrouter", "mehdreaming/shardmail",
    "microlinkhq/free-email-domains",
    "mikedkmilk/instabox-cloudflare-deploy", "mobaiit/luri-blog",
    "muhammdabdulla251-lab/check", "nas200-timmy/caddy-ddns-nas",
    "neihyud/cloudflare_temp_email", "neshboy/ioclens",
    "nikhgupta/mail_provider", "ntxmarketing/expired-domains",
    "null-object-0000/newbie-cloud", "nurmantg/awesome-free-subdomains",
    "oekhf486/rainyun-domain-ssl", "okhosting/awesome-dns",
    "pathumzcode/hacktoberfest-free-tech-domain-guide",
    "peterdavehello/nrd-list-downloader", "peterdavehello/top-1m-domains",
    "pksorensen/alp-domain-watch", "quantumproxies/whois-api",
    "rafaeelaudibert/bloommx", "raman-kumar-kheti/freemailo",
    "raymortwinkle/digitalplat-auto-renew", "rdwz/freesubdoma.in",
    "rohithzr/email-provider-filter", "rohittiwari-dev/mr-email-checker",
    "sajjadh47/disposable-email-blocker-gravityforms",
    "sajjadh47/disposable-email-blocker-wpforms",
    "salaros/is-biz-mail-dotnet", "salaros/is-biz-mail-js",
    "salaros/is-biz-mail-php", "sebdata-tools/sebdata-tools.github.io",
    "shabby-666/apirouter", "shizuku-x/shizukudomainbox",
    "skygreen2001/report", "smetcz/newly-registered-domains",
    "solcreek/terraform-provider-gandi",
    "solcreek/terraform-provider-kinsta", "srworksllc/free-email-domains",
    "stefanzeljkic/resenje-site-mover", "stymiee/email-validator",
    "sujon-ahmed/free-developer-resources",
    "thomasnordenetr/email-domain-filter",
    "tomba-io/free-email-provider-detector",
    "tompec/public-email-providers-domains", "tooladda-online/dns-lookup",
    "us-group-hub/cloudflare-email-webhook-router",
    "validemailchecker/disposable-email-domains",
    "validemailchecker/free-email-provider-domains",
    "vallic/domain-investors", "vempvhs/tencent-ssl-pricing",
    "vijay399399/semrush-api-for-bulk-traffic-check",
    "vinds476283/custom_domain_blocker", "vmpalcon/email-domain-lists",
    "wanconglab/liauto-lark-push", "waybackpress/wayback-html-export",
    "wdhdev/free-for-life", "web-free-site/web-free-hub",
    "webbuilderai/free.subdomain", "whitebite/domain-hunter",
    "whxhjdp/tencent-ssl-certificate", "wicuw1/hexo-cloud-server-blog",
    "wodzy/disposable-email", "xiongleiyyds/xl", "xransum/freemail-domains",
    "xtianowner/mailhub-kit", "xxx91n/cf-email-api",
    "yeshan-jun/free-bulk-domain-rating-checker", "yethdev/freedns-filters",
    "yxcops/cloudflare-site-deploy",))

DOMAIN_DROP_REPOS = frozenset((
    "abhay0406/capstone-project---customer-churn", "afaroo01/frond",
    "agiledataguides/business-event-matrix", "ajay04323/cpanel_cpsp",
    "aryia-behroziuan/neurons",
    "awais-2005/oracle-instance-creation-automation",
    "beast-clone/goocampus-domain-hub", "bhwilkoff/archive-watch",
    "bmeshary-crypto/abofarwla", "boysgmng/eksvancrawgithublicense",
    "calin-m/bookarium", "camircode/twofree-packages",
    "cashcoin123/cashcoin123",
    "clayjartranslation/clayjartranslation.github.io", "codeboy2012/derlist",
    "correia95/word-unscrambler", "danzuep/usfmbible",
    "dev-shiki/apify-mcp-suite", "dhanya-s-14/course_hub-",
    "dormatthew/windrose-pressure", "drsamonline/corpus-extendum",
    "dulanadias/login-screen", "farhadhsn8/search-engine",
    "florisheedless915/grok-register",
    "frankmalcolmkembery/gnu-general-public-license-version-3-29-june-2007-copy",
    "gadziunet/stackmoor",
    # 注：审计清单里这个仓库名被截断成 ...2020-2，线上实际全名是 ...2020-2027
    # （区块链市场报告，与域名无关，移出）——按全名匹配。
    "gmiresearch1/blockchain-as-a-service-market-size-industry-forecast-report-2020-2027",
    "goldenoakmarketing/pinspector", "guagua-666/s008trainingregistration",
    "impydy/admiral", "issamjwd/ginfowell-bot", "jdegreef/ochorus",
    "joek306090/vps-node-skill", "justakhi/hotspot-monitor",
    "key-solana-finder-private-wallet/key-solana-finder-private-wallet",
    "krsnasgr/awesome-saas-startup-listing",
    "lanrenbao/emby-cloudflare-tunnel-deploy", "lazy-mantou/historysentry",
    "limefinance-org/rustdesk-for-cloudflare-worker", "long060106/incipit",
    "lunnar211/resumelab", "manoinc/mano-mobile",
    "matharrismma/concordance-2",
    "me-wallet-balance-solana-api-tool/wallet-balance-solana-api-tool",
    "mehrdad-shahizad/damfeed", "mezmer90/saas-directories",
    "michalik-maciej/projektownia-calculator", "miniuid/pickads",
    "misty-step/scry", "mjansson/rpmalloc", "mjzd7/freellm-api-with-proxy",
    "mr033/-1123", "myon-bioinformatics/pysaiyanweboo", "n-13thehat/morgana",
    "nate0634034090/bug-free-memory", "neodata-io/neokit",
    "ning-utdgss/gegvl", "nomandhoni-cs/nomandhoni-cs",
    "noykarde/noykarderepository", "oksigeniasl/oga-veve",
    "olk453/wallet-balance-solana-api-tool",
    "one-wallet-balance-solana-api-tool/wallet-balance-solana-api-tool",
    "pasnya/vpn-panel-5x-uev", "payroad/payroad-core",
    "piyushranakoti/personal-website",
    "prosefashion/advantages-of-group-buys", "qwsedrtgh/qw-register-helper",
    "rastaman4e/-1", "rcsafter/v50-f50-carplay-box-mu300",
    "ronelyehouenou-sketch/best-brawl-stars-domain",
    "roni5604/hebrew-words-db", "rramatchandran/big-o-performance-java",
    "rtrentjones/greenlight", "rty753/indonesiapayments",
    "rtynbirf/free-man", "ryanaoh/tabrack",
    "santhoshsivan-dev/strapi-plugin-azure-sso",
    "seeken/selecto-api-console", "select/movies-deluxe",
    "shivam-das-191a/shivam-das-news", "shokki-engineering/freebusy",
    "shubham-safaya/us-consumer-pulse", "solardev-xyz/freedom-browser",
    "space-wallet-balance-solana-api-tool/wallet-balance-solana-api-tool",
    "stand-out-of-my-sun/diogenes-micro-gateway",
    "submitaitools/free-ai-directories", "talktorana/agent-harness",
    "themellowhost/full-roblox-domain", "tracyliving606/regpwn",
    "uraeva-nataqhya/how-to-browse-darknet", "vaixlns/vaixlns",
    "vamsikrishna-04/it-and-non-it---course-resource-sheet",
    "verified-wallet-balance-solana-api-tool/wallet-balance-solana-api-tool",
    "vetrihiori23-debug/internship-board", "wbauer-dev/freezer-manager",
    "wgoerlkt/darkmatter-link-darkmatterurl", "wild0oose43/how-to-buy-mdma",
    "woshichenghaibo/creditcards", "wpmoo-org/core",
    "wxissmel01/buy-us-paypal-account", "xinbetween/flowlight",
    "yeayraww24/public-apis", "yindudegugezhanghao-lgtm/hk-brokers-rules",
    "yukido0205/android-agentic-sdlc", "zszszszsz/.config",
    "zuujiscom/free-game-assets",))

# FIX17 二期：2026-11-25 域名未分类 106 条逐条判定（domain_verdicts.json，逐条核实过）：
# provider +40、guide +21、drop +45。带 repo_key 的进 REPOS 名单；
# 只有直接网址的进 URLS 名单（norm_url 口径，与条目比对时同函数）。
# 注意两个弱锚点：JS.ORG 条目的网址被抓成 https://pages.github.com/（GitHub Pages 落地页），getxyz.de 条目被抓成 https://docs.github.com/en——若以后条目地址被修正为真实官网，这两个 URL 要同步更新。
DOMAIN_PROVIDER_REPOS |= frozenset(( 
    "intentfreedomain/isadev.in",
    "is-a-dev/register",
))
DOMAIN_GUIDE_REPOS |= frozenset(( 
    "4ngel2769/freedns.afraid.org-domain-scraper",
    "abirboss8055/hostscout",
    "cenk/nrd",
    "iougemini/dsmc",
    "jgalea/domcheck",
    "kmodabber/sentry",
    "ljpc-solutions/freescout-whmcs-module",
    "mailchannels/craft-mailchannels",
    "rixlhq/work-email-validator",
    "shizuku-x/dsh-dnshe-domain",
    "tashfeenahmed/agentdomains-mcp",
    "tashfeenahmed/agentdomains-web",
    "whoisfreaks/daily-newly-registered-domains",
    "zinn-digital/zinn-reseller-toolkit",
))
DOMAIN_DROP_REPOS |= frozenset(( 
    "app-othecary/aublix-v2",
    "ashayk003/datasmith",
    "awaism257/the-mantle-library",
    "clto/hongguo-adblock",
    "davidmcdougall/conversation-with-machiavelli",
    "erikpr1994/open-exercise-db",
    "jam0k/ransomware-intel",
    "jcarpe315/divided-house",
    "me-warkari/santkrupa",
    "mkhalid1/claude-certification-study-notes",
    "mkhalid1/comptia-a-plus-study-notes",
    "mkhalid1/comptia-network-plus-study-notes",
    "mkhalid1/comptia-security-plus-study-notes",
    "nanocomp/meep",
    "openie-dev/free-evtol-corpus",
    "openie-dev/free-humanoid-corpus",
    "openie-dev/free-wearable-corpus",
    "openparem/openparem",
    "orthodoxoskomvos/orthodoxoskomvos.github.io",
    "qhquanghuy/shipit",
    "sane-apps/viapatrum-translations",
    "wahah2048-cell/landsat",
    "whx57/paper-radar",
    "yoloboydelis/yoloboydelis.github.io",
))
DOMAIN_PROVIDER_URLS = frozenset(( 
    "https://awesomeprojects.tech/",
    "https://cli.rs/",
    "https://cluster.ws/",
    "https://creepers.sbs/",
    "https://customer.l53.net/",
    "https://dev-in.tw/",
    "https://devs.surf/",
    "https://docs.is-a.dev/",
    "https://domain.digitalplat.org/",
    "https://domain.stackryze.com/",
    "https://dynv6.com/docs/apis",
    "https://env.pm/",
    "https://fluxcast.dev/",
    "https://foo.ng/",
    "https://freedns.afraid.org/",
    "https://freedns.my/index.php",
    "https://freemyip.com/main",
    "https://go-live.me/",
    "https://hapdns.com/",
    "https://has-a.space/",
    "https://help.github.com/pages",
    "https://int.yt/",
    "https://is-a-good.dev/",
    "https://is-a.dev/",
    "https://is-a.wiki/",
    "https://is-an.ai/",
    "https://is-cod.in/",
    "https://is-local.host/",
    "https://isroot.in/",
    "https://iz.rs/registracija_domena",
    "https://katabump.com/en",
    "https://linkora.top/",
    "https://meowed-at.me/",
    "https://moe.page/",
    "https://nic.eu.org/",
    "https://nic.ua/en/knowledge-base/pp-ua-domains-restrictions",
    "https://pages.cloudflare.com/",
    "https://pages.github.com/",
    "https://pntr.dev/",
    "https://pp.ua/eng/index.html",
    "https://publicfreesuffix.org/",
    "https://the-dev.xyz/",
    "https://thedev.id/dns-leak-test",
    "https://vexr.dev/",
    "https://www.dnshe.com/",
    "https://www.duckdns.org/",
    "https://www.dynu.com/en-US",
    "https://www.from-mm.dev/waiting-room",
    "https://www.furry.box/",
    "https://www.gname.com/",
    "https://www.gname.vip/tld-eu-cc.html",
    "https://www.lovie.me/docs",
    "https://yunamo.com/",
))
DOMAIN_GUIDE_URLS = frozenset(( 
    "http://makes.fyi/",
    "https://agentdomains.co/",
    "https://deed.page/",
    "https://docs.github.com/en",
    "https://www.cloudflare.com/5xx-error-landing",
    "https://www.name.com/",
))
DOMAIN_DROP_URLS = frozenset(( 
    "https://archive.org/",
    "https://community.abuse.ch/",
    "https://iths.online/",
    "https://josbeak.github.io/spine",
    "https://mit-license.org/",
    "https://openstax.org/",
    "https://otx.alienvault.com/user/projectargus/pulses",
    "https://threatcluster.io/",
    "https://threatcluster.io/api/iocs/public/crypto.txt",
    "https://threatcluster.io/api/iocs/public/domains.txt",
    "https://threatcluster.io/api/iocs/public/feed.txt",
    "https://threatcluster.io/api/iocs/public/full/crypto.txt",
    "https://threatcluster.io/api/iocs/public/full/domains.txt",
    "https://threatcluster.io/api/iocs/public/full/hashes.txt",
    "https://threatcluster.io/api/iocs/public/full/ips.txt",
    "https://threatcluster.io/api/iocs/public/hashes.txt",
    "https://threatcluster.io/api/iocs/public/ips.txt",
    "https://threatcluster.io/feeds",
    "https://threatcluster.io/formats",
    "https://threatcluster.io/integrations",
    "https://threatcluster.io/iocs",
))

ADULT_DROP_REPOS = frozenset((
    "ballad0324/hoopoetv", "bywilliams/homehub_tvbox", "cc-lab7/tvbox-host",
    "cony8888/tvbox-ipv4-live", "davisweee/vod-hub",
    "day253/tvboxosc-tcl-hwdecode", "evenbsies/tvbox-harvest",
    "fe-spark/ecohub", "gabrick75/tvbox-allwinner-h3-with-linux",
    "gabrick75/tvbox-h3-otimizacao", "gasiepgodoy/hackathon-tv-box-e10",
    "grumpyspud/ha80-armbian-fd628-front-panel", "guke1991/safetvbox",
    "guxia66898/tvbox-hub", "hailehu/hubox", "haogege8888/katelyatv",
    "haonanren118/hd-source", "htwmedia/htv", "jycao2/hongguotv",
    "kittymoona/happytv", "lastsurvivalon/ozone-hd-deb-firmware",
    "leohmoraes/hackathon-tech-floripa-incubitech-tvbox-2-hotspot-captivate",
    "liucz000/tvbox-hub", "ljz704/iptv-hub", "longben314/tvbox-hub",
    "louisykl/tvbox-dolbyvision-hdr10-edition", "marschen1984/katelyatv",
    "matheuspiassic/tvbox-hpc-cluster", "mryang2023/video-hub",
    "muedsa/ha-plugin", "openhwlabcom/hg680r-desktop-pc",
    "shandianchengzi/listen-book-src-hzkjdx-chineseall",
    "slothsheepking/iptv-api-home", "smallmarker/tvbox-spider-hub",
    "smallniangao/tvboxhome", "spencerlishaobing/tvboxhelper",
    "starsoul666/tvbox-harmonyos", "suyizhang/harmony-tvbox-client",
    "sylvan97/tvbox-hub", "tao-2026-beep/iptv-hub",
    "thiagonogit/pihole-tvbox-homelab", "ttu571/mediahub-api",
    "tvbox342078879/probe-hub",
    "web-developer-db/t95-tvbox-to-armbian-home-server", "wj-ui/katelyatv2",
    "wtc1069/hanime1-tvbox-source", "xiangfeng681-lang/h618-tvbox-linux",
    "xuehai888/iptv-platform", "yuppiesnotzhuhao/tvboxharmony",
    "zlu1123/tvbox-hub", "zseagate/iptv-tvbox-highend-android",))

ADULT_DROP_URLS = (
    "https://www.mgav1.cc/api.php/provide/vod/at/xml/",
    "https://apihjzy.com/api.php/provide/vod/at/xml/",
    "http://bttcj.com/inc/sapi.php",
)
ADULT_DROP_NORMURLS = frozenset(norm_url(u) for u in ADULT_DROP_URLS)
# 报错/垃圾标题（norm_name 口径：去标点空格后比对，改名也拦得住一部分）
ADULT_DROP_NORMNAMES = frozenset((
    "attentionrequiredcloudflare", "onlinebrowserbrowsersandboxbrowserling",
    "系统发生错误"))
# 真成人站：深审 §五 点名的 3 条，url 都是 MacCMS 点播 API（长视频源）
ADULT_KEEP_LONG_NAMES = frozenset(("🥈91md", "🥈鲨鱼", "🥇010爱"))
# FIX17 加深轮：保留判定不能只靠名字——刷新时 label_item 会按页面标题
# 改名，名字一变名字匹配就落空。3 条的网址都是各自站点的点播 API、
# 特征稳定，名字或网址任一命中就算保留（网址走 norm_url 同口径比对）。
ADULT_KEEP_LONG_NORMURLS = frozenset(norm_url(u) for u in (
    "https://91md.me/api.php/provide/vod/from/mdm3u8/",
    "https://shayuapi.com/api.php/provide/vod/",
    "http://www.010aizy.com/API/macs.php"))


def _repo_key(item):
    """条目的仓库标识（小写 owner/repo）：先看 url，再看 sourceUrl。"""
    repo = github_repo_of(item.get("url") or "") \
        or github_repo_of(item.get("sourceUrl") or "")
    return repo.lower() if repo else ""


def _fix17_domain_split(categories, removed_keys):
    """域名分堆：提供商留主区、教程/清单/工具归 guide、无关移出。
    名单外的仓库（今后新发现的）一律不动，等下一轮深审再判。
    FIX17 加深轮：逐条故障隔离——单条数据畸形（比如条目不是 dict）
    只跳过该条并计数，不再让整堆分堆陪葬；日志只在本轮真有移出或
    新归分区时打，不再每轮把存量数字重复刷一遍。"""
    cat = categories.get("domain") or {}
    n_main = n_guide = n_drop = n_new_guide = n_fail = 0
    for item in cat.get("items") or []:
        if _item_key(item) in removed_keys:
            continue
        try:
            # FIX17 二期：仓库 + 直接网址双通道判定（106 条里 66 条只有
            # 直接网址、没有 repo_key，单看仓库会漏判；种子 15 条的直接
            # 网址也进了 PROVIDER_URLS，口径统一）。
            repo = _repo_key(item)
            u = norm_url(item.get("url") or "")
            su = norm_url(item.get("sourceUrl") or "")
            if repo in DOMAIN_DROP_REPOS or u in DOMAIN_DROP_URLS \
                    or su in DOMAIN_DROP_URLS:
                removed_keys.add(_item_key(item))
                REMOVED.append(("domain", item.get("name") or "",
                                item.get("url") or "",
                                "与域名注册无关的误收录（FIX17 深审清理）"))
                n_drop += 1
            elif repo in DOMAIN_GUIDE_REPOS or u in DOMAIN_GUIDE_URLS \
                    or su in DOMAIN_GUIDE_URLS:
                if (item.get("section") or "") != "guide":
                    item["section"] = "guide"
                    n_new_guide += 1
                n_guide += 1
            elif repo in DOMAIN_PROVIDER_REPOS or u in DOMAIN_PROVIDER_URLS \
                    or su in DOMAIN_PROVIDER_URLS:
                n_main += 1
        except Exception:
            n_fail += 1
    if n_fail:
        log(f"FIX17：域名分堆有 {n_fail} 条处理失败（已跳过该条，"
            "不影响其余条目）")
    if n_drop or n_new_guide:
        log(f"FIX17：域名分堆——提供商 {n_main} 条留主区，教程/清单/工具 "
            f"{n_guide} 条在 guide 分区（本轮新归 {n_new_guide} 条），"
            f"无关误收录移出 {n_drop} 条")


def _fix17_adult_cleanup(categories, removed_keys):
    """成人清理：误收录（TVBox/IPTV/固件/报错标题）移出；3 条真成人站
    按证据归长视频。种子（long/short/app 分区）不在判定表里，不受影响。
    FIX17 加深轮：保留判定名字/网址双通道（见 ADULT_KEEP_LONG_NORMURLS
    注释）；逐条故障隔离；日志只在本轮真有移出或新归长视频时打。"""
    cat = categories.get("adult") or {}
    n_drop = n_long = n_new_long = n_fail = 0
    for item in cat.get("items") or []:
        if _item_key(item) in removed_keys:
            continue
        try:
            name = (item.get("name") or "").strip()
            if name in ADULT_KEEP_LONG_NAMES \
                    or norm_url(item.get("url") or "") \
                    in ADULT_KEEP_LONG_NORMURLS:
                if (item.get("section") or "") != "long":
                    item["section"] = "long"
                    n_new_long += 1
                n_long += 1
                continue
            repo = _repo_key(item)
            if (repo and repo in ADULT_DROP_REPOS) \
                    or norm_url(item.get("url") or "") in ADULT_DROP_NORMURLS \
                    or norm_name(name) in ADULT_DROP_NORMNAMES:
                removed_keys.add(_item_key(item))
                REMOVED.append(("adult", item.get("name") or "",
                                item.get("url") or "",
                                "成人栏误收录清理（FIX17 深审：非成人内容）"))
                n_drop += 1
        except Exception:
            n_fail += 1
    if n_fail:
        log(f"FIX17：成人清理有 {n_fail} 条处理失败（已跳过该条，"
            "不影响其余条目）")
    if n_drop or n_new_long:
        log(f"FIX17：成人清理——误收录移出 {n_drop} 条，"
            f"真成人站 {n_long} 条归长视频（本轮新归 {n_new_long} 条）")


def fix17_cleanup(categories, removed_keys):
    """FIX17 C2/C3 总入口（main 在黑名单移除后调用）：两个子环节各自
    故障隔离，一个挂了另一个照常。"""
    try:
        _fix17_domain_split(categories, removed_keys)
    except Exception as exc:
        log(f"FIX17：域名分堆失败（跳过）：{exc}")
    try:
        _fix17_adult_cleanup(categories, removed_keys)
    except Exception as exc:
        log(f"FIX17：成人清理失败（跳过）：{exc}")


# ---------------- FIX17 二期第三步：优选IP/节点存量清理 ----------------
# 离线实测结论：仅凭名字+简介分不出“工具仓库”和“列表仓库”（评分呈连续
# 分布、无分界点；details/tags/section 也全是同一套通用字段），所以清理
# 只用确定性规则，不猜：
#  1. <x>.github.io 仓库 → 改写成 https://<x>.github.io/ 站点地址（改地址不删条目；
#     站点才是交付物；owner 与仓库前缀不一致时走项目页 https://<owner>.github.io/<repo>/）。
#  2. 简介是模板句、且仓库名没有任何工具信号的 → 移除。
# 绝不删：手写种子（auto=False）、通道来源（feed）、有实测条数/配置链接的、
# YM 自己的仓库（YiMing918/）、通道上游仓库（BestCF）。
# 每轮幂等（跟苹果迁移同规矩）：改写过的地址不再是裸仓库地址，删过的条目
# 下轮无从再删； feeds 若把同一仓库重新收回来，下轮照样按规则处理。
# 移除走 removed_keys/REMOVED，受 main 里两个安全阀约束。
_CLEANUP_TOOL_NAME_RE = re.compile(
    r"screener|scanner|speedtest|speed[-_ ]?test|optimiz|checker|panel|"
    r"manager|client|tool|dashboard|gui|desktop|luci|openwrt|ddns|sync|"
    r"monitor|tester|fetcher|generator|installer|scraper|crawler|"
    r"collector|chooser|finder|skill|bot|hub|toolkit|pick|selector|app|"
    r"cli|dns|worker|api|flclash|v2rayn|nekobox|hiddify|quantumult|"
    r"stash|loon|egern|surge|verge|nyanpasu|sparkle|sing-box|mihomo|"
    r"shadowrocket", re.I)
_CLEANUP_TEMPLATE_SUM_RE = re.compile(r"（英文）。主要用来|简介待补|英文项目", re.I)
_CLEANUP_COUNTED_LABELS = {"节点数", "条数", "频道数", "书源数", "站点数",
                           "节点数量", "IP 数"}


def _cleanup_has_counted(item):
    """条目有没有数出来的实测内容（节点数/IP 数等 >0）。"""
    for dd in item.get("details") or []:
        if not isinstance(dd, dict):
            continue
        if dd.get("label") in _CLEANUP_COUNTED_LABELS:
            m = re.search(r"\d+", str(dd.get("value") or ""))
            if m and int(m.group()) > 0:
                return True
    return False


def _cleanup_github_io_site(url):
    """<x>.github.io 仓库对应的站点地址；不是这类仓库返回 ""。
    用户站（仓库名 = owner.github.io）→ https://<owner>.github.io/；
    否则走项目页 https://<owner>.github.io/<repo>/。"""
    repo = github_repo_of(url or "") or ""
    if "/" not in repo:
        return ""
    owner, name = repo.split("/", 1)
    if not name.lower().endswith(".github.io"):
        return ""
    if name.lower() == f"{owner.lower()}.github.io":
        return f"https://{owner.lower()}.github.io/"
    return f"https://{owner.lower()}.github.io/{name}/"


def cfip_node_cleanup(categories, removed_keys):
    """FIX17 二期第三步：优选IP/节点存量清理。返回 {"rewritten": n, "removed": n}。"""
    stats = {"rewritten": 0, "removed": 0}
    for slug in ("cfip", "node"):
        cat = categories.get(slug) or {}
        for item in cat.get("items") or []:
            if not item.get("auto"):
                continue
            url = (item.get("url") or "").strip()
            if not _is_bare_repo_url(url):
                continue
            if item.get("feed"):
                continue
            if _cleanup_has_counted(item):
                continue
            if (item.get("builtinConfigUrl") or "").strip() or item.get("clientLinks"):
                continue
            repo = github_repo_of(url) or ""
            if repo.lower().startswith("yiming918/"):
                continue
            if slug == "cfip" and repo.lower() == "dustinwin/bestcf":
                continue
            # 1. github.io 仓库 → 站点地址（改地址不删条目）
            site = _cleanup_github_io_site(url)
            if site:
                if not (item.get("sourceUrl") or "").strip():
                    item["sourceUrl"] = url
                item["url"] = site
                stats["rewritten"] += 1
                bump("cleanup_rewritten")
                continue
            # 2. 模板简介 + 仓库名无工具信号 → 移除
            if _CLEANUP_TEMPLATE_SUM_RE.search(item.get("summary") or "") \
                    and not _CLEANUP_TOOL_NAME_RE.search(
                        repo.split("/")[-1] if "/" in repo else repo):
                if _item_key(item) in removed_keys:
                    continue
                removed_keys.add(_item_key(item))
                REMOVED.append((slug, item.get("name") or "", url,
                                "存量清理：模板简介的裸仓库条目（非工具/非种子/非通道）"))
                stats["removed"] += 1
    return stats


def fix17_candidate_block(slug, cand):
    """FIX17：已清理的误收录别再被发现流程收回来（与 is_blocked 并列）。
    返回拦截原因；不是误收录返回 ""。顺带给域名 guide 仓库的候选定分区
    （候选对象在 build_item 之前改 section 是安全的）。"""
    if slug == "domain":
        repo = github_repo_of(cand.get("url") or "")
        repo = repo.lower() if repo else ""
        u = norm_url(cand.get("url") or "")
        if (repo and repo in DOMAIN_DROP_REPOS) or u in DOMAIN_DROP_URLS:
            return "域名栏已清理的无关误收录（FIX17）"
        if (repo and repo in DOMAIN_GUIDE_REPOS) or u in DOMAIN_GUIDE_URLS:
            cand["section"] = "guide"
    elif slug == "apple":
        # FIX17 二期·苹果方案 A：GitHub 仓库来源只进 ipasign；deals/
        # discounts/testflight 不再接受仓库候选（只收商店页/TestFlight
        # 链接/价格通道发现的新限免）；iapfree/disguised 分区已废除。
        section = cand.get("section") or ""
        if section in ("iapfree", "disguised"):
            return "苹果栏：iapfree/伪装分区已废除"
        if github_repo_of(cand.get("url") or "") and section != "ipasign":
            return "苹果栏：仓库来源只进签名分区"
    elif slug == "adult":
        repo = github_repo_of(cand.get("url") or "")
        repo = repo.lower() if repo else ""
        if repo and repo in ADULT_DROP_REPOS:
            return "成人栏已清理的误收录（FIX17）"
        if norm_url(cand.get("url") or "") in ADULT_DROP_NORMURLS:
            return "成人栏已清理的误收录（FIX17）"
    return ""


def _ai_kind_of(item):
    """FIX17 C4：一条 AI 条目是 platform（平台服务，额度字段适用）还是
    tool（GitHub 工具/项目，11 项额度字段先天不适用）。只认硬证据：
    四来源条目、人工种子、已有真实额度值、非 GitHub 的官网型地址
    → platform；自动条目、本体（url）是 GitHub 仓库、且无任何真实
    额度值 → tool（与深审 §八 按 url 分类的口径一致；url 已解析到
    服务站/项目页的按 platform）。判据带自愈：工具条目日后被 kb/
    回填补上真实额度值，下一轮自动翻成 platform。"""
    if item.get("feed"):
        return "platform"
    if not item.get("auto"):
        return "platform"
    for lb in AI_CREDIT_LABELS:
        v = get_detail(item, lb)
        if v and v != "未公布":
            return "platform"
    repo = github_repo_of(item.get("url") or "")
    if repo:
        return "tool"
    return "platform"


def mark_ai_kinds(categories):
    """FIX17 C4：给 ai 栏每条标 aiKind（tool/platform），展示层据此
    区分呈现：工具类不摆额度表，只显示是什么/干什么。幂等，故障隔离。"""
    try:
        items = (categories.get("ai") or {}).get("items") or []
        tools = plats = changed = 0
        for item in items:
            kind = _ai_kind_of(item)
            if kind and item.get("aiKind") != kind:
                item["aiKind"] = kind
                changed += 1
            if kind == "tool":
                tools += 1
            elif kind == "platform":
                plats += 1
        # FIX17 加深轮：日志只在本轮真有标记变化时打——稳态下每轮全栏
        # 标记都不变，原写法会把同一行数字无限重复进每轮日志。
        if changed:
            log(f"FIX17：AI 条目形态标记——平台 {plats} 条 / 工具 {tools} 条"
                f"（本轮变更 {changed} 条）")
    except Exception as exc:
        log(f"FIX17：AI 形态标记失败（跳过）：{exc}")



# ================= FIX17 二期·苹果方案 A =================
# （2026-11-25，方案见 fix17-audit/apple_a_方案.md）
# 原则：苹果栏只收能直接用的东西——App Store 下载页（限免/降价）、
# TestFlight 招募链接、签名包仓库；仓库主页冒充限免/内购限免的条目
# 全部出局。价格/分类以 iTunes Lookup 为唯一事实来源（美区）；原价
# 没有 Lookup 字段，靠台账 data/apple_prices.json 攒历史价，降价前
# 的价格即原价，台账里没有就留空，绝不编造。

# 迁移淘汰名单：现有 300 条里要删的 118 个仓库（deals 仓库 102 +
# iapfree 11 + testflight 仓库 5），按 2026-11-25 live 数据逐条核过。
APPLE_DROP_REPOS = frozenset({
    '3lgroup/prevelet-app-build',
    'abdurahmanalmehdi/x-path',
    'abhaymettu/appmatch',
    'adamwilfred/how-to-create-an-interactive-ebook',
    'ajayrandhawa/grocery-app-documentation',
    'alexdlaird/fastlane-plugin-bosun',
    'alfredang/freefoodapp',
    'aliujun/asc-uploader',
    'altic-dev/fluidvoice',
    'amadotejada/testflight-beta-watcher',
    'anatanczak/meslistes',
    'andrewpk/ios_appsanddaemons',
    'anuragvd/movies-app',
    'appibara-ltd/appitool',
    'arc-hub/ivyarchackathon',
    'arf4th/testflight-availability-monitor',
    'austinea/app-sdlc',
    'axorax/awesome-free-apps',
    'bayou-brains/tigerdroppings-ios-app',
    'bhuvan-rj/midas',
    'blakecrosley/appstore-connect-mcp',
    'boondharika/quint-coin-community-for-free',
    'builde7b0b/ios-doctrine',
    'bytebrewio/bytebrewiossdk',
    'bytebrewio/bytebrewunitysdk',
    'choiceshack/the-arcana-hack',
    'christian-cunanan-caparra/taskswift-premium-minimalist-daily-planner-to-do-list',
    'compo-cf/cosmica',
    'dancingpea/libertyscan',
    'dash1971/critique-ai-2',
    'dcmoderator/apple-testflight-codes',
    'delonsampaio/liftlogic',
    'dinajdayeh/graduation-project',
    'duceum/appstore-ppp-pricing-agent-skill',
    'ekncarcc/osg',
    'elementalequitycode/organyze-ios',
    'finnkliewer/shoppinglist',
    'flexih/snakelist',
    'fmz200/global-testflight-link',
    'ftzi/cloud-functions-iap',
    'gadominas/vintly',
    'getfoulee/foulee',
    'goneforever13/tor-links',
    'gyde-tv/beta-tester-sync',
    'hangbose/qiling-ios',
    'hiru-ge/asc-mcp',
    'hrshitkunwar-tech/argumentsaver',
    'idoo/app-store-connect-mcp',
    'improbablyisaac/pandora-official-guide',
    'indiagrams/apple-shipkit',
    'isherous/score_predictor',
    'issaghostlife/streamwears',
    'javiemir/paperpals3d',
    'jmarrr/ios-submission-checklist',
    'joed1986/app-updates',
    'joshuakozlo/gameswithgold',
    'katapote/katapote-games-site',
    'kelleyparker/uil_pml_ios',
    'kelvinkosbab/appbootstrapai',
    'khushboosingh12/orderyourfood-swiftuiapp',
    'kind-unes/habitnow',
    'kulaklikkontrol/kulaklikkontrol.github.io',
    'kush-coder/autopilot-app-store-connect-cli-v10',
    'kush-coder/autopilot-app-store-connect-cli-v8',
    'kush-coder/autopilot-app-store-connect-cli-v9',
    'leguagou-debug/longshot',
    'leverdeterre/iappinfos',
    'luminati-io/apple-app-store-scraper',
    'marko-p/spoolkid',
    'masudbro94/python-hacked-mobile-phone-',
    'materadetzky/best-free-ios-apps',
    'mengchuanfu/suspecious-apps-detection',
    'miguelosaurus/swift-sim',
    'namiml/figma-ios-paywall-design',
    'natelee2030/awesome-truly-free-ios',
    'ndabney562/hive-kit',
    'needle-mirror/com.unity.purchasing',
    'nepenth/synara-desktop',
    'ngominhtoan/myandroidtestflight',
    'ngominhtoan/mytestflightserver',
    'nickustinov/itsyconnect-macos',
    'nlpope/swiftnovice',
    'now-network/now-app-privacy',
    'odevio/odevio-cli',
    'opd-ai/grow-companion',
    'orellbuehler/testflight-mcp',
    'ourostack/apple-distribution-kit',
    'pexatasoy/build-pipelines',
    'philberhane/fadelistapp',
    'pierpaologumina/open-source-apps',
    'pjones7-del/slate-app',
    'pluwen/awesome-testflight-link',
    'pofky/asc-mcp',
    'powerbeef/vocello',
    're13orn/ipa-decrypt',
    'repat/ibreak',
    'ronald106/surviv.io',
    'rorkai/app-store-connect-cli',
    'rurom/newnews',
    'rurza/hangar',
    'sdrew85-jpg/cricket-empire',
    'sonia6891/investtogo',
    'souhaibbenfarhat/karhabti-ios',
    'soundet/srb',
    'stefancrigler/easylistapp',
    'steffan005/kairos-bridge-ios',
    'swati1024/torrents',
    'takasusuke/app-policies',
    'teaualune/mobile-dev-skills',
    'tianhaoz95/hejitech-apps',
    'timokoethe/appstoreconnect-mcp',
    'tryzealot/zealot',
    'umutserifler/publish-ios-appstore',
    'vidipt89/shoppinglisthome',
    'vitalygashkov/app-sneak',
    'wilburwing-art/phase-training2',
    'wordisborn/beta-sync',
    'zksac/iblockfilter',
})

# 榜单 RSS（观察名单用：美区+国区，限免/付费各 100；feeds.json 里的
# 苹果 RSS 已拿掉，这里是唯一入口，避免绕开台账直接加条目）。
APPLE_RSS_FEEDS = [
    ("us-free", "https://itunes.apple.com/us/rss/topfreeapplications/limit=100/genre=36/json"),
    ("us-paid", "https://itunes.apple.com/us/rss/toppaidapplications/limit=100/genre=36/json"),
    ("cn-free", "https://itunes.apple.com/cn/rss/topfreeapplications/limit=100/genre=36/json"),
    ("cn-paid", "https://itunes.apple.com/cn/rss/toppaidapplications/limit=100/genre=36/json"),
]

TF_SOURCE_URLS = {
    "pluwen": "https://github.com/pluwen/awesome-testflight-link",
    "1xtest": "https://1xtest.com",
    "releaf": "https://releaf.site/beta",
}


def apple_ledger_path(script_dir):
    """价格台账路径：仓库根目录 data/apple_prices.json（update.yml 里
    已把它加进 diff 检查和 git add，否则台账永不落盘）。"""
    return os.path.join(os.path.dirname(script_dir), "data",
                        "apple_prices.json")


def load_apple_ledger(script_dir):
    """读价格台账：{app_id: {name, last_price, max_price,
    first_free_seen, last_seen, misses}}。读不到/坏了返回空台账。"""
    p = apple_ledger_path(script_dir)
    try:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            return d
    except Exception:
        pass
    return {}


def save_apple_ledger(script_dir, ledger):
    p = apple_ledger_path(script_dir)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(ledger, f, ensure_ascii=False, indent=1, sort_keys=True)
        return True
    except Exception as e:
        log(f"价格台账保存失败：{e}")
        return False


def _ledger_seed_from_item(ledger, app_id, item):
    """冷启动播种：台账里没有这个 id 时，用条目上已有的现价/原价详情行
    播种（9 条 discounts 的真实原价就靠这个保住——台账从现价起算的话，
    永远算不出真原价）。只播种，不洗已有台账。"""
    ent = ledger.get(app_id)
    if ent is None:
        ent = {"name": item.get("name") or "", "last_price": None,
               "max_price": None, "first_free_seen": None,
               "last_seen": today_cst(), "misses": 0}
        ledger[app_id] = ent
    if ent.get("last_price") is None:
        cur = _price_amount(get_detail(item, "现价"))
        if cur is not None:
            ent["last_price"] = cur
    if not ent.get("max_price"):
        orig = _price_amount(get_detail(item, "原价"))
        if orig:
            ent["max_price"] = orig
    return ent


def _ledger_seed_from_rec(ledger, app_id, rec):
    """榜单里首次见到的 id：只记台账，不建条目（没有历史价，叫不出
    限免/降价；下轮起它有了历史价，才有资格进栏）。"""
    price = rec.get("price")
    if price is None:
        price = _price_amount(rec.get("formattedPrice") or "")
    ent = {"name": rec.get("trackName") or "", "last_price": price,
           "max_price": price, "first_free_seen": None,
           "last_seen": today_cst(), "misses": 0}
    ledger[app_id] = ent
    return ent


def itunes_lookup_many(ids, country=None):
    """批量 iTunes lookup（一次最多 200 个 id），结果写进 _itunes_cache
    （与 itunes_lookup 同 key，label_item/build_item 直接命中，不再
    重复请求）。返回 {id_str: rec 或 None}。单批失败不重试、不抛错，
    缺的记 None。"""
    out = {}
    ids = [str(i) for i in dict.fromkeys(ids) if i]
    if not ids:
        return out
    for start in range(0, len(ids), 200):
        chunk = ids[start:start + 200]
        try:
            u = "https://itunes.apple.com/lookup?id=" + ",".join(chunk)
            if country:
                u += f"&country={country}"
            status, _ct, body = fetch(u, max_bytes=2 * 1024 * 1024)
            if status != 200:
                raise RuntimeError(f"lookup HTTP {status}")
            data = json.loads(body.decode("utf-8", "replace"))
            found = {}
            for rec in data.get("results") or []:
                tid = str(rec.get("trackId") or "")
                if tid:
                    found[tid] = rec
            for app_id in chunk:
                rec = found.get(app_id)
                out[app_id] = rec
                with _itunes_lock:
                    _itunes_cache[(app_id, country or "")] = rec
        except Exception as e:
            log(f"iTunes 批量查价失败（{len(chunk)} 个 id）：{e}")
            for app_id in chunk:
                out.setdefault(app_id, None)
                with _itunes_lock:
                    _itunes_cache.setdefault((app_id, country or ""), None)
    return out


def apple_watchlist_rss():
    """榜单 RSS（美区+国区，限免/付费各 100）：返回 app id 集合。
    单源失败只跳过，不抛错。"""
    ids = set()
    for _name, u in APPLE_RSS_FEEDS:
        try:
            status, _ct, body = fetch(u, max_bytes=512 * 1024)
            if status != 200:
                continue
            data = json.loads(body.decode("utf-8", "replace"))
            for entry in (data.get("feed") or {}).get("entry") or []:
                tid = (((entry.get("id") or {}).get("attributes") or {})
                       .get("im:id"))
                if tid:
                    ids.add(str(tid))
        except Exception:
            continue
    return ids


def _apple_store_items(categories):
    items = ((categories.get("apple") or {}).get("items") or [])
    out = []
    for it in items:
        app_id = apple_id_of(it.get("url") or "")
        if app_id:
            out.append((app_id, it))
    return out


def _build_apple_store_item(rec, section, ledger_ent, new_id):
    """用 iTunes 记录拼一个苹果商店条目（走 build_item 复用简介/标签
    逻辑，再补价格通道的字段）。材料不够（无名/无地址/介绍太短）
    返回 None。"""
    name = (rec.get("trackName") or "").strip()
    url = (rec.get("trackViewUrl") or "").strip()
    desc = (rec.get("description") or "").strip()
    if not name or not url or len(desc) < 20:
        return None
    material = {"title": name, "text": desc[:1500],
                "lang": lang_label_from_codes(
                    rec.get("languageCodesISO2A") or []),
                "counted": None, "github": None, "itunes": rec,
                "screenshots": itunes_screenshots(rec)}
    item = build_item("apple", {"name": name, "url": url, "section": section},
                      material, new_id)
    price = rec.get("price")
    if price is None:
        price = _price_amount(rec.get("formattedPrice") or "")
    if price is not None:
        set_detail(item, "现价", "免费" if price == 0 else f"${price:g}")
    if section == "discounts" and ledger_ent and ledger_ent.get("max_price"):
        set_detail(item, "原价", f"${ledger_ent['max_price']:g}")
    genre = rec.get("primaryGenreName") or ""
    set_detail(item, "应用类型", "游戏" if genre == "Games" else "APP")
    rel = (rec.get("releaseDate") or "")[:10]
    if rel:
        item["publishedAt"] = rel
        set_detail(item, "发布时间", rel)
    # FIX17 二期：门禁要求 apple 条目必须有版本标签；Lookup 有就填
    # 真值，没有就填未公布（绝不编）。
    set_detail(item, "最新版本", (rec.get("version") or "未公布").strip()
               or "未公布")
    vrel = (rec.get("currentVersionReleaseDate") or "")[:10]
    set_detail(item, "版本日期", vrel or "未公布")
    if rec.get("averageUserRating") is not None:
        set_detail(item, "评分", str(rec.get("averageUserRating")))
    if rec.get("userRatingCount") is not None:
        set_detail(item, "评分数", str(rec.get("userRatingCount")))
    return item


def apple_price_channel(categories, ledger, removed_keys, next_id):
    """价格通道：批量查价 → 更新现价/原价/分类/版本/评分 → 限免/降价
    分区流转（价格回升出栏、下架 3 次未命中删）→ 榜单观察名单发现
    新限免/新降价。返回 (next_id, stats)。单条异常只跳过该条。
    移除走既有约定：removed_keys 是 _item_key(item) 的集合（数字 id，见 _item_key），原因写进 REMOVED，
    同样受两个安全阀约束。
    安全阀：Lookup 返回数比名单少 30% 以上（疑似接口异常），本轮跳过
    所有删除和分区流转，只更新价格和台账。"""
    stats = {"checked": 0, "moved": 0, "removed": 0, "added": 0,
             "misses": 0, "skipped_valve": False}
    apple = categories.get("apple") or {}
    items = apple.get("items") or []
    retained = _apple_store_items(categories)
    if not retained:
        return next_id, stats
    for app_id, it in retained:
        _ledger_seed_from_item(ledger, app_id, it)
    watch = apple_watchlist_rss()
    watch_ids = {app_id for app_id, _ in retained} | watch
    lookup = itunes_lookup_many(sorted(watch_ids))
    got = sum(1 for v in lookup.values() if v)
    valve = len(watch_ids) > 0 and got < len(watch_ids) * 0.7
    if valve:
        stats["skipped_valve"] = True
        log(f"苹果价格通道：Lookup 只返回 {got}/{len(watch_ids)}，"
            "本轮跳过所有删除和分区流转")
    today = today_cst()
    for app_id, it in retained:
        try:
            rec = lookup.get(app_id)
            ent = ledger.get(app_id) or {}
            if rec is None:
                ent["misses"] = int(ent.get("misses") or 0) + 1
                ent["last_seen"] = today
                stats["misses"] += 1
                if not valve and ent["misses"] >= 3:
                    removed_keys.add(_item_key(it))
                    REMOVED.append(("apple", it.get("name") or "",
                                    it.get("url") or "",
                                    "苹果栏：商店下架（连续 3 轮查不到）"))
                    stats["removed"] += 1
                continue
            ent["misses"] = 0
            ent["last_seen"] = today
            ent["name"] = rec.get("trackName") or ent.get("name") or ""
            price = rec.get("price")
            if price is None:
                price = _price_amount(rec.get("formattedPrice") or "")
            if price is None:
                continue
            price = float(price)
            # FIX17 二期：had_max 必须在更新 max 之前记——原价未公布的
            # 条目本轮 max 会被写成现价，若直接用 price >= max 判，会把
            # 现价根本没变过的 46 条降价全删掉（自证预言）。只有之前就
            # 知道真原价（台账/条目原价详情行），回到原价才算降价结束。
            had_max = ent.get("max_price") is not None
            mx = ent.get("max_price")
            if mx is None or price > mx:
                ent["max_price"] = price
            ent["last_price"] = price
            stats["checked"] += 1
            section = it.get("section") or ""
            set_detail(it, "现价", "免费" if price == 0 else f"${price:g}")
            genre = rec.get("primaryGenreName") or ""
            set_detail(it, "应用类型", "游戏" if genre == "Games" else "APP")
            rel = (rec.get("releaseDate") or "")[:10]
            if rel:
                it["publishedAt"] = rel
                set_detail(it, "发布时间", rel)
            if rec.get("averageUserRating") is not None:
                set_detail(it, "评分", str(rec.get("averageUserRating")))
            if rec.get("userRatingCount") is not None:
                set_detail(it, "评分数", str(rec.get("userRatingCount")))
            if valve:
                continue
            if section == "deals" and price > 0:
                mx = ent.get("max_price") or 0
                if mx and price < mx:
                    it["section"] = "discounts"
                    set_detail(it, "原价", f"${mx:g}")
                    stats["moved"] += 1
                    bump("apple_moved")
                else:
                    removed_keys.add(_item_key(it))
                    REMOVED.append(("apple", it.get("name") or "",
                                    it.get("url") or "",
                                    "苹果栏：限免结束（价格回升）"))
                    stats["removed"] += 1
            elif section == "discounts":
                mx = ent.get("max_price") or 0
                if price == 0:
                    it["section"] = "deals"
                    if not ent.get("first_free_seen"):
                        ent["first_free_seen"] = today
                    stats["moved"] += 1
                    bump("apple_moved")
                elif had_max and price >= mx:
                    removed_keys.add(_item_key(it))
                    REMOVED.append(("apple", it.get("name") or "",
                                    it.get("url") or "",
                                    "苹果栏：降价结束（价格回到原价）"))
                    stats["removed"] += 1
            if price == 0 and not ent.get("first_free_seen"):
                ent["first_free_seen"] = today
        except Exception as e:
            log(f"苹果价格通道跳过一条异常条目 {app_id}：{e}")
            continue
    # 观察名单：榜单里出现、台账里有历史价、当前不在栏里的应用
    if not valve:
        counts = {}
        for i in items:
            s = i.get("section") or ""
            counts[s] = counts.get(s, 0) + 1
        total = len(items)
        retained_ids = {app_id for app_id, _ in retained}
        for app_id in sorted(watch):
            if total >= APPLE_TOTAL_CAP:
                break
            if app_id in retained_ids:
                continue
            ent = ledger.get(app_id)
            rec = lookup.get(app_id)
            if not ent or ent.get("last_price") is None:
                # 首次见到：只记台账，不建条目
                if rec is not None:
                    _ledger_seed_from_rec(ledger, app_id, rec)
                continue
            if rec is None:
                continue
            price = rec.get("price")
            if price is None:
                price = _price_amount(rec.get("formattedPrice") or "")
            if price is None:
                continue
            last = ent.get("last_price")
            if last is None or last <= 0:
                continue
            section = None
            if price == 0:
                section = "deals"
            elif price <= 0.7 * last:
                section = "discounts"
            if section is None:
                continue
            if counts.get(section, 0) >= APPLE_SECTION_CAPS[section]:
                continue
            new_item = _build_apple_store_item(rec, section, ent, next_id)
            if new_item is None:
                continue
            items.append(new_item)
            next_id += 1
            counts[section] = counts.get(section, 0) + 1
            total += 1
            stats["added"] += 1
            bump("apple_added")
    return next_id, stats


# ---------------- TestFlight 三源 ----------------

def parse_pluwen_tf(text):
    """pluwen README 表格：返回 ({join_url: (name, updated)},
    {join_url: status})。只认表行里的 TestFlight 链接；状态 Y=招募中，
    F=满，N=不招，D=已删。"""
    open_map = {}
    status_map = {}
    if not text:
        return open_map, status_map
    for m in re.finditer(
            r"\|\s*([^|\n]+?)\s*\|\s*\[https://testflight\.apple\.com/join/"
            r"([A-Za-z0-9]+)\]\([^)]*\)\s*\|\s*([YFND])\s*\|\s*([0-9-]+)?",
            text):
        name, code, st, updated = m.groups()
        url = f"https://testflight.apple.com/join/{code}"
        status_map[url] = st
        if st == "Y":
            open_map[url] = (name.strip(), (updated or "").strip())
    return open_map, status_map


def parse_1xtest_sitemap(text):
    """1xtest sitemap：返回 /app/<slug> 的 loc 列表（按 sitemap 顺序，
    即 lastmod 新到旧）。"""
    out = []
    if not text:
        return out
    for m in re.finditer(
            r"<loc>(https://1xtest\.com/app/[A-Za-z0-9\-]+)</loc>", text):
        out.append(m.group(1))
    return out


def parse_1xtest_detail(html):
    """1xtest 应用详情页：返回 (name, join_url) 或 None。
    只有页面含 Boarding（正在招募）才算数。"""
    if not html or "Boarding" not in html:
        return None
    m = re.search(r"https://testflight\.apple\.com/join/([A-Za-z0-9]+)", html)
    if not m:
        return None
    title = re.search(r"<title>([^<]+)</title>", html or "")
    name = ""
    if title:
        name = re.sub(r"\s*TestFlight beta.*$", "", title.group(1)).strip()
    return (name, f"https://testflight.apple.com/join/{m.group(1)}")


_MONTHS = {"January": 1, "February": 2, "March": 3, "April": 4, "May": 5,
           "June": 6, "July": 7, "August": 8, "September": 9,
           "October": 10, "November": 11, "December": 12}


def _parse_open_through(text):
    """releaf 详情页里的 "open through February 22, 2027" → 2027-02-22。
    解析不出返回空字符串（绝不编日期）。"""
    m = re.search(r"open through ([A-Z][a-z]+) (\d{1,2}), (\d{4})",
                  text or "")
    if not m:
        return ""
    mon, day, year = m.groups()
    if mon not in _MONTHS:
        return ""
    return f"{int(year):04d}-{_MONTHS[mon]:02d}-{int(day):02d}"


def parse_releaf_beta(html):
    """releaf /beta 页：返回 [(slug, name, join_url)]。
    卡片在文档里按同一顺序出现：应用名（alt="… app icon"）、
    TestFlight 链接、分区 id="beta-<slug>"。按文档顺序两两配对
    （样本核过：9 个名、9 个链接一一对上）；没有卡片的分区（如
    占位的 redline）只有 id、没有名和链接，自然被跳过。
    注意：slug 与 name 是按位置配对的（Freetail 的分区 id 叫 echo，
    名字对不上号，只能按顺序认），调用方抓详情页时必须再核对页面的
    <title> 应用名，名字对不上就不取截止日期，绝不张冠李戴。"""
    if not html:
        return []
    names = re.findall(r'alt="([^"]+?)\s+app icon', html)
    joins = []
    seen = set()
    for code in re.findall(
            r"https://testflight\.apple\.com/join/([A-Za-z0-9]+)", html):
        if code not in seen:
            seen.add(code)
            joins.append(code)
    slugs = re.findall(r'id="beta-([A-Za-z0-9\-]+)"', html)
    out = []
    for i, (nm, code) in enumerate(zip(names, joins)):
        slug = slugs[i] if i < len(slugs) else ""
        out.append((slug, nm.strip(),
                    f"https://testflight.apple.com/join/{code}"))
    return out


def _releaf_detail_deadline(html, expect_name):
    """releaf 详情页：先核对 <title> 里的应用名和期望的名字一致，
    再取 "open through <日期>"。名字对不上返回 ""（绝不张冠李戴）。"""
    if not html:
        return ""
    tm = re.search(r"<title>([^<]+?)\s*\|\s*ReLeaf</title>", html)
    page_name = tm.group(1).strip() if tm else ""
    if not page_name or page_name.lower() != (expect_name or "").lower():
        return ""
    return _parse_open_through(html)


def _build_apple_tf_item(name, join_url, source, deadline, new_id):
    """拼一个 TestFlight 招募条目。name/join_url 必须真实；deadline 为空
    就不写这一行，绝不编。名额数苹果从不公布：新条目只写“仅显示开放中”，
    旧条目上已有的实测数（如 97 / 100）绝不洗成未公布。"""
    item = {
        "access": "direct", "builtinConfigName": "", "builtinConfigUrl": "",
        "description": "", "descriptionOriginal": "", "details": [],
        "id": new_id, "imageUrl": "", "name": (name or "TestFlight 测试")[:80],
        "rank": 999, "region": "", "score": 0, "screenshots": [],
        "section": "testflight", "sourceUrl": "", "summary": "",
        "tags": ["自动收录"], "url": join_url, "usageKind": "",
        "usageNote": "", "verifiedAt": today_cst(), "warning": "",
        "auto": True, "publishedAt": "",
    }
    nm = item["name"]
    item["description"] = (f"{nm} 正在 TestFlight 上招募测试者，免费参加，"
                           "适合想提前试用新应用的人。加入方式：点开上面的 "
                           "TestFlight 链接，点接受就能装测试版。")
    item["summary"] = f"{nm} 正在 TestFlight 招募测试者，免费参加。"
    set_detail(item, "现价", "免费参加")
    set_detail(item, "招募状态", "开放中")
    set_detail(item, "剩余名额", "仅显示开放中")
    # FIX17 二期：TestFlight 加入链接里没有应用 id，版本号查不到——
    # 门禁要求 apple 条目必须有这两个标签，查不到就填未公布（绝不编）。
    set_detail(item, "最新版本", "未公布")
    set_detail(item, "版本日期", "未公布")
    if deadline:
        set_detail(item, "截止加入", deadline)
    set_detail(item, "信息来源", source)
    return item


def apple_tf_channel(categories, removed_keys, next_id):
    """TestFlight 三源：pluwen（主，Y 才收）+ 1xtest（补充，详情页确认
    Boarding 才收）+ releaf（量小，给截止日期）。返回 (next_id, stats)。
    单源失败只跳过该源；保留条目连续 2 轮不在任何源里才删。"""
    stats = {"added": 0, "removed": 0, "updated": 0}
    apple = categories.get("apple") or {}
    items = apple.get("items") or []
    retained = [it for it in items
                if (it.get("section") or "") == "testflight"
                and "testflight.apple.com" in (it.get("url") or "")]
    # --- 拉三源（各自 try/except，单源失败不拖垮别的源）---
    pluwen_open, pluwen_status = {}, {}
    try:
        status, _ct, body = fetch(
            "https://raw.githubusercontent.com/pluwen/awesome-testflight-link/main/README.md",
            max_bytes=2 * 1024 * 1024)
        if status != 200:
            raise RuntimeError(f"HTTP {status}")
        pluwen_open, pluwen_status = parse_pluwen_tf(
            body.decode("utf-8", "replace"))
    except Exception as e:
        log(f"TestFlight 来源 pluwen 拉取失败：{e}")
        try:
            status, _ct, body = fetch(
                "https://raw.githubusercontent.com/pluwen/awesome-testflight-link/master/README.md",
                max_bytes=2 * 1024 * 1024)
            if status == 200:
                pluwen_open, pluwen_status = parse_pluwen_tf(
                    body.decode("utf-8", "replace"))
        except Exception:
            pass
    xtest_open = {}
    try:
        status, _ct, body = fetch("https://1xtest.com/sitemap.xml",
                                  max_bytes=4 * 1024 * 1024)
        if status == 200:
            slugs = parse_1xtest_sitemap(body.decode("utf-8", "replace"))
            room = APPLE_SECTION_CAPS["testflight"] - len(retained)
            budget = min(TF_DETAIL_BUDGET, max(0, room * 2)) if room > 0 else 0
            for loc in slugs[:budget]:
                try:
                    st2, _ct2, b2 = fetch(loc, max_bytes=512 * 1024)
                    if st2 != 200:
                        continue
                    parsed = parse_1xtest_detail(
                        b2.decode("utf-8", "replace"))
                    if parsed:
                        nm, ju = parsed
                        xtest_open[ju] = nm
                except Exception:
                    continue
    except Exception as e:
        log(f"TestFlight 来源 1xtest 拉取失败：{e}")
    releaf_open = {}
    try:
        status, _ct, body = fetch("https://releaf.site/beta",
                                  max_bytes=512 * 1024)
        if status == 200:
            html = body.decode("utf-8", "replace")
            for slug, nm, ju in parse_releaf_beta(html):
                deadline = ""
                if slug:
                    try:
                        st3, _ct3, b3 = fetch(f"https://releaf.site/{slug}",
                                              max_bytes=512 * 1024)
                        if st3 == 200:
                            deadline = _releaf_detail_deadline(
                                b3.decode("utf-8", "replace"), nm)
                    except Exception:
                        pass
                releaf_open[ju] = (nm, deadline)
    except Exception as e:
        log(f"TestFlight 来源 releaf 拉取失败：{e}")
    # --- 更新保留条目 ---
    for it in retained:
        try:
            ju = (it.get("url") or "").strip()
            miss = int(it.get("tfMiss") or 0)
            seen = False
            if ju in pluwen_open:
                seen = True
                set_detail(it, "招募状态", "开放中")
            elif ju in pluwen_status:
                st = pluwen_status[ju]
                set_detail(it, "招募状态",
                           {"F": "已满", "N": "停招",
                            "D": "已下架"}.get(st, "未公布"))
            if ju in xtest_open:
                seen = True
                set_detail(it, "招募状态", "开放中")
            if ju in releaf_open:
                seen = True
                _nm, deadline = releaf_open[ju]
                set_detail(it, "招募状态", "开放中")
                if deadline:
                    set_detail(it, "截止加入", deadline)
            if seen:
                if miss:
                    it["tfMiss"] = 0
                stats["updated"] += 1
            else:
                miss += 1
                it["tfMiss"] = miss
                if miss >= 2:
                    removed_keys.add(_item_key(it))
                    REMOVED.append(("apple", it.get("name") or "",
                                    it.get("url") or "",
                                    "苹果栏：TestFlight 招募结束"
                                    "（连续 2 轮不在来源里）"))
                    stats["removed"] += 1
        except Exception as e:
            log(f"TestFlight 更新跳过一条异常条目：{e}")
            continue
    # --- 加新条目（releaf 优先：带截止日期；其次 pluwen；最后 1xtest）---
    have = {((it.get("url") or "").strip()) for it in items}
    cands = {}
    for ju, nm in xtest_open.items():
        cands[ju] = {"name": nm, "source": "1xtest", "deadline": ""}
    for ju, (nm, _upd) in pluwen_open.items():
        cands[ju] = {"name": nm, "source": "pluwen", "deadline": ""}
    for ju, (nm, deadline) in releaf_open.items():
        cands[ju] = {"name": nm, "source": "releaf", "deadline": deadline}
    tf_count = len(retained) - stats["removed"]
    total = len(items) - stats["removed"]
    for ju in sorted(cands):
        if tf_count >= APPLE_SECTION_CAPS["testflight"]:
            break
        if total >= APPLE_TOTAL_CAP:
            break
        if ju in have:
            continue
        c = cands[ju]
        if not c["name"]:
            continue
        new_item = _build_apple_tf_item(c["name"], ju, c["source"],
                                        c["deadline"], next_id)
        new_item["sourceUrl"] = TF_SOURCE_URLS[c["source"]]
        items.append(new_item)
        next_id += 1
        tf_count += 1
        total += 1
        stats["added"] += 1
        bump("apple_tf_added")
    return next_id, stats


def apple_migrate(categories, removed_keys):
    """方案 A 迁移：现有 300 条按 keep 182 / drop 118 落地。
    删：iapfree 整组、disguised 分区、APPLE_DROP_REPOS 里的仓库条目；
    留：deals 商店 38、discounts 55、testflight 真链接 7、ipasign 82；
    所有留下的条目删掉重复的「名额状态」详情行。逐条 try/except，
    出错的条目保留不删（下轮再说），绝不因迁移丢数据。返回淘汰条数。"""
    apple = categories.get("apple") or {}
    items = apple.get("items") or []
    kept = []
    dropped = 0
    for it in items:
        try:
            section = it.get("section") or ""
            if section in ("iapfree", "disguised"):
                removed_keys.add(_item_key(it))
                REMOVED.append(("apple", it.get("name") or "",
                                it.get("url") or "",
                                "苹果栏清理：iapfree/伪装分区废除"))
                dropped += 1
                bump("apple_migrated_drop")
                continue
            repo = _repo_key(it)
            if repo and repo in APPLE_DROP_REPOS:
                removed_keys.add(_item_key(it))
                REMOVED.append(("apple", it.get("name") or "",
                                it.get("url") or "",
                                "苹果栏清理：仓库冒充限免/内购限免/TestFlight"))
                dropped += 1
                bump("apple_migrated_drop")
                continue
            it["details"] = [d for d in (it.get("details") or [])
                             if d.get("label") != "名额状态"]
            kept.append(it)
        except Exception as e:
            log(f"苹果迁移跳过一条异常条目：{e}")
            kept.append(it)
    apple["items"] = kept
    if dropped:
        log(f"苹果迁移：淘汰 {dropped} 条，保留 {len(kept)} 条")
    return dropped



# ================= FIX17 二期第二步：优选IP/节点换源 + 免费VPN 分区 + 事实门 =================
# 依据：fix17-audit/sourcemap-c.md（优选IP/节点来源）、vpn_报告.md +
# vpn_candidates.json（免费 VPN 34 候选）、optimization-2026-11-25/报告.md
# 第 2 条（入库事实门）。设计与既有通道（苹果 TF / FIX16 福利）同构：
# 解析器是纯函数、抓取在通道里逐源故障隔离、条目靠 feed 标记认领、
# 连续 2 轮不在来源里才删（抓失败不计数）。

# ---------------- 第二步·事实门（对标优化第 2 条） ----------------
# 规矩：新自动条目同时满足两条才拦——① summary 命中模板特征；
# ② details 里没有任何真实事实字段（额度/价格/条数/条件类，值不是
# 「未公布」这类占位）。拦下的不丢：进待补清单（pending）等复核，
# 不直接上架。种子/人工条目（auto 非真）不走这道门；福利/苹果通道
# 有各自的门禁自检，不在这里重复拦。
# 2026-11-25 用线上 3638 条实测过口径：模板特征条目 181 条里，
# 无事实的 113 条会被拦，带真实条数/价格/额度的 0 条误杀。

FACT_LABELS = {
    # 数出来的条数（内容实测）
    "节点数", "条数", "频道数", "书源数", "站点数", "节点数量", "IP 数",
    # 价格
    "现价", "原价",
    # AI 额度 11 项 + 福利字段（二期第三步：7→11）
    "首次注册送", "每天额度", "其他途径", "总额度", "额度有效期",
    "是否要绑卡", "领取门槛", "免费模型", "签到额度", "每周额度", "每月额度",
    "免费额度", "领取条件", "使用条件",
    # 免费服务/域名四问
    "免费时长", "能否续费", "续费价格", "能否进 CF", "机器配置",
    "申请要求", "能干什么", "容量与发信量", "额度",
    # 节点/直播等栏目的内容字段
    "协议类型", "地区分布", "招募状态", "截止加入",
}
_FACT_PLACEHOLDERS = {"", "未公布", "暂无配套", "未知", "无"}


def _summary_template_like(summary):
    """summary 是不是模板/兜底句式：FIX14 的 12 句英文模板、build_item
    的英文兜底（简介待补）、中文兜底（{种类}。主要用来{用途}，适合…）。"""
    s = (summary or "").strip()
    if not s:
        return False
    if _is_template_summary(s):
        return True
    if "简介待补" in s:
        return True
    if re.match(r"^.+。主要用来", s):
        return True
    return False


def _fact_labels_of(item):
    """条目身上带真实值的事实字段 label 列表（占位值不算）。"""
    out = []
    for dd in item.get("details") or []:
        if not isinstance(dd, dict):
            continue
        lb = dd.get("label") or ""
        v = str(dd.get("value") or "").strip()
        if lb in FACT_LABELS and v not in _FACT_PLACEHOLDERS:
            out.append(lb)
    return out


def fact_gate_reason(item):
    """命中事实门返回原因字符串，不命中返回 ""。"""
    if not item.get("auto"):
        return ""
    if not _summary_template_like(item.get("summary") or ""):
        return ""
    if _fact_labels_of(item):
        return ""
    return ("事实门：摘要是模板句式，且没有任何真实事实字段"
            "（额度/价格/条数/条件），先进待复核不上架")


# ---------------- 第二步·优选 IP 换源（CFYes / BestCF / WeTest） ----------------
CFYES_API_URL = "https://addressesapi.090227.xyz/CloudFlareYes"
BESTCF_HOME_URL = "https://bestcf.pages.dev/"
WETEST_V4_URL = "https://www.wetest.vip/page/cloudflare/address_v4.html"
_CFIP_BESTCF_MAX = 6          # BestCF 镜像清单每轮最多收几份
_NET_CN = {"CM": "移动", "CU": "联通", "CT": "电信"}


def parse_cfyes(text):
    """CFYes 纯文本接口解析：一行一个 `IP#CM-Default`（分网备注）。
    返回 {"ips": [去重保序], "by_net": {"CM": [ip...], ...}}。"""
    ips, seen, by_net = [], set(), {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or "#" not in line:
            continue
        ip, _, note = line.partition("#")
        ip, note = ip.strip(), note.strip()
        if not re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", ip):
            continue
        if ip not in seen:
            seen.add(ip)
            ips.append(ip)
        m = re.match(r"(CM|CU|CT)\b", note)
        if m:
            by_net.setdefault(m.group(1), []).append(ip)
    return {"ips": ips, "by_net": by_net}


def parse_bestcf_txt(text):
    """BestCF 镜像 txt 清单解析：一行一个 `地址:端口#备注`。"""
    rows = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or "#" not in line:
            continue
        addr, _, note = line.partition("#")
        addr, note = addr.strip(), note.strip()
        host, _, port = addr.rpartition(":")
        if not host:
            continue
        rows.append({"addr": addr, "host": host, "port": port,
                     "note": note})
    return rows


def bestcf_txt_links(html, base=BESTCF_HOME_URL):
    """从 BestCF 导航页提取优选源 txt 直链（相对地址转绝对）：
    只认文件名/路径带源标识（vps789/cfyes/wetest/uouin/090227）的
    .txt，去重，最多 _CFIP_BESTCF_MAX 份。

    实测教训（2026-11-25 首页全文样本 385KB）：vps789 的清单链接
    只出现在页面 JS 字符串里（fetch('vps789/top10.txt')），不在
    href 里；href 里带源标识的 .txt 一个都没有——只扫 href 会
    漏光。所以扫全文 URL（href + 裸文本），按子源优先级排序：
    vps789 自家清单在前，其次 cfyes/wetest/uouin/090227。"""
    raw = set()
    for m in re.finditer(r"(?:https?://[^\s\"'<>]+|[\w][\w./-]*)\.txt",
                         html or ""):
        raw.add(urllib.parse.urljoin(base, m.group(0)))
    keys = ("vps789", "cfyes", "wetest", "uouin", "090227")

    def _rank(u):
        path = urllib.parse.urlparse(u).path.lower()
        for i, k in enumerate(keys):
            if k in path:
                return i
        return 99
    links = [u for u in raw if _rank(u) < 99]
    links.sort(key=lambda u: (_rank(u),
                              0 if "/vps789/" in
                              urllib.parse.urlparse(u).path.lower() else 1,
                              u))
    return links[:_CFIP_BESTCF_MAX]


def parse_wetest_table(html):
    """WeTest 优选地址表解析：服务端渲染的表格，单元格带 data-label。
    返回 [{line, addr, bandwidth, speed, latency_ms, dc, updated}]，
    延迟只认页面给的数字（毫秒），解析不出就不收这一行。"""
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html or "", re.S):
        cells = {}
        for lb, raw in re.findall(
                r'data-label="([^"]+)"[^>]*>(.*?)</td>', tr, re.S):
            cells[lb] = re.sub(r"<[^>]+>", "", raw).strip()
        addr = cells.get("优选地址") or ""
        if not re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", addr):
            continue
        m = re.search(r"(\d+)", cells.get("往返延迟") or "")
        rows.append({
            "line": cells.get("线路名称") or "",
            "addr": addr,
            "bandwidth": cells.get("网络带宽") or "",
            "speed": cells.get("峰值速度") or "",
            "latency_ms": int(m.group(1)) if m else None,
            "dc": cells.get("数据中心") or "",
            "updated": cells.get("更新时间") or "",
        })
    return rows


def _cfip_base_item(new_id, name, url, feed):
    item = {
        "access": "direct", "builtinConfigName": "", "builtinConfigUrl": "",
        "description": "", "descriptionOriginal": "", "details": [],
        "id": new_id, "imageUrl": "", "name": name, "rank": 999,
        "region": "国内", "score": 0, "screenshots": [], "section": "main",
        "sourceUrl": "", "summary": "", "tags": ["自动收录", "优选IP"],
        "url": url, "usageKind": "", "usageNote": "",
        "verifiedAt": today_cst(), "warning": "", "auto": True,
        "publishedAt": "", "feed": feed, "miss": 0,
    }
    return item


def _build_cfip_item(kind, payload, new_id):
    """按来源拼优选 IP 条目。数字全部来自本轮抓取的原文，来源没给的
    字段就不写。过不了门禁（描述 <40 字或缺「适合」）返回 None。"""
    if kind == "cfyes":
        ips, by_net = payload["ips"], payload["by_net"]
        item = _cfip_base_item(new_id, "CFYes 优选 IP 接口（090227）",
                               CFYES_API_URL, "cfyes")
        nets = "、".join(f"{_NET_CN[k]} {len(v)} 个"
                         for k, v in by_net.items() if k in _NET_CN)
        item["summary"] = (f"纯文本优选 IP 接口：当前 {len(ips)} 个 IP，"
                           "按移动/联通/电信分网标注，打开复制就能用。")
        item["description"] = (
            "CFYes 是 090227 维护的 Cloudflare 优选 IP 纯文本接口，一行"
            "一个 IP 并标注适用网络（CM 移动、CU 联通、CT 电信），约 30 "
            f"分钟更新一次，本轮抓到 {len(ips)} 个 IP。用法：打开链接按"
            "自己的运营商挑 IP，填进 edgetunnel 或 CF 自建节点的优选 IP "
            "设置里。适合用 Cloudflare 自建节点、想挑国内快 IP 的人。")
        set_detail(item, "数据形式", "纯文本接口（一行一个 IP#分网备注）")
        set_detail(item, "IP 数", str(len(ips)))
        if nets:
            set_detail(item, "覆盖网络", nets)
        if ips:
            set_detail(item, "优选样本", "、".join(ips[:5]))
        set_detail(item, "更新频率", "约 30 分钟（来源标注）")
        set_detail(item, "信息来源", "CFYes · 090227")
        item["sourceUrl"] = "https://cf.090227.xyz/"
    elif kind == "bestcf":
        url, rows = payload["url"], payload["rows"]
        low = url.lower()
        src = ("VPS789" if "vps789" in low else
               "CFYes" if "cfyes" in low else
               "WeTest" if "wetest" in low else
               "UOUIN" if "uouin" in low else "镜像")
        stem = urllib.parse.urlparse(url).path.rsplit("/", 1)[-1]
        stem = re.sub(r"\.txt$", "", stem)
        item = _cfip_base_item(
            new_id, f"BestCF · {src} {stem.upper()} 优选 IP 清单",
            url, "bestcf")
        item["summary"] = (f"{src} 的优选 IP 纯文本清单（{stem}）："
                           f"本轮 {len(rows)} 条，打开整份复制就能用。")
        item["description"] = (
            f"这是 BestCF 导航镜像的 {src} 优选 IP 清单（{stem}），纯文本，"
            "一行一个「地址:端口#备注」，本轮共 "
            f"{len(rows)} 条。打开就能整份复制，粘进 edgetunnel 的优选 "
            "IP 列表或客户端的优选地址里，不用再去仓库里翻。适合用 "
            "Cloudflare 自建节点的人。")
        set_detail(item, "数据形式", "纯文本清单（一行一个 地址:端口#备注）")
        set_detail(item, "条数", str(len(rows)))
        if rows:
            set_detail(item, "优选样本",
                       "、".join(r["addr"] for r in rows[:5]))
            m = re.search(r"(\d{2}-\d{2}\s+\d{2}:\d{2})", rows[0]["note"])
            if m:
                set_detail(item, "清单时间", m.group(1))
        set_detail(item, "信息来源", "BestCF 导航（bestcf.pages.dev）")
        item["sourceUrl"] = BESTCF_HOME_URL
    elif kind == "wetest":
        rows = payload["rows"]
        item = _cfip_base_item(
            new_id, "WeTest 微测网 · Cloudflare 优选地址表（IPv4）",
            WETEST_V4_URL, "wetest")
        item["summary"] = (f"每 15 分钟实测的优选地址表：分移动/联通/电信"
                           f"给地址、延迟和峰值速度，本轮 {len(rows)} 条。")
        item["description"] = (
            "WeTest 微测网每 15 分钟实测一轮 Cloudflare 优选地址，表格按"
            "线路（移动/联通/电信）给优选地址、往返延迟、峰值速度和数据"
            f"中心，本轮共 {len(rows)} 条。挑自己运营商延迟最低的那条，"
            "填进 edgetunnel 或 CF 自建节点的优选 IP 里。适合用 "
            "Cloudflare 自建节点、想按运营商挑最快 IP 的人。")
        set_detail(item, "数据形式", "网页测速表（服务端渲染）")
        set_detail(item, "测速条数", str(len(rows)))
        by_line = {}
        for r in rows:
            if r["latency_ms"] is None:
                continue
            cur = by_line.get(r["line"])
            if cur is None or r["latency_ms"] < cur["latency_ms"]:
                by_line[r["line"]] = r
        for line, r in by_line.items():
            val = f"{r['addr']}（{r['dc']}，延迟 {r['latency_ms']} 毫秒"
            if r["speed"]:
                val += f"，峰值 {r['speed']}"
            val += "）"
            set_detail(item, f"{line}优选", val)
        upd = max((r["updated"] for r in rows if r["updated"]), default="")
        if upd:
            set_detail(item, "更新时间", upd)
        set_detail(item, "更新频率", "约 15 分钟（来源标注）")
        set_detail(item, "信息来源", "WeTest 微测网")
        item["sourceUrl"] = "https://www.wetest.vip/"
    else:
        return None
    if len(item["description"]) < 40 or "适合" not in item["description"]:
        return None
    if not item["summary"]:
        return None
    return item


def cfip_channel(categories, removed_keys, next_id):
    """优选 IP 换源通道：CFYes 文本接口 + BestCF 镜像清单 + WeTest
    测速表。逐源故障隔离；通道条目（feed 标记）连续 2 轮不在来源里
    才删，抓失败不计数。返回 (next_id, stats)。"""
    stats = {"added": 0, "updated": 0, "removed": 0, "sources_ok": 0}
    cat = categories.get("cfip") or {}
    items = cat.setdefault("items", [])
    live = {}   # url -> (kind, payload)：本轮成功抓到的来源
    # --- CFYes ---
    try:
        status, _ct, body = fetch(CFYES_API_URL, max_bytes=256 * 1024)
        if status != 200:
            raise RuntimeError(f"HTTP {status}")
        parsed = parse_cfyes(body.decode("utf-8", "replace"))
        if parsed["ips"]:
            live[CFYES_API_URL] = ("cfyes", parsed)
            stats["sources_ok"] += 1
    except Exception as e:
        log(f"优选IP 来源 CFYes 拉取失败（跳过）：{e}")
    # --- BestCF（先导航页拿 txt 直链，再逐份拉） ---
    bestcf_home_ok = False
    try:
        status, _ct, body = fetch(BESTCF_HOME_URL, max_bytes=1024 * 1024)
        if status != 200:
            raise RuntimeError(f"HTTP {status}")
        bestcf_home_ok = True
        for u in bestcf_txt_links(body.decode("utf-8", "replace")):
            try:
                st2, _c2, b2 = fetch(u, max_bytes=256 * 1024)
                if st2 != 200:
                    continue
                rows = parse_bestcf_txt(b2.decode("utf-8", "replace"))
                if rows:
                    live[u] = ("bestcf", {"url": u, "rows": rows})
            except Exception:
                continue
        if any(k == "bestcf" for k, _p in live.values()):
            stats["sources_ok"] += 1
    except Exception as e:
        log(f"优选IP 来源 BestCF 拉取失败（跳过）：{e}")
    # --- WeTest ---
    wetest_ok = False
    try:
        status, _ct, body = fetch(WETEST_V4_URL, max_bytes=512 * 1024)
        if status != 200:
            raise RuntimeError(f"HTTP {status}")
        rows = parse_wetest_table(body.decode("utf-8", "replace"))
        if rows:
            live[WETEST_V4_URL] = ("wetest", {"rows": rows})
            wetest_ok = True
            stats["sources_ok"] += 1
    except Exception as e:
        log(f"优选IP 来源 WeTest 拉取失败（跳过）：{e}")
    # --- 更新已有通道条目 + 过期计数 ---
    have = {}
    for it in items:
        if (it.get("feed") or "") in ("cfyes", "bestcf", "wetest"):
            have[(it.get("url") or "").strip()] = it
    for url, it in have.items():
        feed = it.get("feed")
        src_ok = (feed == "cfyes" and CFYES_API_URL in live) or \
                 (feed == "bestcf" and bestcf_home_ok) or \
                 (feed == "wetest" and wetest_ok)
        if url in live:
            kind, payload = live[url]
            fresh = _build_cfip_item(kind, payload, it.get("id") or next_id)
            if fresh:
                it["details"] = fresh["details"]
                it["summary"] = fresh["summary"]
                it["description"] = fresh["description"]
            it["miss"] = 0
            it["verifiedAt"] = today_cst()
            stats["updated"] += 1
        elif src_ok:
            miss = int(it.get("miss") or 0) + 1
            it["miss"] = miss
            if miss >= 2:
                removed_keys.add(_item_key(it))
                REMOVED.append(("cfip", it.get("name") or "",
                                it.get("url") or "",
                                "优选IP 通道：连续 2 轮不在来源清单里"))
                stats["removed"] += 1
    # --- 加新条目 ---
    total = len(items) - sum(1 for it in items
                             if _item_key(it) in removed_keys)
    for url, (kind, payload) in live.items():
        if url in have:
            continue
        if total >= MAX_PER_CAT:
            break
        new_item = _build_cfip_item(kind, payload, next_id)
        if new_item is None:
            continue
        items.append(new_item)
        next_id += 1
        total += 1
        ADDED.append(("cfip", new_item["name"], new_item["url"]))
        bump("cfip_added")
        stats["added"] += 1
    return next_id, stats


# ---------------- 第二步·节点换源（NodeHub 直订 + NodesRadar 验活层） ----------------
NODEHUB_HOME_URL = "https://crazygeeky.com/"
NODEHUB_FILES = [
    ("mihomo.yaml", "Mihomo 全量", "Clash/Mihomo 系客户端（Clash Verge、FlClash 等）"),
    ("all.yaml", "通用全量", "sing-box、Clash/Mihomo 系客户端"),
    ("base64.txt", "Base64 通用", "v2rayN、v2rayNG、Shadowrocket 等通用客户端"),
    ("us.yaml", "美国地区", "Clash/Mihomo 系客户端"),
    ("sg.yaml", "新加坡地区", "Clash/Mihomo 系客户端"),
    ("hk.yaml", "香港地区", "Clash/Mihomo 系客户端"),
    ("tw.yaml", "台湾地区", "Clash/Mihomo 系客户端"),
    ("other.yaml", "其他地区", "Clash/Mihomo 系客户端"),
]
NODESRADAR_URL = "https://nodesradar.com/"
_NODE_SCHEMES = ("vmess", "vless", "trojan", "ss", "ssr",
                 "hysteria2", "hy2", "tuic", "anytls")


def _node_name_region(name):
    """从节点名里认地区：旗帜 emoji（区域指示符对）优先，
    退回 `XX_数字` 形式的国家码。认不出返回 ""。"""
    m = re.search(r"[\U0001F1E6-\U0001F1FF]{2}", name or "")
    if m:
        pair = m.group(0)
        return "".join(chr(ord(c) - 0x1F1E6 + 65) for c in pair)
    m = re.search(r"\b([A-Z]{2})_\d", name or "")
    return m.group(1) if m else ""


def parse_nodehub_yaml(text):
    """NodeHub 的 mihomo/all/地区 yaml 解析：只数 proxies 段。
    条数按段内 `- ` 条目计；协议按 type 行计；地区按节点名认。
    （不引 yaml 库：格式由站方生成、结构稳定，数错了也只是详情数字。）"""
    m = re.search(r"(?ms)^proxies:\s*\n(.*?)(?=^[A-Za-z][\w-]*:|\Z)",
                  text or "")
    sec = m.group(1) if m else ""
    types, regions = {}, {}
    for t in re.findall(r"(?m)^\s+type:\s*([A-Za-z0-9_-]+)", sec):
        types[t] = types.get(t, 0) + 1
    for nm in re.findall(r"(?m)^\s+name:\s*(.+)$", sec):
        cc = _node_name_region(nm.strip().strip("'\""))
        if cc:
            regions[cc] = regions.get(cc, 0) + 1
    return {"count": len(re.findall(r"(?m)^- ", sec)),
            "types": types, "regions": regions}


def parse_node_sub_text(text):
    """通用订阅（base64 或明文）解析：按协议数节点条数。"""
    raw = (text or "").strip()
    plain = raw
    if "://" not in raw:
        try:
            plain = base64.b64decode(
                re.sub(r"\s+", "", raw)).decode("utf-8", "replace")
        except Exception:
            plain = ""
    types = {}
    for sc in re.findall(r"(?m)^\s*([a-z0-9]+)://", plain):
        if sc in _NODE_SCHEMES:
            types[sc] = types.get(sc, 0) + 1
    return {"count": sum(types.values()), "types": types, "regions": {}}


def parse_nodesradar_sources(html):
    """Free Nodes Radar 首页里嵌的上游清单：raw.githubusercontent
    直链对应的 owner/repo 集合（它每 6 小时对这些上游全量测活）。"""
    repos = set()
    for owner, repo in re.findall(
            r"raw\.githubusercontent\.com/([^/\s\"']+)/([^/\s\"']+)/",
            html or ""):
        repos.add(f"{owner}/{repo}")
    return repos


def _build_nodehub_item(fname, label, clients, parsed, new_id):
    url = urllib.parse.urljoin(NODEHUB_HOME_URL, f"sub/{fname}")
    item = {
        "access": "direct", "builtinConfigName": "", "builtinConfigUrl": "",
        "description": "", "descriptionOriginal": "", "details": [],
        "id": new_id, "imageUrl": "", "name": f"NodeHub 订阅 · {label}",
        "rank": 999, "region": "全球多地区", "score": 0, "screenshots": [],
        "section": "main", "sourceUrl": NODEHUB_HOME_URL, "summary": "",
        "tags": ["自动收录", "每日更新"], "url": url, "usageKind": "config",
        "usageNote": "", "verifiedAt": today_cst(), "warning": "",
        "auto": True, "publishedAt": "", "feed": "nodehub", "miss": 0,
    }
    n = parsed["count"]
    item["summary"] = (f"NodeHub 自测订阅（{label}）：本轮 {n} 个节点，"
                       "链接固定，导入客户端后在客户端里更新即可。")
    item["description"] = (
        "NodeHub 是多源聚合加自有测活的节点中心，这个文件是它的"
        f"{label}订阅，站方测活后发布（测试点在海外，给的速度只作"
        f"参考），约 2 小时一轮，本轮数到 {n} 个节点。把链接导入"
        f"{clients}就能用，之后在客户端里点更新订阅拿新节点，不用"
        "反复换链接。适合需要免费节点临时应急的人。")
    if len(item["description"]) < 40 or "适合" not in item["description"]:
        return None
    set_detail(item, "节点数", str(n))
    if parsed["types"]:
        set_detail(item, "协议分布", " · ".join(
            f"{k.upper()} {v}" for k, v in
            sorted(parsed["types"].items(), key=lambda kv: -kv[1])[:6]))
    if parsed["regions"]:
        set_detail(item, "地区分布", " · ".join(
            f"{k} {v}" for k, v in
            sorted(parsed["regions"].items(), key=lambda kv: -kv[1])[:6]))
    set_detail(item, "适合客户端", clients)
    set_detail(item, "更新频率", "约 2 小时（来源标注）")
    set_detail(item, "信息来源", "NodeHub（crazygeeky.com）")
    return item


def _build_nodesradar_item(n_sources, new_id):
    item = {
        "access": "direct", "builtinConfigName": "", "builtinConfigUrl": "",
        "description": "", "descriptionOriginal": "", "details": [],
        "id": new_id, "imageUrl": "", "name": "Free Nodes Radar（免费节点雷达）",
        "rank": 999, "region": "全球多地区", "score": 0, "screenshots": [],
        "section": "main", "sourceUrl": "", "summary": "",
        "tags": ["自动收录", "测活"], "url": NODESRADAR_URL,
        "usageKind": "", "usageNote": "", "verifiedAt": today_cst(),
        "warning": "", "auto": True, "publishedAt": "",
        "feed": "nodesradar", "miss": 0,
    }
    item["summary"] = ("第三方全量测活聚合站：汇总几十个上游订阅去重测活，"
                       "按国家/协议出订阅，本站用它作验活旁证。")
    item["description"] = (
        "Free Nodes Radar 是英文的免费节点聚合测活站，汇总上游订阅、"
        "去重后逐节点测活，每 6 小时一轮，给稳定性、速度和延迟，并按"
        "国家/协议出订阅（测活点在海外，是海外视角）。雷达用它作验活"
        "层：节点条目的上游被它收录时，详情里会标「验活旁证」。适合"
        "想横向比较免费节点来源的人。")
    if len(item["description"]) < 40 or "适合" not in item["description"]:
        return None
    if n_sources:
        set_detail(item, "收录上游", f"{n_sources} 个 GitHub 上游（解析其首页）")
    set_detail(item, "更新频率", "每 6 小时一轮（来源标注）")
    set_detail(item, "信息来源", "nodesradar.com")
    return item


def node_channel(categories, removed_keys, next_id):
    """节点换源通道：直订 NodeHub 订阅文件（地址就是订阅本身），
    Free Nodes Radar 只作验活层（它的上游清单命中本站条目时给条目
    标「验活旁证」）。NodeHub 文件 404 连续 2 轮才删；网络失败不计数。
    返回 (next_id, stats)。"""
    stats = {"added": 0, "updated": 0, "removed": 0, "verified_marks": 0}
    cat = categories.get("node") or {}
    items = cat.setdefault("items", [])
    # --- 拉 NodeHub 首页确认文件清单（失败则用固定清单，且不计 miss）---
    listed = None
    try:
        status, _ct, body = fetch(NODEHUB_HOME_URL, max_bytes=512 * 1024)
        if status == 200:
            found = set(re.findall(r"/sub/([A-Za-z0-9._-]+\.(?:yaml|txt))",
                                   body.decode("utf-8", "replace")))
            listed = {fn for fn, _l, _c in NODEHUB_FILES if fn in found}
    except Exception as e:
        log(f"节点来源 NodeHub 首页拉取失败（用固定清单）：{e}")
    # --- 逐文件拉取解析 ---
    live = {}   # fname -> parsed
    gone = set()  # 明确 404 的文件
    for fname, _label, _clients in NODEHUB_FILES:
        if listed is not None and fname not in listed:
            gone.add(fname)
            continue
        url = urllib.parse.urljoin(NODEHUB_HOME_URL, f"sub/{fname}")
        try:
            status, _ct, body = fetch(url, max_bytes=4 * 1024 * 1024)
            if status == 404:
                gone.add(fname)
                continue
            if status != 200:
                continue
            text = body.decode("utf-8", "replace")
            parsed = parse_nodehub_yaml(text) if fname.endswith(".yaml") \
                else parse_node_sub_text(text)
            if parsed["count"] > 0:
                live[fname] = parsed
        except Exception:
            continue
    # --- NodesRadar 验活层（失败就整层跳过，不影响订阅条目） ---
    radar_repos = set()
    try:
        status, _ct, body = fetch(NODESRADAR_URL, max_bytes=2 * 1024 * 1024)
        if status == 200:
            radar_repos = parse_nodesradar_sources(
                body.decode("utf-8", "replace"))
    except Exception as e:
        log(f"节点验活层 NodesRadar 拉取失败（跳过）：{e}")
    # --- 更新/过期 NodeHub 条目 ---
    have = {}
    for it in items:
        if (it.get("feed") or "") == "nodehub":
            have[(it.get("url") or "").strip()] = it
    fname_by_url = {urllib.parse.urljoin(NODEHUB_HOME_URL, f"sub/{fn}"): fn
                    for fn, _l, _c in NODEHUB_FILES}
    meta_by_fname = {fn: (l, c) for fn, l, c in NODEHUB_FILES}
    for url, it in have.items():
        fn = fname_by_url.get(url)
        if fn and fn in live:
            _label, _clients = meta_by_fname[fn]
            fresh = _build_nodehub_item(fn, _label, _clients, live[fn],
                                        it.get("id") or next_id)
            if fresh:
                it["details"] = fresh["details"]
                it["summary"] = fresh["summary"]
                it["description"] = fresh["description"]
            it["miss"] = 0
            it["verifiedAt"] = today_cst()
            stats["updated"] += 1
        elif fn and fn in gone:
            miss = int(it.get("miss") or 0) + 1
            it["miss"] = miss
            if miss >= 2:
                removed_keys.add(_item_key(it))
                REMOVED.append(("node", it.get("name") or "",
                                it.get("url") or "",
                                "节点通道：NodeHub 文件连续 2 轮不存在"))
                stats["removed"] += 1
    # --- 加新 NodeHub 条目 ---
    total = len(items) - sum(1 for it in items
                             if _item_key(it) in removed_keys)
    for fname, label, clients in NODEHUB_FILES:
        if fname not in live:
            continue
        url = urllib.parse.urljoin(NODEHUB_HOME_URL, f"sub/{fname}")
        if url in have:
            continue
        if total >= MAX_PER_CAT:
            break
        new_item = _build_nodehub_item(fname, label, clients,
                                       live[fname], next_id)
        if new_item is None:
            continue
        items.append(new_item)
        next_id += 1
        total += 1
        ADDED.append(("node", new_item["name"], new_item["url"]))
        bump("nodehub_added")
        stats["added"] += 1
    # --- NodesRadar 条目（有上游清单才建/更新） ---
    if radar_repos:
        radar_item = next((it for it in items
                           if (it.get("feed") or "") == "nodesradar"), None)
        if radar_item is not None:
            set_detail(radar_item, "收录上游",
                       f"{len(radar_repos)} 个 GitHub 上游（解析其首页）")
            radar_item["miss"] = 0
            stats["updated"] += 1
        elif total < MAX_PER_CAT:
            new_item = _build_nodesradar_item(len(radar_repos), next_id)
            if new_item is not None:
                items.append(new_item)
                next_id += 1
                total += 1
                ADDED.append(("node", new_item["name"], new_item["url"]))
                stats["added"] += 1
        # --- 验活旁证：条目上游被 NodesRadar 收录就标注 ---
        for it in items:
            repo = github_repo_of(it.get("url") or "") or \
                github_repo_of(it.get("sourceUrl") or "")
            if repo and repo in radar_repos:
                if get_detail(it, "验活旁证") != \
                        "Free Nodes Radar 收录其上游（每 6 小时全量测活）":
                    set_detail(it, "验活旁证",
                               "Free Nodes Radar 收录其上游（每 6 小时全量测活）")
                    stats["verified_marks"] += 1
    return next_id, stats


# ---------------- 第二步·免费 VPN 分区（proxy 栏 section=vpn） ----------------
_VPN_KB = {"loaded": False, "entries": []}
_vpn_kb_lock = threading.Lock()


def _vpn_kb_path():
    """免费 VPN 知识表路径：collect.py 所在目录的 vpn_kb.json
    （仓库里对应 scripts/vpn_kb.json，与 ai_kb.json 同规矩）。"""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "vpn_kb.json")


def load_vpn_kb():
    """加载免费 VPN 知识表（只读一次，线程安全）。文件缺失/损坏时
    返回 []，通道优雅降级（本轮不建 VPN 分区），不报错、不中断。"""
    with _vpn_kb_lock:
        if _VPN_KB["loaded"]:
            return _VPN_KB["entries"]
        _VPN_KB["loaded"] = True
        try:
            with open(_vpn_kb_path(), encoding="utf-8") as f:
                data = json.load(f)
            entries = data.get("items") if isinstance(data, dict) else data
            if isinstance(entries, list):
                _VPN_KB["entries"] = [e for e in entries
                                      if isinstance(e, dict)
                                      and e.get("name") and e.get("url")]
        except Exception:
            _VPN_KB["entries"] = []
        return _VPN_KB["entries"]


def _vpn_account_label(registration):
    """从调研的注册说明里判「是否要账号」：原文明确不用账号的才写
    不用账号；提到注册/账号的写要注册；都没提到的写未公布，不猜。"""
    r = registration or ""
    if any(k in r for k in ("无需注册", "不用注册", "不需要注册", "不需注册",
                            "不要账号", "无需账号", "不需要账号",
                            "免注册", "不用建号", "无需建号")):
        return "不用账号"
    if "注册" in r or "账号" in r:
        return "要注册"
    return "未公布"


def build_vpn_item(e, new_id):
    """把一条已核实候选拼成 proxy 栏 section=vpn 的种子条目。
    字段全部来自调研原文（官网/商店页核实），报告没给的一律不写；
    过不了门禁（种子口径：描述 >=30 字）返回 (None, 原因)。"""
    name = (e.get("name") or "").strip()
    url = (e.get("url") or "").strip()
    if not name or not url:
        return None, "缺名称或官网地址"
    quota = _truncate_at_sentence((e.get("free_quota") or "").strip(), 200)
    reg = _truncate_at_sentence((e.get("registration") or "").strip(), 200)
    desc = (f"{name} 是一款免费 VPN 服务。免费额度：{quota}。"
            f"注册方面：{reg}。"
            "适合想不花钱临时应急上网、又不想折腾自建节点的人。")
    if e.get("note"):
        desc += e["note"]
    if len(desc) < 30:
        return None, "描述过不了门禁（<30 字）"
    summary = _truncate_at_sentence(
        f"{name}：{e.get('free_quota') or ''}", 70) or name
    tags = ["免费VPN"]
    if int(e.get("batch") or 0) == 1:
        tags.append("首批推荐")
    if e.get("community"):
        tags.append("社区款")
    if int(e.get("batch") or 0) == 3:
        tags.append("商店榜单")
    item = {
        "access": "direct", "builtinConfigName": "", "builtinConfigUrl": "",
        "description": desc, "descriptionOriginal": "", "details": [],
        "id": new_id, "imageUrl": "", "name": name, "rank": 999,
        "region": "", "score": 0, "screenshots": [], "section": "vpn",
        "sourceUrl": (e.get("source") or "").strip(), "summary": summary,
        "tags": tags, "url": url, "usageKind": "", "usageNote": "",
        "verifiedAt": (e.get("verified_at") or ""), "warning":
        (e.get("warning") or ""), "auto": False, "publishedAt": "",
    }
    if quota:
        set_detail(item, "免费额度", quota)
    set_detail(item, "是否要账号", _vpn_account_label(e.get("registration")))
    if reg:
        set_detail(item, "注册说明", reg)
    if (e.get("server_regions") or "").strip():
        set_detail(item, "服务器地区",
                   _truncate_at_sentence(e["server_regions"].strip(), 200))
    if (e.get("speed_limit") or "").strip():
        set_detail(item, "限速",
                   _truncate_at_sentence(e["speed_limit"].strip(), 160))
    if (e.get("platforms") or "").strip():
        set_detail(item, "平台",
                   _truncate_at_sentence(e["platforms"].strip(), 200))
    # vpn_报告口径：这一批条目的国内可达性统一「未实测」，有实测再逐个改
    set_detail(item, "国内可达", "未实测")
    if e.get("verified_at"):
        set_detail(item, "核实日期", e["verified_at"])
    set_detail(item, "信息来源", "官网/商店页核实（免费 VPN 调研）")
    # 门禁硬规矩（proxy 栏）：版本两项查不到填未公布，绝不编；
    # 工具五项走 ensure_tool_fields 的兜底口径（独立 VPN 服务不是
    # 代理工具，KB 命中也只认兜底值，防把工具字段写到 VPN 头上）。
    set_detail(item, "最新版本", "未公布")
    set_detail(item, "版本日期", "未公布")
    for lb, v in (("规则格式", "未公布"), ("模块支持", "未公布"),
                  ("分流方式", "未公布"), ("配套规则", "暂无配套"),
                  ("可加的东西", "未公布")):
        set_detail(item, lb, v)
    return item, ""


def vpn_channel(categories, next_id):
    """免费 VPN 分区：把 vpn_kb.json 的已核实候选 upsert 进 proxy 栏
    section=vpn。知识表是唯一事实来源：已有条目按官网地址对上后
    刷新字段，新候选补进来；表里删掉的条目不在这里删（人工名单，
    删改走知识表评审）。返回 (next_id, stats)。"""
    stats = {"added": 0, "updated": 0, "skipped": 0}
    entries = load_vpn_kb()
    if not entries:
        return next_id, stats
    cat = categories.get("proxy") or {}
    items = cat.setdefault("items", [])
    by_url = {}
    for it in items:
        nu = norm_url(it.get("url") or "")
        if nu and nu not in by_url:
            by_url[nu] = it
    total = len(items)
    for e in entries:
        nu = norm_url(e.get("url") or "")
        tgt = by_url.get(nu)
        if tgt is not None and (tgt.get("section") or "") != "vpn" \
                and "免费VPN" not in (tgt.get("tags") or []):
            # 同址但已是别的定位的条目（理论上不该出现）：不抢，记一笔
            log(f"免费VPN 跳过同址异类条目：{e.get('name')}（{e.get('url')}）")
            stats["skipped"] += 1
            continue
        fresh, _reason = build_vpn_item(e, tgt.get("id") if tgt else next_id)
        if fresh is None:
            stats["skipped"] += 1
            continue
        if tgt is not None:
            for k in ("details", "summary", "description", "warning",
                      "tags", "section", "sourceUrl", "verifiedAt"):
                tgt[k] = fresh[k]
            stats["updated"] += 1
        else:
            if total >= MAX_PER_CAT:
                stats["skipped"] += 1
                continue
            items.append(fresh)
            by_url[nu] = fresh
            next_id += 1
            total += 1
            ADDED.append(("proxy", fresh["name"], fresh["url"]))
            bump("vpn_added")
            stats["added"] += 1
    return next_id, stats


# ---------------- state.json ----------------

def load_state(script_dir):
    """读 state.json：fails（连续验活失败次数）+ pending（待补清单）+
    full_refresh_done（FIX17 二期全量刷新标记）。返回 (fails, path,
    pending_list, full_refresh_done)。老文件没有这个标记时默认 False
    （即下一轮做全量刷新）。"""
    path = os.path.join(script_dir, "state.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        fails = data.get("fails") or {}
        pending = data.get("pending") or []
        if not isinstance(pending, list):
            pending = []
        return ({str(k): int(v) for k, v in fails.items()}, path, pending,
                bool(data.get("full_refresh_done")))
    except Exception:
        return {}, path, [], False


# 事实门待复核条目的保留期：按 lastTried 起算，超过这么多天没再被
# 拦到就清掉（来源不再喂它、或它后来攒够事实字段正式上架了，都会
# 让 lastTried 停更，保留期到点自动出清，不无限积压）。
_FACT_GATE_KEEP_DAYS = 14


def save_state(path, fails, keep_urls, pending=None, full_refresh_done=False):
    """写 state.json：fails 只留还在站上的、次数 >0 的；pending（待补清单）
    只留还在站上的条目（已解析掉 / 已删掉的自然掉出），最多 1000 条；
    full_refresh_done 是 FIX17 二期全量刷新标记（跑完首轮全量刷新后打上）。

    例外（2026-11-26 修）：事实门拦下的条目按设计就不上架，URL 永远
    不在 keep_urls 里，旧写法把它们每轮从待补清单里过滤掉——「进待
    复核」实际等于静默删除，tries 也永远攒不起来。这类条目改按
    lastTried 新鲜度保留（见 _FACT_GATE_KEEP_DAYS）：还在被拦的每轮
    都会刷新 lastTried、长期留队等复核；不再被拦的到点自动出清。"""
    pend = []
    if pending:
        _today = None
        for e in pending:
            if not isinstance(e, dict):
                continue
            _url_n = norm_url(e.get("url") or "")
            if not _url_n:
                continue
            if str(e.get("reason") or "").startswith("事实门"):
                if _today is None:
                    _today = datetime.date.fromisoformat(today_cst())
                _last = str(e.get("lastTried") or e.get("firstSeen") or "")
                try:
                    _age = (_today - datetime.date.fromisoformat(_last)).days
                except Exception:
                    _age = 0  # 日期读不出来时保守保留，别把待复核的弄丢
                if _age <= _FACT_GATE_KEEP_DAYS:
                    pend.append(e)
                continue
            if _url_n in keep_urls:
                pend.append(e)
    data = {"fails": {u: n for u, n in fails.items() if u in keep_urls and n > 0},
            "pending": pend[:1000],
            "full_refresh_done": bool(full_refresh_done),
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

def _process_item_safe(slug, item, state_fails):
    """FIX17 二期·逐条故障隔离：单条刷新抛错只跳过该条、保留旧数据并
    计数，绝不让一条坏数据毁掉整轮（ex.map 会把第一个异常直接抛回
    main，全量刷新轮里 2500 多条解析，单条异常的概率不低）。"""
    try:
        return process_item(slug, item, state_fails)
    except Exception as e:
        log(f"刷新跳过一条异常条目（{slug}，保留旧数据）："
            f"{item.get('name') or ''} {item.get('url') or ''}：{e}")
        bump("refresh_error")
        return False


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    default_path = os.path.join(os.path.dirname(script_dir), "data.json")
    path = sys.argv[1] if len(sys.argv) > 1 else default_path
    root_dir = os.path.dirname(os.path.abspath(path))

    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    categories = data.get("categories") or {}
    state_fails, state_path, state_pending, full_refresh_done = load_state(
        script_dir)
    # FIX14：待补清单读进内存。
    for _e in state_pending:
        if isinstance(_e, dict) and _e.get("url"):
            PENDING[norm_url(_e["url"])] = _e
    # FIX17 二期·全量刷新：标记没打上时，本轮预算=全部剩余模板条目
    # （一次性把现存约 1600 条模板简介刷完）；跑完把标记打上，之后每轮
    # 恢复稳态预算 300。预算先判地址再扣（见 process_item），只有裸仓库
    # 地址的条目才消费预算。
    if full_refresh_done:
        _RESOLVE_BUDGET["left"] = _RESOLVE_BUDGET_STEADY
        log("刷新预算：稳态 300 条/轮（全量刷新已完成）。")
    else:
        _RESOLVE_BUDGET["left"] = 10 ** 9
        log("刷新预算：首轮全量刷新（不限量），跑完打上 full_refresh_done 标记。")
    _SHOT_BUDGET["left"] = 60
    # 苹果价格台账（冷启动为空；本轮结束时落盘）。
    apple_ledger = load_apple_ledger(script_dir)

    normalize_sections(data)

    jobs = []
    for slug, cat in categories.items():
        for item in cat.get("items") or []:
            jobs.append((slug, item))
    total_before = len(jobs)
    log(f"共 {total_before} 条，开始验活刷新（并发 10）……")

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        list(ex.map(lambda j: _process_item_safe(j[0], j[1], state_fails),
                    jobs))
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

    # FIX16：AI 福利四来源（懒猫 AI / AIHot / 赛博鸡蛋 / 白嫖计）——纯增量：
    # 单源失败只跳过并记日志；条目只进 ai 栏，新增/过期删除都走下面既有流程。
    welfare_entries = collect_welfare_entries()

    # ---- FIX17 二期·苹果方案 A ----
    # 顺序：迁移（182/118 落地）→ 价格通道（查价/流转/发现新限免降价）
    # → TestFlight 三源（更新招募状态/加新招募）。三个环节各自故障隔离，
    # 一个挂了不拖垮别的；移除都走 removed_keys/REMOVED，受安全阀约束。
    # （旧的“限免过期复核”块已被价格通道取代：价格通道一次批量查价，
    # 覆盖 deals/discounts 两个分区的流转 + 下架 3 次未命中删除。）
    removed_keys = set()
    _max_id = 0
    for _cat in categories.values():
        for _it in (_cat.get("items") or []):
            try:
                _max_id = max(_max_id, int(_it.get("id") or 0))
            except Exception:
                pass
    next_id = _max_id + 1
    try:
        apple_migrate(categories, removed_keys)
    except Exception as e:
        log(f"苹果迁移失败（跳过）：{e}")
    try:
        next_id, _apple_stats = apple_price_channel(
            categories, apple_ledger, removed_keys, next_id)
        log(f"苹果价格通道：查价 {_apple_stats['checked']} 条，流转 "
            f"{_apple_stats['moved']} 条，移除 {_apple_stats['removed']} 条，"
            f"新增 {_apple_stats['added']} 条"
            + ("（Lookup 返回不足，跳过删除/流转）"
               if _apple_stats["skipped_valve"] else ""))
    except Exception as e:
        log(f"苹果价格通道失败（跳过）：{e}")
    try:
        next_id, _tf_stats = apple_tf_channel(
            categories, removed_keys, next_id)
        log(f"TestFlight 通道：更新 {_tf_stats['updated']} 条，新增 "
            f"{_tf_stats['added']} 条，移除 {_tf_stats['removed']} 条")
    except Exception as e:
        log(f"TestFlight 通道失败（跳过）：{e}")

    # ---- FIX17 二期第二步：优选IP/节点换源 + 免费VPN 分区 ----
    # 与苹果通道同规矩：各自故障隔离，移除走 removed_keys/REMOVED，
    # 受下面两个安全阀约束。
    try:
        next_id, _cfip_stats = cfip_channel(categories, removed_keys, next_id)
        log(f"优选IP 通道：来源成功 {_cfip_stats['sources_ok']} 个，"
            f"更新 {_cfip_stats['updated']} 条，新增 "
            f"{_cfip_stats['added']} 条，移除 {_cfip_stats['removed']} 条")
    except Exception as e:
        log(f"优选IP 通道失败（跳过）：{e}")
    try:
        next_id, _node_stats = node_channel(categories, removed_keys, next_id)
        log(f"节点通道：更新 {_node_stats['updated']} 条，新增 "
            f"{_node_stats['added']} 条，移除 {_node_stats['removed']} 条，"
            f"验活旁证 {_node_stats['verified_marks']} 条")
    except Exception as e:
        log(f"节点通道失败（跳过）：{e}")
    try:
        next_id, _vpn_stats = vpn_channel(categories, next_id)
        log(f"免费VPN 分区：新增 {_vpn_stats['added']} 条，"
            f"刷新 {_vpn_stats['updated']} 条，跳过 {_vpn_stats['skipped']} 条")
    except Exception as e:
        log(f"免费VPN 分区失败（跳过）：{e}")

    # ---- 移除：连续不通（苹果 3 次、其他栏目 6 次） ----
    for slug, cat in categories.items():
        threshold = 3 if slug == "apple" else 6
        for item in cat.get("items") or []:
            if _item_key(item) in removed_keys:
                continue
            if int(state_fails.get(norm_url(item.get("url") or ""), 0)) >= threshold:
                removed_keys.add(_item_key(item))
                REMOVED.append((slug, item.get("name") or "", item.get("url") or "",
                                f"连续 {threshold} 次验活不通"))

    # FIX14b：黑名单——已有条目里命中的，本轮随既有移除流程清掉（同一批
    # REMOVED/removed_keys，同样受下面两个安全阀约束；绝不手改 data.json）。
    # 查 url、sourceUrl、仓库名三处，再查简介/原文里的指纹句（防借壳复刻
    # 的已有条目：URL 和名字都换了，正文还是同一篇）。
    for slug, cat in categories.items():
        for item in cat.get("items") or []:
            if _item_key(item) in removed_keys:
                continue
            if (is_blocked(item.get("url") or "", item.get("name") or "")
                    or is_blocked(item.get("sourceUrl") or "")
                    or text_blocked(item.get("description") or "")
                    or text_blocked(item.get("descriptionOriginal") or "")):
                removed_keys.add(_item_key(item))
                REMOVED.append((slug, item.get("name") or "", item.get("url") or "",
                                "黑名单（内容安全），不再收录"))

    # FIX17 C2/C3：域名分堆 + 成人清理（判定表见 FIX17 块）。移除并进
    # 同一批 REMOVED/removed_keys，同样受下面两个安全阀约束。
    fix17_cleanup(categories, removed_keys)

    # FIX17 二期第三步：优选IP/节点存量清理（github.io 改写 + 模板简介裸仓库
    # 下架；每轮幂等；移除同样走 REMOVED/removed_keys、受安全阀约束）。
    try:
        _cleanup_stats = cfip_node_cleanup(categories, removed_keys)
        log(f"存量清理：github.io 改写 {_cleanup_stats['rewritten']} 条，"
            f"模板简介裸仓库移除 {_cleanup_stats['removed']} 条")
    except Exception as e:
        log(f"存量清理失败（跳过）：{e}")

    # FIX16：四来源过期自动删——来源抓成功的那一轮，标记条目不在最新
    # 列表里 miss+1，连续 2 轮才删；抓失败不计数不删。删除并进 REMOVED，
    # 和其他移除一样受下面两个安全阀约束。
    welfare_expire(categories, removed_keys)

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
        removed_keys = {_item_key(it) for cat in categories.values()
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
            if _item_key(_it) in removed_keys:
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

    # ---- 新增：先补位、再扩充；不重复的新条目全数加进（每栏总量上限 500） ----
    max_id = [0]
    _id_lock = threading.Lock()
    claimed_urls = set(existing_urls)
    for cat in categories.values():
        for item in cat.get("items") or []:
            if isinstance(item.get("id"), int):
                max_id[0] = max(max_id[0], item["id"])

    def _try_add_one(slug, items, cand, tried_urls=None, cap_state=None):
        """深挖并收一条候选；经它（含合集展开）收进 ≥1 条返回 True。
        FIX14：service/domain 合集仓库先拆成单条服务再收（合集本身不收，
        拆不出来也不收）；收进来的条目带 _pending_reason 的进待补清单。
        FIX15b：合集展开改显式栈迭代，不再递归调用自身——合集仓库互相
        引用（A 的 README 列 B、B 又列回 A）时原递归不收敛，运行 #19 在
        983 层后 RecursionError 拖垮整轮。现同一轮新增里每个候选（按
        规范化 URL 记进 tried_urls）最多深挖一次，必然终止；展开顺序
        仍是深度优先、条目原序，与递归版一致。单个候选深挖抛错只跳过
        它（与 _process_item_safe 同款隔离），不拖垮整轮。
        FIX15c：单栏上限写进加法路径——顶层候选之间的 cap 检查拦不
        住合集展开：一次 _try_add_one 调用可把一个合集展开成几百上
        千条（运行 #20 service 被一次展开冲到 2430 条，门禁拦下整
        轮）。现 cap_state 随调用一路带进 _add_one_here：循环顶满
        500 即停（栈内剩余候选直接丢弃，不再深挖），写入前再按
        len(items) − 本轮已移除数核一次上限，两种算法等价。"""
        if tried_urls is None:
            tried_urls = set()
        added_any = False
        stack = [cand]
        while stack:
            # FIX15c：满栏即停——上限只在顶层候选之间检查时，一个合集
            # 展开可一次加几百条（运行 #20 service 冲到 2430）。栈内
            # 剩余候选直接丢弃，不再深挖（省掉无用的网络请求）。
            if cap_state is not None and \
                    cap_state["used"] >= cap_state["limit"]:
                if not cap_state.get("logged"):
                    log(f"{slug} 栏已满 {MAX_PER_CAT} 条，本轮剩余"
                        f"{len(stack)} 个展开候选不再深挖")
                    cap_state["logged"] = True
                break
            cur = stack.pop()
            _nu = norm_url(cur.get("url") or "")
            if _nu in tried_urls:
                continue
            tried_urls.add(_nu)
            try:
                if _add_one_here(slug, items, cur, stack, cap_state):
                    added_any = True
                    if cap_state is not None:
                        cap_state["used"] += 1
            except Exception as e:
                log(f"新增跳过一条异常候选（{slug}）："
                    f"{cur.get('name') or ''}（{e}）")
                bump("add_error")
        return added_any

    def _add_one_here(slug, items, cand, stack, cap_state=None):
        """处理单个候选（_try_add_one 的迭代展开体）：合集仓库把拆出的
        条目压栈待办、自己返回 False；普通候选深挖收录，收进返回 True。
        全部判断与放弃文案与原递归版逐字一致。
        FIX15c：写入前按本栏当前存活数（len(items) − 本轮已移除数）
        再核一次上限——这里是最后一道，任何加法路径想绕过上限都得
        过这一关。"""
        # FIX14b：黑名单第二道（合集拆出来的条目没走 pull_candidates，在这拦）。
        if is_blocked(cand.get("url") or "", cand.get("name") or ""):
            bump("blocked")
            ABANDONED.append((cand.get("name") or "", cand.get("url") or "",
                              "黑名单（内容安全）"))
            return False
        # FIX17：已清理的误收录别收回来（域名无关项/成人误收录）；
        # 域名 guide 仓库的候选在这里定好分区。
        _f17_block = fix17_candidate_block(slug, cand)
        if _f17_block:
            ABANDONED.append((cand.get("name") or "", cand.get("url") or "",
                              _f17_block))
            return False
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
        # FIX14b：正文指纹——借壳复刻（仓库名换了，正文还是同一篇）在这拦。
        if text_blocked(material.get("text") or ""):
            bump("blocked")
            ABANDONED.append((cand.get("name") or "", cand.get("url") or "",
                              "黑名单（内容安全，正文指纹命中）"))
            return False
        if slug in ("service", "domain") and is_collection_repo(slug, cand, material):
            entries = split_collection_repo(slug, cand, material)
            if len(entries) < 3:
                ABANDONED.append((cand.get("name") or "", cand.get("url") or "",
                                  f"合集仓库拆不出条目（仅 {len(entries)} 条），不收"))
                return False
            bump("split")
            # FIX15b：拆出的条目压栈待办（倒序压栈，弹出顺序与条目原序
            # 一致），由 _try_add_one 的迭代循环处理，不再递归。
            stack.extend(reversed(entries))
            return False
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
        # FIX17 二期第二步·事实门：模板摘要 + 零事实字段的新条目不
        # 直接上架，进待补清单等复核（详见 fact_gate_reason）。
        _fg = fact_gate_reason(new_item)
        if _fg:
            pending_add(new_item["url"], new_item["name"], slug, _fg)
            ABANDONED.append((new_item["name"], new_item["url"],
                              f"事实门拦下（进待复核）：{_fg}"))
            bump("fact_gated")
            return False
        # FIX15c：写入前再核一次上限（本栏当前存活数 = len(items) −
        # 本轮已移除数；与 _try_add_one 的计数器算法等价）。满栏时
        # 不收这条——调用方的循环顶检查会随后停掉整个展开。
        if cap_state is not None and \
                len(items) - cap_state["removed_here"] >= MAX_PER_CAT:
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
        removed_here = sum(1 for it in items if _item_key(it) in removed_keys)
        room = MAX_PER_CAT - (len(items) - removed_here)
        cap = max(0, min(MAX_ADD_PER_CAT, room))
        if cap == 0:
            return
        # FIX15c：上限状态随 _try_add_one 一路带进写入点——顶层候选
        # 之间的 cap 检查拦不住合集展开（运行 #20 service 冲到 2430），
        # 展开中途也得按条检查。limit 是本轮本栏还能收的条数（已扣
        # 本轮待删条目）；used 是本轮已收数；removed_here 供写入点
        # 按 len(items) − 已移除数复核。
        cap_state = {"limit": cap, "used": 0,
                     "removed_here": removed_here, "logged": False}
        added_here = 0
        tried = 0
        tried_urls = set()
        for cand in pools.get(slug) or []:
            if added_here >= cap or tried >= MAX_CANDIDATES_TRY:
                break
            tried += 1
            before = len(items)
            try:
                ok = _try_add_one(slug, items, cand, tried_urls, cap_state)
            except Exception as e:
                # FIX15b：调用点再兜一层（_try_add_one 内部已逐条隔离），
                # 单个候选抛任何异常都只跳过它，不拖垮整轮。
                log(f"新增跳过一条异常候选（{slug}）："
                    f"{cand.get('name') or ''}（{e}）")
                bump("add_error")
                ok = False
            if ok:
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

    # FIX16：四来源新条目进 ai 栏（URL 双去重 + 名称近似去重 + 黑名单 +
    # 门禁自检 + 每栏 500 上限的 room，算法与既有新增一致；见 welfare_add）。
    welfare_add(categories, welfare_entries, existing_urls, existing_names,
                claimed_urls, max_id, removed_keys)

    # FIX17 C4：AI 条目形态标记（平台/工具），展示层据此区分呈现。
    mark_ai_kinds(categories)

    # 真正执行移除
    for cat in categories.values():
        if cat.get("items"):
            cat["items"] = [it for it in cat["items"] if _item_key(it) not in removed_keys]

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
    # FIX17 二期·全量刷新：本轮走到这里（正常落盘）才算全量刷新完成，
    # 打上标记；中途崩溃则标记不打，下轮重新全量刷。台账和 state.json
    # 一起落盘（update.yml 里 data/apple_prices.json 已加进提交清单）。
    save_state(state_path, state_fails, keep_urls,
               pending=list(PENDING.values()),
               full_refresh_done=True)
    save_apple_ledger(script_dir, apple_ledger)

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
    log(f"FIX14b：黑名单拦下 {STATS['blocked']} 条（候选 + 已有条目）")
    log(f"二期第二步：事实门拦下 {STATS.get('fact_gated', 0)} 条（进待复核）；"
        f"优选IP 通道新增 {STATS.get('cfip_added', 0)} 条；"
        f"节点通道新增 {STATS.get('nodehub_added', 0)} 条；"
        f"免费VPN 新增 {STATS.get('vpn_added', 0)} 条")
    log(f"FIX17：发布时间回填 {STATS['published']} 条；"
        f"补图命中 {STATS['shots']} / 发起 {STATS['shots_tried']} 次")
    for name, url, reason in ABANDONED:
        log(f"ABANDONED {name} | {url} | {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

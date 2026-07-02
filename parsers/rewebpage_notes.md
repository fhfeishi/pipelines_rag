# crawl4ai + opendataloader-pdf 网页/PDF 解析笔记

本目录现在有两个可复跑脚本：

| 脚本 | 作用 | 默认产物 |
|------|------|----------|
| `script_craw.py` | 用 crawl4ai 抓网页，一次产出结构化 JSON、Markdown、整页 PDF | `outputs/webpages/<slug>/page.{json,md,pdf}` |
| `script_firecrawl.py` | 用 Firecrawl API 抓网页，产出 JSON、Markdown，并在截图可用时转 PDF | `outputs/firecrawl/<slug>/page.{json,md,png,pdf}` |
| `script_oppdf.py` | 用 opendataloader-pdf 解析 PDF，导出 layout JSON、Markdown、图片和元素清单 | `outputs/webpages/<slug>/opendataloader_pdf/` |

默认目标博客：

```text
https://qwen.ai/blog?id=qwen-agentworld
```

推荐从仓库根目录运行：

```bash
# 1) 网页 -> page.json / page.md / page.pdf
.venv/bin/python -m parsers.script_craw

# 2) 自动选择 outputs/webpages 下最新的 page.pdf 解析
.venv/bin/python -m parsers.script_oppdf
```

Firecrawl 版本：

```bash
# 只测试 Firecrawl API 域名连通性，不消耗 scrape 额度
.venv/bin/python -m parsers.script_firecrawl --probe

# 需要 configs/.env 或环境变量里有 FIRECRAWL_API_KEY
.venv/bin/python -m parsers.script_firecrawl
```

也可以显式指定输入：

```bash
.venv/bin/python -m parsers.script_craw \
  https://qwen.ai/blog?id=qwen-agentworld \
  --out-dir outputs/webpages \
  --keep-png

.venv/bin/python -m parsers.script_oppdf \
  outputs/webpages/qwen.ai_blog_id_qwen-agentworld/page.pdf

.venv/bin/python -m parsers.script_firecrawl \
  https://qwen.ai/blog?id=qwen-agentworld \
  --include-images
```

`script_oppdf.py` 的关键产物：

| 文件 | 说明 |
|------|------|
| `*.json` | opendataloader-pdf 原始 layout JSON |
| `*.md` | opendataloader-pdf 生成的 Markdown |
| `images/` | PDF 中抽出的外链 PNG |
| `elements.jsonl` | flatten 后的元素流，保留 `type/page/content/source/bbox` |
| `images.jsonl` | 图片元素索引，便于后续接 L1/L2 filter 或 caption |
| `parse_summary.json` | 元素数量、类型分布、输入输出路径 |

注意：本机 shell 里 `uv` 可能不在 PATH；如果 `uv run ...` 不可用，直接用仓库 `.venv/bin/python -m ...`。

## Firecrawl 使用备注

`script_firecrawl.py` 走 Firecrawl 云端 API，不需要本机 Playwright/Chromium，因此可以作为
`script_craw.py` 的轻量替代或交叉验证工具。输出结构故意做得接近 crawl4ai：

| 文件 | 说明 |
|------|------|
| `page.json` | `snapshot`（归一化字段）+ `raw`（Firecrawl SDK 原始返回） |
| `page.md` | Firecrawl Markdown |
| `page.html` | 传 `--include-html` 时写出 |
| `page.png` | Firecrawl full-page screenshot 可下载时写出 |
| `page.pdf` | 由 `page.png` 切成 A4 分页 PDF，可继续喂给 `script_oppdf.py` |

API key 读取顺序：

1. `--api-key`
2. 环境变量 `FIRECRAWL_API_KEY`
3. `configs/.env` 中的 `FIRECRAWL_API_KEY` / `firecrawl_api_key`

网络路径（本机验证 2026-06-29）：

| 测试 | 结果 | 结论 |
|------|------|------|
| `api.firecrawl.dev` DNS | 解析到 `198.18.0.13` fake-ip | 命中 Clash fake-ip |
| 不走代理 curl | 20s 超时 | 当前 shell 没有代理环境变量时不可直连 |
| `curl -x http://127.0.0.1:7897 https://api.firecrawl.dev/` | HTTP 200 | Firecrawl API 需要经 Clash HTTP 代理访问 |
| `.venv/bin/python -m parsers.script_firecrawl --probe` | HTTP 200 | 脚本默认 `--http-proxy http://127.0.0.1:7897` 可用 |
| 真实 scrape | 未执行成功 | 当前 `configs/.env` 未发现 Firecrawl key；脚本友好提示，不消耗额度 |

如果你的 shell 已经导出 `https_proxy/http_proxy`，脚本会优先使用环境变量；否则默认用
`http://127.0.0.1:7897`。不需要代理时传 `--no-http-proxy`。

## 本次 Qwen 博客验证（2026-06-29）

命令：

```bash
.venv/bin/python -m parsers.script_craw
.venv/bin/python -m parsers.script_oppdf
```

结果：

| 阶段 | 结果 |
|------|------|
| crawl4ai | `https://qwen.ai/blog?id=qwen-agentworld` 返回 200 |
| crawl 输出 | `page.json`、`page.md`、`page.pdf` 成功写入 `outputs/webpages/qwen.ai_blog_id_qwen-agentworld/` |
| PDF 页数 | `page.pdf` 为 7 页 A4 |
| opendataloader 输出 | `opendataloader_pdf/page.json`、`page.md`、`elements.jsonl`、`images.jsonl`、`parse_summary.json` |
| layout 统计 | 7 个元素；0 个文本元素；7 个图片元素 |

解释：`script_craw.py` 生成的 `page.pdf` 是整页截图切分出的 PDF，因此
opendataloader-pdf 会把每一页识别成一个大图片元素，而不会恢复网页文本。
这正好可以测试“网页视觉快照/截图进入图片 RAG”的链路；网页正文文本请使用
crawl4ai 直接产出的 `page.md` / `page.json`。

---

# WSL2 mirrored + Clash TUN 模式下访问"打不开的国内网站"经验笔记

> 场景:Windows 跑 Clash(TUN 模式),WSL2 用 **mirrored 网络模式**共享 Windows 网卡。
> 典型受害站点:`qwen.ai`(国内站,但被海外节点 + DNS 污染双重打死)。
> 本机已按此法配好 qwen.ai,**勿删 hosts / route**。最后验证日期:2026-06-26。

---

## 0. 先搞清楚这套网络长什么样

mirrored 模式下 WSL 直接复用 Windows 的网络栈,所以 **Windows 上的 Clash TUN 会同时劫持 WSL 的流量**。本机实际拓扑:

| 通道 | 路由 | 含义 |
|------|------|------|
| `eth0` | `default via 198.18.0.2` | **Clash TUN 路径**。`198.18.0.0/16` 是 Clash 的 fake-ip 网段,一切默认流量都被它劫持走代理 |
| `eth1` | `via 10.254.4.1` | **真实物理网关**。这是绕过 Clash 的"逃生通道",直连物理网卡出公网 |
| DNS | `resolv.conf → 198.18.0.2` | Clash 内置 DNS。普通域名解析返回 fake-ip,由 Clash 按规则代理 |

记住两个关键 IP:
- `198.18.0.2` = Clash(代理 + DNS)
- `10.254.4.1` = 物理网关(直连/绕过 Clash)

查看真实拓扑:
```bash
ip route                      # 看 default 和各 /32 路由
cat /etc/resolv.conf          # 看 nameserver
env | grep -i proxy           # 看 http_proxy/https_proxy/all_proxy(本机=127.0.0.1:7897)
```

---

## 1. 故障的两个独立成因(必须分开判断)

一个国内站打不开,通常是下面**两个原因叠加**,要分别确认、分别治:

1. **路由问题**:Clash 把这个域名丢给了**海外节点**,而海外节点连不上该国内站 / 被站点拒绝 → 连接直接 `000`。
2. **DNS 污染**:公共 DNS(如 `223.5.5.5`)对该域名返回 `SERVFAIL` 或投毒 IP → 根本解析不出正确地址。

> qwen.ai 就是两者全中:走代理 `000`,`223.5.5.5` 解析 `SERVFAIL`。

---

## 2. 测试 / 诊断三连

### 2.1 判断是不是"路由/代理"问题
```bash
curl -s -o /dev/null -w "%{http_code}\n" --max-time 8 https://站点/             # 走代理
curl -s -o /dev/null -w "%{http_code}\n" --max-time 8 --noproxy '*' https://站点/  # 绕过代理直连
```
- 走代理 `000` + 绕代理 `200` → **确诊路由问题**,该站点应该直连而不是走 Clash 节点。
- 两者都 `000` → 还有 DNS 或路由没打通,继续往下。

### 2.2 判断是不是"DNS 污染"问题
```bash
nslookup 站点 223.5.5.5      # 阿里公共DNS,被污染会 SERVFAIL / 返回错误IP
nslookup 站点 198.18.0.2     # Clash DNS,通常返回 198.18.x.x 的 fake-ip
```
返回 `SERVFAIL` 或明显不对的 IP → 确诊 DNS 污染。

### 2.3 拿到"真实 IP"(关键,用 DoH 绕开污染)
用 Cloudflare DoH,且**必须绕过本机代理**:
```bash
curl -s --noproxy '*' 'https://1.1.1.1/dns-query?name=站点&type=A' \
     -H 'accept: application/dns-json' | python3 -m json.tool
```
> qwen.ai 实测真实 IP:`139.95.10.252`、`139.95.10.165`。

---

## 3. 处理方案(治本三步 + 各客户端适配)

针对"海外节点连不上 + DNS 污染"的国内站,标准修复 = **绕代理 + 钉死真实IP + 甩出TUN**。

### 步骤 A:/etc/hosts 钉死真实 IP(对抗 DNS 污染,管 CLI 工具)
```bash
echo "139.95.10.165 站点" | sudo tee -a /etc/hosts
```
⚠️ WSL 默认会**自动重新生成 /etc/hosts**。要持久化,在 `/etc/wsl.conf` 加:
```ini
[network]
generateHosts = false
```
(同理 `generateResolvConf = false` 可锁定 DNS,按需。)

### 步骤 B:加 /32 路由,把该 IP 甩出 Clash TUN(对抗路由劫持)
```bash
sudo ip route add 139.95.10.165/32 via 10.254.4.1 dev eth1
sudo ip route add 139.95.10.252/32 via 10.254.4.1 dev eth1
```
含义:这两个 IP 不走 `198.18.0.2`(Clash),改走 `10.254.4.1`(物理网关)直连。
⚠️ `ip route` **重启后丢失**,需开机脚本 / 启动时重跑(可放进登录脚本或 systemd 单元)。

### 步骤 C:Clash 配置里放行直连
在 Clash 的 `rules` 顶部加(规则按顺序匹配,要放前面):
```yaml
rules:
  - DOMAIN-SUFFIX,站点,DIRECT
```
如果用 Clash 自带 hosts,**键要写全域名**(`qwen.ai`,不能只写 `qwen`):
```yaml
hosts:
  'qwen.ai': 139.95.10.252
```

### 各客户端怎么吃到这套配置

| 客户端 | 做法 |
|--------|------|
| **curl / wget / CLI** | 加 `--noproxy '*'` 绕过环境变量代理,走 hosts+route 直连。已验证 `curl --noproxy '*' https://qwen.ai/` → 200 |
| **浏览器(系统代理)** | 靠步骤 C 的 `DIRECT` 规则 + hosts/route 生效 |
| **Chromium / Playwright / crawl4ai** | Chromium **既不读环境变量的 bypass,也不一定读 /etc/hosts**,必须用原生 flag,见下 |

### Chromium / crawl4ai 三件套(`script_craw.py`,缺一不可)
crawl4ai 经 `BrowserConfig(extra_args=[...])` 传:
```python
extra_args=[
    "--proxy-server=http://127.0.0.1:7897",      # 1) 通用走 Clash
    "--proxy-bypass-list=qwen.ai;*.qwen.ai",     # 2) qwen 绕过代理直连
    "--host-resolver-rules=MAP qwen.ai 139.95.10.252",  # 3) 直连时强制真实IP
]
```
三者缺一不可:
- 只禁代理 → 其它站点全断(裸直连出不去);
- 只放行不映射 IP → DNS 仍被污染,解析不出来。
已验证抓取返回 200。

---

## 4. 速查:确诊到修复的最短路径

```bash
# 1. 真实IP
curl -s --noproxy '*' 'https://1.1.1.1/dns-query?name=站点&type=A' -H 'accept: application/dns-json'
# 2. 钉 hosts
echo "<真实IP> 站点" | sudo tee -a /etc/hosts
# 3. 甩出 TUN
sudo ip route add <真实IP>/32 via 10.254.4.1 dev eth1
# 4. Clash 规则加 DOMAIN-SUFFIX,站点,DIRECT
# 5. 验证
curl -s -o /dev/null -w "%{http_code}\n" --noproxy '*' https://站点/   # 期望 200
```

---

## 5. 易错点备忘

- `--noproxy '*'` 的 `*` 要带引号,否则被 shell 展开。
- `/etc/hosts` 和 `ip route` **都不持久**(WSL 重生成 / 重启丢路由),长期用需固化(wsl.conf + 开机脚本)。
- Clash 规则**自上而下匹配**,`DIRECT` 规则要放在 `MATCH`/兜底规则之前。
- Clash hosts 键必须是**完整域名**。
- 走代理返回 `000` ≠ 网络坏了,多半是"被丢给了连不上目标的海外节点",优先怀疑路由而不是断网。
- 物理网关 IP(本机 `10.254.4.1`)和 Clash IP(`198.18.0.2`)换机器会变,先 `ip route` 确认再套用。

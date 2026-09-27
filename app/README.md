# CareerSafari · 上手指南

面向**大学初期人群（大一 / 研一 · 方向未定型）**的职业认知与决策支持原型。
做两件事：**定义求职需求** 与 **沉淀岗位证据**，然后把两者接起来。

> 想理解设计原理 → [`docs/原理说明.md`](docs/原理说明.md)
> 想照着操作 → [`docs/操作指南.md`](docs/操作指南.md)

---

## 一、五分钟跑起来

### 前置条件
- macOS 或 Linux
- 网络可达 GitHub / PyPI
- **不需要**手动装 Python —— `run.sh` 会用 `uv` 自动拉一个 3.12

### 步骤

```bash
# 1. 拿到代码（私有仓库，需要 Bruce 给你加权限）
git clone https://github.com/BcLee012/careersafari-app.git
cd careersafari-app/app

# 2. 配置
cp .env.example .env
#    然后编辑 .env，至少决定一件事：要不要接 LLM（见下文）

# 3. 启动
./run.sh

# 4. 打开浏览器
#    http://127.0.0.1:8765/
```

首次运行 `run.sh` 会自动创建 `.venv` 并安装依赖，约 1–2 分钟。
之后每次启动都是秒开。

### 没有 LLM key 能跑吗？

**能，而且功能完整。** 留空 `STEPFUN_API_KEY` 即可：

| 功能 | 无 LLM | 有 LLM |
|---|---|---|
| 用户画像五步采集 | ✅ 完整 | ✅ 完整 |
| 求职意向定义卡 | ✅ 完整 | ✅ 完整 |
| 方向匹配与学习建议 | ✅ 完整（确定性引擎） | ✅ 完整 + 叙述更易读 |
| JD 原子化拆解 | ⚠️ 规则基线（关键词切分） | ✅ 语义拆解，质量明显更好 |
| 建议文字润色 | — | ✅ 额外获得 |

**确定性匹配引擎是主路径，LLM 只负责润色措辞。** 界面上会明确标注当前用的是哪种，
不会假装成功。想体验完整效果再配 key。

### 配 LLM key

编辑 `.env`：

```bash
STEPFUN_API_KEY=你的key
```

Stepfun 有两个端点，**key 不通用**，用错会报错：

| 端点 | 适用 | 报错特征 |
|---|---|---|
| `https://api.stepfun.com/v1` | 平台按量计费（账户需有余额） | 余额用尽 → HTTP 402 |
| `https://api.stepfun.com/step_plan/v1` | coding plan 订阅（用订阅额度） | 拿平台 key 来用 → HTTP 401 |

不确定自己持哪一种就两个都试。项目默认填的是 `step_plan`（coding plan）。
可用模型：`step-3.7-flash` / `step-3.5-flash` / `step-5-preview` / `step-router-v1`。

---

## 二、你会看到什么

### 页面一 · 用户画像（五步向导）

采集顺序就是设计原则——**先问筛子，再问偏好**：

1. **硬约束**：专业 / 年级 / 倾向方向 / 地域 / 时间窗 / 不可接受项 / 实习目标
2. **能力两栏**：通用可迁移 ｜ 目标岗位相关，每项区分「有证据」与「仅自述」
3. **兴趣**：RIASEC 六型 18 题（主结构），MBTI 可选（辅助标签，强制展示信度局限）
4. **价值观**：把 100 分分配到 6 个维度
5. **未知项**：「我不确定」是合法输出，系统不许猜

走完生成两张卡：**求职意向定义卡**（你是谁）+ **求职与学习建议**（你该做什么）。

### 页面二 · 岗位知识库

三种采集入口（手动粘贴 / URL 采集 / 文件导入）→ LLM 拆解为原子化需求 →
**人工逐条确认** → 入库。库里已沉淀 **118 个岗位 / 1304 条需求 / 232 条 JD 原文**
（来自字节跳动校招官网，P1 可信度）。

---

## 三、命令行工具

```bash
# 端到端演示：不依赖浏览器，输出完整报告
.venv/bin/python demo_case.py

# 重新灌入 3 个管科对口岗位种子数据
.venv/bin/python seed.py

# 采集真实 JD（字节跳动校招）
.venv/bin/python collect_bytedance.py --limit 15
.venv/bin/python collect_bytedance.py --extract 50 --workers 3
```

`--extract N` 对 N 条未解析 JD 跑 LLM 拆解。每条 30–50 秒（推理模型固定产生
10–14k reasoning token，关不掉），建议 `--workers 3`。

---

## 四、配置项速查

| 键 | 默认 | 说明 |
|---|---|---|
| `STEPFUN_API_KEY` | 空 | LLM key。留空则走降级路径，功能仍可用 |
| `STEPFUN_BASE_URL` | `…/step_plan/v1` | 注意与 key 匹配 |
| `STEPFUN_MODEL` | `step-3.7-flash` | |
| `LLM_MAX_TOKENS` | `8192` | 仅默认值；代码显式传值时以参数为准 |
| `LLM_REASONING_EFFORT` | 空 | `low`/`medium`/`high` |
| `ADMIN_TOKEN` | 空 | 留空则首次启动自动生成；写操作 fail-closed |
| `FETCH_ALLOW_NON_PUBLIC` | `0` | `1` = 关闭 SSRF 公网校验，**仅本地开发** |
| `FETCH_ALLOWLIST_MODE` | `warn` | `off` / `warn` / `enforce` |
| `FETCH_ALLOWED_SUFFIXES` | 空 | 追加采集白名单 |

---

## 五、常见问题

**Q：提示「写操作未启用：服务端未配置 ADMIN_TOKEN」**
A：`.env` 里 `ADMIN_TOKEN=` 是空的。删掉那一行重启，服务端会自动生成并写回。

**Q：URL 采集抓不到内容**
A：主流招聘官网是 JS 渲染的 SPA，本地 `requests` 拿不到正文——这是技术现实。
用「手动粘贴」或「文件导入」。接口返回 401/402 时按上表的端点说明排查。

**Q：JD 拆解很慢**
A：单条 30–50 秒是正常的，推理模型的 reasoning 占用大部分 token。批量用
`--workers 3` 并发。

**Q：我的方向覆盖率都很低**
A：因为能力项大多是「仅自述」。把一两项变成「有证据」，覆盖率会明显变化——
这正是系统在逼你区分「会说」和「做过」。详见 `docs/操作指南.md`。

**Q：数据库要重新生成吗？**
A：不用。`data/careersafari.db` 已入库（118 岗位 / 1304 需求）。
它是有意提交的——重建需要约 1 小时付费 LLM 调用，属数据资产而非构建产物。
想从零重建：删掉 db 文件 → `python seed.py` → `python collect_bytedance.py --extract N`。

---

## 六、代码结构

```
app/
├── run.sh / requirements.txt / .env.example
├── demo_case.py          端到端演示脚本
├── seed.py               种子数据
├── collect_bytedance.py  真实 JD 采集器
├── docs/
│   ├── 操作指南.md        给使用者
│   └── 原理说明.md        给想判断方案可行性的人
├── careersafari/
│   ├── schema.py         数据契约 · 单一真相源
│   ├── curriculum.py     能力 Taxonomy + 培养方案课程映射 + 岗位族
│   ├── db.py             SQLite（responses append-only + schema_version）
│   ├── profile.py        问卷定义 + 画像组装 + 诚实性摘要
│   ├── match.py          确定性匹配引擎
│   ├── advice.py         建议生成（确定性 + LLM 润色 + 引用校验）
│   ├── extractor.py      JD 原子化拆解 + evidence_span 回查校验
│   ├── fetcher.py        URL 采集（SSRF + robots.txt + 白名单）
│   ├── security.py       鉴权 / 限流 / SSRF 防护
│   ├── llm.py            Stepfun 接入（熔断 + 错误分类 + 推理适配）
│   └── main.py           FastAPI 服务层
├── static/               index.html + style.css + app.js
└── data/careersafari.db  已提交，含 118 岗位 / 1304 需求
```

---

## 七、安全设计（改代码前先读）

- **写操作 fail-closed**：`ADMIN_TOKEN` 未配置时 `/api/jd/confirm` 直接返回 401，
  不放行。别为了方便改成默认放行——那是公网部署的删库后门。
- **SSRF 防护**：协议白名单 + DNS 全 IP 校验 + **逐跳重定向校验**
  （否则 302 到 `169.254.169.254` 能绕过首跳检查拿到云主机元数据）。
- **LLM 输出永不直接进库**：必须经 `/api/jd/confirm` 人工确认。
- **生成后校验**：每条需求的 `evidence_span` 回 JD 原文做子串匹配，
  匹配不上的降级为团队判断，全部不通过则整体拒绝。

---

## 八、已知边界

- URL 采集抓不到 JS 渲染的 SPA 站点（技术限制）
- 未实现跨会话的长期记忆 / 个人档案（v2）
- 不做岗位聚合与投递撮合；定位是职业认知与决策支持工具
- 以个人身份在中国大陆无法合规公开运营招聘信息站
  （个人 ICP 备案禁止涉及招聘内容；经营性网络招聘需《人力资源服务许可证》）
- `responses` 表含演示用问卷回答，仓库为 private，公开前需脱敏

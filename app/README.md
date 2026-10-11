# CareerSafari 本地应用

本目录包含 CareerSafari 0.3 原型的 FastAPI 服务、岗位证据处理、匹配逻辑和本地网页。产品范围、实际覆盖和已知缺口以仓库根目录的[项目进展与续接](../docs/项目进展与续接.md)为准。

## 快速启动

需要 macOS/Linux、Git 和 `uv`。启动脚本会创建 Python 3.12 虚拟环境并安装依赖。

```bash
cp .env.example .env
# 根据需要设置 STEPFUN_API_KEY；模型密钥不应提交到 Git
./run.sh
```

打开 <http://127.0.0.1:8765/>。用户画像与确定性匹配可以浏览；自动岗位提取、语义复核和未缓存的 RAG 问答需要 Stepfun API 密钥。管理端的写操作需要管理令牌，首次本地启动会自动生成。

请只在本机或可信开发网络使用，不要直接把管理端口暴露到公网。

## 可选：准备 O*NET 本地索引

O*NET 原始表和检索索引不包含在仓库克隆中。首次运行需要联网下载官方数据库并生成本地文件：

```bash
.venv/bin/python import_onet.py
```

原始数据依 CC BY 4.0 许可使用；版本、归属和本地候选映射限制见[岗位覆盖与匹配方法](../docs/岗位覆盖与匹配方法.md)。

## 常用命令

在仓库根目录运行：

```bash
# 回归检查
app/.venv/bin/python -m unittest discover -s app/tests -p 'test_*.py' -v

# 查看并导出当前行业来源覆盖快照；不会采集或修改审核状态
app/.venv/bin/python app/evaluate_sectors.py
```

来源采集和 AI 检查会访问招聘网站并消耗模型额度，运行前先阅读[行业来源规则](../docs/行业岗位来源.md)及[自动核验说明](docs/自动核验与岗位匹配.md)。仅在确认需要补充时再启动采集器。

## 目录导航

- `careersafari/`：画像、提取、来源适配、匹配、O*NET 检索及 API。
- `static/`：本地前端。
- `collect_evidence.py`、`collect_sectors.py`：已有来源的续跑采集器。
- `tests/`：固定响应与临时数据库回归检查。
- `data/careersafari.db`：随仓库保存的应用数据库快照。
- `data/evidence/`、`data/onet/`：原始招聘快照、官方 O*NET 表和索引，位于本机忽略目录；不会随克隆下载。

旧版用户向导与原理说明记录了早期构想，其中的问卷流程、匹配百分比、人工逐条确认和岗位数量不代表当前版本。当前规则见[自动核验与岗位匹配](docs/自动核验与岗位匹配.md)。

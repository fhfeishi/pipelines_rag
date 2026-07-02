

### `opendataloader-pdf`   --> `redox_opendataloaderpdf.py`
特点：`CPU`友好



### `mineru`    -->  `redox_mineru.py`
特点：loacl、cloud都行




### `baidu Unlimited-OCR`   --> `redox_unlimitedocr.py`
特点：gpu





### `llama-index liteparse` 

无论通过 `npm`、`pip` 还是 `cargo install` 安装，命令行接口都是一致的。

解析文件
```bash
# 基本解析
lit parse document.pdf

# 指定输出格式
lit parse document.pdf --format json -o output.json

# 只解析特定页
lit parse document.pdf --target-pages "1-5,10,15-20"

# 关闭 OCR
lit parse document.pdf --no-ocr

# 解析远程 PDF
curl -sL https://example.com/report.pdf | lit parse -
```


批量解析，对整个目录中的文档进行批量解析
```bash
lit batch-parse ./input-directory ./output-directory
```


生成截图,页面截图对 LLM 智能体很关键——它能让模型获取那些仅靠文本无法表达的视觉信息。
```bash
# 截图所有页
lit screenshot document.pdf -o ./screenshots

# 只截特定页
lit screenshot document.pdf --target-pages "1,3,5" -o ./screenshots

# 自定义 DPI
lit screenshot document.pdf --dpi 300 -o ./screenshots
```
> 安装 ImageMagick 以启用图像转 PDF：
```plain
# macOS
brew install imagemagick

# Ubuntu/Debian
apt-get install imagemagick

# Windows
choco install imagemagick.app
```




###  `langchian document_loaders ` --> `redox_langchaindocumentloaders.py`
特点： langchain集成第三方工具, `from langchain_community.document_loaders import xx`


## redox_opendataloaderpdf.py 本地静态文档 smoke（2026-07-02）

目标：先验证单个 PDF parse 工具的接口和输出契约，不批量解析 `/mnt/e/static_docs` 里的所有文档。

测试输入：

```text
/mnt/e/static_docs/sources/getting-the-most-out-of-codex.pdf
```

命令：

```bash
wsl -d Ubuntu-22.04 --cd /home/baheas/wslcodespace/pipelines_rag \
  /home/baheas/.local/bin/uv run -m parsers.redox_opendataloaderpdf \
  /mnt/e/static_docs/sources/getting-the-most-out-of-codex.pdf \
  --out outputs/static_docs/getting-the-most-out-of-codex/opendataloader_pdf \
  --quiet
```

结果：通过。

输出包：

```text
outputs/static_docs/getting-the-most-out-of-codex/
├── source.pdf
└── opendataloader_pdf/
    ├── getting-the-most-out-of-codex.json
    ├── getting-the-most-out-of-codex.md
    ├── document.md
    ├── elements.jsonl
    ├── images.jsonl
    ├── parse_summary.json
    └── images/
```

统计：

| item | count |
|------|-------|
| total elements | 64 |
| text elements | 59 |
| image elements | 4 |
| text chars | 14905 |

观察：

- `source.pdf` 已保存到输出包同级，便于把一个解析包独立迁移/复查。
- `images.jsonl` 中图片路径为 `images/imageFileN.png` 相对路径，且 `exists=true`。
- `document.md` 能按页输出文本和图片块，图片块带 `element_index/type/page/bbox/source/path` metadata。
- 第 1 页的小 X 图标也会作为 image element 保留；后续进入 RAG caption 前仍需要 `rag_pdfs.pdf_filter` 做小图过滤。
- 该 PDF 文本抽取可读，但包含中英混排和部分机器翻译风格文本；这是源 PDF 内容/生成方式的问题，不是 redox 包装层失败。

当前推荐最小验证：

```bash
/home/baheas/.local/bin/uv run -m parsers.redox_opendataloaderpdf --help
/home/baheas/.local/bin/uv run python -m compileall parsers/redox_opendataloaderpdf.py
```

后续只需挑代表性 PDF 继续试：

- 图片/截图多的技术文章：看 `document.md` 图文邻近关系。
- 中文网页 PDF：看编码、标题、段落顺序。
- 大 PDF：看 opendataloader 性能和输出体积。




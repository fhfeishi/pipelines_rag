

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





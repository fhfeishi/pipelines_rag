"""兼容 shim：parse 核心已下沉到 `parsers.document.opendataloader`（2026-07-07）。

新代码请直接 import `parsers.document.opendataloader`。
"""

from __future__ import annotations

from parsers.document.opendataloader import (  # noqa: F401
    NON_LAYOUT_JSON_NAMES,
    find_existing_layout_json,
    flatten_layout,
    load_elements,
    path_for_storage,
    require_java,
    resolve_image_path,
    run_opendataloader,
)

# download_fonts.py
# 一次性脚本：下载 Google Fonts 字体到本地 fonts/ 目录
# 用法：python download_fonts.py

import os
import re
import urllib.request
import ssl
import json

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
FONTS_DIR = os.path.join(PLUGIN_DIR, "fonts")

# Google Fonts CSS URL（与模板中的完全一致）
GOOGLE_FONTS_CSS_URL = (
    "https://fonts.googleapis.com/css2?"
    "family=Cinzel+Decorative:wght@400;700;900"
    "&family=Cinzel:wght@400;600;700"
    "&family=IM+Fell+English:ital@0;1"
    "&family=Cormorant+Garamond:ital,wght@0,300;0,400;0,600;1,300;1,400;1,600"
    "&family=ZCOOL+KuaiLe"
    "&family=Ma+Shan+Zheng"
    "&family=Noto+Sans+SC:wght@300;400;500;700"
    "&family=Noto+Serif+SC:wght@300;400;700"
    "&display=swap"
)

# 用桌面浏览器 User-Agent 请求，Google Fonts 会返回 woff2 格式
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def download_file(url: str, dest: str) -> bool:
    """下载单个文件"""
    try:
        ctx = ssl.create_default_context()
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, context=ctx, timeout=60) as resp:
            data = resp.read()
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(data)
        size_kb = len(data) / 1024
        print(f"  ✅ {os.path.basename(dest)} ({size_kb:.1f} KB)")
        return True
    except Exception as e:
        print(f"  ❌ 下载失败 {url}: {e}")
        return False


def main():
    print("=" * 60)
    print("Google Fonts 本地化下载工具")
    print("=" * 60)

    os.makedirs(FONTS_DIR, exist_ok=True)

    # 第一步：下载 CSS 文件
    print("\n📥 正在获取 Google Fonts CSS...")
    try:
        ctx = ssl.create_default_context()
        req = urllib.request.Request(GOOGLE_FONTS_CSS_URL, headers=HEADERS)
        with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
            css_text = resp.read().decode("utf-8")
    except Exception as e:
        print(f"❌ 获取 CSS 失败: {e}")
        print("请检查网络连接，或尝试使用代理。")
        return

    # 保存原始 CSS 供参考
    css_path = os.path.join(FONTS_DIR, "google_fonts_original.css")
    with open(css_path, "w", encoding="utf-8") as f:
        f.write(css_text)
    print(f"✅ CSS 已保存: {css_path}")

    # 第二步：解析 CSS 中的所有字体 URL
    # 匹配 src: url(...) format('woff2')
    url_pattern = re.compile(r'url\((https://fonts\.gstatic\.com/[^)]+)\)')
    font_urls = url_pattern.findall(css_text)
    print(f"\n📋 发现 {len(font_urls)} 个字体文件")

    # 第三步：逐个下载字体文件
    print("\n📥 开始下载字体文件...")
    downloaded = 0
    failed = 0
    local_css = css_text  # 用于生成本地化 CSS

    for url in font_urls:
        # 从 URL 提取文件名
        # 例: https://fonts.gstatic.com/s/cinzeldecorative/v17/daaCSScvJGqLYhG8nNt8KPPswUAPni7TTMw.woff2
        parts = url.split("/")
        # 用 字体名/文件名 作为本地路径
        if len(parts) >= 3:
            font_family = parts[-3]  # 如 cinzeldecorative
            filename = parts[-1]      # 如 daaCSScvJGqLYhG8nNt8KPPswUAPni7TTMw.woff2
        else:
            font_family = "unknown"
            filename = url.split("/")[-1]

        font_dir = os.path.join(FONTS_DIR, font_family)
        dest_path = os.path.join(font_dir, filename)

        # 跳过已下载的
        if os.path.exists(dest_path) and os.path.getsize(dest_path) > 0:
            print(f"  ⏭️  {font_family}/{filename} (已存在)")
            # 仍需替换 CSS 中的 URL
            rel_path = f"fonts/{font_family}/{filename}"
            local_css = local_css.replace(url, rel_path)
            downloaded += 1
            continue

        if download_file(url, dest_path):
            rel_path = f"fonts/{font_family}/{filename}"
            local_css = local_css.replace(url, rel_path)
            downloaded += 1
        else:
            failed += 1

    # 第四步：保存本地化 CSS
    local_css_path = os.path.join(FONTS_DIR, "fonts_local.css")
    with open(local_css_path, "w", encoding="utf-8") as f:
        f.write(local_css)

    # 第五步：生成字体清单 JSON（供渲染器路由映射使用）
    manifest = {}
    for url in font_urls:
        parts = url.split("/")
        if len(parts) >= 3:
            font_family = parts[-3]
            filename = parts[-1]
        else:
            font_family = "unknown"
            filename = url.split("/")[-1]

        local_path = os.path.join(FONTS_DIR, font_family, filename)
        if os.path.exists(local_path):
            manifest[url] = os.path.relpath(local_path, PLUGIN_DIR)

    manifest_path = os.path.join(FONTS_DIR, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    print(f"\n{'=' * 60}")
    print(f"📊 下载完成：成功 {downloaded}，失败 {failed}")
    print(f"📁 字体目录：{FONTS_DIR}")
    print(f"📄 本地化CSS：{local_css_path}")
    print(f"📋 字体清单：{manifest_path}")
    print(f"{'=' * 60}")

    if failed > 0:
        print("\n⚠️ 部分字体下载失败，请检查网络后重新运行此脚本。")
        print("已下载的文件不会重复下载。")
    else:
        print("\n✅ 所有字体已下载完成！")
        print("接下来请按照说明修改 renderer.py 以启用本地字体加载。")


if __name__ == "__main__":
    main()
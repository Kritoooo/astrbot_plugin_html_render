# main.py
# 插件入口：HtmlRenderPlugin 主类 + 命令 + 事件处理

import asyncio
import json
import os
import re
import sys
import uuid
import base64
from typing import Dict, List, Optional

# 将插件目录加入搜索路径，使同目录模块可导入
_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.message_components import Image, Plain
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.star.star_tools import StarTools

from renderer import html_to_image_playwright, init_browser, close_browser
from template_manager import TemplateManager
import text_processing as _text_processing
from text_processing import (
    detect_render_tag,
    detect_html_tags,
    detect_dialogue,
    preserve_newlines,
    nl2br,
    markdown_to_html,
    format_dialogue,
)


def _contains_math(content: str) -> bool:
    """Backward-compatible math detection so old cached modules won't break startup."""
    detector = getattr(_text_processing, "contains_math", None)
    if callable(detector):
        return detector(content)

    if not content:
        return False

    return bool(
        re.search(r"(?<!\\)\$(?!\$).+?(?<!\\)\$(?!\$)", content, re.DOTALL)
        or re.search(r"(?<!\\)\$\$[\s\S]+?(?<!\\)\$\$", content, re.DOTALL)
        or re.search(r"\\\(.+?\\\)", content, re.DOTALL)
        or re.search(r"\\\[[\s\S]+?\\\]", content, re.DOTALL)
        or re.search(r"\\begin\{([a-zA-Z*]+)\}[\s\S]+?\\end\{\1\}", content, re.DOTALL)
    )


@register(
    "astrbot_plugin_html_render",
    "lumingya",
    "将 AI 返回的 HTML/CSS 内容渲染成精美图片发送",
    "1.0.1",
)
class HtmlRenderPlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        self.DATA_DIR = os.path.normpath(StarTools.get_data_dir())
        self.IMAGE_CACHE_DIR = os.path.join(self.DATA_DIR, "html_render_cache")

        # 模板管理器
        template_dir = os.path.join(os.path.dirname(__file__), "templates")
        self.template_mgr = TemplateManager(template_dir)

        # 用户默认模板设置（用户ID -> 模板名）
        self.user_default_template: Dict[str, str] = {}

        # GIF 配置
        self.gif_duration = config.get("gif_duration", 3.0)
        self.gif_fps = config.get("gif_fps", 15)
# 背景图缓存（None = 未加载，"" = 无背景图，非空 = data URL）
        self._bg_data_url: Optional[str] = None
        self._horror_template_pattern = re.compile(
            r"(恐怖|惊悚|诡异|阴森|噩梦|鬼|亡灵|血|病栋|午夜|深夜|低语|尖叫|尸|诅咒|怪谈)"
        )

    # ==================== 生命周期 ====================

    async def initialize(self):
        try:
            os.makedirs(self.IMAGE_CACHE_DIR, exist_ok=True)
            self._cleanup_cache()
            await self.template_mgr.load_templates()
            self.template_mgr.update_template_id_map()
            await self._ensure_playwright()
            # 预启动浏览器实例（后续渲染复用，避免首次渲染等待）
            await init_browser()
            logger.info("HTML 渲染插件初始化完成")
        except Exception as e:
            logger.error(f"HTML 渲染插件初始化失败: {e}")

    async def _ensure_playwright(self):
        logger.info("HTML渲染插件: 检查 Playwright 依赖...")
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "playwright", "install", "chromium",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()
            if process.returncode != 0:
                logger.error(f"Playwright Chromium 安装失败: {stderr.decode('utf-8', errors='ignore')}")
        except Exception as e:
            logger.error(f"执行命令失败: {e}")

    async def terminate(self):
        await close_browser()
        logger.info("HTML 渲染插件已停止")

    def _get_bg_data_url(self) -> str:
            """读取配置的背景图片并转为 base64 Data URL（结果缓存，首次调用后不再重复读取）"""
            if self._bg_data_url is not None:
                return self._bg_data_url

            bg_config = self.config.get("background_image", "").strip()
            if not bg_config:
                self._bg_data_url = ""
                return ""

            bg_path = os.path.join(_PLUGIN_DIR, bg_config)
            if not os.path.isfile(bg_path):
                logger.warning(f"[HTML渲染] 背景图片不存在: {bg_path}，将使用默认背景")
                self._bg_data_url = ""
                return ""

            try:
                ext = os.path.splitext(bg_path)[1].lower()
                mime_map = {
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".png": "image/png",
                    ".webp": "image/webp",
                    ".gif": "image/gif",
                }
                mime = mime_map.get(ext, "image/png")
                with open(bg_path, "rb") as f:
                    encoded = base64.b64encode(f.read()).decode("utf-8")
                self._bg_data_url = f"data:{mime};base64,{encoded}"
                logger.info(f"[HTML渲染] 背景图片已加载: {bg_config} ({mime})")
            except Exception as e:
                logger.warning(f"[HTML渲染] 读取背景图片失败: {e}")
                self._bg_data_url = ""

            return self._bg_data_url

    def _inject_math_assets(self, html_content: str) -> str:
            """为包含数学公式的页面注入 MathJax 资源。"""
            if 'id="astrbot-mathjax-script"' in html_content:
                return html_content

            math_assets = """
<style>
.astr-math-inline,
.astr-math-block {
  max-width: 100%;
}
.astr-math-block {
  display: block;
  margin: 0.9em 0;
  overflow-x: auto;
  overflow-y: hidden;
  text-align: center;
}
mjx-container,
mjx-container * {
  word-break: normal !important;
  overflow-wrap: normal !important;
}
mjx-container[jax="SVG"] {
  max-width: 100%;
}
.astr-math-block mjx-container[jax="SVG"] {
  display: inline-block !important;
  margin: 0 auto !important;
}
</style>
<script>
window.__ASTR_MATH_READY__ = false;
window.MathJax = {
  tex: {
    inlineMath: [['$', '$'], ['\\\\(', '\\\\)']],
    displayMath: [['$$', '$$'], ['\\\\[', '\\\\]']],
    processEscapes: true,
    processEnvironments: true,
    packages: {'[+]': ['ams', 'noerrors', 'noundefined']}
  },
  svg: {
    fontCache: 'global'
  },
  options: {
    skipHtmlTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code']
  },
  startup: {
    pageReady: () => MathJax.startup.defaultPageReady().then(() => {
      window.__ASTR_MATH_READY__ = true;
    })
  }
};
</script>
<script
  id="astrbot-mathjax-script"
  defer
  src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-svg.js"
  onerror="window.__ASTR_MATH_READY__ = true;"
></script>
"""

            if "</head>" in html_content:
                return html_content.replace("</head>", math_assets + "</head>", 1)

            return math_assets + html_content

    def _cleanup_cache(self, max_age_seconds: int = 300):
        """清理缓存目录中的过期文件"""
        import time
        now = time.time()
        count = 0
        try:
            for f in os.listdir(self.IMAGE_CACHE_DIR):
                fp = os.path.join(self.IMAGE_CACHE_DIR, f)
                if os.path.isfile(fp) and (now - os.path.getmtime(fp)) > max_age_seconds:
                    os.remove(fp)
                    count += 1
            if count:
                logger.info(f"[HTML渲染] 已清理 {count} 个缓存文件")
        except Exception as e:
            logger.warning(f"[HTML渲染] 清理缓存失败: {e}")

    def _schedule_delete(self, *paths):
        """延迟删除文件（给消息发送留足时间，多图模式下图片生成耗时较长）"""
        async def _delete():
            await asyncio.sleep(300)
            for p in paths:
                try:
                    if os.path.exists(p):
                        os.remove(p)
                except Exception:
                    pass
        asyncio.create_task(_delete())

    # ==================== 工具方法 ====================

    def _get_user_id(self, event: AstrMessageEvent) -> str:
        try:
            if hasattr(event, 'get_sender_id') and callable(event.get_sender_id):
                return str(event.get_sender_id())
            if hasattr(event, 'sender') and hasattr(event.sender, 'user_id'):
                return str(event.sender.user_id)
            return str(event.unified_msg_origin)
        except Exception:
            return "default_user"

    def _select_template(self, content: str, specified_template: Optional[str] = None, user_id: Optional[str] = None) -> str:
        available = self.template_mgr.get_available_templates()

        if specified_template and specified_template in available:
            return specified_template

        if user_id and user_id in self.user_default_template:
            user_tpl = self.user_default_template[user_id]
            if user_tpl in available:
                return user_tpl

        if "猩红噩梦" in available and self._horror_template_pattern.search(content):
            return "猩红噩梦"

        if self.config.get("auto_dialogue_detection", True):
            quote_pat = self.config.get("dialogue_quote_pattern", r'[""「」『』]')
            quote_thr = self.config.get("dialogue_quote_threshold", 2)
            if detect_dialogue(content, quote_pat, quote_thr) and "dialogue" in available:
                return "dialogue"

        if self.config.get("auto_render_all", False):
            fallback = self.config.get("auto_render_template", "novel")
            if fallback in available:
                return fallback

        default = self.config.get("default_template", "card")
        if default in available:
            return default

        return "card"

    def _apply_template(self, content: str, template_name: str, is_raw_html: bool = False) -> str:
        """
        应用模板。
        :param is_raw_html: 若为 True，跳过 markdown/nl2br 处理，直接嵌入原始 HTML
        """
        template = self.template_mgr.load_template(template_name)

        if is_raw_html:
            # 内容自带完整 HTML+CSS，不做任何文本处理
            return template.replace("{{content}}", content)

        if template_name == "dialogue":
            content = format_dialogue(content)
        else:
            if self.config.get("enable_markdown", True):
                content = markdown_to_html(content)
                return template.replace("{{content}}", content)
            else:
                content = preserve_newlines(content)

        content = nl2br(content)
        return template.replace("{{content}}", content)

    # ==================== 渲染核心 ====================

    async def _render_content(self, content: str, specified_template: Optional[str], user_id: Optional[str] = None, is_gif: bool = False):
        """
        执行渲染。
        GIF 模式返回 List[Image]（静态图 + GIF），普通模式返回单个 Image。
        失败返回 None。
        """
        try:
            template_name = self._select_template(content, specified_template, user_id)
            logger.debug(f"HTML渲染: 使用模板 {template_name}, GIF模式: {is_gif}")

            # 检测内容是否自带 <style> 标签，若有则为完整 HTML，跳过文本处理
            has_own_style = bool(re.search(r'<style\b', content, re.IGNORECASE))
            full_html = self._apply_template(content, template_name, is_raw_html=has_own_style)
            if self.config.get("enable_math", True) and _contains_math(content):
                full_html = self._inject_math_assets(full_html)
            # 注入自定义背景图（转为 base64 内嵌，避免 Playwright 沙箱限制）
            bg_data_url = self._get_bg_data_url()
            if bg_data_url:
                bg_style = (
                    '<style>'
                    'body {'
                    f'  background-image: url("{bg_data_url}") !important;'
                    '  background-size: cover !important;'
                    '  background-position: center !important;'
                    '  background-repeat: no-repeat !important;'
                    '  background-attachment: local !important;'
                    '}'
                    '</style>'
                )
                if '</head>' in full_html:
                    full_html = full_html.replace('</head>', bg_style + '</head>', 1)
                else:
                    full_html = bg_style + full_html
            # GIF 模式始终用 .jpg 作为主输出（JPEG体积远小于PNG，渲染更快）
            filename_base = f"render_{uuid.uuid4().hex[:12]}"
            output_path = os.path.join(self.IMAGE_CACHE_DIR, f"{filename_base}.jpg")

            width = self.config.get("render_width", 600)
            if is_gif:
                scale = self.config.get("gif_scale", self.config.get("render_scale", 2))
            else:
                scale = self.config.get("render_scale", 2)

            success = await html_to_image_playwright(
                html_content=full_html,
                output_image_path=output_path,
                scale=scale,
                width=width,
                is_gif=is_gif,
                duration=self.gif_duration,
                fps=self.gif_fps,
            )

            if not success:
                return None

            if is_gif:
                results = []
                delete_paths = []
                if os.path.exists(output_path):
                    results.append(Image.fromFileSystem(output_path))
                    delete_paths.append(output_path)
                gif_path = os.path.join(self.IMAGE_CACHE_DIR, f"{filename_base}.gif")
                if os.path.exists(gif_path):
                    results.append(Image.fromFileSystem(gif_path))
                    delete_paths.append(gif_path)
                if delete_paths:
                    self._schedule_delete(*delete_paths)
                return results if results else None
            else:
                if os.path.exists(output_path):
                    img = Image.fromFileSystem(output_path)
                    self._schedule_delete(output_path)
                    return img
                return None
        except Exception as e:
            logger.error(f"渲染过程异常: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None

    async def _process_text(self, text: str, user_id: Optional[str] = None) -> List:
        components: List = []
        render_matches = detect_render_tag(text)

        if render_matches:
            # 有 <render> 标签：按标签分割，每个块用指定模板渲染
            # 标签之间/之后的剩余内容也一并渲染（不发纯文本）
            logger.info(f"[HTML渲染] 检测到 {len(render_matches)} 个 <render> 标签")
            remaining = text
            last_template = None

            for full_match, tpl_name, content, is_gif in render_matches:
                parts = remaining.split(full_match, 1)
                before = parts[0]
                remaining = parts[1] if len(parts) > 1 else ""

                # render 块之前的文本：也渲染成图片
                # 过滤掉 HTML 注释、纯空白、纯符号等无意义内容
                before_clean = before.strip() if before else ""
                before_clean = re.sub(r'<!--.*?-->', '', before_clean, flags=re.DOTALL).strip()
                if before_clean and len(before_clean) > 0 and not re.fullmatch(r'[\s\n\r\t.,;:!?…—\-_=+*/\\|@#$%^&(){}[\]<>\'\"~`]+', before_clean):
                    before_result = await self._render_content(
                        before.strip(), last_template or tpl_name, user_id, False
                    )
                    if before_result:
                        if isinstance(before_result, list):
                            components.extend(before_result)
                        else:
                            components.append(before_result)
                    else:
                        logger.warning("[HTML渲染] render块之间的内容渲染失败，跳过")

                # render 块本身
                result = await self._render_content(content, tpl_name, user_id, is_gif)
                if result:
                    if isinstance(result, list):
                        components.extend(result)
                    else:
                        components.append(result)
                else:
                    logger.warning(f"[HTML渲染] render块渲染失败，模板: {tpl_name}")

                if tpl_name:
                    last_template = tpl_name

            # 最后一个 render 块之后的剩余文本：也渲染成图片
            if remaining and remaining.strip():
                remaining_result = await self._render_content(
                    remaining.strip(), last_template, user_id, False
                )
                if remaining_result:
                    if isinstance(remaining_result, list):
                        components.extend(remaining_result)
                    else:
                        components.append(remaining_result)
                else:
                    logger.warning("[HTML渲染] render标签后的剩余内容渲染失败，跳过")

        else:
            # 无 <render> 标签：整体用默认模板渲染
            logger.info("[HTML渲染] 无 <render> 标签，整体渲染")
            result = await self._render_content(text.strip(), None, user_id, False)
            if result:
                if isinstance(result, list):
                    components.extend(result)
                else:
                    components.append(result)
            else:
                logger.warning("[HTML渲染] 整体渲染失败，跳过")

        return components

    def _detect_should_render(self, text: str, has_render_tag: bool) -> bool:
        if has_render_tag:
            return False
        return detect_html_tags(text)

    # ==================== 命令 ====================

    @filter.command("测试", aliases=["test"])
    async def cmd_test_render(self, event: AstrMessageEvent):
        full_msg = event.message_str.strip()
        full_msg = re.sub(r'\[At:\d+\]\s*', '', full_msg).strip()
        parts = full_msg.split(None, 1)
        text = parts[1].strip() if len(parts) > 1 else ""

        user_id = self._get_user_id(event)

        if not text:
            tpl = self.user_default_template.get(user_id, self.config.get("default_template", "card"))
            text = TemplateManager.get_default_test_content(tpl)
        elif text.strip().lower() == "gif":
            text = TemplateManager.get_gif_test_content()
            logger.info("[HTML渲染] 使用 GIF 弹幕测试内容")

        if '<render' in text:
            comps = await self._process_text(text, user_id)
            filtered = [c for c in comps if not (isinstance(c, Plain) and not c.text.strip())]
            if filtered:
                yield event.chain_result(filtered)
            else:
                yield event.plain_result("❌ 渲染失败，请检查日志获取详细信息")
        else:
            tpl = self.user_default_template.get(user_id, self.config.get("default_template", "card"))
            image = await self._render_content(text, tpl, user_id, False)
            if image:
                yield event.chain_result([image])
            else:
                yield event.plain_result("❌ 渲染失败，请检查日志获取详细信息")

    @filter.command("切换", aliases=["switch"])
    async def cmd_switch_template(self, event: AstrMessageEvent):
        full_msg = event.message_str.strip()
        full_msg = re.sub(r'\[At:\d+\]\s*', '', full_msg).strip()
        parts = full_msg.split(None, 1)
        arg = parts[1].strip() if len(parts) > 1 else ""

        user_id = self._get_user_id(event)
        current = self.user_default_template.get(user_id, self.config.get("default_template", "card"))

        if not arg:
            yield event.plain_result(
                f"🔄 切换渲染模板\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"用法: /切换 <模板名或ID>\n"
                f"当前模板: {current}\n\n"
                f"示例:\n  /切换 novel\n  /切换 1\n\n"
                f"使用 /查看 查看可用模板列表"
            )
            return

        template_name = None
        try:
            tid = int(arg)
            template_name = self.template_mgr.template_id_map.get(tid)
        except ValueError:
            pass

        if not template_name:
            available = self.template_mgr.get_available_templates()
            if arg in available:
                template_name = arg

        if not template_name:
            yield event.plain_result(f"❌ 未找到模板: {arg}\n\n请使用 /查看 查看可用模板列表")
            return

        self.user_default_template[user_id] = template_name
        logger.info(f"[HTML渲染] 用户 {user_id} 切换默认模板: {current} -> {template_name}")
        yield event.plain_result(f"✅ 已切换默认模板为: {template_name}")
    @filter.command("探针gif", aliases=["probegif"])
    async def cmd_probe_gif(self, event: AstrMessageEvent):
        """诊断 GIF 渲染问题：截取多帧并保存为独立图片"""
        from playwright.async_api import async_playwright
        from template_manager import TemplateManager

        html_content = TemplateManager.get_gif_test_content()
        # 移除 <render gif> 标签，只保留 HTML
        html_content = re.sub(r'<render[^>]*>', '', html_content)
        html_content = re.sub(r'</render>', '', html_content)

        yield event.plain_result("🔍 开始 GIF 渲染探针，请稍候...")

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch()
                context = await browser.new_context(
                    device_scale_factor=2,
                    viewport={"width": 600, "height": 800},
                )
                page = await context.new_page()
                await page.set_content(html_content, wait_until="networkidle")

                # 展开视口
                content_h = await page.evaluate("document.body.scrollHeight")
                await page.set_viewport_size({"width": 600, "height": max(content_h, 200)})
                await asyncio.sleep(1.0)

                # 检查弹幕元素是否存在
                danmu_count = await page.evaluate("document.querySelectorAll('.danmu-line').length")
                logger.info(f"[探针] 弹幕元素数量: {danmu_count}")

                # 检查弹幕元素的实际位置和样式
                danmu_info = await page.evaluate("""() => {
                    const items = document.querySelectorAll('.danmu-line');
                    return Array.from(items).map((el, i) => {
                        const rect = el.getBoundingClientRect();
                        const style = getComputedStyle(el);
                        return {
                            index: i,
                            text: el.textContent.substring(0, 20),
                            x: Math.round(rect.x),
                            y: Math.round(rect.y),
                            width: Math.round(rect.width),
                            height: Math.round(rect.height),
                            visible: rect.width > 0 && rect.height > 0,
                            animation: style.animation,
                            animationPlayState: style.animationPlayState,
                            transform: style.transform,
                            left: style.left,
                            opacity: style.opacity,
                            display: style.display,
                        };
                    });
                }""")

                for info in danmu_info:
                    logger.info(f"[探针] 弹幕#{info['index']}: "
                               f"text='{info['text']}' "
                               f"pos=({info['x']},{info['y']}) "
                               f"size={info['width']}x{info['height']} "
                               f"visible={info['visible']} "
                               f"animation='{info['animation']}' "
                               f"state='{info['animationPlayState']}' "
                               f"transform='{info['transform']}' "
                               f"left='{info['left']}'")

                # 截取 3 帧，间隔 1 秒
                probe_images = []
                for i in range(3):
                    shot_path = os.path.join(self.IMAGE_CACHE_DIR, f"probe_frame_{i}.png")
                    await page.screenshot(path=shot_path, full_page=True)
                    probe_images.append(Image.fromFileSystem(shot_path))
                    logger.info(f"[探针] 已截取第 {i+1} 帧")
                    if i < 2:
                        await asyncio.sleep(1.0)

                await browser.close()

            # 发送 3 帧截图
            result_chain = [Plain(f"🔍 探针结果：检测到 {danmu_count} 个弹幕元素\n详细信息请查看控制台日志\n\n以下是间隔1秒的3帧截图：")]
            result_chain.extend(probe_images)
            yield event.chain_result(result_chain)

        except Exception as e:
            logger.error(f"[探针] 失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            yield event.plain_result(f"❌ 探针失败: {e}")
    @filter.command("预览模板", aliases=["previewtpl", "tplpreview"])
    async def cmd_preview_template(self, event: AstrMessageEvent):
        full_msg = event.message_str.strip()
        full_msg = re.sub(r'\[At:\d+\]\s*', '', full_msg).strip()
        parts = full_msg.split(None, 2)
        arg = parts[1].strip() if len(parts) > 1 else ""
        text = parts[2].strip() if len(parts) > 2 else ""

        if not arg:
            yield event.plain_result("📖 用法: /预览模板 <模板名或ID> [文本]\n示例: /预览模板 novel 晚风穿过旧街，灯火一盏盏亮起来。")
            return

        self.template_mgr.update_template_id_map()
        template_name = None
        try:
            tid = int(arg)
            template_name = self.template_mgr.template_id_map.get(tid)
        except ValueError:
            pass
        if not template_name and arg in self.template_mgr.get_available_templates():
            template_name = arg
        if not template_name:
            yield event.plain_result(f"❌ 未找到模板: {arg}")
            return

        user_id = self._get_user_id(event)
        if not text:
            text = TemplateManager.get_default_test_content(template_name)
        image = await self._render_content(text, template_name, user_id, False)
        if image:
            yield event.chain_result([Plain(f"🖼️ 模板预览: {template_name}"), image])
        else:
            yield event.plain_result("❌ 模板预览失败，请检查日志")

    @filter.command("查看", aliases=["templates"])
    async def cmd_list_templates(self, event: AstrMessageEvent):
        available = self.template_mgr.get_available_templates()
        if not available:
            yield event.plain_result("❌ 当前没有可用的模板")
            return

        self.template_mgr.update_template_id_map()
        user_id = self._get_user_id(event)
        current = self.user_default_template.get(user_id, self.config.get("default_template", "card"))

        lines = ["📋 可用模板列表", "━━━━━━━━━━━━━━━━━━", ""]
        for idx in sorted(self.template_mgr.template_id_map.keys()):
            name = self.template_mgr.template_id_map[idx]
            marker = " ← 当前" if name == current else ""
            lines.append(f"  {idx}. {name}{marker}")

        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━")
        lines.append("使用方法:")
        lines.append("  /切换 <ID或名称>      切换默认模板")
        lines.append("  /测试 <文本>          测试渲染效果")
        lines.append("  /预览模板 <ID或名称> [文本]  临时预览指定模板")

        yield event.plain_result("\n".join(lines))

    # ==================== 事件钩子 ====================

    @filter.on_llm_request()
    async def on_llm_req(self, event: AstrMessageEvent, req: ProviderRequest):
        if not self.config.get("inject_prompt", True):
            return

        template_list = ", ".join(self.template_mgr.get_available_templates())

        instruction = f"""
## HTML 渲染功能

### 背景说明
你的回复会被渲染系统自动转换成精美图片发送给用户。渲染系统的工作原理是：
1. 系统解析你回复中的 <render> 标签，提取内容
2. 将内容嵌入到模板 HTML 的 {{{{content}}}} 占位符位置
3. 使用无头浏览器将完整 HTML 截图为图片发送

因此，你需要理解两种内容模式：

**模式A - 模板内容（常用）**：你只需输出纯文本和语义标签，系统自动套用模板样式。适用于日常对话、小说创作、角色扮演等。

**模式B - 自定义HTML内容**：你自己编写完整的 <style> 和 HTML 结构，系统检测到 <style> 标签后会跳过文本处理，直接将你的 HTML 嵌入模板容器中渲染。适用于用户要求制作特殊页面、数据可视化、自定义排版等场景。

**两种模式不冲突**，都需要用 <render> 标签包裹，区别仅在于内容是否自带 <style>。

### 语义标签（模式A使用）
在模板内容中，使用以下语义标签可以让渲染效果更丰富：
- <q>对话内容</q> → 对话台词，显示为引号样式
- <inner>想法</inner> → 内心活动，显示为灰色斜体
- <act>动作</act> → 动作描写，显示为特殊颜色
- <scene>场景</scene> → 场景环境描写，显示为独立段落块
- <aside>旁白</aside> → 叙述性旁白，居中显示

### <render> 标签语法
```
<render>内容</render>                          — 使用用户默认模板
<render template="模板名">内容</render>         — 指定模板
<render gif>内容</render>                       — 默认模板 + 生成GIF动图
<render template="模板名" gif>内容</render>      — 指定模板 + 生成GIF动图
```
可用模板: {template_list}
不指定 template 时，系统自动使用用户的默认模板。

### GIF 动图模式
当你在 <render> 标签中加入 `gif` 属性时，系统会：
1. 先生成一张完整的静态截图（PNG）
2. 自动检测页面中带有 CSS 动画（@keyframes）的区域
3. 对该动画区域录制多帧并合成为 GIF 动图
4. 同时发送静态图和 GIF 给用户

使用 GIF 模式时，你必须使用**模式B（自定义HTML）**，在 <style> 中定义 @keyframes 动画。系统会自动检测动画并录制。

典型应用场景：弹幕滚动效果、文字逐帧出现、元素移动/渐变动画等。

GIF 示例结构：
<render gif>
<style>
.container {{ /* 容器样式 */ }}
.animated-item {{
    animation: myAnimation 6s linear infinite;
}}
@keyframes myAnimation {{
    0% {{ transform: translateX(0); }}
    100% {{ transform: translateX(-100%); }}
}}
</style>
<div class="container">
    <div class="animated-item">滚动的内容</div>
</div>
</render>

### 重要规则（按优先级排列）
1. **（最高优先级）标签完整性**：如果使用 <render> 标签，所有内容必须在标签内部，标签外不要遗留任何内容。回复的结尾必须是 </render> 闭合标签。
   即：
   <render template="模板名">
   所有回复内容都在这里
   </render>

2. **禁止代码块包裹**：不要用 ```html ``` 或任何 ``` 代码块包裹你的输出。直接写内容即可，代码块标记会导致内容被当作纯文本展示而无法渲染。

3. **内容完整性**：你的所有回复内容都会被渲染成图片，请确保所有内容（包括状态面板、角色信息等）都在回复中完整输出。

4. **模式B注意事项**：当用户要求你输出自定义 HTML（如制作页面、卡片、可视化等），使用模式B——在 <render> 内部直接写 <style> 和 HTML 标签。不要把 HTML 放在 <render> 标签外面。

### 示例

**模式A示例（模板内容）：**
<render template="novel">
<scene>月光如水，洒落在寂静的庭院中。</scene>

林晓站在门口，望着眼前的身影，心跳不由得加速起来。

<act>她缓缓转过身来</act>，月光勾勒出她清冷的轮廓。

<q>你怎么会在这里？</q>

<inner>不对，这个时间他不应该出现才对……</inner>

他没有回答，只是静静地看着她。

<aside>命运的齿轮，从这一刻开始转动。</aside>
</render>

**模式B示例（自定义HTML）：**
<render>
<style>
.my-card {{ background: #fff; border-radius: 12px; padding: 24px; box-shadow: 0 4px 12px rgba(0,0,0,0.1); }}
.my-card h2 {{ color: #333; margin-bottom: 12px; }}
</style>
<div class="my-card">
    <h2>自定义卡片标题</h2>
    <p>这里是自定义排版的内容。</p>
</div>
</render>
"""
        req.system_prompt += f"\n\n{instruction}"

        # 注入所有模板的内置提示词（方案C：全部注入 + 标注用户偏好）
        all_prompts = self.template_mgr.extract_all_builtin_prompts()
        if all_prompts:
            user_id = self._get_user_id(event)
            current_template = self.user_default_template.get(
                user_id, self.config.get("default_template", "card")
            )

            prompt_sections = []
            prompt_sections.append("## 模板专属指令")
            prompt_sections.append(f"当前用户偏好的模板是: **{current_template}**")
            prompt_sections.append(f"如果用户没有特别指定模板，请优先使用 {current_template} 模板。")
            prompt_sections.append("")

            for tpl_name, tpl_prompt in all_prompts.items():
                is_current = " （当前用户偏好）" if tpl_name == current_template else ""
                prompt_sections.append(f"### 模板「{tpl_name}」的专属指令{is_current}")
                prompt_sections.append(tpl_prompt)
                prompt_sections.append("")

            builtin_block = "\n".join(prompt_sections)
            req.system_prompt += f"\n\n{builtin_block}"
            logger.info(f"[HTML渲染] 已注入 {len(all_prompts)} 个模板的内置提示词，当前偏好: {current_template}")

    @filter.on_llm_response(priority=40)
    async def on_llm_response(self, event: AstrMessageEvent, resp):
        if resp and resp.completion_text:
            # 优先使用 ComfyUI 清理后的文本（已移除 <pic> 和 <think> 标签）
            cleaned = event.get_extra("comfy_cleaned_text")
            text_to_save = cleaned if cleaned else resp.completion_text
            event.set_extra("html_render_original_text", text_to_save)

    @filter.on_decorating_result(priority=40)
    async def on_decorating_result(self, event: AstrMessageEvent):
        result = event.get_result()
        if not result or not result.chain:
            return

        original_text = event.get_extra("html_render_original_text")

        # 回退机制：当其他插件（如主动消息插件）绕过标准 LLM 链路时，
        # on_llm_response 不会被触发，html_render_original_text 不会被设置。
        # 此时从 chain 中的 Plain 组件提取文本作为渲染源。
        if not original_text:
            plain_texts = []
            for item in result.chain:
                if isinstance(item, Plain) and item.text and item.text.strip():
                    plain_texts.append(item.text)
            if not plain_texts:
                return
            original_text = "\n".join(plain_texts)
            logger.debug("[HTML渲染] 未找到 original_text extra，从消息链中提取文本进行渲染")

        user_id = self._get_user_id(event)

        # 渲染消息链
        new_chain: List = []
        for item in result.chain:
            if isinstance(item, Plain):
                # 清理可能残留的 <pic> 和 <think> 标签
                text_to_render = re.sub(r'<pic\s+prompt=".*?">', '', item.text, flags=re.DOTALL)
                text_to_render = re.sub(r'<think>.*?</think>', '', text_to_render, flags=re.DOTALL)

                # 在渲染前剥离 <ctx> 标签（仅移除标签本身，保留内部内容）
                text_to_render = re.sub(r'</?ctx>', '', text_to_render)

                text_to_render = text_to_render.strip()
                if text_to_render:
                    comps = await self._process_text(text_to_render, user_id)
                    new_chain.extend(comps)
            else:
                new_chain.append(item)
        result.chain = new_chain

        # 手动更新历史记录
        try:
            conv_mgr = self.context.conversation_manager
            unified_msg_origin = event.unified_msg_origin
            conv_id = await conv_mgr.get_curr_conversation_id(unified_msg_origin)

            if conv_id:
                conversation = await conv_mgr.get_conversation(unified_msg_origin, conv_id)
                if conversation:
                    try:
                        history = json.loads(conversation.history) if conversation.history else []
                    except json.JSONDecodeError:
                        history = []

                    # --- 开始替换 ---
                    clean_text = original_text
                    # 清理 HTML 渲染标签
                    clean_text = re.sub(r'<render[^>]*>', '', clean_text)
                    clean_text = re.sub(r'</render>', '', clean_text)
                    clean_text = re.sub(r'</?ctx>', '', clean_text)
                    
                    # 🔴 核心修复：补充清理绘图和思考标签，防止脏数据写回数据库
                    clean_text = re.sub(r'<pic\s+prompt=".*?">', '', clean_text, flags=re.DOTALL)
                    clean_text = re.sub(r'<think>.*?</think>', '', clean_text, flags=re.DOTALL)
                    
                    clean_text = clean_text.strip()

                    # 修复并发重复追加：如果最后一条已经是当前角色的文本，则更新而非盲目堆叠
                    if history and history[-1].get("role") == "assistant":
                        history[-1]["content"] = clean_text
                    else:
                        history.append({"role": "assistant", "content": clean_text})
                    # --- 替换结束 ---

                    await conv_mgr.update_conversation(
                        unified_msg_origin=unified_msg_origin,
                        conversation_id=conv_id,
                        history=history,
                    )
                    logger.info(f"[HTML渲染] 已手动保存历史记录，当前历史条数: {len(history)}")
        except Exception as e:
            logger.error(f"[HTML渲染] 保存历史记录失败: {e}")
            import traceback
            logger.error(traceback.format_exc())

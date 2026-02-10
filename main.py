# main.py
# 插件入口：HtmlRenderPlugin 主类 + 命令 + 事件处理

import asyncio
import json
import os
import re
import sys
import uuid
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
from text_processing import (
    detect_render_tag,
    detect_html_tags,
    detect_dialogue,
    preserve_newlines,
    nl2br,
    markdown_to_html,
    format_dialogue,
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

            # GIF 模式始终用 .png 作为主输出（静态图），GIF 另存
            filename_base = f"render_{uuid.uuid4().hex[:12]}"
            output_path = os.path.join(self.IMAGE_CACHE_DIR, f"{filename_base}.png")

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
        has_render_tag = bool(render_matches)

        # 自动合并模式（仅当有多个 render 标签时才合并，单个标签走标准分割以保留模板名）
        if render_matches and len(render_matches) > 1 and self.config.get("auto_merge_renders", False):
            logger.info(f"[HTML渲染] 检测到 {len(render_matches)} 个 <render> 标签，启用自动合并模式")
            merged_content = text
            specified_template = None
            is_gif = False
            for full_match, tpl_name, content, gif_flag in render_matches:
                if tpl_name and not specified_template:
                    specified_template = tpl_name
                if gif_flag:
                    is_gif = True
                merged_content = merged_content.replace(full_match, content)

            final_tpl = specified_template if specified_template else self.config.get("merged_template", "novel")
            result = await self._render_content(merged_content.strip(), final_tpl, user_id, is_gif)
            if result:
                if isinstance(result, list):
                    components.extend(result)
                else:
                    components.append(result)
            else:
                components.append(Plain(text))
            return components

        # 标准分割模式
        if render_matches:
            logger.info(f"[HTML渲染] 检测到 {len(render_matches)} 个 <render> 标签")
            remaining = text
            for full_match, tpl_name, content, is_gif in render_matches:
                parts = remaining.split(full_match, 1)
                before = parts[0]
                remaining = parts[1] if len(parts) > 1 else ""

                if before and before.strip():
                    components.append(Plain(before))

                result = await self._render_content(content, tpl_name, user_id, is_gif)
                if result:
                    if isinstance(result, list):
                        components.extend(result)
                    else:
                        components.append(result)
                else:
                    components.append(Plain(f"[渲染失败]\n{content}"))

            if remaining and remaining.strip():
                components.append(Plain(remaining))

        elif self.config.get("enable_auto_detect", True) and self._detect_should_render(text, has_render_tag):
            logger.info("[HTML渲染] 检测到 HTML 标签，触发自动渲染")
            image = await self._render_content(text, None, user_id, False)
            components.append(image if image else Plain(text))

        elif self.config.get("auto_render_all", False):
            min_len = self.config.get("auto_render_min_length", 20)
            if len(text.strip()) >= min_len:
                image = await self._render_content(text, None, user_id, False)
                components.append(image if image else Plain(text))
            else:
                components.append(Plain(text))
        else:
            components.append(Plain(text))

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
        lines.append("  /切换 <ID或名称>  切换默认模板")
        lines.append("  /测试 <文本>      测试渲染效果")

        yield event.plain_result("\n".join(lines))

    # ==================== 事件钩子 ====================

    @filter.on_llm_request()
    async def on_llm_req(self, event: AstrMessageEvent, req: ProviderRequest):
        if not self.config.get("inject_prompt", True):
            return

        template_list = ", ".join(self.template_mgr.get_available_templates())

        instruction = f"""
## HTML 渲染功能
你的回复会被渲染成精美图片。请使用语义标签标记不同类型的内容。

### 语义标签用法
- <q>对话内容</q> → 对话台词，会显示为引号样式
- <inner>想法</inner> → 内心活动，会显示为灰色斜体
- <act>动作</act> → 动作描写，会显示为特殊颜色
- <scene>场景</scene> → 场景环境描写，会显示为独立段落块
- <aside>旁白</aside> → 叙述性旁白，会居中显示

### 格式要求
1. 用 <render template="模板名"> 包裹正文内容
2. 可用模板: {template_list}
3. 在标签外写普通叙述文字

### 完整示例
<render template="novel">
<scene>月光如水，洒落在寂静的庭院中。</scene>

林晓站在门口，望着眼前的身影，心跳不由得加速起来。

<act>她缓缓转过身来</act>，月光勾勒出她清冷的轮廓。

<q>你怎么会在这里？</q>

<inner>不对，这个时间他不应该出现才对……</inner>

他没有回答，只是静静地看着她。

<aside>命运的齿轮，从这一刻开始转动。</aside>
</render>

### 回复格式
<ctx>
你的完整回复内容
</ctx>
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
        if not original_text:
            return

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

                    clean_text = original_text
                    clean_text = re.sub(r'<render[^>]*>', '', clean_text)
                    clean_text = re.sub(r'</render>', '', clean_text)
                    clean_text = re.sub(r'</?ctx>', '', clean_text)
                    clean_text = clean_text.strip()

                    history.append({"role": "assistant", "content": clean_text})

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
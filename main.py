import asyncio
import os
import re
import sys
import uuid
import io
from typing import Dict, List, Optional, Tuple    
import mistune
from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.message_components import Image, Plain
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.star.star_tools import StarTools

from playwright.async_api import async_playwright
# GIF 合成支持
try:
    from PIL import Image as PILImage
    GIF_AVAILABLE = True
except ImportError:
    GIF_AVAILABLE = False
    logger.warning("HTML渲染插件: Pillow 未安装，GIF 动画功能将不可用。可通过 pip install Pillow 安装。")
# Markdown 渲染支持
_markdown_renderer = None
try:
    
    # 兼容 mistune 不同版本
    if hasattr(mistune, 'create_markdown'):
        # mistune 2.x / 3.x：escape=False 保留内联 HTML（语义标签如 <q>、<inner> 等）
        try:
            _markdown_renderer = mistune.create_markdown(escape=False, plugins=['table', 'strikethrough'])
        except (TypeError, KeyError):
            # 某些版本插件名不同或不支持
            try:
                _markdown_renderer = mistune.create_markdown(escape=False)
            except TypeError:
                # 极端回退：某些版本不支持 escape 参数
                _markdown_renderer = mistune.create_markdown()
                logger.warning("HTML渲染插件: 当前 mistune 版本可能不保留内联 HTML")
    elif hasattr(mistune, 'Markdown'):
        # mistune 0.x
        _markdown_renderer = mistune.Markdown()
    else:
        # 最终回退
        _markdown_renderer = mistune.html
    
    MARKDOWN_AVAILABLE = True
    logger.info(f"HTML渲染插件: mistune {getattr(mistune, '__version__', 'unknown')} 初始化成功")
except ImportError:
    MARKDOWN_AVAILABLE = False
    logger.warning("HTML渲染插件: mistune 未安装，Markdown 渲染功能将不可用。可通过 pip install mistune 安装。")


async def html_to_image_playwright(
    html_content: str,
    output_image_path: str,
    scale: int = 2,
    width: int = 600,
    is_gif: bool = False,
    duration: float = 3.0,
    fps: int = 15
) -> bool:
    """
    使用 Playwright 将 HTML 内容渲染成图片（支持 GIF 动画）。
    
    :param html_content: HTML 内容
    :param output_image_path: 输出路径（.png 或 .gif）
    :param scale: 缩放比例
    :param width: 视口宽度
    :param is_gif: 是否生成 GIF 动画
    :param duration: GIF 动画时长（秒）
    :param fps: GIF 帧率
    :return: 是否成功
    """
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            context = await browser.new_context(
                device_scale_factor=scale,
                viewport={"width": width, "height": 800}
            )
            page = await context.new_page()
            await page.set_content(html_content, wait_until="networkidle")
            await asyncio.sleep(0.3)  # 等待渲染稳定

            if not is_gif:
                # 静态图片模式：full_page=True 捕获完整页面，避免内容被裁切
                await page.screenshot(path=output_image_path, full_page=True)
            else:
                # GIF 动画模式
                if not GIF_AVAILABLE:
                    logger.warning("Pillow 未安装，回退到静态截图")
                    await target.screenshot(path=output_image_path.replace('.gif', '.png'))
                else:
                    import time
                    
                    frame_count = int(duration * fps)
                    frame_interval = 1.0 / fps
                    frames = []
                    
                    logger.info(f"[GIF] 开始录制：{frame_count}帧 @ {fps}fps，预计{duration}s")
                    record_start = time.time()
                    
                    for i in range(frame_count):
                        frame_start = time.time()
                        
                        # 使用 JPEG 格式截图，full_page=True 防止裁切
                        frame_bytes = await page.screenshot(full_page=True, type='jpeg', quality=85)
                        frame_img = PILImage.open(io.BytesIO(frame_bytes)).convert('RGB')
                        
                        # 转换为调色板模式（GIF 需要）
                        frame_img = frame_img.convert('P', palette=PILImage.ADAPTIVE, colors=256)
                        frames.append(frame_img)
                        
                        # 精确控制帧间隔：减去截图耗时
                        elapsed = time.time() - frame_start
                        remaining = frame_interval - elapsed
                        if remaining > 0:
                            await asyncio.sleep(remaining)
                    
                    record_time = time.time() - record_start
                    logger.info(f"[GIF] 录制完成：{len(frames)}帧，耗时{record_time:.1f}s")
                        # 确保目录存在
                    os.makedirs(os.path.dirname(output_image_path), exist_ok=True)                    
                    # 合成 GIF
                    if frames:
                        compose_start = time.time()
                        frames[0].save(
                            output_image_path,
                            save_all=True,
                            append_images=frames[1:],
                            duration=int(1000 / fps),
                            loop=0,
                            optimize=True  # 启用优化，减小文件
                        )
                        compose_time = time.time() - compose_start
                        logger.info(f"[GIF] 合成完成，耗时{compose_time:.1f}s")

            await browser.close()
            return True
    except Exception as e:
        logger.error(f"Playwright 渲染失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


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
        self.TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")
        
        # 模板缓存
        self.templates: Dict[str, str] = {}
        
        # 用户默认模板设置（用户ID -> 模板名）
        self.user_default_template: Dict[str, str] = {}
        # 模板ID映射（ID -> 模板名），用于 /切换 命令
        self.template_id_map: Dict[int, str] = {}
                
        # GIF 配置
        self.gif_duration = config.get("gif_duration", 3.0)
        self.gif_fps = config.get("gif_fps", 15)

    async def initialize(self):
        """初始化插件"""
        try:
            # 创建缓存目录
            os.makedirs(self.IMAGE_CACHE_DIR, exist_ok=True)

            # 加载模板
            await self._load_templates()

            # 更新模板ID映射
            self._update_template_id_map()

            # 安装 Playwright
            await self._ensure_playwright()

            logger.info("HTML 渲染插件初始化完成")
        except Exception as e:
            logger.error(f"HTML 渲染插件初始化失败: {e}")

    async def _load_templates(self):
        """加载所有模板文件"""
        if not os.path.exists(self.TEMPLATE_DIR):
            # 如果目录不存在，先尝试创建（虽然通常由用户手动放置，但为了写入默认模板做准备）
            try:
                os.makedirs(self.TEMPLATE_DIR, exist_ok=True)
            except Exception:
                pass
        
        # 读取目录下的 html 文件
        if os.path.exists(self.TEMPLATE_DIR):
            for filename in os.listdir(self.TEMPLATE_DIR):
                if filename.endswith('.html'):
                    template_name = filename[:-5]  # 移除 .html
                    filepath = os.path.join(self.TEMPLATE_DIR, filename)
                    try:
                        with open(filepath, 'r', encoding='utf-8') as f:
                            self.templates[template_name] = f.read()
                        logger.info(f"已加载模板: {template_name}")
                    except Exception as e:
                        logger.error(f"加载模板 {filename} 失败: {e}")

        # 如果没有加载到任何模板（或目录为空），创建内置默认模板到内存，并尝试写入文件
        if not self.templates:
            logger.info("未找到外部模板文件，加载内置默认模板")
            self._create_default_templates()

    def _create_default_templates(self):
        """创建内置默认模板（保底逻辑）"""
        self.templates["card"] = self._get_default_card_template()
        self.templates["dialogue"] = self._get_default_dialogue_template()
        self.templates["novel"] = self._get_default_novel_template()
        
        # 尝试写入文件以便用户修改
        try:
            os.makedirs(self.TEMPLATE_DIR, exist_ok=True)
            with open(os.path.join(self.TEMPLATE_DIR, "card.html"), "w", encoding="utf-8") as f:
                f.write(self.templates["card"])
            with open(os.path.join(self.TEMPLATE_DIR, "dialogue.html"), "w", encoding="utf-8") as f:
                f.write(self.templates["dialogue"])
            with open(os.path.join(self.TEMPLATE_DIR, "novel.html"), "w", encoding="utf-8") as f:
                f.write(self.templates["novel"])
        except Exception as e:
            logger.warning(f"无法写入默认模板文件: {e}")

    def _get_available_templates(self) -> List[str]:
        """获取可用模板列表（实时扫描目录）"""
        templates = set()
        
        # 扫描目录中的模板文件
        if os.path.exists(self.TEMPLATE_DIR):
            for filename in os.listdir(self.TEMPLATE_DIR):
                if filename.endswith('.html'):
                    templates.add(filename[:-5])
        
        # 始终包含内置模板（作为回退）
        templates.update(["card", "dialogue", "novel"])
        
        return sorted(templates)

    def _load_template(self, template_name: str) -> str:
        """从硬盘实时加载模板（每次调用都读取文件）"""
        filepath = os.path.join(self.TEMPLATE_DIR, f"{template_name}.html")
        
        # 尝试从硬盘读取
        if os.path.exists(filepath):
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
                    logger.debug(f"[HTML渲染] 已从硬盘加载模板: {template_name}")
                    return content
            except Exception as e:
                logger.error(f"[HTML渲染] 读取模板 {template_name} 失败: {e}")
        
        # 回退到内置模板
        logger.debug(f"[HTML渲染] 使用内置模板: {template_name}")
        if template_name == "card":
            return self._get_default_card_template()
        elif template_name == "dialogue":
            return self._get_default_dialogue_template()
        elif template_name == "novel":
            return self._get_default_novel_template()
        else:
            # 未知模板名，使用 card 作为最终回退
            logger.warning(f"[HTML渲染] 未知模板 {template_name}，回退到 card")
            return self._get_default_card_template()
    def _update_template_id_map(self):
        """更新模板ID映射（按名称排序，实时扫描）"""
        available = self._get_available_templates()
        self.template_id_map = {
            idx: name for idx, name in enumerate(available, start=1)
        }
        logger.debug(f"模板ID映射已更新: {self.template_id_map}")   

    def _get_user_id(self, event: AstrMessageEvent) -> str:
        """获取用户唯一标识"""
        try:
            # 尝试多种方式获取用户ID
            if hasattr(event, 'get_sender_id') and callable(event.get_sender_id):
                return str(event.get_sender_id())
            if hasattr(event, 'sender') and hasattr(event.sender, 'user_id'):
                return str(event.sender.user_id)
            # 备用：使用消息来源
            return str(event.unified_msg_origin)
        except Exception:
            return "default_user"
    def _get_default_test_content(self, template_name: str) -> str:
        """获取完整测试内容（覆盖所有功能）"""
        
        test_content = '''<scene>夕阳的余晖洒落在古老的图书馆中，尘埃在光柱里缓缓飘舞。窗外的梧桐树叶沙沙作响，为这个寂静的午后增添了几分诗意。</scene>

## 第一章 相遇

林晓推开那扇厚重的橡木门，铃铛发出清脆的"叮铃"声，打破了图书馆内的宁静。

<act>她环顾四周，目光在一排排书架间游移</act>，最终落在靠窗的那个位置——那里坐着一个正在专注阅读的青年。

<q>不好意思，请问这里有人吗？</q>

青年缓缓抬起头，露出一个温和的笑容。阳光恰好照在他的侧脸上，勾勒出柔和的轮廓。

<q>没有，请坐。</q>

<inner>他的声音……好像在哪里听过。那种熟悉的感觉，像是很久以前的梦境。</inner>

林晓道了声谢，在对面坐下。她不经意间打量着眼前的人：干净的白衬衫，微微卷曲的黑发，还有那双仿佛能看透一切的眼眸。

<aside>命运的齿轮，就在这个平凡的午后，悄然开始转动。</aside>

---

## Markdown 格式测试

### 文本样式

这是**加粗文字**，这是*斜体文字*，这是***粗斜体***。

这是`行内代码`测试，用于显示代码片段如 `print("Hello")` 或变量名 `user_name`。

### 引用块

> 时光荏苒，岁月如梭。
> 那些曾经以为会永远铭记的瞬间，终将在记忆的长河中渐渐模糊。
> 唯有文字，能将那些珍贵的片刻永远定格。

### 无序列表

**购物清单：**

* 新鲜的咖啡豆（哥伦比亚产区）
* 全脂牛奶一升
* 方糖一盒
* 肉桂粉少许

### 有序列表

**制作步骤：**

1. 将咖啡豆研磨成细粉
2. 用90度热水冲泡
3. 加入适量牛奶
4. 撒上肉桂粉装饰
5. 轻轻搅拌均匀后享用

---

## 长文本换行测试

这是一段超长的连续文本用于测试自动换行功能：ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcdefghijklmnopqrstuvwxyz这是中文和英文混合的超长文本测试TheQuickBrownFoxJumpsOverTheLazyDog敏捷的棕色狐狸跳过了懒惰的狗

超长无空格字符串：一二三四五六七八九十壹贰叁肆伍陆柒捌玖拾甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥

---

<details open>
<summary>🗂️ 角色档案 - 折叠块测试</summary>

**基本信息**
🪪 姓名：林晓（Lin Xiao） | 📅 年龄：22岁 | 🎓 身份：江南大学文学系大三学生
📍 籍贯：江南水乡・苏州 | 🩸 血型：O型 | ⭐ 星座：双鱼座

**性格特点**
温柔细腻，善于观察周围的人和事，总能捕捉到别人忽略的细节。热爱文学，尤其钟情于古典诗词和民国时期的散文。性格有些内向，但对朋友真诚坦率，偶尔会陷入自己的思绪中无法自拔。

**外貌描述**
一头乌黑的长发及腰，通常扎成简单利落的马尾。眼眸清澈明亮如秋水，嘴角常带着若有若无的微笑。身高165cm，喜欢穿素雅的棉麻连衣裙，手腕上总戴着一串外婆留下的珍珠手链。

**背景故事**
出生于一个书香门第，从小在外婆的熏陶下爱上了阅读。外婆去世后，她常常独自来到这家百年老图书馆，因为这里珍藏着外婆最喜欢的那套民国初版《红楼梦》古籍。

</details>

<details open>
<summary>📊 数据表格测试</summary>

| 属性 | 数值 | 等级 | 说明 |
| --- | --- | --- | --- |
| 智力 | 85 | A | 学业成绩优异，多次获得奖学金 |
| 魅力 | 78 | B+ | 温婉气质，清新脱俗 |
| 体力 | 45 | C | 不擅长运动，但喜欢散步 |
| 幸运 | 62 | B | 普通水平，偶有小确幸 |
| 文学 | 95 | S | 精通古典文学，写作能力出众 |

</details>

---

## 更多语义标签

<scene>夜幕降临，图书馆即将关门。橘黄色的灯光在书架间投下长长的影子，空气中弥漫着旧书纸张特有的香气。</scene>

青年合上书本，站起身来。

<act>他整理好桌上的书籍，将椅子轻轻推回原位</act>

<q>时间不早了，你也该回去了。</q>

林晓这才注意到窗外已是华灯初上。她有些恋恋不舍地合上手中的书。

<inner>真奇怪，明明是第一次见面，却感觉和他相处得如此自然。像是……像是久别重逢的老友。</inner>

<q>谢谢你今天的陪伴。我叫林晓，你呢？</q>

青年微微一笑，月光恰好透过窗户照在他的脸上。

<q>我叫顾言。很高兴认识你，林晓。</q>

<aside>就这样，两个陌生人的故事，在这个飘着书香的夜晚，悄然拉开了序幕。</aside>

---

**测试完成** ✓

以上内容包含：语义标签（scene/act/q/inner/aside）、Markdown格式（标题/列表/引用/加粗/斜体）、details折叠块、表格、长文本换行测试等全部功能。'''
        
        return f'''<render template="{template_name}">
{test_content}
</render>'''

    def _get_default_card_template(self) -> str:
        """默认卡片模板 HTML"""
        return """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: "Microsoft YaHei", "PingFang SC", -apple-system, sans-serif;
            padding: 24px;
            display: inline-block;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100px;
        }
        .card {
            background: white;
            border-radius: 16px;
            padding: 24px 28px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.15);
            line-height: 1.8;
            font-size: 16px;
            color: #333;
            min-width: 300px;
            white-space: pre-wrap;
        }
        /* 强制所有子元素也保留换行 */
        .card * {
            white-space: pre-wrap;
        }
        /* Markdown 样式 */
        .card p { margin-bottom: 12px; }
        .card p:last-child { margin-bottom: 0; }
        .card h1, .card h2, .card h3, .card h4, .card h5, .card h6 {
            margin-top: 16px;
            margin-bottom: 8px;
            font-weight: 600;
            color: #2c3e50;
        }
        .card h1 { font-size: 24px; }
        .card h2 { font-size: 22px; }
        .card h3 { font-size: 20px; }
        .card ul, .card ol { 
            margin-left: 24px; 
            margin-bottom: 12px; 
        }
        .card li { margin-bottom: 6px; }
        .card blockquote {
            border-left: 4px solid #667eea;
            padding-left: 16px;
            margin: 12px 0;
            color: #666;
            font-style: italic;
        }
        .card code {
            background: #f4f4f4;
            padding: 2px 6px;
            border-radius: 4px;
            font-family: Consolas, "Courier New", monospace;
            font-size: 14px;
        }
        .card pre {
            background: #f4f4f4;
            padding: 12px;
            border-radius: 8px;
            overflow-x: auto;
            margin: 12px 0;
        }
        .card pre code {
            background: none;
            padding: 0;
        }
        .card strong { font-weight: 600; color: #2c3e50; }
        .card em { font-style: italic; }
        .card hr {
            border: none;
            border-top: 2px solid #e0e0e0;
            margin: 16px 0;
        }
    </style>
</head>
<body>
    <div class="card">{{content}}</div>
</body>
</html>"""

    def _get_default_dialogue_template(self) -> str:
        """默认对话气泡模板 HTML（支持混合对话+叙事描述）"""
        return """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: "Microsoft YaHei", "PingFang SC", -apple-system, sans-serif;
            padding: 24px;
            display: inline-block;
            background: linear-gradient(180deg, #f0f4f8 0%, #e8ecf1 100%);
            min-height: 100px;
            min-width: 400px;
        }
        .dialogue-container {
            max-width: 100%;
            display: flex;
            flex-direction: column;
            white-space: pre-wrap;
        }
        
        /* 对话气泡样式 */
        .bubble {
            background: linear-gradient(135deg, #a8d8ea 0%, #89c4e1 100%);
            border-radius: 20px 20px 20px 4px;
            padding: 16px 20px;
            margin-bottom: 12px;
            box-shadow: 0 4px 15px rgba(0,0,0,0.1);
            line-height: 1.7;
            font-size: 16px;
            color: #2c3e50;
            position: relative;
            align-self: flex-start;
            max-width: 85%;
        }
        .bubble.right {
            background: linear-gradient(135deg, #b8e6cf 0%, #95d5b2 100%);
            border-radius: 20px 20px 4px 20px;
            align-self: flex-end;
        }
        
        /* 叙事描述样式（小说页面风格） */
        .narration {
            font-family: "Source Han Serif SC", "Noto Serif CJK SC", "Microsoft YaHei", serif;
            background: rgba(255, 255, 255, 0.7);
            border-left: 3px solid #c9b896;
            padding: 12px 16px;
            margin: 8px 0 16px 0;
            border-radius: 4px;
            line-height: 2;
            font-size: 15px;
            color: #555;
            font-style: italic;
            text-align: justify;
            box-shadow: 0 2px 8px rgba(0,0,0,0.05);
        }
        .narration p {
            margin-bottom: 8px;
        }
        .narration p:last-child {
            margin-bottom: 0;
        }
        
        /* 最后一个元素去除底部边距 */
        .dialogue-container > *:last-child {
            margin-bottom: 0;
        }
    </style>
</head>
<body>
    <div class="dialogue-container">{{content}}</div>
</body>
</html>"""

    def _get_default_novel_template(self) -> str:
        """默认小说页面模板 HTML"""
        return """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: "Source Han Serif SC", "Noto Serif CJK SC", "Microsoft YaHei", serif;
            display: inline-block;
            background: #f9f6f0;
            min-height: 100px;
        }
        .page {
            background: linear-gradient(to right, #f5f0e6 0%, #fffef9 50%, #f5f0e6 100%);
            padding: 40px 48px;
            border: 1px solid #e0d8c8;
            box-shadow: 
                inset 0 0 80px rgba(0,0,0,0.03),
                0 4px 20px rgba(0,0,0,0.08);
            position: relative;
            min-width: 350px;
        }
        .page::before {
            content: "";
            position: absolute;
            left: 0;
            top: 0;
            bottom: 0;
            width: 3px;
            background: linear-gradient(to bottom, #d4c4a8, #c9b896, #d4c4a8);
        }
        .content {
            font-size: 17px;
            line-height: 2;
            color: #3a3a3a;
            text-align: justify;
            white-space: pre-wrap;
        }
        /* 小说模板的段落首行缩进 */
        .content p {
            margin-bottom: 1em;
            text-indent: 2em;
        }
        .content p:last-child { margin-bottom: 0; }
        /* Markdown 标题不缩进 */
        .content h1, .content h2, .content h3, .content h4 {
            text-indent: 0;
            margin-top: 1.5em;
            margin-bottom: 0.5em;
            font-weight: 600;
        }
        .content h1 { font-size: 22px; }
        .content h2 { font-size: 20px; }
        .content h3 { font-size: 18px; }
        /* 列表不缩进 */
        .content ul, .content ol {
            text-indent: 0;
            margin-left: 2em;
            margin-bottom: 1em;
        }
        .content li { margin-bottom: 0.5em; }
        /* 引用块 */
        .content blockquote {
            text-indent: 0;
            border-left: 3px solid #c9b896;
            padding-left: 1em;
            margin: 1em 0;
            font-style: italic;
            color: #666;
        }
        /* 代码 */
        .content code {
            background: #f0ebe0;
            padding: 2px 6px;
            border-radius: 3px;
            font-family: Consolas, monospace;
            font-size: 15px;
        }
        .content pre {
            background: #f0ebe0;
            padding: 12px;
            border-radius: 6px;
            overflow-x: auto;
            margin: 1em 0;
            text-indent: 0;
        }
        .content pre code {
            background: none;
            padding: 0;
        }
        .content strong { font-weight: 600; }
        .content em { font-style: italic; }
        .content hr {
            border: none;
            border-top: 1px solid #d4c4a8;
            margin: 1.5em 0;
        }
    </style>
</head>
<body>
    <div class="page">
        <div class="content">{{content}}</div>
    </div>
</body>
</html>"""

    async def _ensure_playwright(self):
        """确保 Playwright 浏览器已安装"""
        logger.info("HTML渲染插件: 检查 Playwright 依赖...")

        async def run_command(cmd: list, desc: str) -> bool:
            try:
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await process.communicate()
                if process.returncode != 0:
                    logger.error(f"Playwright {desc} 安装失败: {stderr.decode('utf-8', errors='ignore')}")
                    return False
                return True
            except Exception as e:
                logger.error(f"执行命令失败: {e}")
                return False

        await run_command(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            "Chromium"
        )

    async def terminate(self):
        """插件停用"""
        logger.info("HTML 渲染插件已停止")

    # ==================== 检测与逻辑处理 ====================

    def _detect_render_tag(self, text: str) -> List[Tuple[str, Optional[str], str, bool]]:
        """
        检测 <render> 标签
        :return: List[(完整匹配, 模板名|None, 内容, 是否GIF)]
        """
        # 匹配：<render template="xxx" gif> 或 <render gif> 或 <render template="xxx">
        pattern = r'<render(?:\s+template=["\']([^"\']+)["\'])?(\s+gif)?\s*>(.*?)</render>'
        matches = re.findall(pattern, text, re.DOTALL)
        
        result = []
        for m in matches:
            template_name = m[0] if m[0] else None
            is_gif = bool(m[1].strip()) if m[1] else False
            content = m[2].strip()
            
            # 重建完整匹配字符串
            full_match = f'<render'
            if template_name:
                full_match += f' template="{template_name}"'
            if is_gif:
                full_match += ' gif'
            full_match += f'>{m[2]}</render>'
            
            result.append((full_match, template_name, content, is_gif))
        
        return result

    def _detect_html_tags(self, text: str) -> bool:
        """检测是否包含 HTML 标签（排除 <render>）"""
        # 常见 HTML 标签，排除 render
        html_pattern = r'<(?!render\b)(div|span|p|h[1-6]|table|ul|ol|li|a|img|style|br|hr|pre|code)\b[^>]*>'
        return bool(re.search(html_pattern, text, re.IGNORECASE))

    def _detect_dialogue(self, text: str) -> bool:
        """检测是否是对话内容（包含多个引号）"""
        quote_pattern = self.config.get("dialogue_quote_pattern", r'[""「」『』]')
        quotes = re.findall(quote_pattern, text)
        threshold = self.config.get("dialogue_quote_threshold", 2)
        # 一句对话通常有一对引号，所以 数量 >= 阈值 * 2
        return len(quotes) >= threshold * 2

    def _preserve_newlines(self, text: str) -> str:
        """
        保留文本中的换行符，将 \\n 转换为 <br> 或 <p> 标签
        """
        # 检查是否已经包含 HTML 块级标签
        if re.search(r'<(p|div|br|table|ul|ol|li|h[1-6])\b', text, re.IGNORECASE):
            return text  # HTML 内容直接返回，依赖 CSS white-space
        
        # 处理换行符
        lines = text.split('\n')
        
        if len(lines) == 1:
            return text
        
        # 将非空行包装，空行作为段落分隔
        result_parts = []
        current_paragraph = []
        
        for line in lines:
            stripped = line.strip()
            if stripped:
                current_paragraph.append(stripped)
            else:
                # 空行 = 段落分隔
                if current_paragraph:
                    result_parts.append('<br>'.join(current_paragraph))
                    current_paragraph = []
        
        # 处理最后一个段落
        if current_paragraph:
            result_parts.append('<br>'.join(current_paragraph))
        
        # 用段落标签包裹
        if len(result_parts) > 1:
            return ''.join(f'<p>{p}</p>' for p in result_parts)
        elif result_parts:
            return result_parts[0]
        else:
            return text

    # ---------- 统一换行处理 ----------
    def _nl2br(self, html: str) -> str:
        """
        A：保留空行（\\n\\n -> <br><br>）

        规则：
        - "标签间缩进换行"（例如 </div>\\n    <div>）不显示（避免换行过多）
        - "空行"（包含至少两个 \\n 的空白段）显示为 <br><br>
        - "正文换行"（文本节点中的 \\n）显示为 <br>
        - 保护 <style>/<script>/<pre>/<code>：不在其内部插入 <br>
        """
        if not html:
            return html

        # 统一换行符
        html = html.replace("\r\n", "\n").replace("\r", "\n")

        protected_blocks: List[str] = []

        def _protect(m: re.Match) -> str:
            protected_blocks.append(m.group(0))
            return f"__ASTR_HTML_RENDER_PROTECTED_{len(protected_blocks) - 1}__"

        # 保护可能包含"语法敏感换行"的块
        html = re.sub(r"<style\b[^>]*>[\s\S]*?</style>", _protect, html, flags=re.IGNORECASE)
        html = re.sub(r"<script\b[^>]*>[\s\S]*?</script>", _protect, html, flags=re.IGNORECASE)
        html = re.sub(r"<pre\b[^>]*>[\s\S]*?</pre>", _protect, html, flags=re.IGNORECASE)
        html = re.sub(r"<code\b[^>]*>[\s\S]*?</code>", _protect, html, flags=re.IGNORECASE)

        # 仅消掉"单行缩进换行"： >\n< 或 >\n    <
        # 注意：不处理 \n\n（空行），因为你要求保留空行表现
        html = re.sub(r">[ \t]*\n[ \t]*<", "><", html)

        parts = re.split(r"(<[^>]+?>)", html)
        out: List[str] = []

        for seg in parts:
            if seg.startswith("<"):
                out.append(seg)
                continue

            # 统一把 3 行以上空行压到最多 2 行（防止极端撑爆图片）
            seg = re.sub(r"\n{3,}", "\n\n", seg)

            # 纯空白段：区分"缩进换行"(忽略) vs "空行"(保留)
            if seg.strip() == "":
                if seg.count("\n") >= 2:
                    out.append("<br><br>")
                # 单个换行/缩进：忽略
                continue

            # 正文段：保留空行 + 换行
            seg = seg.replace("\n\n", "<br><br>")
            seg = seg.replace("\n", "<br>")
            out.append(seg)

        result = "".join(out)

        # 合并连续 <br>，最多保留两个
        result = re.sub(r"(?:<br>){3,}", "<br><br>", result)

        # 还原保护块
        for i, block in enumerate(protected_blocks):
            result = result.replace(f"__ASTR_HTML_RENDER_PROTECTED_{i}__", block)

        return result
    def _convert_markdown_tables(self, text: str) -> str:
        """
        将 Markdown 表格转换为 HTML 表格（用于混合内容场景）
        """
        lines = text.split('\n')
        result = []
        i = 0
        
        while i < len(lines):
            line = lines[i]
            
            # 检测表格：当前行有 |，下一行是分隔行 (| --- | --- |)
            if '|' in line and i + 1 < len(lines):
                next_line = lines[i + 1]
                if re.match(r'^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$', next_line):
                    # 收集表格行
                    table_lines = [line, next_line]
                    i += 2
                    while i < len(lines) and '|' in lines[i]:
                        # 排除分隔行
                        if not re.match(r'^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$', lines[i]):
                            table_lines.append(lines[i])
                        i += 1
                    
                    # 转换为 HTML
                    result.append(self._parse_markdown_table(table_lines))
                    continue
            
            result.append(line)
            i += 1
        
        return '\n'.join(result)

    def _parse_markdown_table(self, lines: List[str]) -> str:
        """解析 Markdown 表格并生成 HTML"""
        if len(lines) < 2:
            return '\n'.join(lines)
        
        def parse_row(line: str) -> List[str]:
            line = line.strip()
            if line.startswith('|'):
                line = line[1:]
            if line.endswith('|'):
                line = line[:-1]
            return [cell.strip() for cell in line.split('|')]
        
        # 表头
        header_cells = parse_row(lines[0])
        
        # 数据行（跳过分隔行 lines[1]）
        body_rows = [parse_row(line) for line in lines[2:]]
        
        # 生成 HTML（样式适配小说/卡片模板）
        html = ['<table style="border-collapse:collapse;width:100%;margin:1em 0;font-size:14px;">']
        
        html.append('<thead><tr>')
        for cell in header_cells:
            html.append(f'<th style="border:1px solid #d4c4a8;padding:8px 12px;background:#f5f0e6;text-align:left;font-weight:600;">{cell}</th>')
        html.append('</tr></thead>')
        
        html.append('<tbody>')
        for row in body_rows:
            html.append('<tr>')
            for cell in row:
                html.append(f'<td style="border:1px solid #d4c4a8;padding:8px 12px;background:#fffef9;">{cell}</td>')
            html.append('</tr>')
        html.append('</tbody></table>')
        
        return ''.join(html)
    def _markdown_to_html(self, text: str) -> str:
        """
        将 Markdown 转换为 HTML
        
        :param text: 可能包含 Markdown 的文本
        :return: HTML 字符串
        """
        if not MARKDOWN_AVAILABLE or _markdown_renderer is None:
            # mistune 不可用，只做基础换行处理
            return self._preserve_newlines(text)
        
        try:
            # 使用预初始化的渲染器（mistune 默认保留内联 HTML，不会破坏语义标签）
            html = _markdown_renderer(text)
            logger.debug(f"[Markdown] 渲染成功，输入长度: {len(text)}, 输出长度: {len(html)}")
            return html
        except Exception as e:
            logger.error(f"Markdown 渲染失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return self._preserve_newlines(text)

    def _select_template(self, content: str, specified_template: Optional[str] = None, user_id: Optional[str] = None) -> str:
        """选择合适的模板"""
        # 实时获取可用模板列表
        available = self._get_available_templates()
        
        # 1. 显式指定
        if specified_template and specified_template in available:
            return specified_template

        # 2. 用户自定义默认模板
        if user_id and user_id in self.user_default_template:
            user_template = self.user_default_template[user_id]
            if user_template in available:
                return user_template

        # 3. 自动对话检测：包含引号对话 → dialogue 模板
        if self.config.get("auto_dialogue_detection", True) and self._detect_dialogue(content):
            if "dialogue" in available:
                return "dialogue"

        # 4. 自动渲染模式下的默认模板（通常是 novel）
        if self.config.get("auto_render_all", False):
            fallback = self.config.get("auto_render_template", "novel")
            if fallback in available:
                return fallback

        # 5. 默认配置
        default = self.config.get("default_template", "card")
        if default in available:
            return default

        # 6. 回退到 card（内置模板，始终可用）
        return "card"

    def _apply_template(self, content: str, template_name: str) -> str:
        """应用模板"""
        # 实时从硬盘加载模板
        template = self._load_template(template_name)
        
        # 对话模板特殊处理：将引号内容转为气泡
        if template_name == "dialogue":
            content = self._format_dialogue(content)
        else:
            # Markdown 渲染
            # 注意：mistune 默认保留内联 HTML，不会破坏语义标签或已有的 HTML 结构
            if self.config.get("enable_markdown", True):
                content = self._markdown_to_html(content)
            else:
                content = self._preserve_newlines(content)
        
        # 统一换行处理
        content = self._nl2br(content)

        html = template.replace("{{content}}", content)
        return html

    def _format_dialogue(self, text: str) -> str:
        """
        将文本转换为混合对话+描述的 HTML 结构
        - 引号内容 → 对话气泡
        - 引号外内容 → 叙事描述（小说页面样式）
        """
        # 匹配双引号或直角引号内的内容
        pattern = r'[""「]([^""」]+)[""」]'
        
        parts = []
        last_end = 0
        is_right = False
        
        for match in re.finditer(pattern, text):
            # 处理引号前的描述性文字（旁白、动作描写等）
            before = text[last_end:match.start()].strip()
            if before:
                # 描述性文字用普通段落样式（非气泡）
                # 移除括号（如果有）
                before_clean = re.sub(r'^\(|\)$', '', before).strip()
                if before_clean:
                    parts.append(f'<div class="narration">{self._preserve_newlines(before_clean)}</div>')
            
            # 处理引号内的对话
            dialogue = match.group(1).strip()
            parts.append(f'<div class="bubble {"right" if is_right else ""}">{dialogue}</div>')
            is_right = not is_right
            last_end = match.end()
        
        # 处理剩余的文本
        remaining = text[last_end:].strip()
        if remaining:
            # 剩余文字也作为描述
            remaining_clean = re.sub(r'^\(|\)$', '', remaining).strip()
            if remaining_clean:
                parts.append(f'<div class="narration">{self._preserve_newlines(remaining_clean)}</div>')
        
        # 如果没有匹配到任何对话，全部作为描述
        if not parts:
            return f'<div class="narration">{self._preserve_newlines(text)}</div>'
        
        return "\n".join(parts)

    # ==================== 命令处理 ====================

    @filter.command("测试", aliases=["test"])
    async def cmd_test_render(self, event: AstrMessageEvent):
        """
        测试渲染命令
        用法: /测试 [文本内容]
        """
        # 获取消息内容并解析参数
        full_message = event.message_str.strip()
        
        # 移除 At 标记（如 [At:123456]）
        full_message = re.sub(r'\[At:\d+\]\s*', '', full_message).strip()
        
        # 按空格分割：第一部分是命令，第二部分是参数
        parts = full_message.split(None, 1)
        text = parts[1].strip() if len(parts) > 1 else ""
        
        user_id = self._get_user_id(event)
        
        # 如果没有提供文本，使用默认测试内容
        if not text:
            template = self.user_default_template.get(
                user_id, 
                self.config.get("default_template", "card")
            )
            text = self._get_default_test_content(template)
            logger.info(f"[HTML渲染] 使用默认测试内容，模板: {template}")
        
        
        # 检查是否包含 <render> 标签
        if '<render' in text:
            # 包含标签：完整解析（支持 gif、template 等属性）
            logger.info(f"[HTML渲染] 测试命令检测到 <render> 标签，启用完整解析")
            components = await self._process_text(text, user_id)
            
            if components:
                # 过滤掉空的 Plain 组件
                filtered = [c for c in components if not (isinstance(c, Plain) and not c.text.strip())]
                if filtered:
                    yield event.chain_result(filtered)
                else:
                    yield event.plain_result("❌ 渲染失败，请检查日志获取详细信息")
            else:
                yield event.plain_result("❌ 渲染失败，请检查日志获取详细信息")
        else:
            # 不包含标签：使用默认模板快速渲染
            template = self.user_default_template.get(
                user_id, 
                self.config.get("default_template", "card")
            )
            logger.info(f"[HTML渲染] 执行测试渲染，用户: {user_id}，模板: {template}，内容长度: {len(text)}")
            
            image = await self._render_content(text, template, user_id, False)
            if image:
                yield event.chain_result([image])
            else:
                yield event.plain_result("❌ 渲染失败，请检查日志获取详细信息")

    @filter.command("切换", aliases=["switch"])
    async def cmd_switch_template(self, event: AstrMessageEvent):
        """
        切换默认渲染模板
        用法: /切换 <模板名或ID>
        """
        # 获取消息内容
        full_message = event.message_str.strip()
        
        # 移除 At 标记（如 [At:123456]）
        full_message = re.sub(r'\[At:\d+\]\s*', '', full_message).strip()
        
        # 按空格分割：第一部分是命令，第二部分是参数
        parts = full_message.split(None, 1)
        arg = parts[1].strip() if len(parts) > 1 else ""
        
        user_id = self._get_user_id(event)
        current_template = self.user_default_template.get(
            user_id, 
            self.config.get("default_template", "card")
        )
        
        if not arg:
            # 显示用法和当前设置
            yield event.plain_result(
                f"🔄 切换渲染模板\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"用法: /切换 <模板名或ID>\n"
                f"当前模板: {current_template}\n\n"
                f"示例:\n"
                f"  /切换 novel\n"
                f"  /切换 1\n\n"
                f"使用 /查看 查看可用模板列表"
            )
            return
        
        # 尝试解析为 ID 或模板名
        template_name = None
        
        # 尝试作为数字 ID 解析
        try:
            template_id = int(arg)
            template_name = self.template_id_map.get(template_id)
            if template_name:
                logger.debug(f"[HTML渲染] 通过ID {template_id} 解析到模板: {template_name}")
        except ValueError:
            pass
        
        # 尝试作为模板名直接匹配
        if not template_name and arg in self.templates:
            template_name = arg
            logger.debug(f"[HTML渲染] 直接匹配模板名: {template_name}")
        
        if not template_name:
            yield event.plain_result(
                f"❌ 未找到模板: {arg}\n\n"
                f"请使用 /查看 查看可用模板列表"
            )
            return
        
        # 设置用户默认模板
        self.user_default_template[user_id] = template_name
        
        logger.info(f"[HTML渲染] 用户 {user_id} 切换默认模板: {current_template} -> {template_name}")
        yield event.plain_result(f"✅ 已切换默认模板为: {template_name}")

    @filter.command("查看", aliases=["templates"])
    async def cmd_list_templates(self, event: AstrMessageEvent):
        """
        查看可用模板列表
        用法: /查看
        """
        # 实时获取可用模板
        available = self._get_available_templates()
        
        if not available:
            yield event.plain_result("❌ 当前没有可用的模板")
            return
        
        # 实时更新模板ID映射
        self._update_template_id_map()
        
        # 获取用户当前默认模板
        user_id = self._get_user_id(event)
        current_template = self.user_default_template.get(
            user_id, 
            self.config.get("default_template", "card")
        )
        
        # 构建模板列表
        lines = ["📋 可用模板列表", "━━━━━━━━━━━━━━━━━━", ""]
        for idx in sorted(self.template_id_map.keys()):
            name = self.template_id_map[idx]
            marker = " ← 当前" if name == current_template else ""
            lines.append(f"  {idx}. {name}{marker}")
        
        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━")
        lines.append("使用方法:")
        lines.append("  /切换 <ID或名称>  切换默认模板")
        lines.append("  /测试 <文本>      测试渲染效果")
        
        yield event.plain_result("\n".join(lines))

    # ==================== 事件处理 ====================

    @filter.on_llm_request()
    async def on_llm_req(self, event: AstrMessageEvent, req: ProviderRequest):
        """注入 Prompt"""
        if not self.config.get("inject_prompt", True):
            return

        template_list = ", ".join(self._get_available_templates())
        
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

    @filter.on_llm_response(priority=40)
    async def on_llm_response(self, event: AstrMessageEvent, resp):
        """
        将模型原文存入 event.extra，
        供 decorating_result 阶段渲染与手动写历史使用。
        """
        if resp and resp.completion_text:
            event.set_extra("html_render_original_text", resp.completion_text)
            logger.debug(f"[HTML渲染] 已保存原文到 extra（长度: {len(resp.completion_text)}）")

    @filter.on_decorating_result(priority=40)
    async def on_decorating_result(self, event: AstrMessageEvent):
        """
        处理 LLM 返回结果：渲染图片 + 保存历史记录
        """
        result = event.get_result()
        if not result or not result.chain:
            return

        original_text = event.get_extra("html_render_original_text")
        if not original_text:
            return
        
        logger.debug(f"[HTML渲染] 开始处理，原始文本长度: {len(original_text)}")

        # 获取用户ID用于模板选择
        user_id = self._get_user_id(event)

        # 处理消息链，渲染图片
        new_chain = []
        for item in result.chain:
            if isinstance(item, Plain):
                components = await self._process_text(item.text, user_id)
                new_chain.extend(components)
            else:
                new_chain.append(item)

        result.chain = new_chain

        # 删除展示用的 <ctx> 标记及其内容（如果有）
        ctx_pattern = re.compile(r"<ctx>[\s\S]*?</ctx>", re.DOTALL)
        final_chain = []
        for comp in result.chain:
            if isinstance(comp, Plain):
                text = comp.text
                cleaned = ctx_pattern.sub("", text)
                if cleaned.strip():
                    final_chain.append(Plain(cleaned))
            else:
                final_chain.append(comp)
        result.chain = final_chain
        
        # ========== 关键：手动更新历史记录 ==========
        try:
            conv_mgr = self.context.conversation_manager
            unified_msg_origin = event.unified_msg_origin
            conv_id = await conv_mgr.get_curr_conversation_id(unified_msg_origin)
            
            if conv_id:
                # 获取当前历史
                conversation = await conv_mgr.get_conversation(unified_msg_origin, conv_id)
                
                if conversation:
                    import json
                    
                    # 解析现有历史
                    try:
                        history = json.loads(conversation.history) if conversation.history else []
                    except json.JSONDecodeError:
                        history = []
                    
                    # 清理原始文本（移除 render 标签但保留内容）
                    clean_text = original_text
                    # 移除 <render> 标签
                    clean_text = re.sub(r'<render[^>]*>', '', clean_text)
                    clean_text = re.sub(r'</render>', '', clean_text)
                    # 移除 <ctx> 标签
                    clean_text = re.sub(r'</?ctx>', '', clean_text)
                    clean_text = clean_text.strip()
                    
                    # 添加助手消息到历史
                    # 注意：用户消息应该已经在历史中了，我们只需要添加助手回复
                    assistant_msg = {
                        "role": "assistant",
                        "content": clean_text
                    }
                    history.append(assistant_msg)
                    
                    # 更新历史记录
                    await conv_mgr.update_conversation(
                        unified_msg_origin=unified_msg_origin,
                        conversation_id=conv_id,
                        history=history
                    )
                    
                    logger.info(f"[HTML渲染] 已手动保存历史记录，当前历史条数: {len(history)}")
        except Exception as e:
            logger.error(f"[HTML渲染] 保存历史记录失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
        
        logger.debug(f"[HTML渲染] 最终 chain 组件数: {len(result.chain)}")

    async def _process_text(self, text: str, user_id: Optional[str] = None) -> List:
        """处理文本段，解析渲染标签"""
        components = []
        
        # 1. 优先处理显式 <render> 标签
        render_matches = self._detect_render_tag(text)
        has_render_tag = bool(render_matches)    # ← 新增：标记本段是否含 <render>
        
        # ===== 自动合并模式 =====
        if render_matches and self.config.get("auto_merge_renders", False):
            logger.info(f"[HTML渲染] 检测到 {len(render_matches)} 个 <render> 标签，启用自动合并模式")
            
            # 移除所有 <render> 标签，保留内容
            merged_content = text
            
            # 收集所有指定的模板和 GIF 标志
            specified_template = None
            is_gif = False
            for full_match, template_name, content, gif_flag in render_matches:
                if template_name and not specified_template:
                    specified_template = template_name
                if gif_flag:
                    is_gif = True
                merged_content = merged_content.replace(full_match, content)
            
            final_template = specified_template if specified_template else self.config.get("merged_template", "novel")
            logger.info(f"[HTML渲染] 合并后使用模板: {final_template}, GIF: {is_gif}")
            
            image_component = await self._render_content(merged_content.strip(), final_template, user_id, is_gif)
            if image_component:
                components.append(image_component)
            else:
                logger.warning("[HTML渲染] 合并渲染失败，回退到纯文本")
                components.append(Plain(text))
            
            return components
        # ===== 合并模式结束 =====
        
        if render_matches:
            # 标准模式：按标签分割处理
            logger.info(f"[HTML渲染] 检测到 {len(render_matches)} 个 <render> 标签，使用标准分割模式")
            remaining_text = text
            for full_match, template_name, content, is_gif in render_matches:
                # 分割：标签前的文本
                parts = remaining_text.split(full_match, 1)
                before_text = parts[0]
                remaining_text = parts[1] if len(parts) > 1 else ""

                # 添加标签前的纯文本
                if before_text and before_text.strip():
                    components.append(Plain(before_text))

                # 渲染标签内容
                image_component = await self._render_content(content, template_name, user_id, is_gif)
                if image_component:
                    components.append(image_component)
                else:
                    components.append(Plain(f"[渲染失败]\n{content}"))

            # 添加剩余文本
            if remaining_text and remaining_text.strip():
                components.append(Plain(remaining_text))
        
        elif self.config.get("enable_auto_detect", True) and self._detect_html_tags(text):
            # 2. 自动检测 HTML 标签
            if has_render_tag:
                # 已出现 <render>，剩余文本保持纯文本，避免再次整体渲染
                logger.debug("[HTML渲染] 已出现 <render> 标签，跳过自动 HTML 渲染")
                components.append(Plain(text))
            else:
                logger.info("[HTML渲染] 检测到 HTML 标签，触发自动渲染")
                image_component = await self._render_content(text, None, user_id, False)
                if image_component:
                    components.append(image_component)
                else:
                    components.append(Plain(text))
        
        else:
            # 3. 检查是否启用了自动渲染所有内容
            if self.config.get("auto_render_all", False):
                # 检查文本长度阈值
                min_length = self.config.get("auto_render_min_length", 20)
                if len(text.strip()) >= min_length:
                    logger.info("[HTML渲染] 自动渲染模式：触发渲染")
                    image_component = await self._render_content(text, None, user_id, False)
                    if image_component:
                        components.append(image_component)
                    else:
                        logger.warning("[HTML渲染] 自动渲染失败，回退到纯文本")
                        components.append(Plain(text))
                else:
                    logger.debug(f"[HTML渲染] 文本长度 {len(text.strip())} < {min_length}，跳过渲染")
                    components.append(Plain(text))
            else:
                logger.debug("[HTML渲染] 未匹配任何渲染条件，保持纯文本")
                components.append(Plain(text))
        
        return components

    async def _render_content(self, content: str, specified_template: Optional[str], user_id: Optional[str] = None, is_gif: bool = False) -> Optional[Image]:
        """执行渲染"""
        try:
            template_name = self._select_template(content, specified_template, user_id)
            logger.debug(f"HTML渲染: 使用模板 {template_name}, GIF模式: {is_gif}")
            
            full_html = self._apply_template(content, template_name)
            
            # 根据是否 GIF 选择文件扩展名
            ext = ".gif" if is_gif else ".png"
            filename = f"render_{uuid.uuid4().hex[:12]}{ext}"
            output_path = os.path.join(self.IMAGE_CACHE_DIR, filename)
            
            width = self.config.get("render_width", 600)
            # GIF 模式降低 scale 以减小文件
            scale = 1 if is_gif else self.config.get("render_scale", 2)
            
            success = await html_to_image_playwright(
                html_content=full_html,
                output_image_path=output_path,
                scale=scale,
                width=width,
                is_gif=is_gif,
                duration=self.gif_duration,
                fps=self.gif_fps
            )
            
            if success and os.path.exists(output_path):
                return Image.fromFileSystem(output_path)
            else:
                return None
                
        except Exception as e:
            logger.error(f"渲染过程异常: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
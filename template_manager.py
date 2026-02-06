# template_manager.py
# 模板加载、管理、内置默认模板

import os
from typing import Dict, List

from astrbot.api import logger


class TemplateManager:
    """模板管理器：负责加载、缓存、查询模板"""

    def __init__(self, template_dir: str):
        self.TEMPLATE_DIR = template_dir
        self.templates: Dict[str, str] = {}
        self.template_id_map: Dict[int, str] = {}

    # ==================== 加载与管理 ====================

    async def load_templates(self):
        """加载所有模板文件"""
        if not os.path.exists(self.TEMPLATE_DIR):
            try:
                os.makedirs(self.TEMPLATE_DIR, exist_ok=True)
            except Exception:
                pass

        if os.path.exists(self.TEMPLATE_DIR):
            for filename in os.listdir(self.TEMPLATE_DIR):
                if filename.endswith(".html"):
                    template_name = filename[:-5]
                    filepath = os.path.join(self.TEMPLATE_DIR, filename)
                    try:
                        with open(filepath, "r", encoding="utf-8") as f:
                            self.templates[template_name] = f.read()
                        logger.info(f"已加载模板: {template_name}")
                    except Exception as e:
                        logger.error(f"加载模板 {filename} 失败: {e}")

        if not self.templates:
            logger.info("未找到外部模板文件，加载内置默认模板")
            self.create_default_templates()

    def create_default_templates(self):
        """创建内置默认模板（保底逻辑）"""
        self.templates["card"] = self.get_default_card_template()
        self.templates["dialogue"] = self.get_default_dialogue_template()
        self.templates["novel"] = self.get_default_novel_template()

        try:
            os.makedirs(self.TEMPLATE_DIR, exist_ok=True)
            for name in ("card", "dialogue", "novel"):
                filepath = os.path.join(self.TEMPLATE_DIR, f"{name}.html")
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(self.templates[name])
        except Exception as e:
            logger.warning(f"无法写入默认模板文件: {e}")

    def get_available_templates(self) -> List[str]:
        """获取可用模板列表（实时扫描目录）"""
        templates = set()

        if os.path.exists(self.TEMPLATE_DIR):
            for filename in os.listdir(self.TEMPLATE_DIR):
                if filename.endswith(".html"):
                    templates.add(filename[:-5])

        # 始终包含内置模板
        templates.update(["card", "dialogue", "novel"])
        return sorted(templates)

    def load_template(self, template_name: str) -> str:
        """从硬盘实时加载模板（每次调用都读取文件）"""
        filepath = os.path.join(self.TEMPLATE_DIR, f"{template_name}.html")

        if os.path.exists(filepath):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
                    logger.debug(f"[HTML渲染] 已从硬盘加载模板: {template_name}")
                    return content
            except Exception as e:
                logger.error(f"[HTML渲染] 读取模板 {template_name} 失败: {e}")

        # 回退到内置模板
        logger.debug(f"[HTML渲染] 使用内置模板: {template_name}")
        builtin_map = {
            "card": self.get_default_card_template,
            "dialogue": self.get_default_dialogue_template,
            "novel": self.get_default_novel_template,
        }
        getter = builtin_map.get(template_name)
        if getter:
            return getter()

        logger.warning(f"[HTML渲染] 未知模板 {template_name}，回退到 card")
        return self.get_default_card_template()

    def update_template_id_map(self):
        """更新模板 ID 映射（按名称排序，实时扫描）"""
        available = self.get_available_templates()
        self.template_id_map = {
            idx: name for idx, name in enumerate(available, start=1)
        }
        logger.debug(f"模板ID映射已更新: {self.template_id_map}")

    # ==================== 内置默认模板 ====================

    @staticmethod
    def get_default_card_template() -> str:
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
        .card * {
            white-space: pre-wrap;
        }
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

    @staticmethod
    def get_default_dialogue_template() -> str:
        """默认对话气泡模板 HTML"""
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
        .dialogue-container > *:last-child {
            margin-bottom: 0;
        }
    </style>
</head>
<body>
    <div class="dialogue-container">{{content}}</div>
</body>
</html>"""

    @staticmethod
    def get_default_novel_template() -> str:
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
        .content p {
            margin-bottom: 1em;
            text-indent: 2em;
        }
        .content p:last-child { margin-bottom: 0; }
        .content h1, .content h2, .content h3, .content h4 {
            text-indent: 0;
            margin-top: 1.5em;
            margin-bottom: 0.5em;
            font-weight: 600;
        }
        .content h1 { font-size: 22px; }
        .content h2 { font-size: 20px; }
        .content h3 { font-size: 18px; }
        .content ul, .content ol {
            text-indent: 0;
            margin-left: 2em;
            margin-bottom: 1em;
        }
        .content li { margin-bottom: 0.5em; }
        .content blockquote {
            text-indent: 0;
            border-left: 3px solid #c9b896;
            padding-left: 1em;
            margin: 1em 0;
            font-style: italic;
            color: #666;
        }
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

    # ==================== 测试内容 ====================

    @staticmethod
    def get_default_test_content(template_name: str) -> str:
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
    
    @staticmethod
    def get_gif_test_content() -> str:
        """获取 GIF 弹幕动画测试内容"""
        return '''<render gif>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
    font-family: "Source Han Serif SC", "Noto Serif CJK SC", "Microsoft YaHei", serif;
    display: inline-block;
    background: #f9f6f0;
}
.page {
    background: linear-gradient(to right, #f5f0e6 0%, #fffef9 50%, #f5f0e6 100%);
    padding: 40px 48px 28px 48px;
    border: 1px solid #e0d8c8;
    box-shadow: inset 0 0 80px rgba(0,0,0,0.03), 0 4px 20px rgba(0,0,0,0.08);
    position: relative;
    min-width: 500px;
    max-width: 600px;
}
.page::before {
    content: "";
    position: absolute;
    left: 0; top: 0; bottom: 0;
    width: 3px;
    background: linear-gradient(to bottom, #d4c4a8, #c9b896, #d4c4a8);
}
.content {
    font-size: 17px;
    line-height: 2;
    color: #3a3a3a;
    text-align: justify;
}
.content p {
    margin-bottom: 1em;
    text-indent: 2em;
}
.content p:last-of-type { margin-bottom: 0; }
.chapter-title {
    text-indent: 0;
    font-size: 20px;
    font-weight: 600;
    margin-bottom: 0.8em;
    color: #2c3e50;
}
.scene {
    text-indent: 0;
    background: rgba(201,184,150,0.1);
    border-left: 3px solid #c9b896;
    padding: 10px 14px;
    margin: 1em 0;
    border-radius: 4px;
    font-style: italic;
    color: #666;
}
.dialogue {
    text-indent: 0;
    position: relative;
    padding-left: 1.5em;
    margin: 0.5em 0;
}
.dialogue::before {
    content: "";
    position: absolute;
    left: 0; top: 0.6em;
    width: 6px; height: 6px;
    background: #c9b896;
    border-radius: 50%;
}
.inner {
    text-indent: 2em;
    color: #888;
    font-style: italic;
    font-size: 16px;
}
.aside-text {
    text-indent: 0;
    text-align: center;
    color: #aaa;
    font-size: 14px;
    margin: 1.5em 0 0.5em 0;
    letter-spacing: 2px;
}
.divider {
    border: none;
    border-top: 1px solid #d4c4a8;
    margin: 1.5em 0;
}
.danmu-section {
    margin-top: 28px;
    border-radius: 12px;
    overflow: hidden;
    box-shadow: 0 8px 32px rgba(0,0,0,0.08);
    font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
}
.danmu-header {
    background: linear-gradient(135deg, #667eea 0%, #764ba2 50%, #f093fb 100%);
    padding: 12px 20px;
    display: flex;
    justify-content: center;
    align-items: center;
    gap: 12px;
}
.danmu-title {
    font-size: 15px;
    font-weight: 600;
    color: #fff;
    letter-spacing: 4px;
    text-shadow: 0 2px 4px rgba(0,0,0,0.2);
}
.danmu-icon {
    font-size: 16px;
    animation: bounce 1.5s ease-in-out infinite;
}
@keyframes bounce {
    0%, 100% { transform: scale(1); }
    50% { transform: scale(1.2); }
}
.danmu-area {
    position: relative;
    overflow: hidden;
    width: 100%;
    height: 160px;
    background: linear-gradient(180deg, #f8f4eb 0%, #fdf6e3 100%);
    border: 1px solid #e0d6c2;
    border-top: none;
    border-radius: 0 0 12px 12px;
    mask-image: linear-gradient(90deg, transparent, #000 5%, #000 95%, transparent);
    -webkit-mask-image: linear-gradient(90deg, transparent, #000 5%, #000 95%, transparent);
}
.danmu-line {
    position: absolute;
    left: 100%;
    top: calc(var(--row) * 45px + 20px);
    white-space: nowrap;
    padding: 6px 16px;
    border-radius: 50px;
    background: rgba(255, 255, 255, 0.7);
    backdrop-filter: blur(5px);
    border: 1px solid rgba(255, 255, 255, 0.6);
    font-size: 13px;
    color: var(--color);
    font-weight: 600;
    box-shadow: 0 2px 10px rgba(0,0,0,0.05);
    animation: scrollLeft 8s linear infinite;
    animation-delay: var(--delay);
}
@keyframes scrollLeft {
    0% { transform: translateX(0); }
    100% { transform: translateX(calc(-100% - 700px)); }
}
</style>

<div class="page">
    <div class="content">
        <p class="chapter-title">第三章 · 旧书与新雨</p>
        <div class="scene">细雨敲窗，图书馆的铜质台灯在长桌上投下一圈暖黄的光晕。空气中弥漫着旧纸页与雨水交织的气息，世界仿佛被按下了静音键。</div>
        <p>林晓翻开那本泛黄的《浮生六记》，指尖拂过沈复的墨迹时，忽然感到一阵微凉——是窗缝里钻进来的风，裹着三月的雨腥气。</p>
        <p>她不由得打了个寒颤。</p>
        <p class="dialogue">「冷吗？」</p>
        <p>顾言不知何时已站在身旁，手里多了一杯冒着热气的可可。纸杯上印着图书馆的标志——一只蹲在书堆上打盹的猫。</p>
        <p class="inner">又是这样。每次她还没开口，他就已经知道了。这种默契让她觉得温暖，又隐隐有些不安。</p>
        <p>林晓接过杯子，指尖在交递的瞬间碰到了他的手背，触感微凉而干燥。</p>
        <p class="dialogue">「谢谢……你的手好凉。」</p>
        <p class="dialogue">「刚从外面回来。」顾言在她对面坐下，雨水还挂在他的发梢上，「今天在看什么？」</p>
        <p class="dialogue">「《浮生六记》。沈复写芸娘那段，每次读都觉得……世间怎么会有人把日常写得这么动人。」</p>
        <p>顾言低头看了一眼，念出那行字：</p>
        <p class="dialogue" style="color:#8b5a2b; font-style:italic;">「——情之所钟，虽丑不嫌。」</p>
        <p>他念完后抬起头，目光穿过热气缭绕的可可杯沿，落在林晓脸上。</p>
        <p class="dialogue">「不过我觉得，他更厉害的是把"记得"这件事，写成了一种深情。」</p>
        <p class="inner">心跳漏了半拍。林晓低下头，假装去翻书页，耳朵却已经红透了。</p>
        <p>窗外的雨突然大了起来。有人推门进来，带进一阵湿漉漉的风，吹得书页哗啦啦地翻动。</p>
        <p>林晓伸手去按住书页，顾言也同时伸了手。</p>
        <p>两只手叠在了同一页纸上。</p>
        <p>谁都没有先抽开。</p>
        <hr class="divider">
        <p class="aside-text">—— 窗外的雨声，忽然变得很远很远 ——</p>
    </div>

    <div class="danmu-section">
        <div class="danmu-header">
            <span class="danmu-icon">💬</span>
            <span class="danmu-title">本章说 · 实时互动</span>
            <span class="danmu-icon">✨</span>
        </div>
        <div class="danmu-area">
            <div class="danmu-line" style="--row:0; --color:#8b5a2b; --delay:-2s;">追更狂魔：手叠在一起了啊啊啊！！</div>
            <div class="danmu-line" style="--row:0; --color:#8b4513; --delay:-7s;">纯路人：沈复那句念出来杀伤力太大了</div>
            <div class="danmu-line" style="--row:0; --color:#4a6fa5; --delay:-12s;">迟到的白开水：这氛围感绝了</div>
            <div class="danmu-line" style="--row:1; --color:#556b2f; --delay:-3.5s;">嗑学家：情之所钟虽丑不嫌——他在暗示！！</div>
            <div class="danmu-line" style="--row:1; --color:#6b5b4f; --delay:-8s;">熬夜修仙：可可杯上的猫好可爱这个细节</div>
            <div class="danmu-line" style="--row:1; --color:#8b5a2b; --delay:-14s;">梦游小狗：谁都没有先抽开（尖叫）</div>
            <div class="danmu-line" style="--row:2; --color:#4a6fa5; --delay:-2.5s;">眼神已离线：泪目了😭 这就是文学的力量</div>
            <div class="danmu-line" style="--row:2; --color:#8b4513; --delay:-9s;">赛博薄荷糖：作者你故意的吧，雨声变远那句</div>
            <div class="danmu-line" style="--row:2; --color:#556b2f; --delay:-15s;">-打工崽-：催更催更！后面呢？！</div>
        </div>
    </div>
</div>
</render>'''
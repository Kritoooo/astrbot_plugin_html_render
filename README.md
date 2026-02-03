\# AstrBot HTML 渲染插件



\[!\[AstrBot](https://img.shields.io/badge/AstrBot-Plugin-blue)](https://github.com/Soulter/AstrBot)

\[!\[Python](https://img.shields.io/badge/Python-3.9+-green)](https://www.python.org/)

\[!\[License](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)



将 AI 返回的文本内容渲染成精美图片发送，支持多种预设模板、GIF 动画、Markdown 语法，让聊天体验更加丰富多彩。



\## ✨ 功能特性



\- 🎨 \*\*多模板支持\*\* - 内置卡片、对话气泡、小说页面三种精美模板

\- 📝 \*\*Markdown 渲染\*\* - 自动解析 Markdown 语法（标题、列表、代码块等）

\- 🎬 \*\*GIF 动画\*\* - 支持将 CSS 动画渲染为 GIF 动图

\- 🔄 \*\*热更新模板\*\* - 修改模板文件后立即生效，无需重启

\- 🤖 \*\*AI 自动触发\*\* - 注入提示词让 AI 自动使用渲染功能

\- 👤 \*\*用户个性化\*\* - 每个用户可设置自己的默认模板

\- 🎯 \*\*智能检测\*\* - 自动识别对话内容并使用对话模板



\## 📦 安装



\### 1. 安装插件



在 AstrBot 管理面板中搜索 `astrbot\_plugin\_html\_render` 并安装，或手动克隆到插件目录：



```bash

cd data/plugins

git clone https://github.com/你的用户名/astrbot\_plugin\_html\_render.git

```



\### 2. 安装依赖



```bash

\# 核心依赖（必需）

pip install playwright

playwright install chromium



\# 可选依赖

pip install Pillow    # GIF 动画支持

pip install mistune   # Markdown 渲染支持

```



\## ⚙️ 配置说明



在 AstrBot 管理面板中配置插件，或直接编辑配置文件：



```yaml

\# 基础配置

inject\_prompt: true          # 是否向 AI 注入渲染提示词

default\_template: "card"     # 默认模板：card / dialogue / novel



\# 渲染参数

render\_width: 600            # 图片宽度（像素）

render\_scale: 2              # 缩放比例（影响清晰度）



\# GIF 配置

gif\_duration: 3.0            # GIF 动画时长（秒）

gif\_fps: 15                  # GIF 帧率



\# Markdown 配置

enable\_markdown: true        # 启用 Markdown 渲染



\# 自动检测配置

auto\_dialogue\_detection: true      # 自动检测对话内容

dialogue\_quote\_pattern: '\[""「」『』]'  # 对话引号匹配规则

dialogue\_quote\_threshold: 2        # 触发对话模板的引号对数



\# 自动渲染配置

enable\_auto\_detect: true           # 自动检测 HTML 标签并渲染

auto\_render\_all: false             # 是否渲染所有 AI 回复

auto\_render\_min\_length: 20         # 自动渲染的最小文本长度

auto\_render\_template: "novel"      # 自动渲染使用的模板



\# 合并渲染配置

auto\_merge\_renders: false          # 多个 render 标签是否合并为一张图

merged\_template: "novel"           # 合并渲染使用的模板

```



\## 📖 使用方法



\### 用户命令



| 命令 | 说明 | 示例 |

|------|------|------|

| `/测试 <文本>` | 测试渲染效果 | `/测试 你好世界` |

| `/切换 <模板>` | 切换默认模板 | `/切换 novel` 或 `/切换 1` |

| `/查看` | 查看可用模板列表 | `/查看` |

| `/重载模板` | 从硬盘刷新模板 | `/重载模板` |



\### AI 自动渲染



当 `inject\_prompt` 启用时，AI 会自动学习使用 `<render>` 标签：



```

用户：帮我写一首诗

AI：

<render template="novel">

春风拂柳绿丝绦，

夏雨润荷红更娇。

秋月照庭霜满地，

冬雪飘窗梦已遥。

</render>

```



\### 手动触发渲染



在消息中使用 `<render>` 标签：



```html

<!-- 使用默认模板 -->

<render>

这段文字会被渲染成图片

</render>



<!-- 指定模板 -->

<render template="dialogue">

"你好啊！"

"你好，很高兴认识你。"

</render>



<!-- 生成 GIF 动画 -->

<render gif>

<div class="animate">动画内容</div>

<style>

.animate { animation: fade 2s infinite; }

@keyframes fade { 0%,100% { opacity: 0; } 50% { opacity: 1; } }

</style>

</render>



<!-- 指定模板 + GIF -->

<render template="card" gif>

带动画的卡片

</render>

```



\## 🎨 内置模板



\### 1. Card（卡片）

简洁优雅的卡片样式，适合通用内容展示。



!\[card预览](docs/card-preview.png)



\### 2. Dialogue（对话）

聊天气泡样式，自动将引号内容转为左右交替的气泡，引号外的内容作为叙事描述。



!\[dialogue预览](docs/dialogue-preview.png)



\### 3. Novel（小说）

仿书页样式，适合长文本、故事、诗歌等文学内容。



!\[novel预览](docs/novel-preview.png)



\## 🔧 自定义模板



模板文件位于 `data/plugin\_data/astrbot\_plugin\_html\_render/templates/` 目录。



\### 创建新模板



1\. 在模板目录创建 `my\_template.html` 文件

2\. 使用 `{{content}}` 占位符标记内容插入位置

3\. 保存后立即可用，无需重启



\### 模板示例



```html

<!DOCTYPE html>

<html>

<head>

&nbsp;   <meta charset="UTF-8">

&nbsp;   <style>

&nbsp;       body {

&nbsp;           font-family: "Microsoft YaHei", sans-serif;

&nbsp;           padding: 20px;

&nbsp;           background: linear-gradient(135deg, #667eea, #764ba2);

&nbsp;       }

&nbsp;       .content {

&nbsp;           background: white;

&nbsp;           border-radius: 12px;

&nbsp;           padding: 24px;

&nbsp;           box-shadow: 0 4px 20px rgba(0,0,0,0.15);

&nbsp;       }

&nbsp;   </style>

</head>

<body>

&nbsp;   <div class="content">{{content}}</div>

</body>

</html>

```



\### 使用自定义模板



```html

<render template="my\_template">

使用自定义模板渲染的内容

</render>

```



或通过命令设为默认：



```

/切换 my\_template

```



\## 📁 目录结构



```

data/plugin\_data/astrbot\_plugin\_html\_render/

├── templates/              # 模板目录（可自定义）

│   ├── card.html

│   ├── dialogue.html

│   └── novel.html

└── html\_render\_cache/      # 图片缓存目录（自动生成）

&nbsp;   └── render\_xxx.png

```



\## ❓ 常见问题



\### Q: 图片生成失败？

\*\*A:\*\* 检查 Playwright 是否正确安装：

```bash

playwright install chromium

```



\### Q: 修改模板后没有生效？

\*\*A:\*\* 插件已支持热更新。如果仍无效，请检查：

\- 文件名是否正确（`xxx.html`）

\- 文件编码是否为 UTF-8

\- HTML 语法是否正确



\### Q: GIF 功能不可用？

\*\*A:\*\* 安装 Pillow：

```bash

pip install Pillow

```



\### Q: Markdown 没有渲染？

\*\*A:\*\* 安装 mistune：

```bash

pip install mistune

```



\### Q: 如何让 AI 停止自动使用渲染？

\*\*A:\*\* 在配置中将 `inject\_prompt` 设为 `false`。



\### Q: 图片太小/太模糊？

\*\*A:\*\* 调整配置：

\- `render\_width`: 增加宽度（如 800）

\- `render\_scale`: 增加缩放比例（如 3）


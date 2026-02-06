# renderer.py
# HTML → 图片渲染（Playwright），支持静态 PNG 与 GIF 动画（时间轴跳帧）

import asyncio
import io
import os
from typing import Optional

from astrbot.api import logger

# GIF 合成支持
try:
    from PIL import Image as PILImage, ImageChops
    GIF_AVAILABLE = True
except ImportError:
    GIF_AVAILABLE = False
    logger.warning(
        "HTML渲染插件: Pillow 未安装，GIF 动画功能将不可用。"
        "可通过 pip install Pillow 安装。"
    )


async def _detect_animated_region(
    page,
    scale: int,
    viewport_width: int,
    viewport_height: int,
) -> Optional[dict]:
    """
    检测页面中的动画区域。
    策略1：JS 查找带 animation 的元素的公共父容器
    策略2：像素对比回退
    """
    # ====== 策略1：JS 查找动画容器 ======
    try:
        clip_from_js = await page.evaluate("""() => {
            const allEls = document.querySelectorAll('*');
            const animatedEls = [];
            for (const el of allEls) {
                const style = getComputedStyle(el);
                if (style.animationName && style.animationName !== 'none') {
                    animatedEls.push(el);
                }
            }
            if (animatedEls.length === 0) return null;

            let container = animatedEls[0].parentElement;
            while (container && container !== document.body) {
                const style = getComputedStyle(container);
                if (style.overflow === 'hidden' || style.overflowX === 'hidden') {
                    break;
                }
                container = container.parentElement;
            }
            if (!container || container === document.body) {
                container = animatedEls[0].parentElement;
            }

            const rect = container.getBoundingClientRect();
            if (rect.width <= 0 || rect.height <= 0) return null;

            return {
                x: rect.x,
                y: rect.y,
                width: rect.width,
                height: rect.height
            };
        }""")

        if clip_from_js:
            pad = 10
            clip = {
                "x": max(0, clip_from_js["x"] - pad),
                "y": max(0, clip_from_js["y"] - pad),
                "width": min(clip_from_js["width"] + pad * 2, viewport_width),
                "height": min(clip_from_js["height"] + pad * 2, viewport_height),
            }

            page_area = viewport_width * viewport_height
            clip_area = clip["width"] * clip["height"]
            ratio = clip_area / page_area

            if ratio > 0.8:
                logger.info(f"[GIF] 动画容器占页面 {ratio*100:.0f}%，不裁切")
                return None

            logger.info(
                f"[GIF] JS定位动画容器: {clip['width']:.0f}×{clip['height']:.0f} CSS px "
                f"(占比 {ratio*100:.1f}%)"
            )
            return clip

    except Exception as e:
        logger.warning(f"[GIF] JS定位失败: {e}")

    # ====== 策略2：像素对比回退 ======
    try:
        # 用时间轴跳帧方式对比两个时间点
        has_animations = await page.evaluate("document.getAnimations().length > 0")
        if has_animations:
            await page.evaluate("""() => {
                document.getAnimations().forEach(a => {
                    a.pause();
                    a.currentTime = 0;
                });
            }""")
            await asyncio.sleep(0.1)
            raw_a = await page.screenshot(type="png")
            shot_a = PILImage.open(io.BytesIO(raw_a)).convert("RGB")

            await page.evaluate("""() => {
                document.getAnimations().forEach(a => {
                    a.currentTime = 2000;
                });
            }""")
            await asyncio.sleep(0.1)
            raw_b = await page.screenshot(type="png")
            shot_b = PILImage.open(io.BytesIO(raw_b)).convert("RGB")

            # 恢复播放
            await page.evaluate("document.getAnimations().forEach(a => a.play())")

            diff = ImageChops.difference(shot_a, shot_b).convert("L")
            diff = diff.point(lambda p: 255 if p > 3 else 0)
            bbox = diff.getbbox()

            if bbox:
                page_area = shot_a.width * shot_a.height
                region_w = bbox[2] - bbox[0]
                region_h = bbox[3] - bbox[1]
                ratio = region_w * region_h / page_area

                if ratio > 0.8:
                    logger.info(f"[GIF] 像素变化区域占页面 {ratio*100:.0f}%，不裁切")
                    return None

                pad = int(30 * scale)
                clip = {
                    "x": max(0, bbox[0] - pad) / scale,
                    "y": max(0, bbox[1] - pad) / scale,
                    "width": min(region_w + pad * 2, shot_a.width) / scale,
                    "height": min(region_h + pad * 2, shot_a.height) / scale,
                }
                logger.info(
                    f"[GIF] 像素对比定位: {clip['width']:.0f}×{clip['height']:.0f} CSS px"
                )
                return clip

        logger.info("[GIF] 未检测到动画")
        return None

    except Exception as e:
        logger.warning(f"[GIF] 像素对比失败: {e}")
        return None


async def _get_animation_duration(page) -> float:
    """获取页面中最长动画的周期（毫秒）"""
    try:
        duration_ms = await page.evaluate("""() => {
            const anims = document.getAnimations();
            if (anims.length === 0) return 3000;
            let maxDuration = 0;
            for (const a of anims) {
                const timing = a.effect.getComputedTiming();
                const d = timing.duration || 0;
                if (d > maxDuration) maxDuration = d;
            }
            return maxDuration || 3000;
        }""")
        return float(duration_ms)
    except Exception:
        return 3000.0


async def html_to_image_playwright(
    html_content: str,
    output_image_path: str,
    scale: int = 2,
    width: int = 600,
    is_gif: bool = False,
    duration: float = 3.0,
    fps: int = 15,
) -> bool:
    """
    使用 Playwright 将 HTML 内容渲染成图片。
    GIF 模式使用时间轴跳帧：暂停动画 → seek到每帧时间点 → 截图，零等待。
    """
    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch()
            context = await browser.new_context(
                device_scale_factor=scale,
                viewport={"width": width, "height": 800},
            )
            page = await context.new_page()
            await page.set_content(html_content, wait_until="networkidle")
            await asyncio.sleep(0.3)

            if not is_gif:
                await page.screenshot(path=output_image_path, full_page=True)
            else:
                if not GIF_AVAILABLE:
                    logger.warning("Pillow 未安装，回退到静态截图")
                    await page.screenshot(
                        path=output_image_path, full_page=True,
                    )
                else:
                    import time

                    # 展开视口到完整内容高度
                    content_h = await page.evaluate("document.body.scrollHeight")
                    full_height = max(content_h, 200)
                    await page.set_viewport_size(
                        {"width": width, "height": full_height}
                    )
                    await asyncio.sleep(0.3)

                    # 1. 先截完整静态图
                    static_path = output_image_path  # .png
                    await page.screenshot(path=static_path, full_page=True)
                    logger.info(f"[GIF] 已生成静态全页截图")

                    # 2. 让动画跑一下再检测区域
                    await asyncio.sleep(0.5)

                    # 3. 检测动画区域
                    clip = await _detect_animated_region(
                        page, scale, width, full_height
                    )

                    # 4. 如果有动画区域，用时间轴跳帧录制
                    if clip:
                        gif_path = os.path.splitext(output_image_path)[0] + ".gif"

                        # 获取动画实际周期
                        anim_duration_ms = await _get_animation_duration(page)
                        # 录制时长：取配置时长和动画周期的较小值
                        record_duration_ms = min(duration * 1000, anim_duration_ms)

                        frame_count = int(record_duration_ms / 1000 * fps)
                        frame_count = max(frame_count, 10)  # 至少10帧
                        frame_interval_ms = record_duration_ms / frame_count

                        logger.info(
                            f"[GIF] 时间轴跳帧模式：动画周期={anim_duration_ms:.0f}ms，"
                            f"录制={record_duration_ms:.0f}ms，{frame_count}帧，"
                            f"裁切={clip['width']:.0f}×{clip['height']:.0f}"
                        )

                        # 暂停所有动画
                        await page.evaluate("document.getAnimations().forEach(a => a.pause())")

                        frames = []
                        record_start = time.time()

                        for i in range(frame_count):
                            # 跳到指定时间点
                            target_time = i * frame_interval_ms
                            await page.evaluate(
                                f"document.getAnimations().forEach(a => a.currentTime = {target_time})"
                            )
                            # 给渲染引擎一点时间应用变化
                            await asyncio.sleep(0.02)

                            frame_bytes = await page.screenshot(
                                clip=clip, type="jpeg", quality=85
                            )
                            frame_img = PILImage.open(
                                io.BytesIO(frame_bytes)
                            ).convert("RGB")
                            frame_img = frame_img.convert(
                                "P", palette=PILImage.ADAPTIVE, colors=256
                            )
                            frames.append(frame_img)

                        # 恢复播放
                        await page.evaluate("document.getAnimations().forEach(a => a.play())")

                        record_time = time.time() - record_start
                        logger.info(
                            f"[GIF] 跳帧完成：{len(frames)}帧，耗时{record_time:.1f}s"
                        )

                        out_dir = os.path.dirname(output_image_path)
                        if out_dir:
                            os.makedirs(out_dir, exist_ok=True)

                        if frames:
                            compose_start = time.time()
                            # 每帧实际显示时长
                            frame_display_ms = int(frame_interval_ms)
                            frames[0].save(
                                gif_path,
                                save_all=True,
                                append_images=frames[1:],
                                duration=frame_display_ms,
                                loop=0,
                                optimize=True,
                            )
                            compose_time = time.time() - compose_start
                            logger.info(f"[GIF] 合成完成，耗时{compose_time:.1f}s")
                    else:
                        logger.info("[GIF] 未检测到动画区域，仅输出静态图")

            await browser.close()
            return True
    except Exception as e:
        logger.error(f"Playwright 渲染失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False
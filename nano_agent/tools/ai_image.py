"""AI 图像生成 — 多后端: diffusers 本地优先, pollinations.ai 降级。

优先级:
  1. diffusers 本地 (需 torch + diffusers; 稳定、无外网依赖) — 首选
     (2026-08-25 切换: pollinations 从大陆到 Cloudflare 的 TLS 握手约 1/3 概率被丢弃,
      实测 14 次请求失败 5 次; diffusers 慢(~5min/512px CPU)但可靠)
  2. pollinations.ai (云端 GPU, 快, 免费; diffusers 失败时降级, 带重试)
  3. Stability AI API (需 STABILITY_API_KEY; 目前不参与自动检测)

AI_IMAGE_BACKEND 环境变量可强制指定后端 (diffusers / pollinations / stability)。
"""

import json
import logging
import os
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("nano_agent.tools.ai_image")

# 懒加载全局 pipeline + 推理锁 (7.4GB 内存无 swap, 并发推理会 OOM, 必须串行)
_pipeline = None
_pipeline_lock = threading.Lock()


class AIImage:
    # 工具注册声明
    TOOLS = [
        ("ai_image", "AI photo/art generation. ONLY for: realistic photos, digital art, animals, people, landscapes. "
         "NEVER for: geometric shapes, cubes, diagrams, charts — use generate_chart or mermaid_chart instead. "
         "Prompt must be in English.", "generate_image",
         {"prompt": {"type": "string", "description": "Visual description in English. NOT for geometric shapes or diagrams."},
          "negative_prompt": {"type": "string", "description": "What to exclude"},
          "width": {"type": "integer", "description": "Image width (default: 512)"},
          "height": {"type": "integer", "description": "Image height (default: 512)"}},
         ["prompt"]),
    ]

    def __init__(self, work_dir: str, charts_dir: str = ""):
        if charts_dir:
            self.charts_dir = Path(charts_dir)
        else:
            web_static = Path(__file__).parent.parent.parent / "web" / "static"
            self.charts_dir = web_static / "charts"
        self.charts_dir.mkdir(parents=True, exist_ok=True)
        self._backend = None  # 延迟检测
        self._diffusers_broken = False  # 本地 pipeline 加载失败后不再重试

    def _detect_backend(self) -> str:
        """检测可用的后端。默认 diffusers 本地（稳定），
        可用 AI_IMAGE_BACKEND 环境变量覆盖；主后端失败时
        在 generate_image 里降级另一后端。"""
        if self._backend:
            return self._backend

        backend = os.environ.get("AI_IMAGE_BACKEND", "").strip().lower() or "diffusers"
        if backend not in ("diffusers", "pollinations", "stability"):
            logger.warning(f"Unknown AI_IMAGE_BACKEND '{backend}', using diffusers")
            backend = "diffusers"
        self._backend = backend
        logger.info(f"AI Image backend: {backend}")
        return self._backend

    def generate_image(self, prompt: str, negative_prompt: str = "",
                       width: int = 512, height: int = 512, **kwargs) -> str:
        """
        用 AI 生成图片。自动选择最佳后端。

        Args:
            prompt: 英文提示词 (e.g. 'a cute orange cat sitting on a desk')
            negative_prompt: 反向提示词
            width/height: 图片尺寸 (默认512x512)
        """
        if not prompt.strip():
            return "Error: prompt is required"

        width = max(256, min(width, 1024))
        height = max(256, min(height, 1024))

        backend = self._detect_backend()

        try:
            if backend == "diffusers":
                if self._diffusers_broken:
                    raise RuntimeError("diffusers pipeline unavailable")
                return self._generate_diffusers(prompt, negative_prompt, width, height)
            elif backend == "stability":
                return self._generate_stability(prompt, negative_prompt, width, height)
            else:
                return self._generate_pollinations(prompt, negative_prompt, width, height)
        except Exception as e:
            # 主后端失败 → 降级另一后端（diffusers 加载失败后置 broken，不再反复尝试）
            if backend == "pollinations" and not self._diffusers_broken:
                logger.warning(f"pollinations failed, falling back to diffusers: {e}")
                try:
                    return self._generate_diffusers(prompt, negative_prompt, width, height)
                except Exception as e2:
                    self._diffusers_broken = True
                    logger.warning(f"diffusers fallback failed too (marking broken): {e2}")
                    return f"Error: All backends failed. {e}; {e2}"
            if backend == "diffusers":
                logger.warning(f"diffusers failed, falling back to pollinations: {e}")
                self._diffusers_broken = True
                try:
                    return self._generate_pollinations(prompt, negative_prompt, width, height)
                except Exception as e2:
                    return f"Error: All backends failed. {e}; {e2}"
            return f"Error generating image: {e}"

    def _save_image(self, image_data: bytes, prompt: str) -> str:
        """保存图片到 charts 目录，返回 URL。"""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        # PNG 或 JPEG
        if image_data[:4] == b'\x89PNG':
            ext = "png"
        elif image_data[:2] == b'\xff\xd8':
            ext = "jpg"
        else:
            ext = "bin"
        filename = f"ai_{ts}.{ext}"
        filepath = self.charts_dir / filename
        filepath.write_bytes(image_data)
        url = f"/charts/{filename}"
        logger.info(f"Image saved: {url}")
        alt = prompt[:80].replace('[', '(').replace(']', ')').replace('(', ' ').replace(')', ' ')
        return f"![AI Image]({url})\n> {alt}\n{url}"

    # ── 后端 1: pollinations.ai (免费兜底) ──────────────

    def _generate_pollinations(self, prompt: str, negative_prompt: str,
                               width: int, height: int) -> str:
        """pollinations.ai — 免费, 无需 API key。"""
        params = {
            "width": width,
            "height": height,
            "nologo": "true",
            "nofeed": "true",
        }
        if negative_prompt:
            params["negative"] = negative_prompt

        encoded_prompt = urllib.parse.quote(prompt)
        query = urllib.parse.urlencode(params)
        url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?{query}"

        logger.info(f"pollinations.ai: '{prompt[:80]}' ({width}x{height})")

        # 大陆→Cloudflare TLS 握手约 1/3 概率被丢弃: 重试 3 次, 每次 20s
        for attempt in (1, 2, 3):
            try:
                req = urllib.request.Request(url, headers={
                    "User-Agent": "nano_agent_plus/1.0",
                })
                with urllib.request.urlopen(req, timeout=20) as resp:
                    image_data = resp.read()
                break
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 3:
                    logger.warning(f"pollinations.ai 429, attempt {attempt}/3, retry in 5s")
                    time.sleep(5)
                    continue
                raise RuntimeError(f"pollinations.ai request failed: HTTP {e.code} {e.reason}")
            except urllib.error.URLError as e:
                if attempt < 3:
                    logger.warning(f"pollinations.ai attempt {attempt}/3 failed: {e.reason}, retrying")
                    time.sleep(1)
                    continue
                raise RuntimeError(f"pollinations.ai request failed: {e.reason}")
            except Exception as e:
                if attempt < 3:
                    logger.warning(f"pollinations.ai attempt {attempt}/3 failed: {e}, retrying")
                    time.sleep(1)
                    continue
                raise RuntimeError(f"pollinations.ai request failed: {e}")

        if len(image_data) < 1000:
            raise RuntimeError(f"pollinations.ai returned invalid data ({len(image_data)} bytes)")

        # 检查是否是图片 (PNG/JPEG)
        if image_data[:4] != b'\x89PNG' and image_data[:2] != b'\xff\xd8':
            raise RuntimeError(f"pollinations.ai returned non-image data ({len(image_data)} bytes, starts with {image_data[:4].hex()})")

        return self._save_image(image_data, prompt)

    # ── 后端 2: Stability AI API ──────────────────────

    def _generate_stability(self, prompt: str, negative_prompt: str,
                            width: int, height: int) -> str:
        """Stability AI API — 高质量, 需 API key。"""
        api_key = os.environ.get("STABILITY_API_KEY")
        if not api_key:
            raise RuntimeError("STABILITY_API_KEY not set")

        payload = json.dumps({
            "prompt": prompt,
            "negative_prompt": negative_prompt or "blurry, ugly, low quality",
            "width": width,
            "height": height,
            "steps": 30,
            "cfg_scale": 7,
            "output_format": "png",
        }).encode()

        try:
            req = urllib.request.Request(
                "https://api.stability.ai/v2/stable-image/generate/core",
                data=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "Accept": "image/png",
                    "User-Agent": "nano_agent_plus/1.0",
                },
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                image_data = resp.read()
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Stability API error: HTTP {e.code} — {e.reason}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"Stability API request failed: {e.reason}")
        except Exception as e:
            raise RuntimeError(f"Stability API request failed: {e}")

        if len(image_data) < 1000:
            raise RuntimeError(f"Stability API returned invalid data ({len(image_data)} bytes)")

        if image_data[:4] != b'\x89PNG':
            raise RuntimeError("Stability API returned non-PNG data")

        return self._save_image(image_data, prompt)

    # ── 后端 3: diffusers 本地 ──────────────────────

    def _generate_diffusers(self, prompt: str, negative_prompt: str,
                            width: int, height: int) -> str:
        """diffusers 本地 — 需 PyTorch + diffusers。"""
        global _pipeline
        pipe = self._load_pipeline()
        if pipe is None:
            raise RuntimeError("Failed to load diffusers pipeline")

        import torch
        device = "mps" if torch.backends.mps.is_available() else \
                 "cuda" if torch.cuda.is_available() else "cpu"

        # CPU 推理: 512x512 ~5min, 768 翻倍 — 收到 512 保证可预期
        width = max(256, min(width, 512))
        height = max(256, min(height, 512))

        # 串行化推理: 7.4GB 内存无 swap, 并发 pipe() 会 OOM 杀进程
        with _pipeline_lock:
            logger.info(f"diffusers: '{prompt[:80]}' on {device} ({width}x{height})")
            result = pipe(
                prompt=prompt,
                negative_prompt=negative_prompt or "blurry, ugly, low quality, distorted",
                num_inference_steps=20,
                width=width,
                height=height,
                guidance_scale=7.5,
            )
            image = result.images[0]

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"ai_{ts}.png"
        filepath = self.charts_dir / filename
        image.save(filepath, "PNG")

        url = f"/charts/{filename}"
        alt = prompt[:80].replace('[', '(').replace(']', ')').replace('(', ' ').replace(')', ' ')
        return f"![AI Image]({url})\n> {alt}\n{url}"

    @staticmethod
    def _load_pipeline():
        """懒加载 diffusers pipeline（锁内: 防止并发重复加载 OOM）。"""
        global _pipeline
        with _pipeline_lock:
            if _pipeline is not None:
                return _pipeline
            try:
                from diffusers import StableDiffusionPipeline
                import torch

                device = "mps" if torch.backends.mps.is_available() else \
                         "cuda" if torch.cuda.is_available() else "cpu"
                model_id = "runwayml/stable-diffusion-v1-5"
                logger.info(f"Loading Stable Diffusion ({model_id}) on {device}...")

                dtype = torch.float16 if device == "cuda" else torch.float32
                pipe = StableDiffusionPipeline.from_pretrained(
                    model_id, torch_dtype=dtype, safety_checker=None
                )
                pipe = pipe.to(device)
                pipe.enable_attention_slicing()
                _pipeline = pipe
                logger.info("Stable Diffusion loaded successfully")
                return pipe
            except Exception as e:
                logger.error(f"Failed to load Stable Diffusion: {e}")
                return None

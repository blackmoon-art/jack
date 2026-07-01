"""AI 图像生成 — 多后端: diffusers本地 > Stability API > pollinations.ai 兜底。

优先级:
  1. diffusers 本地 (需 torch + diffusers, 自动检测)
  2. Stability AI API (需 STABILITY_API_KEY 环境变量)
  3. pollinations.ai (免费, 无需配置, 兜底)
"""

import json
import logging
import os
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("nano_agent.tools.ai_image")

# 懒加载全局 pipeline
_pipeline = None


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

    def _detect_backend(self) -> str:
        """检测可用的后端。"""
        if self._backend:
            return self._backend

        # 1. 检查 diffusers 本地
        try:
            import diffusers  # noqa: F401
            import torch  # noqa: F401
            self._backend = "diffusers"
            logger.info("AI Image backend: diffusers (local)")
            return self._backend
        except ImportError:
            pass

        # 2. 检查 Stability API key
        if os.environ.get("STABILITY_API_KEY"):
            self._backend = "stability"
            logger.info("AI Image backend: Stability AI API")
            return self._backend

        # 3. 兜底: pollinations.ai
        self._backend = "pollinations"
        logger.info("AI Image backend: pollinations.ai (free)")
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
                return self._generate_diffusers(prompt, negative_prompt, width, height)
            elif backend == "stability":
                return self._generate_stability(prompt, negative_prompt, width, height)
            else:
                return self._generate_pollinations(prompt, negative_prompt, width, height)
        except Exception as e:
            # 如果首选后端失败，降级到 pollinations
            if backend != "pollinations":
                logger.warning(f"{backend} failed, falling back to pollinations: {e}")
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

        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "nano_agent_plus/1.0",
            })
            with urllib.request.urlopen(req, timeout=60) as resp:
                image_data = resp.read()
        except urllib.error.URLError as e:
            raise RuntimeError(f"pollinations.ai request failed: {e.reason}")
        except Exception as e:
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

        width = max(256, min(width, 768))
        height = max(256, min(height, 768))

        logger.info(f"diffusers: '{prompt[:80]}' on {device}")
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
        """懒加载 diffusers pipeline。"""
        global _pipeline
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

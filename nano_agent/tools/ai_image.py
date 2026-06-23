"""AI 图像生成 — Stable Diffusion via diffusers。

首次使用自动下载模型 (~2GB)，后续秒级生成。
"""

import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("nano_agent.tools.ai_image")

# 懒加载全局 pipeline
_pipeline = None
_device = None


def _get_device():
    global _device
    if _device is None:
        import torch
        _device = "mps" if torch.backends.mps.is_available() else \
                  "cuda" if torch.cuda.is_available() else "cpu"
    return _device


class AIImage:
    # 工具注册声明
    TOOLS = [
        ("ai_image", "Generate AI images from text prompts using Stable Diffusion. Use for: animals, people, scenes, art. Prompt in English.", "generate_image",
         {"prompt": {"type": "string", "description": "Image description in English"},
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

    def generate_image(self, prompt: str, negative_prompt: str = "",
                       steps: int = 20, width: int = 512, height: int = 512) -> str:
        """
        用 Stable Diffusion 生成图片。

        Args:
            prompt: 英文提示词，越具体越好 (e.g. 'a cute orange cat sitting on a desk')
            negative_prompt: 反向提示词，不想要的内容
            steps: 推理步数 (15-30, 默认20, 越多越精细)
            width/height: 图片尺寸 (默认512x512)
        """
        if not prompt.strip():
            return "Error: prompt is required"

        # 懒加载 pipeline
        pipe = self._load_pipeline()
        if pipe is None:
            return "Error: Failed to load Stable Diffusion model"

        device = _get_device()
        try:
            # 限制图片尺寸
            width = max(256, min(width, 768))
            height = max(256, min(height, 768))
            steps = max(10, min(steps, 50))

            logger.info(f"Generating image: '{prompt[:100]}' on {device} ({steps} steps)")
            result = pipe(
                prompt=prompt,
                negative_prompt=negative_prompt or "blurry, ugly, low quality, distorted",
                num_inference_steps=steps,
                width=width,
                height=height,
                guidance_scale=7.5,
            )
            image = result.images[0]

            # 保存
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"ai_{ts}.png"
            filepath = self.charts_dir / filename
            image.save(filepath, "PNG")

            url = f"/charts/{filename}"
            logger.info(f"Image generated: {url} ({width}x{height})")
            return f"AI Image generated: {url}\n![{prompt[:80]}]({url})"

        except Exception as e:
            return f"Error generating image: {e}"

    @staticmethod
    def _load_pipeline():
        global _pipeline
        if _pipeline is not None:
            return _pipeline
        try:
            from diffusers import StableDiffusionPipeline
            import torch

            device = _get_device()
            model_id = "runwayml/stable-diffusion-v1-5"
            logger.info(f"Loading Stable Diffusion ({model_id}) on {device}...")

            if device == "mps":
                pipe = StableDiffusionPipeline.from_pretrained(
                    model_id, torch_dtype=torch.float32, safety_checker=None
                )
                pipe = pipe.to(device)
            elif device == "cuda":
                pipe = StableDiffusionPipeline.from_pretrained(
                    model_id, torch_dtype=torch.float16, safety_checker=None
                )
                pipe = pipe.to(device)
            else:  # CPU
                pipe = StableDiffusionPipeline.from_pretrained(
                    model_id, torch_dtype=torch.float32, safety_checker=None
                )

            pipe.enable_attention_slicing()  # 省内存
            _pipeline = pipe
            logger.info("Stable Diffusion loaded successfully")
            return pipe
        except Exception as e:
            logger.error(f"Failed to load Stable Diffusion: {e}")
            return None

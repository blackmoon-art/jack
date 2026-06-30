"""图片分析工具 — 调用 Vision API 分析图片。支持 OpenAI / Anthropic / Google Gemini。"""

import base64
import json
import logging
import os
import shutil
import threading
from pathlib import Path

logger = logging.getLogger("nano_agent.tools.image_analyze")

# 模型缓存：避免每次分析都重新加载 15GB 模型
_vision_cache: dict = {}  # model_path → {"model": ..., "processor": ...}
_vision_cache_lock = threading.Lock()


class ImageAnalyzer:
    TOOLS = [
        ("analyze_image", "Analyze an uploaded image file. Can describe what's in the image, "
         "extract text (OCR), read charts/tables/screenshots, or answer specific questions. "
         "Supported formats: PNG, JPG, JPEG, GIF, WEBP.",
         "analyze_image",
         {"path": {"type": "string", "description": "Path to the image file in workspace"},
          "question": {"type": "string", "description": "What to analyze. 'describe' for general description, 'extract text' for OCR, or a specific question. Default: describe in detail."}},
         ["path"]),
    ]

    def __init__(self, work_dir: str):
        self.work_dir = Path(work_dir).resolve()

    def _detect_provider(self) -> tuple[str, str, str, str]:
        """检测可用的 Vision 提供商。

        优先级:
          1. MiniCPM-V 本地模型 (免费，离线，最强中文)
          2. Ollama 本地视觉模型 (免费，离线)
          3. Google Gemini (免费，1500次/天)
          4. VISION_API_KEY + VISION_BASE_URL (OpenAI 兼容)
          5. ANTHROPIC_API_KEY (Claude, 原生 vision)
          6. Tesseract 本地 OCR (免费，离线，只提取文字)

        Returns: (provider, api_key, base_url, model)
        """
        from ..config import _ensure_dotenv
        _ensure_dotenv()

        # 1. MiniCPM-V 本地模型 (优先，已内置，中文最强)
        _model_dir = Path(os.getenv("LOCAL_VISION_MODEL", "") or
                          Path(__file__).parent.parent.parent / "web" / "models" / "MiniCPM-V-2_6")
        if (_model_dir / "config.json").exists():
            try:
                import torch  # noqa: F401
                logger.info(f"Vision provider: MiniCPM-V local (free, offline) — {_model_dir}")
                return ("transformers", "", "", str(_model_dir))
            except ImportError:
                logger.warning("MiniCPM-V model found but torch not installed. Run: pip install torch transformers")

        # 2. Ollama 本地视觉模型
        if shutil.which("ollama"):
            model = os.getenv("VISION_MODEL", os.getenv("OLLAMA_VISION_MODEL", "minicpm-v:8b"))
            base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
            if self._ollama_model_available(base_url, model):
                logger.info(f"Vision provider: Ollama local ({model}) — free, offline")
                return ("openai", "ollama", base_url, model)
            else:
                logger.warning(f"Ollama running but model '{model}' not installed. Skipping.")

        # 3. Google Gemini (免费)
        gemini_key = os.getenv("GEMINI_API_KEY", "")
        if gemini_key:
            base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
            model = os.getenv("VISION_MODEL", "gemini-2.5-flash")
            logger.info(f"Vision provider: Google Gemini (free) — {model}")
            return ("openai", gemini_key, base_url, model)

        # 4. 专用 Vision API Key (OpenAI 兼容)
        vision_key = os.getenv("VISION_API_KEY", "")
        if vision_key:
            base_url = os.getenv("VISION_BASE_URL", "https://api.openai.com/v1")
            model = os.getenv("VISION_MODEL", "gpt-4o")
            logger.info(f"Vision provider: OpenAI-compatible ({model})")
            return ("openai", vision_key, base_url, model)

        # 5. Anthropic Claude (原生 vision)
        anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
        if anthropic_key:
            base_url = os.getenv("ANTHROPIC_BASE_URL") or None
            model = os.getenv("VISION_MODEL", "claude-sonnet-4-6")
            logger.info(f"Vision provider: Anthropic ({model})")
            return ("anthropic", anthropic_key, base_url, model)

        # 6. Tesseract 本地 OCR (免费，离线)
        if shutil.which("tesseract"):
            logger.info("Vision provider: Tesseract OCR (local, offline, free)")
            return ("tesseract", "", "", "tesseract")

        return ("none", "", "", "")

    def _analyze_openai(self, data_url: str, mime_type: str, question: str,
                        api_key: str, base_url: str, model: str) -> str:
        """OpenAI 兼容 Vision API (含 Gemini OpenAI 兼容端点)。"""
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=base_url)
        response = client.chat.completions.create(
            model=model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": question},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }],
            max_tokens=2000,
        )
        return response.choices[0].message.content or ""

    def _analyze_anthropic(self, image_data: bytes, mime_type: str, question: str,
                           api_key: str, base_url: str, model: str) -> str:
        """Anthropic Claude Vision API。"""
        from anthropic import Anthropic

        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        client = Anthropic(**kwargs)

        image_b64 = base64.b64encode(image_data).decode("utf-8")
        response = client.messages.create(
            model=model,
            max_tokens=2000,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {
                        "type": "base64",
                        "media_type": mime_type,
                        "data": image_b64,
                    }},
                    {"type": "text", "text": question},
                ],
            }],
        )
        return response.content[0].text

    @staticmethod
    def _load_vision_model(model_path: str) -> dict:
        """懒加载并缓存 Vision 模型（15GB，只加载一次，线程安全）。"""
        # 快速路径：已缓存则直接返回，无需加锁
        if model_path in _vision_cache:
            return _vision_cache[model_path]

        with _vision_cache_lock:
            # 双重检查：可能在等锁时被其他线程加载好了
            if model_path in _vision_cache:
                return _vision_cache[model_path]

            from transformers import AutoModel, AutoProcessor, AutoTokenizer
            import torch

            device = "mps" if torch.backends.mps.is_available() else "cpu"
            logger.info(f"Loading MiniCPM-V from {model_path} on {device} (first call, ~30s)...")

            model = AutoModel.from_pretrained(
                model_path, trust_remote_code=True,
                torch_dtype=torch.float16 if device == "mps" else torch.float32,
            )
            model = model.to(device).eval()
            processor = AutoProcessor.from_pretrained(model_path, trust_remote_code=True, local_files_only=True)

            # transformers≥5.x 的 AutoTokenizer 不再遵守 tokenizer_config.json 的
            # auto_map，导致加载的是 TokenizersBackend 而非 MiniCPMVTokenizerFast，
            # 缺失 im_start_id/im_end_id 等属性。显式加载自定义 tokenizer 修复。
            if not hasattr(processor.tokenizer, "im_start_id"):
                import sys as _sys
                _sys.path.insert(0, model_path)
                from tokenization_minicpmv_fast import MiniCPMVTokenizerFast
                custom_tok = MiniCPMVTokenizerFast.from_pretrained(
                    model_path, trust_remote_code=True, local_files_only=True
                )
                processor.tokenizer = custom_tok
                logger.info("MiniCPM-V tokenizer patched: MiniCPMVTokenizerFast")

            cached = {"model": model, "processor": processor, "device": device}
            _vision_cache[model_path] = cached
        logger.info(f"MiniCPM-V loaded ({device}), cached for reuse")
        return cached

    def _analyze_transformers(self, image_data: bytes, mime_type: str, question: str,
                              model_path: str) -> str:
        """本地 MiniCPM-V 模型分析图片（模型缓存，首次加载后复用）。"""
        try:
            import torch
            from PIL import Image
            import io

            cached = self._load_vision_model(model_path)
            model, processor, device = cached["model"], cached["processor"], cached["device"]

            img = Image.open(io.BytesIO(image_data))

            # MiniCPM-V 要求文本中包含 (<image>./</image>) 标签标记图片位置
            if "(<image>./</image>)" not in question:
                question = f"(<image>./</image>)\n{question}"

            inputs = processor(images=img, text=question, return_tensors="pt").to(device)
            # processor 返回的 image_sizes 不被模型 generate() 接受，需移除
            inputs.pop("image_sizes", None)
            with torch.no_grad():
                result = model.generate(**inputs, max_new_tokens=512, tokenizer=processor.tokenizer)
            answer = processor.decode(result[0], skip_special_tokens=True)

            logger.info(f"MiniCPM-V inference done ({device})")
            return f"[Local MiniCPM-V — offline, free]\n\n{answer}"
        except ImportError as e:
            return f"Error: dependencies not installed — {e}. Run: pip install transformers accelerate torch pillow"
        except Exception as e:
            logger.error(f"MiniCPM-V inference failed: {e}")
            return f"Error: MiniCPM-V inference failed — {e}"

    def _analyze_tesseract(self, filepath, image_data: bytes, question: str) -> str:
        """Tesseract 本地 OCR：从图片提取文字。免费、离线。"""
        try:
            from PIL import Image
            import pytesseract
            import io

            img = Image.open(io.BytesIO(image_data))
            # 自动检测中英文
            # 根据用户意图调整 OCR 策略
            is_extract = any(kw in question.lower() for kw in
                           ('extract', 'ocr', '文字', '提取', 'read', '读取', '文本'))
            text = pytesseract.image_to_string(img, lang="chi_sim+eng")
            text = text.strip()
            if not text:
                return "[Tesseract OCR] No text found in image. This tool only extracts text (OCR). For visual description, configure GEMINI_API_KEY or VISION_API_KEY."

            prefix = "Extracted text" if is_extract else "This OCR tool only extracts text from images. For visual description, use a vision API.\n\nFound text"
            result = f"[Tesseract OCR — offline, free]\n\n{prefix}:\n\n{text}"
            if len(result) > 5000:
                result = result[:5000] + "\n...(truncated)"
            return result
        except ImportError as e:
            return f"Error: Tesseract not available — {e}. Run: brew install tesseract tesseract-lang && pip install pytesseract pillow"
        except Exception as e:
            return f"Error: Tesseract OCR failed — {e}"

    @staticmethod
    def _ollama_model_available(base_url: str, model: str) -> bool:
        """Check if a model is actually installed in Ollama via /api/tags."""
        import urllib.request
        try:
            tags_url = base_url.rstrip("/").removesuffix("/v1") + "/api/tags"
            with urllib.request.urlopen(tags_url, timeout=3) as resp:
                data = json.loads(resp.read())
            installed = [m.get("name", "") for m in data.get("models", [])]
            model_base = model.split(":")[0]
            return any(name.split(":")[0] == model_base for name in installed)
        except Exception:
            return False

    def analyze_image(self, path: str, question: str = "Describe this image in detail") -> str:
        """分析图片：读取 → base64 → Vision API → 返回结果。"""
        try:
            filepath = (self.work_dir / path).resolve()
            filepath.relative_to(self.work_dir)
        except (ValueError, OSError):
            return "Error: Access denied — path traversal blocked"

        if not filepath.exists():
            return f"Error: File not found — {path}"

        ext = filepath.suffix.lower()
        mime_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                    ".gif": "image/gif", ".webp": "image/webp"}
        if ext not in mime_map:
            return f"Error: Unsupported image format '{ext}'. Supported: {', '.join(mime_map.keys())}"

        mime_type = mime_map[ext]

        try:
            image_data = filepath.read_bytes()
            if len(image_data) > 20 * 1024 * 1024:
                return "Error: Image too large (max 20MB)"
        except Exception as e:
            return f"Error reading image: {e}"

        provider, api_key, base_url, model = self._detect_provider()
        if provider == "none":
            return (
                "Error: No vision API configured. Options (sorted by cost):\n"
                "  1. GEMINI_API_KEY — Google Gemini, free tier (1500 req/day)\n"
                "     Get key: https://aistudio.google.com/apikey\n"
                "  2. VISION_API_KEY + VISION_BASE_URL — OpenAI or compatible\n"
                "  3. ANTHROPIC_API_KEY — Claude, native vision"
            )

        image_b64 = base64.b64encode(image_data).decode("utf-8")
        data_url = f"data:{mime_type};base64,{image_b64}"

        try:
            if provider == "anthropic":
                result = self._analyze_anthropic(image_data, mime_type, question,
                                                 api_key, base_url or "", model)
            elif provider == "transformers":
                result = self._analyze_transformers(image_data, mime_type, question, model)
            elif provider == "tesseract":
                result = self._analyze_tesseract(filepath, image_data, question)
            else:
                result = self._analyze_openai(data_url, mime_type, question,
                                              api_key, base_url, model)

            logger.info(f"Image analysis complete: {filepath.name} ({len(image_data)} bytes, {provider})")
            return result
        except Exception as e:
            logger.error(f"Vision API error ({provider}): {e}")
            return f"Error analyzing image: {e}"

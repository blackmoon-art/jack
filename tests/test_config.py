"""Config 模块测试 — 环境变量加载、override、单例。"""

import os
import unittest
from unittest.mock import patch

from nano_agent.config import Config, get_config, _ensure_dotenv


class TestConfigCreation(unittest.TestCase):
    """Config 基本创建。"""

    def test_default_values(self):
        """Config 不传任何参数时有合理默认值。"""
        config = Config()
        self.assertTrue(config.provider)  # 有值即可
        self.assertTrue(config.model)
        self.assertTrue(config.work_dir)
        self.assertGreater(config.max_iterations, 0)
        self.assertGreater(config.memory_window, 0)

    def test_has_expected_fields(self):
        config = Config()
        for attr in ("provider", "model", "work_dir", "max_iterations",
                     "memory_window", "bash_timeout", "charts_dir",
                     "react_max_steps", "reflexion_max_retries",
                     "tot_num_candidates", "tot_score_threshold"):
            self.assertTrue(hasattr(config, attr), f"Missing field: {attr}")

    def test_env_var_override(self):
        """环境变量应覆盖默认值。"""
        with patch.dict(os.environ, {"AGENT_MAX_ITERATIONS": "20"}):
            config = Config()
            self.assertEqual(config.max_iterations, 20)


class TestConfigWithOverrides(unittest.TestCase):
    """Config.with_overrides 不可变性。"""

    def test_override_creates_new_instance(self):
        config = Config()
        config2 = config.with_overrides(max_iterations=99)
        self.assertIsNot(config, config2)
        self.assertNotEqual(config.max_iterations, 99)
        self.assertEqual(config2.max_iterations, 99)

    def test_override_preserves_other_fields(self):
        config = Config()
        original_iter = config.max_iterations
        config2 = config.with_overrides(memory_window=99)
        self.assertEqual(config2.max_iterations, original_iter)
        self.assertEqual(config2.memory_window, 99)


class TestEnsureDotenv(unittest.TestCase):
    """_ensure_dotenv 线程安全。"""

    def test_idempotent(self):
        """多次调用不会重复加载。"""
        import nano_agent.config as cfg_module
        cfg_module._dotenv_loaded = True
        _ensure_dotenv()
        cfg_module._dotenv_loaded = False

    def test_thread_safety(self):
        """并发调用 _ensure_dotenv 不出错。"""
        import nano_agent.config as cfg_module
        import threading
        cfg_module._dotenv_loaded = False
        results = []
        def call():
            try:
                _ensure_dotenv()
                results.append(True)
            except Exception:
                results.append(False)
        threads = [threading.Thread(target=call) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertTrue(all(results))


class TestGetConfigSingleton(unittest.TestCase):
    """get_config() 单例。"""

    def test_returns_same_instance(self):
        c1 = get_config()
        c2 = get_config()
        self.assertIs(c1, c2)


if __name__ == "__main__":
    unittest.main()

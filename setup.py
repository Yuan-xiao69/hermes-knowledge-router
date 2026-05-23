"""knowledge-router — Hermes 行为合规引擎插件。

安装后，在 Hermes 的 config.yaml 中添加:
  plugins:
    enabled:
      - knowledge-router
"""

from setuptools import setup, find_packages

setup(
    name="hermes-knowledge-router",
    version="1.0.0",
    description="Hermes 行为合规引擎 — 路径路由、工具选择、技能强制、步骤顺序、失败汇报",
    author="knowledge-router contributors",
    python_requires=">=3.10",
    packages=find_packages(),
    install_requires=[
        "pyyaml>=5.1",
    ],
    entry_points={
        "hermes_agent.plugins": [
            "knowledge-router = knowledge_router:register",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
)

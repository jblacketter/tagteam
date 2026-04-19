"""
Tagteam

A collaboration framework for structured AI-to-AI handoffs with human oversight.
Configure your lead and reviewer agents via tagteam.yaml.
"""

from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    __version__ = _pkg_version("tagteam")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"

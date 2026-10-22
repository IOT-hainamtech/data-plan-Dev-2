"""Compatibility module for existing imports; use rules.engine in new code."""

from rules.engine import *  # noqa: F401,F403
from rules.engine import _alarm_key, _key  # compatibility for existing contract tests

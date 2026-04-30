"""Pytest configuration for Music Speaks tests."""

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')")
    config.addinivalue_line("markers", "integration: marks tests as integration tests")


# Set test environment variables
import os
os.environ.setdefault("MINIMAX_API_KEY", os.getenv("MINIMAX_API_KEY", ""))
os.environ.setdefault("ADMIN_KEY", os.getenv("ADMIN_KEY", "test-admin-key"))
os.environ.setdefault("OUTPUT_DIR", "/tmp/terry_music_test_outputs")

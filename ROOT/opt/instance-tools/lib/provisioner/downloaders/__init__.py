"""Download handlers for HuggingFace, CivitAI, and generic wget downloads."""

from .huggingface import download_hf
from .wget import download_wget

__all__ = ["download_hf", "download_wget"]

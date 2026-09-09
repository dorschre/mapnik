"""Compatibility entry point; implementation lives in OTTO's adv-dlm50-adapter."""
import sys
from _adv_adapter import load

implementation = load('source')
sys.modules[__name__] = implementation

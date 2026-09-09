"""Compatibility entry point; implementation lives in OTTO's adv-dlm50-adapter."""
import sys
from _adv_adapter import load

implementation = load('fetch_adv')
if __name__ == '__main__':
    implementation.main()
else:
    sys.modules[__name__] = implementation

"""Load the installed ADV adapter, or its neighboring OTTO source checkout."""
from importlib import import_module
from pathlib import Path
import sys


def load(module):
    try:
        return import_module('adv_dlm50_adapter.' + module)
    except ModuleNotFoundError as error:
        if error.name != 'adv_dlm50_adapter':
            raise
    source = (Path(__file__).resolve().parents[3].parent / 'otto-usecase-3' /
              'data-pipeline/adv-dlm50-adapter/src')
    if not (source / 'adv_dlm50_adapter/__init__.py').is_file():
        raise ModuleNotFoundError(
            'Install adv-dlm50-adapter into this Python environment, or place its '
            'checkout at otto-usecase-3/data-pipeline/adv-dlm50-adapter next to Mapnik.')
    sys.path.insert(0, str(source))
    return import_module('adv_dlm50_adapter.' + module)

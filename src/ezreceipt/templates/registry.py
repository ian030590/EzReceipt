"""Discover built-in folders and optional user overrides."""

import os
import shutil
from pathlib import Path

from .model import Template, load_template

BUILTIN_DIR = Path(__file__).parent / "builtin"


class TemplateRegistry:
    def __init__(self, templates_dir: str | Path | None = None) -> None:
        self.templates_dir = templates_dir or os.environ.get("EZRECEIPT_TEMPLATES_DIR")
        found = {t.id: t for t in self._discover(BUILTIN_DIR, user=False)}
        if self.templates_dir:
            found.update({t.id: t for t in self._discover(Path(self.templates_dir), user=True)})
        self.templates = list(found.values())

    @staticmethod
    def _discover(directory: Path, *, user: bool) -> list[Template]:
        return [
            load_template(path, user=user) for path in sorted(directory.glob("*/template.json"))
        ]

    def get(self, template_id: str) -> Template:
        for template in self.templates:
            if template.id == template_id:
                return template
        raise ValueError(f"Unknown template: {template_id}")


def copy_builtin(template_id: str, templates_dir: str | Path) -> Path:
    builtins = TemplateRegistry._discover(BUILTIN_DIR, user=False)
    template = next((t for t in builtins if t.id == template_id), None)
    if template is None:
        raise ValueError(f"Unknown built-in template: {template_id}")
    target = Path(templates_dir) / template.id
    if target.exists():
        raise ValueError(f"Template directory already exists: {target}")
    shutil.copytree(template.directory, target)
    return target / "template.json"

from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Awaitable

import yaml

logger = logging.getLogger(__name__)


@dataclass
class Skill:
    """Loaded skill definition."""
    name: str
    description: str
    version: str = "1.0"
    author: str = ""
    trigger: str = ""           # slash command, e.g. "/weather"
    keywords: list[str] = field(default_factory=list)  # auto-trigger keywords
    system_prompt: str = ""     # injected into LLM context
    examples: list[str] = field(default_factory=list)
    enabled: bool = True
    # runtime
    execute: Callable[..., Awaitable[str]] | None = None
    path: Path | None = None


class SkillsService:
    """Manages skill loading, matching, and execution."""

    def __init__(self, skills_dir: str = "skills") -> None:
        self._skills_dir = Path(skills_dir)
        self._skills: dict[str, Skill] = {}

    @property
    def skills(self) -> dict[str, Skill]:
        return self._skills

    async def load_all(self) -> None:
        """Scan skills directory and load all valid skills."""
        if not self._skills_dir.exists():
            logger.warning("Skills directory not found: %s", self._skills_dir)
            return

        for entry in sorted(self._skills_dir.iterdir()):
            if entry.is_dir():
                await self._load_skill_dir(entry)
            elif entry.suffix in (".yml", ".yaml"):
                await self._load_skill_file(entry)

        logger.info("Loaded %d skills: %s", len(self._skills), list(self._skills.keys()))

    async def _load_skill_dir(self, path: Path) -> None:
        """Load skill from a directory (skill.yml + optional script.py)."""
        manifest = path / "skill.yml"
        if not manifest.exists():
            manifest = path / "skill.yaml"
        if not manifest.exists():
            return

        try:
            with open(manifest, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)

            skill = self._parse_manifest(data, manifest)
            skill.path = path

            # load Python handler if exists
            script = path / "handler.py"
            if script.exists():
                skill.execute = self._load_handler(script, skill.name)

            self._skills[skill.name] = skill
        except Exception:
            logger.exception("Failed to load skill from %s", path)

    async def _load_skill_file(self, path: Path) -> None:
        """Load a single-file skill (just YAML, uses LLM for execution)."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)

            skill = self._parse_manifest(data, path)
            skill.path = path.parent
            self._skills[skill.name] = skill
        except Exception:
            logger.exception("Failed to load skill from %s", path)

    def _parse_manifest(self, data: dict, source: Path) -> Skill:
        """Parse skill manifest YAML into Skill object."""
        name = data.get("name", source.stem)
        return Skill(
            name=name,
            description=data.get("description", ""),
            version=str(data.get("version", "1.0")),
            author=data.get("author", ""),
            trigger=data.get("trigger", f"/{name}"),
            keywords=data.get("keywords", []),
            system_prompt=data.get("system_prompt", ""),
            examples=data.get("examples", []),
            enabled=data.get("enabled", True),
        )

    def _load_handler(self, script: Path, skill_name: str) -> Callable:
        """Dynamically import handler.py and return its execute() function."""
        spec = importlib.util.spec_from_file_location(f"skill_{skill_name}", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        if not hasattr(module, "execute"):
            raise AttributeError(f"Skill '{skill_name}' handler.py has no execute() function")

        return module.execute

    def find_by_trigger(self, command: str) -> Skill | None:
        """Find skill by slash command trigger."""
        for skill in self._skills.values():
            if skill.enabled and skill.trigger == command:
                return skill
        return None

    def find_by_keywords(self, text: str) -> Skill | None:
        """Find skill whose keywords match the text."""
        text_lower = text.lower()
        for skill in self._skills.values():
            if not skill.enabled or not skill.keywords:
                continue
            for kw in skill.keywords:
                if kw.lower() in text_lower:
                    return skill
        return None

    def get_skills_prompt(self) -> str:
        """Build system prompt section describing all available skills."""
        if not self._skills:
            return ""

        lines = [
            "You have access to the following skills/tools. "
            "When a user's request matches a skill, use the information it provides.\n"
        ]
        for skill in self._skills.values():
            if not skill.enabled:
                continue
            lines.append(f"### Skill: {skill.name}")
            lines.append(f"Trigger: {skill.trigger}")
            lines.append(f"Description: {skill.description}")
            if skill.examples:
                lines.append("Examples: " + ", ".join(f'"{e}"' for e in skill.examples))
            if skill.system_prompt:
                lines.append(f"Instructions:\n{skill.system_prompt}")
            lines.append("")

        return "\n".join(lines)

    def list_skills_text(self) -> str:
        """Human-readable list of skills for /skills command."""
        if not self._skills:
            return "No skills installed. Add .yml files to the skills/ directory."

        lines = ["Installed Skills:\n"]
        for skill in self._skills.values():
            status = "+" if skill.enabled else "-"
            has_handler = "[py]" if skill.execute else "[llm]"
            lines.append(
                f"{status} {skill.name} v{skill.version} {has_handler}\n"
                f"  {skill.description}\n"
                f"  Trigger: {skill.trigger}"
            )
        lines.append("\n[py] = Python handler, [llm] = LLM-powered")
        return "\n".join(lines)

    async def execute_skill(self, skill: Skill, query: str, **kwargs) -> str | None:
        """Execute a skill's Python handler if it has one."""
        if skill.execute is None:
            return None

        try:
            result = skill.execute(query, **kwargs)
            if asyncio.iscoroutine(result):
                result = await result
            return str(result)
        except Exception:
            logger.exception("Skill '%s' execution error", skill.name)
            return f"Skill '{skill.name}' failed to execute."

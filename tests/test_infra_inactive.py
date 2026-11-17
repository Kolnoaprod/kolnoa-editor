"""Garde-fou de l'issue #30 : l'infrastructure de revue assistée reste INACTIVE (budget 0 €).
Aucun workflow actif ne doit appeler une IA, lire un secret d'IA, utiliser `pull_request_target` ou un runner payant."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = ("claude-code-action", "codex-action", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN",
             "pull_request_target")


def workflows():
    d = ROOT / ".github" / "workflows"
    return sorted(p for p in d.glob("*") if p.is_file()) if d.is_dir() else []


def test_no_active_workflow_uses_ai_or_ai_secrets():
    for p in workflows():
        txt = p.read_text(encoding="utf-8")
        for bad in FORBIDDEN:
            assert bad not in txt, f"{p.name} référence {bad} : infrastructure d'IA interdite (issue #30)"


def test_only_standard_free_runners():
    for p in workflows():
        for m in re.finditer(r"runs-on:\s*(.+)", p.read_text(encoding="utf-8")):
            v = m.group(1).strip().lower()
            assert re.fullmatch(r"(ubuntu-latest|ubuntu-2\d\.04|ubuntu-24\.04|windows-latest|macos-latest)", v) or "matrix" in v, \
                f"runner non standard dans {p.name} : {v}"
            assert not re.search(r"(large|xlarge|\d+-core|gpu|arm\d*-)", v), f"runner payant dans {p.name}"


def test_inactive_template_cannot_be_read_by_github():
    t = ROOT / "infra" / "inactive" / "claude-sur-commande.yml.modele"
    assert t.exists() and t.suffix == ".modele"
    txt = t.read_text(encoding="utf-8")
    assert "NE PAS COPIER" in txt and "exit 1" in txt and "workflow_dispatch" in txt
    assert not re.search(r"^\s*(issue_comment|pull_request|schedule):", txt, re.M)
    assert not (ROOT / ".github" / "workflows" / t.name).exists()


def test_doc_states_zero_budget():
    d = (ROOT / "docs" / "INFRA_REVUE_ASSISTEE.md").read_text(encoding="utf-8")
    assert "0 €" in d and "INACTIVE" in d and "revues manuelles" in d

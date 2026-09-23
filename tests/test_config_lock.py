import pytest
from pydantic import ValidationError

from skillordeal.config import ModelSpec, load_trial
from skillordeal.lock import build_lock, check_drift
from skillordeal.matrix import expand, shard


@pytest.mark.parametrize("alias", ["opus", "sonnet", "haiku", "fable", "default"])
def test_model_alias_rejected(alias):
    with pytest.raises(ValidationError):
        ModelSpec(id=alias)


def test_full_model_id_ok():
    assert ModelSpec(id="claude-opus-5-5", effort="high").slug == "claude-opus-5-5@high"


def test_trial_loads_with_baseline(trial_dir):
    lt = load_trial(trial_dir / "trial.yaml")
    assert [c.id for c in lt.contenders] == ["baseline", "my-skill", "wrapped"]


def test_lock_materializes_and_wraps(trial_dir):
    lock = build_lock(load_trial(trial_dir / "trial.yaml"), skip_image=True)
    sk = lock["contenders"]["my-skill"]
    assert sk["skill_name"] == "ordeal:my-skill"
    assert sk["expected_skills"] == ["ordeal:my-skill"]
    assert sk["stripped"] == ["validate.cjs"]
    assert lock["contenders"]["wrapped"]["skill_name"] == "ordeal:wrapped"
    assert len(lock["arenas"]["demo"]["sha"]) == 40
    assert lock["contenders"]["baseline"]["expected_skills"] == []


def test_bout_ids_deterministic_and_resume_safe(trial_dir):
    lt = load_trial(trial_dir / "trial.yaml")
    a = expand(build_lock(lt, skip_image=True))
    b = expand(build_lock(lt, skip_image=True))
    assert [k.bout_id for k in a] == [k.bout_id for k in b]
    assert len(a) == 3 * 2 and len({k.bout_id for k in a}) == 6
    # dropping a contender keeps the other bouts' ids
    lock = build_lock(lt, skip_image=True)
    del lock["contenders"]["wrapped"]
    kept = {k.bout_id for k in expand(lock)}
    assert kept <= {k.bout_id for k in a}


def test_shards_partition(trial_dir):
    keys = expand(build_lock(load_trial(trial_dir / "trial.yaml"), skip_image=True))
    parts = [shard(keys, i, 4) for i in range(1, 5)]
    assert sorted(k.bout_id for p in parts for k in p) == sorted(k.bout_id for k in keys)


def test_drift_detects_skill_edit(trial_dir, tmp_path):
    lt = load_trial(trial_dir / "trial.yaml")
    lock = build_lock(lt, skip_image=True)
    assert check_drift(lock, lt, skip_image=True) == []
    (tmp_path / "skills" / "my-skill" / "SKILL.md").write_text(
        "---\nname: my-skill\n---\nchanged\n"
    )
    problems = check_drift(lock, load_trial(trial_dir / "trial.yaml"), skip_image=True)
    assert "contenders.my-skill.tree_hash changed" in problems


def test_drift_detects_prompt_edit(trial_dir):
    lt = load_trial(trial_dir / "trial.yaml")
    lock = build_lock(lt, skip_image=True)
    (trial_dir / "tasks" / "audit.md").write_text("different\n")
    problems = check_drift(lock, load_trial(trial_dir / "trial.yaml"), skip_image=True)
    assert "tasks.audit.prompt_sha256 changed" in problems


def test_scoring_only_edits_keep_bout_ids(trial_dir):
    lt = load_trial(trial_dir / "trial.yaml")
    before = {k.bout_id for k in expand(build_lock(lt, skip_image=True))}
    arena = trial_dir / "arenas" / "demo" / "arena.yaml"
    arena.write_text(arena.read_text() + "notes: now with a note\n")
    cf = trial_dir / "contenders.yaml"
    cf.write_text(cf.read_text().replace("strip: ['*.cjs']}", "strip: ['*.cjs'], license: MIT}"))
    after = {
        k.bout_id for k in expand(build_lock(load_trial(trial_dir / "trial.yaml"), skip_image=True))
    }
    assert before == after
    arena.write_text(arena.read_text().replace("strip: [dist/]", "strip: [dist/, app/]"))
    changed = {
        k.bout_id for k in expand(build_lock(load_trial(trial_dir / "trial.yaml"), skip_image=True))
    }
    assert changed != before

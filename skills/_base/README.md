# skills/ — Convention reference

A **skill** is a single, versioned capability a role can invoke.  Each
skill lives at `skills/<skill_id>/` and consists of two files:

| File | Required | Purpose |
|------|----------|---------|
| `skill.yaml` | yes | Manifest validated by `acc.skills.SkillManifest`. |
| `adapter.py` | yes | Python module exposing the class named in `adapter_class` (must subclass `acc.skills.Skill`). |

`skills/_base/skill.yaml` provides field defaults that every per-skill
manifest deep-merges over.  Lists are replaced wholesale; nested dicts
are merged key-by-key — same rule as `roles/_base/role.yaml`.

## Minimal skill

```yaml
# skills/hello/skill.yaml
purpose: "Say hello to a name."
adapter_class: "HelloSkill"
input_schema:
  type: object
  properties:
    name: {type: string}
  required: [name]
output_schema:
  type: object
  properties:
    greeting: {type: string}
  required: [greeting]
```

```python
# skills/hello/adapter.py
from acc.skills import Skill

class HelloSkill(Skill):
    async def invoke(self, args):
        return {"greeting": f"hello, {args['name']}"}
```

## Loading

```python
from acc.skills import SkillRegistry

reg = SkillRegistry()
reg.load_from()                         # reads $ACC_SKILLS_ROOT or ./skills
print(reg.list_skill_ids())             # → ['echo', 'hello']
result = await reg.invoke("hello", {"name": "world"})
# → {"greeting": "hello, world"}
```

## Risk levels

| Level | Meaning | Cat-A behaviour (Phase 4.3) |
|-------|---------|----------------------------|
| `LOW` | Pure-function, no side effects | Always allowed if in `allowed_skills`. |
| `MEDIUM` | Reads external state | Allowed; logged for audit. |
| `HIGH` | Writes external state | Allowed only if all `requires_actions` are in role's `allowed_actions`. |
| `CRITICAL` | Irreversible side effects | Allowed AND triggers `OVERSIGHT_SUBMIT` to the human-in-the-loop queue. |

Excluded directories during discovery: `_base`, `TEMPLATE`,
`__pycache__`.

See [`acc/skills/manifest.py`](../../acc/skills/manifest.py) for the
authoritative field list.

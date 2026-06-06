"""Provider definitions — credentials and metadata for each LLM service.

this is the only module that touches environment variables for api keys.
keeping it separate from ``models.py`` means:
- credential loading is contained in one file
- ``models.py`` stays pure data composition, easy to read and test
- adding a new provider never touches the model/tier definitions

Schema
------
ModelProvider       -> label + api_key + api_base (defined in schemas/llm.py)

Usage
-----
>>> from delta.llm.providers import NEBIUS
>>> NEBIUS.name
'nebius'
"""

import os
from pathlib import Path

from dotenv import load_dotenv

from delta.schemas.llm import ModelProvider

# -- load env vars --
# walk up from this file's directory to find the project root
# (where ``.env.dev`` lives).  this makes the module self-contained —
# no matter where the app is launched from, the right env file gets
# loaded.
# walk up: providers.py -> llm/ -> delta/ -> src/ -> server/ -> project root
# parents[0] = llm/, parents[1] = delta/, parents[2] = src/,
# parents[3] = server/, parents[4] = project root.
_project_root = Path(__file__).resolve().parents[4]
_env_file = _project_root / ".env.dev"
if _env_file.exists():
    load_dotenv(_env_file)

# -- Nebius AI Studio --
# serves open-weight models at competitive prices.
# docs: https://docs.litellm.ai/docs/providers/nebius
# api keys at https://studio.nebius.ai/settings/api-keys
NEBIUS = ModelProvider(
    name="nebius",
    # fails loudly at import time if the var is missing — which is
    # what we want (better than failing silently at request time).
    api_key=os.environ["NEBIUS_API_KEY"],
    # litellm knows the default endpoint (https://api.studio.nebius.ai/v1),
    # so no api_base override needed.
)

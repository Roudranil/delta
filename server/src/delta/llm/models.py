"""Concrete model definitions for Delta's LLM tiers.

this module is the single source of truth for what models are available,
who hosts them, and how they're routed.  i keep everything here so
adding a new model or provider is a one-file change.

Schema
------
ModelProvider       → label for a service (nebius, together, ...)
ModelVariant        → architecture metadata (name, context, params)
ModelDeployment     → one variant × one provider + pricing + routing
ModelTier           → named group of deployments (light, medium, heavy)

Usage
-----
>>> from delta.llm.models import TIERS
>>> TIERS["light"].deployments[0].litellm_id
'nebius/nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B'
"""

import os

from delta.schemas.llm import ModelDeployment, ModelProvider, ModelTier, ModelVariant

# ── Providers ──
# each provider maps to a litellm-compatible name.  the name becomes
# the first segment of every model string (e.g. "nebius/...").
# api key is loaded from the environment here — this fails fast at
# import time if the var is missing, which is what we want.

NEBIUS = ModelProvider(
    name="nebius",
    # resolved at import time.  make sure ``.env.dev`` (or equivalent)
    # is loaded before this module is imported — typically done in the
    # app's entrypoint or via a ``dotenv.load_dotenv()`` call.
    api_key=os.environ["NEBIUS_API_KEY"],
    # no api_base — litellm knows the default nebius endpoint
    # (https://api.studio.nebius.ai/v1).
)

# ── Model Variants (architecture, no provider) ──
# these describe what the model *is*, regardless of who hosts it.
# i define them separately from deployments so that adding a second
# provider for the same model is just a new ModelDeployment — no
# variant duplication.

NEMOTRON_3_NANO = ModelVariant(
    name="nemotron-3-nano",
    # the fp8 variant on huggingface; used for tokenizer loading.
    huggingface_id="nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-FP8",
    # 262k context window — nebius supports this for the model.
    context_window=262_000,
    # 30b total, ~3b active (moe).  stored for informational purposes.
    num_parameters=30_000_000_000,
)

# slightly more capable model for medium-complexity tasks.
# nebius serves the fp8 quant with reasoning support.
QWEN_235B_THINKING = ModelVariant(
    name="qwen3-235b-thinking",
    huggingface_id="Qwen/Qwen3-235B-A22B-Thinking-2507-FP8",
    context_window=262_000,
    # 235b total, ~22b active (moe).  fast for its size.
    num_parameters=235_000_000_000,
)

# ── Deployments (variant × provider with pricing + routing) ──
# each deployment is a single endpoint the router can call.
# pricing is per token in USD — convert from /1M to per-token by
# dividing by 1_000_000.

# the cheap, fast tier for simple lookups and quick answers.
# nemotron-3 nano on nebius at $0.06/$0.24 per 1M tokens.
LIGHT_DEPLOYMENTS = [
    ModelDeployment(
        provider=NEBIUS,
        model=NEMOTRON_3_NANO,
        # this is the exact model name nebius expects in the api call.
        provider_model_name="nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",
        input_cost_per_token=0.00000006,  # $0.06 / 1M
        output_cost_per_token=0.00000024,  # $0.24 / 1M
        weight=1,
        order=1,
    ),
]

# the capable tier for synthesis, analysis, and complex reasoning.
# qwen 3 235b thinking on nebius at $0.50/$2.00 per 1M tokens.
MEDIUM_DEPLOYMENTS = [
    ModelDeployment(
        provider=NEBIUS,
        model=QWEN_235B_THINKING,
        provider_model_name="Qwen/Qwen3-235B-A22B-Thinking-2507-fast",
        input_cost_per_token=0.00000050,  # $0.50 / 1M
        output_cost_per_token=0.00000200,  # $2.00 / 1M
        weight=1,
        order=1,
    ),
]

# ── Tiers ──
# tiers are what the rest of the app refers to.  graph nodes call
# get_model("light") → gets a router that picks among all deployments
# with model_name="light".

TIERS: dict[str, ModelTier] = {
    "light": ModelTier(
        name="light",
        deployments=LIGHT_DEPLOYMENTS,
        # using default simple-shuffle — cheap models don't need
        # sophisticated routing.
    ),
    "medium": ModelTier(
        name="medium",
        deployments=MEDIUM_DEPLOYMENTS,
    ),
}

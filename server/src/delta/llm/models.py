"""Concrete model definitions for Delta's LLM tiers.

this module is the single source of truth for what models are available,
who hosts them, and how they're routed.  i keep everything here so
adding a new model or provider is a one-file change.

Schema
------
ModelVariant        -> architecture metadata (name, context, params)
ModelDeployment     -> one variant x one provider + pricing + routing
ModelTier           -> named group of deployments (light, medium, heavy)

Providers live in ``delta.llm.providers`` — that's where credentials
and env-var loading happen.

Usage
-----
>>> from delta.llm.models import TIERS
>>> TIERS["light"].deployments[0].litellm_id
'nebius/nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B'
"""

from delta.llm.providers import NEBIUS
from delta.schemas.llm import ModelDeployment, ModelTier, ModelVariant

# -- Model Variants (architecture, no provider) --
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

# second light variant: qwen 3 30b a3b (moe, ~3b active).
# same class as nemotron but a different model family — good for
# diversity when routing between deployments.
QWEN3_30B_A3B = ModelVariant(
    name="qwen3-30b-a3b",
    huggingface_id="Qwen/Qwen3-30B-A3B-Instruct-2507",
    context_window=262_000,
    # 30b total, ~3b active (moe).  comparable to nemotron-3 nano.
    num_parameters=30_000_000_000,
)

# -- Deployments (variant × provider with pricing + routing) --
# each deployment is a single endpoint the router can call.
# pricing is per token in USD — convert from /1M to per-token by
# dividing by 1_000_000.

# the cheap, fast tier for simple lookups and quick answers.
# two model families for diversity: if one goes down or degrades,
# the router can fail over to the other.
LIGHT_DEPLOYMENTS = [
    ModelDeployment(
        provider=NEBIUS,
        model=NEMOTRON_3_NANO,
        # this is the exact model name nebius expects in the api call.
        provider_model_name="nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",
        input_cost_per_token=0.00000006,  # $0.06 / 1M
        output_cost_per_token=0.00000024,  # $0.24 / 1M
        weight=2,
        order=1,
    ),
    # second light deployment — different model, same provider.
    # weight=1 means it gets picked as often as nemotron when both
    # are healthy.  lower the weight if you want to prefer nemotron.
    ModelDeployment(
        provider=NEBIUS,
        model=QWEN3_30B_A3B,
        provider_model_name="Qwen/Qwen3-30B-A3B-Instruct-2507",
        input_cost_per_token=0.00000010,  # $0.10 / 1M
        output_cost_per_token=0.00000030,  # $0.30 / 1M
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

# -- Tiers --
# tiers are what the rest of the app refers to.  graph nodes call
# get_model("light") -> gets a router that picks among all deployments
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

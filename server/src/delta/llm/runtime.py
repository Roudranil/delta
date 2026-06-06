"""LLM runtime — model resolution and cost tracking.

this module provides the single entrypoint the rest of the app uses
to get an LLM instance.  it builds a ``ChatLiteLLMRouter`` from the
tier definitions in ``delta.llm.models``, registers pricing globally
so cost tracking works, and caches the router as a process-level
singleton.

Usage
-----
>>> from delta.llm.runtime import get_model
>>> llm = get_model("light")
>>> response = await llm.ainvoke(messages, model="light")

Notes
-----
the router is built once and reused.  this is safe because
``litellm.Router`` is threadsafe and manages its own internal
deployment health state (cooldowns, rate-limit tracking, etc.)
across all concurrent calls.
"""

import litellm
from langchain_litellm import ChatLiteLLMRouter
from litellm.router import Router as _Router

from delta.llm.models import TIERS


def build_router() -> ChatLiteLLMRouter:
    """Build and return a fully-configured ``ChatLiteLLMRouter``.

    two-phase initialisation:
    1. register all model pricing in ``litellm.model_cost`` so the
       cost calculator can compute ``response_cost`` automatically.
       this is a global side-effect but runs exactly once.
    2. construct a single ``litellm.Router`` with all tiers'
       deployments, then wrap it in ``ChatLiteLLMRouter`` for
       langchain compatibility.

    i keep this separate from ``get_model()`` so tests can call it
    directly with a clean litellm state if needed.

    Returns
    -------
    ChatLiteLLMRouter
        langchain-compatible chat model backed by a litellm Router.

    Notes
    -----
    the router settings below were chosen based on empirical testing
    with nebius endpoints:
    - ``simple-shuffle``: lowest latency overhead for our model count.
    - ``num_retries=2``: one immediate retry + one after cooldown.
    - ``allowed_fails=3``: cool down a deployment after 3 failures/min.
    - ``cooldown_time=30``: 30 seconds before retrying a cooled-down
      deployment.
    - ``enable_weighted_failover``: retry within the same model group
      before escalating to fallbacks.
    """
    # step 1: register all pricing globally.
    # without this, ``response_cost`` stays 0 and the cost callback
    # receives nothing.  i do it here rather than in models.py to
    # keep the side-effect close to the router construction.
    for tier in TIERS.values():
        for dep in tier.deployments:
            # each deployment produces a single-key dict like
            # {"nebius/...": {"input_cost_per_token": ..., ...}}.
            litellm.register_model(dep.to_registration_dict())

    # step 2: build the combined model_list from all tiers.
    # each deployment produces one dict tagged with its tier name as
    # ``model_name``.  deployments sharing the same ``model_name``
    # form a model group that the router balances across.
    model_list: list[dict] = []
    for tier in TIERS.values():
        model_list.extend(tier.to_router_entries())

    # step 3: create the router.
    # using conservative defaults — tune as you gather production data.
    router = _Router(
        model_list=model_list,
        routing_strategy="simple-shuffle",
        # retry failed requests up to 2 times (exponential backoff
        # for rate limits, immediate for other errors).
        num_retries=2,
        # cool down a deployment if it fails more than 3 times in a
        # minute.  prevents hammering a degraded endpoint.
        allowed_fails=3,
        # how long (seconds) a cooled-down deployment stays out of
        # rotation before being re-tried.
        cooldown_time=30,
        # on failure, re-pick within the same model group first
        # (using remaining deployments' weights), then escalate to
        # cross-group fallbacks only if every peer has been tried.
        enable_weighted_failover=True,
    )
    return ChatLiteLLMRouter(router=router)


# process-level singleton.  built once on first call to get_model().
_router: ChatLiteLLMRouter | None = None


def get_model(tier: str) -> ChatLiteLLMRouter:
    """Return the shared ``ChatLiteLLMRouter`` instance.

    the router is lazily initialised on first call and reused for the
    lifetime of the process.  this is safe because ``litellm.Router``
    manages its own internal state (cooldowns, rate-limit tracking,
    deployment health) across all concurrent calls.

    Parameters
    ----------
    tier : str
        the model tier to use.  passed as the ``model`` kwarg at
        call time so the router knows which model group to pick from.
        must be a key in ``TIERS`` (``"light"``, ``"medium"``, ...).

    Returns
    -------
    ChatLiteLLMRouter
        the shared router instance.  call ``ainvoke(messages, model=tier)``
        on it to get a completion.

    Examples
    --------
    >>> llm = get_model("light")
    >>> response = await llm.ainvoke(
    ...     [{"role": "user", "content": "hello"}],
    ...     model="light",
    ... )

    Notes
    -----
    the ``model`` kwarg must be passed explicitly — the router's
    default model is the first entry in ``model_list``, which is
    the first deployment of the first tier iterated.  relying on
    that default would give unpredictable results.
    """
    global _router
    if _router is None:
        # build once, cache forever.  the Router's internal state
        # (cooldowns, usage tracking) persists across calls.
        _router = build_router()
    return _router

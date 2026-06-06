from typing import Literal

from pydantic import BaseModel, computed_field


class ModelProvider(BaseModel):
    """Metadata for a single model provider.

    this is deliberately lightweight.  a provider is just a label plus
    the credentials needed to reach it.  i keep `api_key` and `api_base`
    here (rather than on the deployment) because they're the same for
    every model a provider hosts — duplicating them per deployment
    would be noise and a maintenance trap.

    Parameters
    ----------
    name : str
        short human-readable identifier, e.g. ``"nebius"``, ``"together"``.
        this becomes the first segment of the litellm model string
        (``{name}/{provider_model_name}``), so it must match what
        litellm expects for that provider.
    api_key : str | None
        the api key value.  typically loaded from an env var at the
        call site (``models.py``).  ``None`` means litellm should use
        its default key resolution (e.g. the ``OPENAI_API_KEY`` env
        var for openai models).
    api_base : str | None
        optional base url override.  ``None`` means litellm uses the
        provider's default endpoint.  useful for proxies or self-hosted
        endpoints.

    Notes
    -----
    i store the **value** of the api key, not the env var name.
    this means the env var must be resolved before constructing the
    ``ModelProvider`` instance (typically in ``models.py``).  i chose
    this over lazy env-var-name resolution because:
    - it's explicit — you see the credential flow at the call site
    - it fails early — missing keys are caught at import time, not at
      request time
    - it keeps ``to_router_entry()`` a pure data transformation
    """

    name: str
    api_key: str | None = None
    api_base: str | None = None


class ModelVariant(BaseModel):
    """A specific model architecture, not tied to any provider.

    think of this as the "what" — the model's identity and capabilities.
    the same variant can be deployed by multiple providers (nebius,
    together, huggingface) under different names, quantisations, and
    prices.  that variability lives in ``ModelDeployment``, not here.

    i keep this separate from deployment so adding a second provider
    for the same model only needs a new ``ModelDeployment`` — no
    variant duplication.

    Parameters
    ----------
    name : str
        short unique key, e.g. ``"nemotron-3-nano"``.  used as
        ``base_model`` in router ``model_info``.
    huggingface_id : str | None
        huggingface hub path, e.g. ``"nvidia/...-FP8"``.
        useful for loading the correct tokenizer.
    context_window : int | None
        maximum input length in tokens.  the router uses this for
        pre-call context-window filtering.
    num_parameters : int | None
        total parameter count.  purely informational.
    """

    name: str
    huggingface_id: str | None = None
    context_window: int | None = None
    num_parameters: int | None = None


class ModelDeployment(BaseModel):
    """A specific provider hosting a specific model variant.

    this is the concrete unit of routing — it maps 1:1 to a
    ``litellm.Router`` ``model_list`` entry.  everything the router
    needs to know about a single endpoint lives here: which provider
    serves it, what the provider calls it, how much it costs, and
    how the router should prioritise it.

    i deliberately collapsed pricing and routing into one class
    instead of splitting them, because:
    - every deployment has a price (even if unknown)
    - every deployment has routing knobs
    - splitting would mean repeating the ``provider + variant`` pair

    Parameters
    ----------
    provider : ModelProvider
        who hosts this deployment (nebius, together, ...).
    model : ModelVariant
        what architecture this deployment runs.
    provider_model_name : str
        the name the provider uses for this model.  this is the string
        sent in the API call body, so it must match exactly what the
        provider expects (e.g. ``"nvidia/...-30B-A3B"``, not the
        huggingface id).
    quantisation : str | None
        optional quant info, e.g. ``"fp8"``.  purely informational.
    input_cost_per_token : float | None
        cost per input token in usd.  ``None`` means unknown — cost
        tracking won't work until this is set or the model is
        registered externally.
    output_cost_per_token : float | None
        cost per output token in usd.
    cache_hit_input_cost_per_token : float | None
        cost per input token served from prompt cache, in usd.
        only relevant for providers that support prompt caching
        (anthropic, deepseek, ...).  ``None`` = same as uncached.
    weight : int
        relative selection weight within an ``order`` tier.
        a deployment with ``weight=5`` is picked ~5x more often than
        one with ``weight=1``.  only used by ``simple-shuffle``.
    rpm : int | None
        requests-per-minute cap.  the router tracks usage and avoids
        deployments that would exceed this.
    tpm : int | None
        tokens-per-minute cap.
    order : int
        priority tier.  lower = tried first.  after all deployments
        in ``order=1`` have failed, the router escalates to
        ``order=2``, then ``order=3``, etc.

    Notes
    -----
    ``cache_hit_input_cost_per_token`` is stored on the deployment
    but **not** passed to the router's ``litellm_params`` (the
    ``LiteLLMParamsTypedDict`` has no field for it).  instead i
    inject it into ``litellm.model_cost`` via ``register_model()``,
    which the cost calculator *does* read.  see
    ``to_registration_dict()``.
    """

    provider: ModelProvider
    model: ModelVariant

    # the name this provider uses in its api, e.g.
    # "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B".  this gets sent in the
    # request body, so it must match the provider's model catalogue.
    provider_model_name: str

    # optional quantisation string, purely informational for now.
    quantisation: str | None = None

    # -- Per-deployment pricing (per token, in USD) --
    # these are fed into litellm.model_cost via register_model() so
    # the cost calculator can compute response_cost automatically.
    # None means "i don't know" — cost will be 0 until the model is
    # registered through another mechanism.
    input_cost_per_token: float | None = None
    output_cost_per_token: float | None = None

    # cost per cached input token.  only providers that support prompt
    # caching (anthropic, deepseek) have this; for most it's None.
    cache_hit_input_cost_per_token: float | None = None

    # -- Routing knobs --
    # these control how the litellm Router picks among deployments
    # within the same model group (tier).
    # relative pick frequency within an order tier.  default 1 = even.
    weight: int = 1
    # max requests per minute.  the router avoids hitting this cap.
    rpm: int | None = None
    # max tokens per minute (input + output).
    tpm: int | None = None
    # fallback priority.  lower = tried first.  the router exhausts
    # all deployments in order=1 before moving to order=2.
    order: int = 1

    @computed_field  # type: ignore[prop-decorator]
    @property
    def litellm_id(self) -> str:
        """Full model string litellm uses: ``{provider}/{provider_model_name}``.

        this is what you pass as ``model`` to ``litellm.completion()``
        or set as ``litellm_params.model`` in a router entry.  litellm
        splits on ``/`` to determine the provider and the model name
        to send in the request body.
        """
        return f"{self.provider.name}/{self.provider_model_name}"

    # helpers to build litllm dicts
    # we need two
    # 1. to build the registration dict for litellm.register_model
    # 2. to build the router model list
    def to_registration_dict(self) -> dict:
        """Build a ``{litellm_id: model_info}`` dict for ``litellm.register_model()``.

        this registers pricing in ``litellm.model_cost`` so the cost
        calculator can compute ``response_cost`` automatically.  i
        call this once at startup in ``build_router()``.

        i do **not** inline this into ``to_router_entry()`` because
        ``register_model()`` is a global side-effect that should be
        explicit, not hidden inside a dict getter.

        Returns
        -------
        dict
            a single-key dict mapping ``self.litellm_id`` to a pricing
            info dict.  ready to pass to ``litellm.register_model()``.

        Notes
        -----
        - ``cache_read_input_token_cost`` is set **only** when
          ``cache_hit_input_cost_per_token`` is not None.  litellm's
          cost calculator reads this key to apply discounted pricing
          for cached prompt tokens.
        - ``max_tokens`` defaults to 262k when ``context_window`` is
          not set — that's a safe upper bound for current models.
        """
        info: dict = {
            "max_tokens": self.model.context_window or 262_000,
            "input_cost_per_token": self.input_cost_per_token or 0.0,
            "output_cost_per_token": self.output_cost_per_token or 0.0,
            "litellm_provider": self.provider.name,
            "mode": "chat",
        }
        if self.cache_hit_input_cost_per_token is not None:
            # litellm's cost calculator looks for this exact key.
            info["cache_read_input_token_cost"] = self.cache_hit_input_cost_per_token
        return {self.litellm_id: info}

    def to_router_entry(self, model_name: str) -> dict:
        """Build a ``model_list`` entry for ``litellm.Router(model_list=[...])``.

        Parameters
        ----------
        model_name : str
            the logical model alias (typically the tier name, e.g.
            ``"light"``).  all deployments sharing the same
            ``model_name`` form a model group, and the router picks
            among them using the configured routing strategy.

        Returns
        -------
        dict
            a single entry ready to append to ``Router(model_list=...)``.
            has three top-level keys: ``model_name``, ``litellm_params``,
            and ``model_info``.

        Notes
        -----
        i intentionally omit ``input_cost_per_token`` / ``output_cost_per_token``
        from the returned dict.  although ``LiteLLMParamsTypedDict``
        accepts them, they **don't** flow into the cost calculator
        automatically — the calculator reads from ``litellm.model_cost``
        instead (populated via ``to_registration_dict()`` ->
        ``register_model()``).  including them here would be misleading.
        """
        # start with the routing-specific params.  credentials come
        # from the provider and are only included when set — litellm
        # falls back to env vars when they're absent.
        litellm_params: dict = {
            "model": self.litellm_id,
            "weight": self.weight,
            "rpm": self.rpm,
            "tpm": self.tpm,
            "order": self.order,
        }
        if self.provider.api_key is not None:
            litellm_params["api_key"] = self.provider.api_key
        if self.provider.api_base is not None:
            litellm_params["api_base"] = self.provider.api_base

        entry: dict = {
            "model_name": model_name,
            "litellm_params": litellm_params,
            "model_info": {
                "base_model": self.model.name,
                "huggingface_id": self.model.huggingface_id,
                "context": self.model.context_window,
            },
        }
        return entry


class ModelTier(BaseModel):
    """A named group of deployments that serve the same purpose.

    a tier is what the rest of the application sees — graph nodes call
    ``get_model("light")`` and get a router that picks among all
    deployments in the ``"light"`` tier.  tiers are the unit of routing
    and cost allocation.

    i keep the ``name`` a ``Literal`` here to catch typos at import
    time rather than at runtime.

    Parameters
    ----------
    name : Literal["light", "medium", "heavy"]
        tier identifier.  passed as ``model`` to the router at call
        time.
    deployments : list[ModelDeployment]
        all deployments in this tier.  the router picks among them
        using the configured routing strategy and per-deployment
        weights / orders.
    routing_strategy : str | None
        optional per-group routing strategy override.  when set, this
        tier gets its own ``RoutingGroup`` in the router, independent
        of the router-wide default.  useful when, say, the ``"light"``
        tier should use ``cost-based-routing`` while everything else
        uses ``simple-shuffle``.
    routing_strategy_args : dict
        extra kwargs for the per-group routing strategy (e.g.
        ``{"ttl": 3600}`` for latency-based routing's time window).

    Notes
    -----
    per-group routing requires litellm >= 1.50.  if you set
    ``routing_strategy`` on a tier without the corresponding litellm
    version, the router will silently ignore it and fall back to the
    router-wide strategy.
    """

    name: Literal["light", "medium", "heavy"]
    deployments: list[ModelDeployment]

    # optional per-group routing override.  when set, the router
    # creates a RoutingGroup for this tier instead of using the
    # default strategy.  i keep this optional because most tiers
    # are fine with the router-wide default (simple-shuffle).
    routing_strategy: str | None = None
    routing_strategy_args: dict = {}

    def to_router_entries(self) -> list[dict]:
        """Flatten all deployments into router ``model_list`` entries.

        each deployment produces one entry via its own
        ``to_router_entry()``, tagged with this tier's name.
        the router groups entries by ``model_name``, so all
        entries from the same tier end up in the same model group.

        Returns
        -------
        list[dict]
            one dict per deployment, ready to ``extend`` into
            ``Router(model_list=...)``.
        """
        return [dep.to_router_entry(self.name) for dep in self.deployments]

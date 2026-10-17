"""Explicit Weave spans without serializing service instances or credentials."""

from contextlib import contextmanager

from .config import Settings


class Tracer:
    def __init__(self, config: Settings):
        self.config = config
        self.client = None
        self.initialized = False
        self.warning = None

    def initialize(self):
        if self.initialized:
            return
        self.initialized = True
        if self.config.mode != "live":
            return
        if not self.config.wandb_api_key or not self.config.wandb_project:
            self.warning = "Weave tracing is not configured."
            return
        try:
            import weave

            self.client = weave.init(
                self.config.wandb_project,
                settings={
                    "http_timeout": min(10.0, self.config.request_timeout),
                    "retry_max_attempts": 1,
                },
            )
        except Exception:
            self.warning = "Weave could not initialize; this run has no confirmed remote trace."

    @contextmanager
    def span(self, name: str, inputs: dict, parent=None):
        call = None
        if self.client:
            try:
                call = self.client.create_call(name, inputs, parent=parent, use_stack=False)
            except Exception:
                self.warning = "Weave trace creation failed; inspect the saved local manifest."
        record = {"call": call, "output": None, "url": None}
        if call:
            try:
                record["url"] = call.ui_url
            except Exception:
                self.warning = (
                    "Weave did not provide a trace URL; inspect the saved local manifest."
                )
        try:
            yield record
        except Exception as error:
            if call:
                try:
                    self.client.finish_call(call, exception=RuntimeError(type(error).__name__))
                except Exception:
                    self.warning = "Weave did not confirm trace completion."
            raise
        else:
            if call:
                try:
                    self.client.finish_call(call, output=record["output"])
                except Exception:
                    self.warning = "Weave did not confirm trace completion."

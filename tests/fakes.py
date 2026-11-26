from types import SimpleNamespace


class FakeModel:
    """Scripted stand-in for a chat model. `replies` are consumed in call order:
    a dict for a structured call, a str for a plain call, an Exception to raise."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []          # the message list of every call, in order

    def _next(self, messages):
        self.calls.append(messages)
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply

    def with_structured_output(self, schema):
        return SimpleNamespace(invoke=lambda messages: schema(**self._next(messages)))

    def invoke(self, messages):
        return SimpleNamespace(text=self._next(messages))

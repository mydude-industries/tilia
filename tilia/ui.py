"""Calm terminal screens for setup: one step at a time, a counter, a footer, help at every prompt."""
from __future__ import annotations

# The spec's footer, word for word. Printed on two rows so it never wraps mid-word at 80 columns.
FOOTER = 'Stuck? Type "help". Need a break? Close this. Run ./setup later and you\'ll be right here.'
_FOOTER_ROWS = ('Stuck? Type "help". Need a break? Close this.',
                "Run ./setup later and you'll be right here.")
_RULE = "─" * 48


class UI:
    def __init__(self, io):
        self.io = io
        self.help = ""

    def _c(self, code: str, s: str) -> str:
        return f"\033[{code}m{s}\033[0m" if getattr(self.io, "color", False) else s

    def screen(self, step, total, title, minutes, lines, help: str | None = None) -> None:
        """Clear, then: counter + title + time, the body, and the footer."""
        if help is not None:
            self.help = help
        p = self.io.print
        self.io.clear()
        p("")
        p(self._c("1", f"  Step {step} of {total} · {title}") + self._c("2", f" · about {minutes} min"))
        p(self._c("2", "  " + _RULE))
        p("")
        for line in lines:
            p(f"  {line}" if line else "")
        p("")
        for row in _FOOTER_ROWS:
            p(self._c("2", f"  {row}"))
        p("")

    def ask(self, prompt: str, secret: bool = False) -> str:
        """Ask; `help` prints this step's help and asks again. EOF/Ctrl-C propagate.
        secret=True reads without echo (the typed text never reaches the screen)."""
        read = self.io.secret_input if secret else self.io.input
        while True:
            ans = read(f"  {prompt}")
            if ans.strip().lower() == "help":
                self.io.print("")
                for line in self.help.splitlines():
                    self.io.print(f"  {line}" if line else "")
                self.io.print("")
                continue
            return ans

    def choose(self, prompt: str, options: dict[str, str], default: str | None = None) -> str:
        """Ask until the answer is one of `options` (keys); returns the mapped value."""
        while True:
            ans = self.ask(prompt).strip().lower()
            if not ans and default is not None:
                ans = default
            if ans in options:
                return options[ans]
            keys = list(options)
            self.fail(f"Please type {', '.join(keys[:-1])} or {keys[-1]}.")

    def ok(self, msg: str) -> None:
        self.io.print(f"  {self._c('32', '✓')} {msg}")

    def fail(self, sentence: str) -> None:
        """One calm sentence: what went wrong and what to do."""
        self.io.print(f"  {self._c('33', '✗')} {sentence}")

    def say(self, line: str = "") -> None:
        self.io.print(f"  {line}" if line else "")

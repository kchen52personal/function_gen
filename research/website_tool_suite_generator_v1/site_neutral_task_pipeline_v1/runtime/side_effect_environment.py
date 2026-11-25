"""Avoid automatic execution of generated scripts when actions may persist.

This is not a browser action sandbox: agent-issued commands may still change state.
"""
import ast
import hashlib

from webwright_safe_environment import (SafeToolWorkspaceEnvironment,
    DiagnosticToolWorkspaceEnvironment, PublicGetDiagnosticWorkspaceEnvironment)


class StaticSmokeMixin:
    def _smoke_changed_script(self):
        script = self._final_script_path()
        if not script.is_file():
            return None
        try:
            source = script.read_bytes()
            digest = hashlib.sha256(source).hexdigest()
            if digest == getattr(self, '_codex_smoked_digest', None):
                return None
            self._codex_smoked_digest = digest
            ast.parse(source, filename=str(script))
            return ('STATIC CHECK ONLY: Python syntax passed. The script was NOT executed; '
                    'format, behavior, and task success were not checked. Manual browser commands '
                    'can still change website state.')
        except (OSError, SyntaxError, ValueError) as exc:
            return 'STATIC CHECK FAILED: ' + type(exc).__name__ + '; script was not executed.'


class StateChangingWorkspaceEnvironment(StaticSmokeMixin, SafeToolWorkspaceEnvironment):
    pass


class StateChangingDiagnosticWorkspaceEnvironment(StaticSmokeMixin, DiagnosticToolWorkspaceEnvironment):
    pass


class StateChangingPublicGetWorkspaceEnvironment(StaticSmokeMixin, PublicGetDiagnosticWorkspaceEnvironment):
    pass

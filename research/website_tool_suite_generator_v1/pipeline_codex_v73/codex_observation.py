"""Bounded WebWright observations, including shell and smoke-test output."""

OBSERVATION_TEMPLATE = '''Observation:
Status: {{ 'ok' if observation.success else 'error' }}
Exit code: {{ observation.returncode }}
{% if observation.exception %}Exception: {{ observation.exception }}
{% endif %}Command output (bounded):
{{ observation.command_output }}
{% if observation.final_script_exists %}A final_script.py exists in the workspace.
{% endif %}'''

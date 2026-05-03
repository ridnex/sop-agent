"""Web recorder import pipeline.

The Chrome extension under ``recorder_web/extension/`` produces three files
per session (``recording.webm``, ``events.json``, ``manifest.json``).  The
:mod:`recorder_web.adapter` module converts that bundle into the standard
``outputs/<name> @ <ts>/`` layout the rest of the project already consumes.
"""

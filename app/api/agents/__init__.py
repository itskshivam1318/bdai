"""The agent pipeline.

Planner, Generator, Healer and the meta-agent that coordinates them. See
`app/CLAUDE.md` for what each owns.

`explorer/` is the observation substrate underneath the Planner: it turns a live
page into evidence the rest of the pipeline can reason over. It is deliberately
model-free -- see `explorer/__init__.py`.
"""

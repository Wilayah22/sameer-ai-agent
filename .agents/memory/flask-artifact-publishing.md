---
name: Flask artifact publishing
description: Deployment constraints for serving the standalone Flask product through the artifact publisher.
---

The artifact publisher, not the legacy standalone workflow, owns the public service. Its manifest must map `/` to the Flask service, and commands must reference the Flask app and requirements file relative to the artifact service directory.

**Why:** The artifact runtime starts from `artifacts/api-server`; a root-relative-looking command such as `python sameer-ai-agent/app.py` resolves inside that directory, and a leftover `/api` path leaves the public root without a proxy.

**How to apply:** When changing the published Flask service, update the validated artifact manifest, keep the production health path on `/`, and use `../../sameer-ai-agent/...` paths from the artifact working directory.
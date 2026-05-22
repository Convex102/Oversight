# ASGL-A Cloud (Option A) — v2 Full Repo + Web UI + Stripe + Metering

This repo contains a **working, comprehensive** Option A build:

## Included
- FastAPI backend (multi-tenant)
- API key auth + rate limiting
- Stripe Checkout + Billing Portal + webhooks (provisioning + entitlement updates)
- Usage metering (per job / packs processed) with overage-ready accounting
- Templates (save / list / apply), and selection metadata support
- Async workers (Celery + Redis) with Flower monitoring
- Local or S3-compatible storage for uploads + artifacts
- Servination-styled web UI (FastAPI + Jinja + static assets)
- Excel Office.js add-in + VBA sample

## Quickstart
1) Copy `.env.example` -> `.env` and set keys
2) `docker compose up --build`
3) Web UI: http://localhost:8000/
4) API Docs: http://localhost:8000/docs
5) Flower: http://localhost:5555

> Replace `app/asgl_engine/*` with your proprietary ASGL v13.4 code for production.
